import gzip
import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from log_config import configure_file_logging

pytestmark = pytest.mark.config


def _file_handlers(logger):
    return [h for h in logger.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)]


@pytest.fixture(autouse=True)
def clean_root_handlers():
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for h in list(root.handlers):
        if h not in before:
            h.close()
            root.removeHandler(h)


class TestDisabled:
    def test_no_handler_added_when_disabled(self, tmp_path):
        cfg = {"FILE_LOGGING": {"ENABLED": False, "LOG_DIR": str(tmp_path / "logs")}}
        configure_file_logging(cfg)
        assert _file_handlers(logging.getLogger()) == []

    def test_no_handler_added_when_key_missing(self, tmp_path):
        configure_file_logging({})
        assert _file_handlers(logging.getLogger()) == []

    def test_existing_handler_removed_when_disabled(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg_on = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg_on)
        assert len(_file_handlers(logging.getLogger())) == 1

        cfg_off = {"FILE_LOGGING": {"ENABLED": False}}
        configure_file_logging(cfg_off)
        assert _file_handlers(logging.getLogger()) == []


class TestEnabled:
    def test_handler_added_when_enabled(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        handlers = _file_handlers(logging.getLogger())
        assert len(handlers) == 1

    def test_log_file_created(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        logging.getLogger("test.log_config").info("test message")
        assert (log_dir / "app.log").exists()

    def test_level_applied(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "WARNING", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        handlers = _file_handlers(logging.getLogger())
        assert handlers[0].level == logging.WARNING

    def test_invalid_level_falls_back_to_info(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "NONSENSE", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        handlers = _file_handlers(logging.getLogger())
        assert handlers[0].level == logging.INFO

    def test_hot_reload_replaces_handler(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg1 = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg1)
        handler_1 = _file_handlers(logging.getLogger())[0]

        cfg2 = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "ERROR", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg2)
        handlers = _file_handlers(logging.getLogger())
        assert len(handlers) == 1
        assert handlers[0] is not handler_1
        assert handlers[0].level == logging.ERROR

    def test_log_dir_created_if_missing(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        assert not log_dir.exists()
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        assert log_dir.exists()


class TestArchive:
    def test_archive_sets_namer_and_rotator(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": True, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        h = _file_handlers(logging.getLogger())[0]
        assert h.namer is not None
        assert h.rotator is not None

    def test_archive_namer_appends_gz(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": True, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        h = _file_handlers(logging.getLogger())[0]
        assert h.namer("app.log.2026-06-07") == "app.log.2026-06-07.gz"

    def test_archive_rotator_produces_gz_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        source = log_dir / "app.log.2026-06-07"
        dest = log_dir / "app.log.2026-06-07.gz"
        source.write_text("hello log")

        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": True, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        h = _file_handlers(logging.getLogger())[0]
        h.rotator(str(source), str(dest))

        assert dest.exists()
        assert not source.exists()
        with gzip.open(dest, "rb") as f:
            assert f.read() == b"hello log"

    def test_no_archive_leaves_namer_default(self, tmp_path):
        log_dir = tmp_path / "logs"
        cfg = {"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "DAYS_TO_KEEP": 7, "ARCHIVE": False, "LOG_DIR": str(log_dir)}}
        configure_file_logging(cfg)
        h = _file_handlers(logging.getLogger())[0]
        assert not hasattr(h, 'namer') or h.namer is None or h.namer == logging.handlers.BaseRotatingHandler.namer
