"""Application logging: Info/Debug/Trace levels, a rotating log file under
``<data dir>/logs``, and redaction of secrets before anything is written.

Info is Mangarr's own messages. Debug adds Mangarr's detail, web UI requests
and scheduler runs. Trace adds every outbound HTTP request (httpx). Keys,
passwords, tokens, cookies and URL credentials are masked in every handler,
including the container console.
"""
import io
import logging
import logging.handlers
import re
import threading
import zipfile
from contextlib import nullcontext
from functools import wraps
from pathlib import Path

TRACE = 5
logging.addLevelName(TRACE, "TRACE")
LEVELS = {"info": logging.INFO, "debug": logging.DEBUG, "trace": TRACE}
FILE_NAME = "mangarr.txt"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 4  # mangarr.txt + 4 rotated files
MASK = "<redacted>"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Query/form parameters and header-like "name: value" pairs whose values are secrets.
_SECRET_NAMES = r"api[_-]?key|apikey|x-api-key|token|access[_-]?token|refresh[_-]?token|password|passwd|secret|client[_-]?secret|authorization|cookie|session"
_PARAM = re.compile(rf"""(?i)\b({_SECRET_NAMES})(=|%3D)(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^&\s"'#]+)""")
_HEADER = re.compile(rf"""(?i)\b({_SECRET_NAMES})(["']?\s*:\s*)(?:"((?:\\.|[^"\\\r\n])*)"|'((?:\\.|[^'\\\r\n])*)'|([^\r\n,}}]+))""")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")

_lock = threading.Lock()
_secrets: frozenset[str] = frozenset()
_file_handler: logging.Handler | None = None
_log_dir: Path | None = None
_level = "info"


def redact(text: str) -> str:
    for value in sorted(_secrets, key=len, reverse=True):
        if value in text:
            text = text.replace(value, MASK)
    text = _USERINFO.sub(rf"\1{MASK}@", text)
    text = _PARAM.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    def header(match):
        quote = '"' if match.group(3) is not None else "'" if match.group(4) is not None else ""
        value = next(v for v in match.groups()[2:] if v is not None)
        scheme = re.match(r"(?i)^(bearer\s+|basic\s+)", value)
        prefix = scheme.group() if scheme else ""
        return f"{match.group(1)}{match.group(2)}{quote}{prefix}{MASK}{quote}"
    return _HEADER.sub(header, text)


class RedactingFormatter(logging.Formatter):
    """Masks secrets in the fully formatted record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def set_secrets(values) -> None:
    """Exact secret values to mask wherever they appear (settings secrets, API keys).
    Very short values are ignored so ordinary words are not masked."""
    global _secrets
    _secrets = frozenset(v for v in values if isinstance(v, str) and len(v) >= 6)


def add_secrets(values) -> None:
    set_secrets(set(_secrets) | {v for v in values if isinstance(v, str)})


def setup(data_dir=None, level: str = "info") -> None:
    """Install the console handler, plus the rotating file handler when a data
    directory is given (at startup, not at import), on the root logger. Idempotent."""
    global _file_handler, _log_dir
    root = logging.getLogger()
    formatter = RedactingFormatter(FORMAT)
    with _lock:
        for handler in list(root.handlers):
            if getattr(handler, "_mangarr", False):
                root.removeHandler(handler)
                handler.close()
        console = logging.StreamHandler()
        console._mangarr = True
        console.setFormatter(formatter)
        root.addHandler(console)
        # Uvicorn's loggers go through the same redacting handlers.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            named = logging.getLogger(name)
            for handler in list(named.handlers):
                named.removeHandler(handler)
            named.propagate = True
        _file_handler = None
        _log_dir = None if data_dir is None else Path(data_dir) / "logs"
        if _log_dir is not None:
            try:
                _log_dir.mkdir(parents=True, exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    _log_dir / FILE_NAME, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
            except OSError as exc:
                _log_dir = None
                logging.getLogger("mangarr").warning("Log file disabled; cannot write %s: %s", Path(data_dir) / "logs", exc)
            else:
                handler._mangarr = True
                handler.setFormatter(formatter)
                root.addHandler(handler)
                _file_handler = handler
    set_level(level)


def normalize(level: str) -> str:
    level = (level or "").strip().lower()
    if level in ("warning", "warn", "error", "critical"):
        return "info"  # the old MANGARR_LOG_LEVEL accepted these; Info is the quietest choice now
    if level not in LEVELS:
        raise ValueError("Log level must be info, debug or trace")
    return level


def environment_level(value: str) -> str:
    """MANGARR_LOG_LEVEL, tolerating old or mistyped values (falls back to Info)."""
    try:
        return normalize(value)
    except ValueError:
        return "info"


def set_level(level: str) -> str:
    """Apply a level immediately to Mangarr and the noisy third-party loggers."""
    global _level
    level = normalize(level)
    number = LEVELS[level]
    # Libraries stay at Info (aiosqlite alone logs every query at Debug); only
    # Mangarr's own loggers follow the chosen level.
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("mangarr").setLevel(number)
    # Outbound requests (one line each) only at Trace.
    logging.getLogger("httpx").setLevel(logging.INFO if level == "trace" else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # Web UI requests and "job executed" lines from Debug up.
    quiet = logging.WARNING if level == "info" else logging.INFO
    logging.getLogger("uvicorn.access").setLevel(quiet)
    logging.getLogger("apscheduler").setLevel(quiet)
    _level = level
    return level


def current_level() -> str:
    return _level


def current_file() -> Path | None:
    return None if _log_dir is None else _log_dir / FILE_NAME


def _files() -> list[Path]:
    if _log_dir is None:
        return []
    names = [FILE_NAME] + [f"{FILE_NAME}.{n}" for n in range(1, BACKUPS + 1)]
    return [p for p in (_log_dir / n for n in names) if p.is_file()]


def _read_snapshot(function):
    @wraps(function)
    def read(*args, **kwargs):
        handler = _file_handler
        # RotatingFileHandler uses this same reentrant lock while writing.
        # Keep its filenames stable throughout a view or archive request.
        with handler.lock if handler is not None else nullcontext():
            return function(*args, **kwargs)
    return read


@_read_snapshot
def files() -> list[dict]:
    out = []
    for path in _files():
        stat = path.stat()
        out.append({"name": path.name, "bytes": stat.st_size, "modified": stat.st_mtime})
    return out


@_read_snapshot
def tail(lines: int = 300) -> list[str]:
    """Last ``lines`` lines of the current log file (reads at most 2 MB)."""
    paths = _files()
    if not paths:
        return []
    if _file_handler is not None:
        _file_handler.flush()
    with paths[0].open("rb") as handle:
        handle.seek(0, io.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - 2 * 1024 * 1024))
        data = handle.read().decode("utf-8", errors="replace")
    result = data.splitlines()
    if size > 2 * 1024 * 1024:
        result = result[1:]  # first line is probably cut in half
    return [redact(line) for line in result[-lines:]]


@_read_snapshot
def archive() -> bytes:
    """ZIP of the current and rotated log files. Re-redacted with the current
    secrets, so values saved after a line was written are masked too."""
    if _file_handler is not None:
        _file_handler.flush()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for path in _files():
            text = path.read_text(encoding="utf-8", errors="replace")
            z.writestr(path.name, "\n".join(redact(line) for line in text.split("\n")))
    return buffer.getvalue()
