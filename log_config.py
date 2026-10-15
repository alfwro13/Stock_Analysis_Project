import copy
import gzip
import logging
import logging.handlers
import re
import shutil
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

from config import BASE_DIR

logger = logging.getLogger(__name__)

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

_SECRET_QUERY_VALUE = re.compile(r"(?i)\b([a-z0-9_]*(?:api_?key|token))=[^&\s\"']+")

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def redact_secrets(text: str) -> str:
    """Masks api_key=/token=/embed_token= query values: urllib3 and requests put the full URL in their own log lines and exception text."""
    return _SECRET_QUERY_VALUE.sub(r"\1=***", text)


class SafeFormatter(logging.Formatter):
    """A CR/LF inside a logged external value must not start a forged log line; tracebacks stay multi-line."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        line = super().formatMessage(record).replace("\r", "\\r").replace("\n", "\\n")
        return redact_secrets(line)

    def formatException(self, ei) -> str:
        return redact_secrets(super().formatException(ei))


class RedactAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], redact_secrets(args[2]), args[3], args[4])
        return True


def uvicorn_log_config() -> dict:
    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg.setdefault("filters", {})["redact_secrets"] = {"()": "log_config.RedactAccessLogFilter"}
    cfg["handlers"]["access"]["filters"] = ["redact_secrets"]
    return cfg


DEFAULT_LOG_DIR = "logs"


def resolve_log_dir(raw) -> Path | None:
    """A relative folder under the project root, or None: Settings must not let app.log be written (or read back) anywhere else."""
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        return None
    relative = Path(raw.strip())
    if relative.is_absolute():
        return None
    resolved = (BASE_DIR / relative).resolve()
    return resolved if resolved.is_relative_to(BASE_DIR.resolve()) else None


def effective_log_dir(raw) -> Path:
    resolved = resolve_log_dir(raw)
    if resolved is None:
        logger.warning("FILE_LOGGING.LOG_DIR %r is not a folder inside the project; using %s/.", raw, DEFAULT_LOG_DIR)
        return resolve_log_dir(DEFAULT_LOG_DIR)
    return resolved


def configure_file_logging(cfg: dict) -> None:
    """Hot-swappable file handler — safe to call repeatedly; removes old handler first."""
    root = logging.getLogger()

    for handler in list(root.handlers):
        if isinstance(handler, logging.handlers.TimedRotatingFileHandler):
            handler.close()
            root.removeHandler(handler)

    fl = cfg.get("FILE_LOGGING", {})
    if not fl.get("ENABLED", False):
        return

    level_name = fl.get("LEVEL", "INFO").upper()
    if level_name not in _VALID_LEVELS:
        level_name = "INFO"
    level = getattr(logging, level_name)

    days_to_keep = int(fl.get("DAYS_TO_KEEP", 30))
    archive = bool(fl.get("ARCHIVE", False))
    log_dir = effective_log_dir(fl.get("LOG_DIR", DEFAULT_LOG_DIR))
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / "app.log"
    handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path),
        when="midnight",
        backupCount=days_to_keep,
        encoding="utf-8",
        utc=True,
    )
    handler.setLevel(level)
    handler.setFormatter(SafeFormatter(LOG_FORMAT))

    if archive:
        def _namer(name: str) -> str:
            return name + ".gz"

        def _rotator(source: str, dest: str) -> None:
            with open(source, "rb") as f_in, gzip.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            Path(source).unlink()

        handler.namer = _namer
        handler.rotator = _rotator

    root.addHandler(handler)
