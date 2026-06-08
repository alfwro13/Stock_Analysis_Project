import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from report_dispatcher import (
    push_morning_quant_briefing,
    push_lunchtime_quant_briefing,
    _dispatch_briefing,
)


def _config_with_dispatcher(enabled=True, key="DISPATCHER"):
    return {"SCHEDULING": {key: {"ENABLED": enabled}}}


class TestDispatchBriefingCredentialGuard:

    def test_returns_false_when_credentials_missing(self, tmp_path):
        fake_file = tmp_path / "briefing.md"
        fake_file.write_text("content")
        with patch.dict(os.environ, {}, clear=False):
            for var in ["NEXTCLOUD_URL", "NEXTCLOUD_BOT_USERNAME",
                        "NEXTCLOUD_APP_PASSWORD", "NEXTCLOUD_CONVERSATION_TOKEN"]:
                os.environ.pop(var, None)
            result = _dispatch_briefing(
                local_file_path=str(fake_file),
                remote_folder="quant_briefings",
                remote_filename="briefing.md",
                notify_msg="test",
                config={},
            )
        assert result is False

    def test_returns_false_when_file_missing(self):
        creds = {
            "NEXTCLOUD_URL": "https://nc.example.com",
            "NEXTCLOUD_BOT_USERNAME": "bot",
            "NEXTCLOUD_APP_PASSWORD": "pass",
            "NEXTCLOUD_CONVERSATION_TOKEN": "tok",
        }
        with patch.dict(os.environ, creds):
            result = _dispatch_briefing(
                local_file_path="/nonexistent/path/briefing.md",
                remote_folder="quant_briefings",
                remote_filename="briefing.md",
                notify_msg="test",
                config={},
            )
        assert result is False

    def test_returns_false_when_upload_fails(self, tmp_path):
        fake_file = tmp_path / "briefing.md"
        fake_file.write_text("content")
        creds = {
            "NEXTCLOUD_URL": "https://nc.example.com",
            "NEXTCLOUD_BOT_USERNAME": "bot",
            "NEXTCLOUD_APP_PASSWORD": "pass",
            "NEXTCLOUD_CONVERSATION_TOKEN": "tok",
        }
        with (
            patch.dict(os.environ, creds),
            patch("report_dispatcher.ensure_remote_directory"),
            patch("report_dispatcher.upload_file_webdav", return_value=False),
        ):
            result = _dispatch_briefing(
                local_file_path=str(fake_file),
                remote_folder="quant_briefings",
                remote_filename="briefing.md",
                notify_msg="test",
                config={},
            )
        assert result is False

    def test_returns_true_on_full_success(self, tmp_path):
        fake_file = tmp_path / "briefing.md"
        fake_file.write_text("content")
        creds = {
            "NEXTCLOUD_URL": "https://nc.example.com",
            "NEXTCLOUD_BOT_USERNAME": "bot",
            "NEXTCLOUD_APP_PASSWORD": "pass",
            "NEXTCLOUD_CONVERSATION_TOKEN": "tok",
        }
        with (
            patch.dict(os.environ, creds),
            patch("report_dispatcher.ensure_remote_directory"),
            patch("report_dispatcher.upload_file_webdav", return_value=True),
            patch("report_dispatcher.share_file_to_talk", return_value=True),
            patch("report_dispatcher.send_text_message", return_value=True),
        ):
            result = _dispatch_briefing(
                local_file_path=str(fake_file),
                remote_folder="quant_briefings",
                remote_filename="briefing.md",
                notify_msg="test",
                config={},
            )
        assert result is True


class TestPushMorningBriefing:

    def test_dispatch_disabled_returns_true_without_upload(self):
        with (
            patch("report_dispatcher.load_config", return_value=_config_with_dispatcher(False)),
            patch("report_dispatcher.generate_morning_briefing") as mock_gen,
            patch("report_dispatcher.upload_file_webdav") as mock_upload,
        ):
            result = push_morning_quant_briefing()
        assert result is True
        mock_gen.assert_called_once()
        mock_upload.assert_not_called()

    def test_dispatch_enabled_calls_dispatch_pipeline(self, tmp_path):
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        today = "2026-06-08"

        with (
            patch("report_dispatcher.load_config", return_value=_config_with_dispatcher(True)),
            patch("report_dispatcher.time_engine.now_local") as mock_now,
            patch("report_dispatcher.generate_morning_briefing") as mock_gen,
            patch("report_dispatcher._dispatch_briefing", return_value=True) as mock_disp,
            patch("report_dispatcher._share_charts_to_talk") as mock_charts,
            patch("report_dispatcher.os.path.dirname", return_value=str(tmp_path)),
        ):
            mock_now.return_value.strftime.return_value = today
            result = push_morning_quant_briefing()

        assert result is True
        mock_gen.assert_called_once_with(today)
        mock_disp.assert_called_once()
        mock_charts.assert_called_once()

    def test_uses_local_date_not_utc(self):
        """target_date must come from time_engine.now_local(), not datetime.now()."""
        captured = []
        with (
            patch("report_dispatcher.load_config", return_value=_config_with_dispatcher(False)),
            patch("report_dispatcher.generate_morning_briefing", side_effect=lambda d: captured.append(d)),
        ):
            push_morning_quant_briefing()
        assert len(captured) == 1
        # Date format check: YYYY-MM-DD
        import re
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", captured[0])


class TestPushLunchtimeBriefing:

    def test_dispatch_disabled_returns_true_without_upload(self):
        with (
            patch("report_dispatcher.load_config",
                  return_value=_config_with_dispatcher(False, key="LUNCH_DISPATCHER")),
            patch("report_dispatcher.generate_lunchtime_briefing") as mock_gen,
            patch("report_dispatcher.upload_file_webdav") as mock_upload,
        ):
            result = push_lunchtime_quant_briefing()
        assert result is True
        mock_gen.assert_called_once()
        mock_upload.assert_not_called()

    def test_dispatch_enabled_calls_dispatch_pipeline(self):
        today = "2026-06-08"
        with (
            patch("report_dispatcher.load_config",
                  return_value=_config_with_dispatcher(True, key="LUNCH_DISPATCHER")),
            patch("report_dispatcher.time_engine.now_local") as mock_now,
            patch("report_dispatcher.generate_lunchtime_briefing"),
            patch("report_dispatcher._dispatch_briefing", return_value=True) as mock_disp,
        ):
            mock_now.return_value.strftime.return_value = today
            result = push_lunchtime_quant_briefing()
        assert result is True
        mock_disp.assert_called_once()
        call_kwargs = mock_disp.call_args[1]
        assert call_kwargs["remote_filename"] == f"lunch_briefing_{today}.md"
