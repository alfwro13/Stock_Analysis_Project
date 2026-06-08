# log_config.py
import gzip
import logging
import logging.handlers
import shutil
from pathlib import Path

_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_file_logging(cfg: dict) -> None:
    """Attach or remove a rotating file handler on the root logger.

    Safe to call multiple times — removes any existing file handler first so
    settings changes take effect without a restart.
    """
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
    log_dir = Path(fl.get("LOG_DIR", "logs"))
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
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

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
