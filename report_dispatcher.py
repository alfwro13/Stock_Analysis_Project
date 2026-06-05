"""
report_dispatcher.py

Automated dispatch microservice for the Quantamental Dashboard.
Generates Morning and Lunchtime Quant Briefings and pushes them securely to
Nextcloud Talk via WebDAV file upload and OCS API sharing.
"""

import os
import logging
import requests
from datetime import datetime, timedelta

from config import load_config
from morning_briefing import generate_morning_briefing
from lunchtime_briefing import generate_lunchtime_briefing
from nextcloud_talk import upload_file_webdav, share_file_to_talk, send_text_message

logger = logging.getLogger(__name__)


def ensure_remote_directory(nc_url: str, nc_user: str, nc_pass: str, folder_name: str) -> None:
    """
    Executes a WebDAV MKCOL request to ensure the target directory exists on Nextcloud.
    Prevents 404 errors during the file upload process.
    """
    webdav_url = f"{nc_url}/remote.php/dav/files/{nc_user}/{folder_name}"
    try:
        response = requests.request(
            "MKCOL", 
            webdav_url, 
            auth=(nc_user, nc_pass), 
            headers={"OCS-APIRequest": "true"},
            timeout=15
        )
        # 201 Created = Success. 
        # 405 Method Not Allowed = Directory already exists (which is fine).
        if response.status_code == 201:
            logger.info(f"Successfully created remote directory: {folder_name}")
        elif response.status_code == 405:
            logger.debug(f"Remote directory '{folder_name}' already exists.")
        else:
            logger.warning(f"MKCOL returned unexpected status {response.status_code} for {folder_name}. Upload may fail.")
    except Exception as e:
        logger.error(f"Failed to verify or create remote directory: {e}")


def _dispatch_briefing(
    local_file_path: str,
    remote_folder: str,
    remote_filename: str,
    notify_msg: str,
    config: dict,
) -> bool:
    """
    Shared upload-and-notify pipeline: ensures remote dir exists, uploads the file,
    shares it to the Talk conversation, then sends a text notification.
    Returns True on full success.
    """
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


def push_morning_quant_briefing() -> bool:
    """
    Generates the Morning Quant Briefing and saves it to disk.
    Only sends to Nextcloud Talk if SCHEDULING.DISPATCHER.ENABLED is true.

    Returns:
        bool: True if generation succeeded (Talk send is best-effort).
    """
    logger.info("Running Morning Quant Briefing generation...")

    config = load_config()
    target_date = datetime.now().strftime("%Y-%m-%d")

    generate_morning_briefing(target_date)
    logger.info("Morning Briefing generated for %s.", target_date)

    send_to_talk = config.get("SCHEDULING", {}).get("DISPATCHER", {}).get("ENABLED", False)
    if not send_to_talk:
        logger.info("Nextcloud Talk dispatch disabled (DISPATCHER.ENABLED=false). Briefing saved locally only.")
        return True

    local_file_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reports",
        f"morning_briefing_{target_date}.md",
    )
    msg = (
        f"🌅 *Morning Quant Briefing — {target_date}*\n"
        "Overnight news, US futures, UK pre-open snapshot & quant signals attached. "
        "Or visit your dashboard for live views."
    )

    return _dispatch_briefing(
        local_file_path=local_file_path,
        remote_folder="quant_briefings",
        remote_filename=f"morning_briefing_{target_date}.md",
        notify_msg=msg,
        config=config,
    )


def push_lunchtime_quant_briefing() -> bool:
    """
    Generates the Lunchtime Quant Briefing and saves it to disk.
    Only sends to Nextcloud Talk if SCHEDULING.LUNCH_DISPATCHER.ENABLED is true.

    Returns:
        bool: True if generation succeeded (Talk send is best-effort).
    """
    logger.info("Running Lunchtime Quant Briefing generation...")

    config = load_config()
    target_date = datetime.now().strftime("%Y-%m-%d")

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