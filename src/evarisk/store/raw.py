import hashlib
import os
from datetime import datetime
from pathlib import Path

from pydantic import model_validator

from evarisk.types import Frozen, PublishedAtKind

_EXT = {
    "application/json": "json",
    "text/plain": "txt",
    "text/html": "html",
    "text/csv": "csv",
    "application/x-netcdf": "nc",
    "application/xml": "xml",
    "text/xml": "xml",
}


class RawRecord(Frozen):
    id: str
    source: str
    product: str
    url: str
    fetched_at: datetime
    published_at: datetime | None
    published_at_kind: PublishedAtKind
    data_version: str
    sha256: str
    path: str
    size: int
    content_type: str
    http_status: int
    notes: str = ""

    @model_validator(mode="after")
    def _published_consistent(self) -> "RawRecord":
        if (self.published_at is None) != (self.published_at_kind == "unknown"):
            raise ValueError("published_at отсутствует тогда и только тогда, когда kind == unknown")
        return self


def _extension(content_type: str, url: str) -> str:
    base = content_type.split(";")[0].strip().lower()
    if base in _EXT:
        return _EXT[base]
    tail = url.rsplit("/", 1)[-1]
    if "." in tail and len(tail.rsplit(".", 1)[-1]) <= 5:
        return tail.rsplit(".", 1)[-1].lower()
    return "bin"


class RawStore:
    def __init__(self, root: Path, disabled: frozenset[str] = frozenset()) -> None:
        self.root = root
        self.disabled = disabled
        self._index_dir = root / "index"
        self._records: dict[str, RawRecord] | None = None
        self._signature: tuple[tuple[str, int], ...] | None = None

    def without(self, sources: frozenset[str]) -> "RawStore":
        """Вид того же хранилища без записей отключённых источников: расчёт идёт так, будто источника нет.

        `get` и `read` по `raw_id` работают и для отключённых источников: доказательства сохранённых расчётов
        остаются доступными.
        """
        view = RawStore(self.root, frozenset(sources))
        view._records, view._signature = self._records, self._signature
        return view

    def _index_signature(self) -> tuple[tuple[str, int], ...]:
        if not self._index_dir.exists():
            return ()
        return tuple((f.name, f.stat().st_size) for f in sorted(self._index_dir.glob("*.jsonl")))

    def _load(self) -> dict[str, RawRecord]:
        signature = self._index_signature()
        if self._records is None or signature != self._signature:
            records: dict[str, RawRecord] = {}
            for name, _ in signature:
                for line in (self._index_dir / name).read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rec = RawRecord.model_validate_json(line)
                        records[rec.id] = rec
            self._records = records
            self._signature = signature
        return self._records

    def put(
        self,
        *,
        source: str,
        product: str,
        url: str,
        content: bytes,
        fetched_at: datetime,
        published_at: datetime | None,
        published_at_kind: PublishedAtKind,
        content_type: str,
        data_version: str,
        http_status: int = 200,
        notes: str = "",
    ) -> tuple[RawRecord, bool]:
        sha = hashlib.sha256(content).hexdigest()
        raw_id = hashlib.sha256(f"{source}\n{product}\n{url}\n{sha}".encode()).hexdigest()[:24]
        records = self._load()
        if raw_id in records:
            return records[raw_id], False
        rel = Path(source) / product / f"{fetched_at:%Y%m%dT%H%M%SZ}_{sha[:12]}.{_extension(content_type, url)}"
        rec = RawRecord(
            id=raw_id,
            source=source,
            product=product,
            url=url,
            fetched_at=fetched_at,
            published_at=published_at,
            published_at_kind=published_at_kind,
            data_version=data_version,
            sha256=sha,
            path=rel.as_posix(),
            size=len(content),
            content_type=content_type,
            http_status=http_status,
            notes=notes,
        )
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self._index_dir.mkdir(parents=True, exist_ok=True)
        with (self._index_dir / f"{source}__{product}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(rec.model_dump_json() + "\n")
        records[raw_id] = rec
        self._signature = self._index_signature()
        return rec, True

    def amend_availability(
        self, raw_id: str, published_at: datetime | None, published_at_kind: PublishedAtKind, reason: str
    ) -> RawRecord:
        """Исправление времени доступности записи: содержимое неизменно, строка индекса переписывается, причина
        и прежнее значение остаются в `notes`.

        Файл индекса заменяется целиком через временный файл в том же каталоге и `os.replace`: прерванная
        перезапись оставляет прежний файл, а не обрезанный. Имя временного файла не оканчивается на `.jsonl`,
        поэтому в подпись индекса и в чтение он не попадает.
        """
        records = self._load()
        old = records[raw_id]
        trace = f"{reason}; прежняя доступность {old.published_at.isoformat() if old.published_at else 'нет'}"
        new = old.model_copy(
            update={
                "published_at": published_at,
                "published_at_kind": published_at_kind,
                "notes": f"{old.notes}; {trace}" if old.notes else trace,
            }
        )
        index_file = self._index_dir / f"{old.source}__{old.product}.jsonl"
        lines = [
            new.model_dump_json() if RawRecord.model_validate_json(line).id == raw_id else line
            for line in index_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        tmp = index_file.with_name(f"{index_file.name}.{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, index_file)
        self._records = None
        return new

    def get(self, raw_id: str) -> RawRecord:
        return self._load()[raw_id]

    def read(self, raw_id: str) -> bytes:
        return (self.root / self.get(raw_id).path).read_bytes()

    def records(self, source: str | None = None, product: str | None = None) -> list[RawRecord]:
        found = [
            r
            for r in self._load().values()
            if r.source not in self.disabled
            and (source is None or r.source == source)
            and (product is None or r.product == product)
        ]
        return sorted(found, key=lambda r: (r.fetched_at, r.id))
