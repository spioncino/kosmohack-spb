import hashlib
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

CONFIG_DIR = Path("config")
CODE_DIRS = ("src", "config")
GIT_SHA_ENV = "EVARISK_GIT_SHA"


def clean_git_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Окружение без переменных GIT_*: внутри хука git задаёт GIT_DIR и GIT_INDEX_FILE, и дочерний git с ними
    работает с репозиторием хука, а не с каталогом из -C."""
    source = os.environ if env is None else env
    return {k: v for k, v in source.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=clean_git_env()
    ).stdout.strip()


def git_sha(repo: Path = Path("."), env: Mapping[str, str] | None = None) -> str:
    """Короткий sha HEAD; суффикс -dirty при незакоммиченных изменениях в src/ и config/.

    Значение переменной EVARISK_GIT_SHA имеет приоритет: так образ Docker без git сообщает sha сборки.
    Без git и без переменной результат nogit.
    """
    values = os.environ if env is None else env
    fixed = values.get(GIT_SHA_ENV, "").strip()
    if fixed:
        return fixed
    try:
        sha = _git(repo, "rev-parse", "--short", "HEAD")
        changed = _git(repo, "status", "--porcelain", "--", *CODE_DIRS)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"
    return f"{sha}-dirty" if changed else sha


# Sha фиксируется при старте процесса: он описывает загруженный код, а не текущий HEAD репозитория.
PROCESS_GIT_SHA = git_sha()


def _config_content(content: bytes) -> bytes:
    """Содержимое файла для хеша: в тексте UTF-8 CRLF заменяется на LF, файл не в UTF-8 берётся байт в байт.

    Замена по байтам безопасна для UTF-8: байты 0x0D и 0x0A не встречаются внутри многобайтовых символов.
    """
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    return content.replace(b"\r\n", b"\n")


def config_hash(config_dir: Path = CONFIG_DIR) -> str:
    """Первые 8 знаков sha256 по всем файлам каталога конфигурации, включая подкаталоги.

    Файлы идут в порядке относительного пути с разделителем `/`; для каждого в хеш входят длина и байты пути, длина
    и байты содержимого. Поэтому хеш меняют правка любого файла (правила окон, линии SEP, метеоров, сближений,
    прокси дозы, траектории, таблицы данных), новый файл и переименование. Текст UTF-8 хешируется с концами строк
    LF: checkout того же коммита с CRLF (Windows, core.autocrlf) даёт тот же хеш. Без каталога это хеш пустого набора.
    """
    h = hashlib.sha256()
    files = (
        sorted((p.relative_to(config_dir).as_posix(), p) for p in config_dir.rglob("*") if p.is_file())
        if config_dir.is_dir()
        else []
    )
    for name, path in files:
        for part in (name.encode("utf-8"), _config_content(path.read_bytes())):
            h.update(len(part).to_bytes(8, "big"))
            h.update(part)
    return h.hexdigest()[:8]


def algorithm_version() -> str:
    """Sha кода процесса плюс `config_hash()` каталога `config/` в момент вызова: конфиги читаются на каждый расчёт.

    Формат `<sha>+cfg<8 hex>`; одинаковый коммит и одинаковые конфиги дают одинаковую версию на LF и CRLF checkout.
    """
    return f"{PROCESS_GIT_SHA}+cfg{config_hash()}"
