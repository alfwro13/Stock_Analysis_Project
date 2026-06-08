"""Generates and dispatches Morning/Lunchtime Quant Briefings to Nextcloud Talk via WebDAV upload and OCS sharing."""

import os
import logging
import requests

from config import load_config
import time_engine
from morning_briefing import generate_morning_briefing, generate_uk_charts
from lunchtime_briefing import generate_lunchtime_briefing
from nextcloud_talk import upload_file_webdav, share_file_to_talk, send_text_message

logger = logging.getLogger(__name__)


def ensure_remote_directory(nc_url: str, nc_user: str, nc_pass: str, folder_name: str) -> None:
    """MKCOL the target folder on Nextcloud; 405 means it already exists (fine)."""
    webdav_url = f"{nc_url}/remote.php/dav/files/{nc_user}/{folder_name}"
    try:
        response = requests.request(
            "MKCOL",
            webdav_url,
            auth=(nc_user, nc_pass),
            headers={"OCS-APIRequest": "true"},
            timeout=15
        )
        # 201 = created, 405 = already exists
        if response.status_code == 201:
            logger.info("Successfully created remote directory: %s", folder_name)
        elif response.status_code == 405:
            logger.debug("Remote directory '%s' already exists.", folder_name)
        else:
            logger.warning("MKCOL returned unexpected status %s for %s. Upload may fail.", response.status_code, folder_name)
    except Exception as e:
        logger.error("Failed to verify or create remote directory: %s", e)


def _dispatch_briefing(
    local_file_path: str,
    remote_folder: str,
    remote_filename: str,
    notify_msg: str,
    config: dict,
) -> bool:
    # Env vars take precedence (credentials are never stored in config.json)
    nc_url = (os.environ.get("NEXTCLOUD_URL") or config.get("NEXTCLOUD_URL", "")).rstrip("/")
    nc_user = os.environ.get("NEXTCLOUD_BOT_USERNAME") or config.get("BOT_USERNAME", "")
    nc_pass = os.environ.get("NEXTCLOUD_APP_PASSWORD") or config.get("APP_PASSWORD", "")
    nc_token = os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN") or config.get("CONVERSATION_TOKEN", "")

    if not all([nc_url, nc_user, nc_pass, nc_token]):
        logger.warning("Nextcloud credentials missing or incomplete (checked env vars + config). Aborting dispatch.")
        return False

    if not os.path.exists(local_file_path):
        logger.error("Report file not found locally at %s. Aborting.", local_file_path)
        return False

    remote_path = f"{remote_folder}/{remote_filename}"

    ensure_remote_directory(nc_url, nc_user, nc_pass, remote_folder)

    upload_success = upload_file_webdav(
        local_path=local_file_path,
        remote_path=remote_path,
        nextcloud_url=nc_url,
        bot_username=nc_user,
        app_password=nc_pass,
        log_message=logger.info,
    )
    if not upload_success:
        logger.error("Failed to upload briefing to Nextcloud. Aborting.")
        return False

    share_success = share_file_to_talk(
        remote_path=remote_path,
        conversation_token=nc_token,
        nextcloud_url=nc_url,
        bot_username=nc_user,
        app_password=nc_pass,
        log_message=logger.info,
    )
    if not share_success:
        logger.warning("File uploaded but failed to share to Talk conversation. Proceeding to text dispatch.")

    send_success = send_text_message(notify_msg, config)
    if send_success:
        logger.info("Briefing dispatch completed successfully.")
        return True
    else:
        logger.error("Failed to send summary text message to Talk.")
        return False


def _share_charts_to_talk(chart_paths: list[str], config: dict) -> None:
    nc_url = (os.environ.get("NEXTCLOUD_URL") or config.get("NEXTCLOUD_URL", "")).rstrip("/")
    nc_user = os.environ.get("NEXTCLOUD_BOT_USERNAME") or config.get("BOT_USERNAME", "")
    nc_pass = os.environ.get("NEXTCLOUD_APP_PASSWORD") or config.get("APP_PASSWORD", "")
    nc_token = os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN") or config.get("CONVERSATION_TOKEN", "")

    if not all([nc_url, nc_user, nc_pass, nc_token]):
        return

    ensure_remote_directory(nc_url, nc_user, nc_pass, "quant_briefings/charts")

    for local_path in chart_paths:
        if not os.path.exists(local_path):
            continue
        filename = os.path.basename(local_path)
        remote_path = f"quant_briefings/charts/{filename}"
        ok = upload_file_webdav(
            local_path=local_path,
            remote_path=remote_path,
            nextcloud_url=nc_url,
            bot_username=nc_user,
            app_password=nc_pass,
            log_message=logger.debug,
        )
        if ok:
            share_file_to_talk(
                remote_path=remote_path,
                conversation_token=nc_token,
                nextcloud_url=nc_url,
                bot_username=nc_user,
                app_password=nc_pass,
                log_message=logger.debug,
            )


def push_morning_quant_briefing() -> bool:
    """Generates the morning briefing and dispatches it to Talk if SCHEDULING.DISPATCHER.ENABLED is true."""
    logger.info("Running Morning Quant Briefing generation...")

    config = load_config()
    target_date = time_engine.now_local().strftime("%Y-%m-%d")

    generate_morning_briefing(target_date)
    logger.info("Morning Briefing generated for %s.", target_date)

    send_to_talk = config.get("SCHEDULING", {}).get("DISPATCHER", {}).get("ENABLED", False)
    if not send_to_talk:
        logger.info("Nextcloud Talk dispatch disabled (DISPATCHER.ENABLED=false). Briefing saved locally only.")
        return True

    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_file_path = os.path.join(base_dir, "reports", f"morning_briefing_{target_date}.md")
    msg = (
        f"🌅 *Morning Quant Briefing — {target_date}*\n"
        "Overnight news, US futures, UK pre-open charts & quant signals attached."
    )

    ok = _dispatch_briefing(
        local_file_path=local_file_path,
        remote_folder="quant_briefings",
        remote_filename=f"morning_briefing_{target_date}.md",
        notify_msg=msg,
        config=config,
    )

    charts_dir = os.path.join(base_dir, "static", "briefing_charts")
    chart_files = [
        os.path.join(charts_dir, f"ftse_{target_date}.png"),
        os.path.join(charts_dir, f"gilt_{target_date}.png"),
        os.path.join(charts_dir, f"gbpusd_{target_date}.png"),
    ]
    _share_charts_to_talk([p for p in chart_files if os.path.exists(p)], config)

    return ok


def push_lunchtime_quant_briefing() -> bool:
    """Generates the lunchtime briefing and dispatches it to Talk if SCHEDULING.LUNCH_DISPATCHER.ENABLED is true."""
    logger.info("Running Lunchtime Quant Briefing generation...")

    config = load_config()
    target_date = time_engine.now_local().strftime("%Y-%m-%d")

    generate_lunchtime_briefing(target_date)
    logger.info("Lunchtime Briefing generated for %s.", target_date)

    send_to_talk = config.get("SCHEDULING", {}).get("LUNCH_DISPATCHER", {}).get("ENABLED", False)
    if not send_to_talk:
        logger.info("Nextcloud Talk dispatch disabled (LUNCH_DISPATCHER.ENABLED=false). Briefing saved locally only.")
        return True

    local_file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reports",
        f"lunch_briefing_{target_date}.md",
    )
    msg = (
        f"🕛 *Lunch Quant Briefing — {target_date}*\n"
        "Morning session news, UK mid-session & US pre-market snapshot attached."
    )

    return _dispatch_briefing(
        local_file_path=local_file_path,
        remote_folder="quant_briefings",
        remote_filename=f"lunch_briefing_{target_date}.md",
        notify_msg=msg,
        config=config,
    )


if __name__ == "__main__":
    push_morning_quant_briefing()
