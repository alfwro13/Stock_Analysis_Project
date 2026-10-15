import gzip
import logging
import logging.config
import logging.handlers
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from log_config import (
    LOG_FORMAT, RedactAccessLogFilter, SafeFormatter, configure_file_logging, redact_secrets, uvicorn_log_config,
)

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


def _record(msg, *args, exc_info=None):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, exc_info)


class TestSafeFormatter:
    def test_cr_and_lf_in_message_are_escaped(self):
        out = SafeFormatter(LOG_FORMAT).format(_record("headline %s", "ok\r\n2026-01-01 - fake - ERROR - forged"))
        assert "\n" not in out and "\r" not in out
        assert "ok\\r\\n2026-01-01 - fake - ERROR - forged" in out

    def test_traceback_stays_multi_line(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            out = SafeFormatter(LOG_FORMAT).format(_record("failed", exc_info=sys.exc_info()))
        assert out.count("\n") >= 2
        assert "ValueError: boom" in out

    def test_traceback_text_is_redacted(self):
        try:
            raise ValueError("GET /fred?api_key=SECRETKEY&x=1 failed")
        except ValueError:
            import sys
            out = SafeFormatter(LOG_FORMAT).format(_record("failed", exc_info=sys.exc_info()))
        assert "SECRETKEY" not in out

    def test_file_handler_writes_one_line_per_record(self, tmp_path):
        log_dir = tmp_path / "logs"
        configure_file_logging({"FILE_LOGGING": {"ENABLED": True, "LEVEL": "INFO", "LOG_DIR": str(log_dir)}})
        logging.getLogger("test.safe").warning("value=%s", "a\nb")
        for h in logging.getLogger().handlers:
            h.flush()
        assert len((log_dir / "app.log").read_text().splitlines()) == 1


class TestRedactSecrets:
    @pytest.mark.parametrize("text", [
        "GET /fred/series/observations?series_id=GDP&api_key=SECRETKEY&file_type=json",
        "HTTPError 401 for url: https://api.stlouisfed.org/x?api_key=SECRETKEY",
        "/portfolio?embed=true&embed_token=SECRETKEY",
        "/reset-password?token=SECRETKEY",
        "retry after 'x': /fred?apikey=SECRETKEY)",
    ])
    def test_secret_value_masked(self, text):
        assert "SECRETKEY" not in redact_secrets(text)
        assert "=***" in redact_secrets(text)

    def test_other_parameters_left_alone(self):
        text = "GET /fred?series_id=GDP&file_type=json tokens=5"
        assert redact_secrets(text) == text


class TestUvicornAccessRedaction:
    def test_filter_masks_the_request_path_argument(self):
        rec = _record('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/portfolio?embed_token=SECRETKEY", "1.1", 200)
        assert RedactAccessLogFilter().filter(rec) is True
        assert "SECRETKEY" not in rec.getMessage()
        assert rec.args[0] == "1.2.3.4:5" and rec.args[4] == 200

    def test_filter_ignores_other_records(self):
        rec = _record("plain %s", "x")
        assert RedactAccessLogFilter().filter(rec) is True
        assert rec.getMessage() == "plain x"

    def test_uvicorn_config_attaches_filter_to_access_handler(self):
        cfg = uvicorn_log_config()
        assert cfg["handlers"]["access"]["filters"] == ["redact_secrets"]
        assert cfg["filters"]["redact_secrets"]["()"] == "log_config.RedactAccessLogFilter"
        logging.config.dictConfig({**cfg, "disable_existing_loggers": False})
