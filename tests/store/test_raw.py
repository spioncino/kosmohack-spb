from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from evarisk.store.raw import RawRecord, RawStore

T = datetime(2024, 5, 10, 18, 51, 53, tzinfo=UTC)
F = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def put(store: RawStore, content: bytes = b"abc", url: str = "https://x.invalid/a.txt") -> tuple[RawRecord, bool]:
    return store.put(
        source="nasa_oem",
        product="oem",
        url=url,
        content=content,
        fetched_at=F,
        published_at=T,
        published_at_kind="exact",
        content_type="text/plain",
        data_version="test",
    )


def test_put_creates_file_and_index(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    rec, created = put(store)
    assert created
    assert (tmp_path / rec.path).read_bytes() == b"abc"
    assert rec.path == "nasa_oem/oem/20260918T120000Z_ba7816bf8f01.txt"
    index = (tmp_path / "index" / "nasa_oem__oem.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(index) == 1
    assert rec.size == 3


def test_same_content_same_url_is_deduplicated(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    a, _ = put(store)
    b, created = put(store)
    assert not created
    assert a.id == b.id
    assert len(list((tmp_path / "nasa_oem" / "oem").iterdir())) == 1


def test_new_content_same_url_is_new_record(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    a, _ = put(store, b"abc")
    b, created = put(store, b"abd")
    assert created and a.id != b.id


def test_records_persist_across_instances(tmp_path: Path) -> None:
    rec, _ = put(RawStore(tmp_path))
    again = RawStore(tmp_path)
    assert again.get(rec.id) == rec
    assert again.read(rec.id) == b"abc"
    assert [r.id for r in again.records(source="nasa_oem")] == [rec.id]
    assert again.records(source="other") == []


def test_records_written_by_another_instance_become_visible(tmp_path: Path) -> None:
    reader = RawStore(tmp_path)
    writer = RawStore(tmp_path)
    assert reader.records() == []
    rec, _ = put(writer)
    assert reader.records() == [rec] and reader.get(rec.id) == rec
    other, _ = put(writer, content=b"xyz")
    assert [r.id for r in reader.records()] == [rec.id, other.id]
    assert writer.records() == reader.records()


def test_unknown_published_requires_unknown_kind(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    with pytest.raises(ValidationError):
        store.put(
            source="s",
            product="p",
            url="u",
            content=b"x",
            fetched_at=F,
            published_at=None,
            published_at_kind="exact",
            content_type="text/plain",
            data_version="test",
        )
    with pytest.raises(ValidationError):
        store.put(
            source="s",
            product="p",
            url="u",
            content=b"x",
            fetched_at=F,
            published_at=T,
            published_at_kind="unknown",
            content_type="text/plain",
            data_version="test",
        )


def test_naive_fetched_at_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        RawStore(tmp_path).put(
            source="s",
            product="p",
            url="u",
            content=b"x",
            fetched_at=datetime(2026, 9, 18),
            published_at=None,
            published_at_kind="unknown",
            content_type="text/plain",
            data_version="test",
        )


def test_extension_from_content_type_and_url(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    j, _ = store.put(
        source="s",
        product="p",
        url="https://x.invalid/a",
        content=b"{}",
        fetched_at=F,
        published_at=None,
        published_at_kind="unknown",
        content_type="application/json",
        data_version="test",
    )
    n, _ = store.put(
        source="s",
        product="p",
        url="https://x.invalid/b.nc",
        content=b"\x89HDF",
        fetched_at=F,
        published_at=None,
        published_at_kind="unknown",
        content_type="application/octet-stream",
        data_version="test",
    )
    assert j.path.endswith(".json") and n.path.endswith(".nc")


def test_version_is_stored(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    rec, _ = store.put(
        source="s",
        product="p",
        url="u",
        content=b"x",
        fetched_at=F,
        published_at=T,
        published_at_kind="exact",
        content_type="text/plain",
        data_version="issue 0030",
    )
    assert RawStore(tmp_path).get(rec.id).data_version == "issue 0030"


def test_view_without_a_source_hides_its_records_but_keeps_evidence_readable(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    rec, _ = put(store)
    view = store.without(frozenset({"nasa_oem"}))
    assert view.records("nasa_oem") == [] and view.records() == []
    assert view.get(rec.id) == rec and view.read(rec.id) == b"abc"
    assert store.records("nasa_oem") == [rec]
    assert store.without(frozenset({"donki"})).records("nasa_oem") == [rec]


# S3 Last-Modified файла OEM зеркала: время доступности точнее, чем CREATION_DATE эфемериды (D57).
AMENDED = datetime(2024, 5, 10, 19, 12, 7, tzinfo=UTC)


def test_amend_availability_replaces_the_index_file_without_leaving_temporaries(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    rec, _ = put(store)
    other, _ = put(store, content=b"xyz")
    new = store.amend_availability(rec.id, AMENDED, "derived", "S3 Last-Modified")
    assert new.published_at == AMENDED and new.published_at_kind == "derived"
    assert "S3 Last-Modified" in new.notes and T.isoformat() in new.notes
    index_dir = tmp_path / "index"
    assert sorted(p.name for p in index_dir.iterdir()) == ["nasa_oem__oem.jsonl"]
    again = RawStore(tmp_path)
    assert again.get(rec.id) == new
    assert again.get(other.id) == other
    assert [r.id for r in again.records()] == [rec.id, other.id]
