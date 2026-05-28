"""
report_dispatcher.py

Automated dispatch microservice for the Quantamental Dashboard.
Generates the Morning Quant Briefing and pushes it securely to Nextcloud Talk
via WebDAV file upload and OCS API sharing.
"""

import os
import logging
import requests
from datetime import datetime, timedelta

from config import load_config
from quant_screener import fetch_latest_signals, generate_markdown_briefing
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


def push_morning_quant_briefing() -> bool:
    """
    Generates the Morning Quant Briefing and automatically pushes the file 
    and a summary message to the user's Nextcloud Talk app.
    
    Returns:
        bool: True if the entire dispatch pipeline succeeds, False otherwise.
    """
    logger.info("Initiating Morning Quant Briefing dispatch pipeline...")
    
    # 1. Load Nextcloud Configurations
    config = load_config()
    nc_url = config.get("NEXTCLOUD_URL", "").rstrip("/")
    nc_user = config.get("BOT_USERNAME", "")
    nc_pass = config.get("APP_PASSWORD", "")
    nc_token = config.get("CONVERSATION_TOKEN", "")
    
    if not all([nc_url, nc_user, nc_pass, nc_token]):
        logger.warning("Nextcloud credentials missing or incomplete in config. Aborting dispatch.")
        return False

    # 2. Determine the Target Date and Fetch Signals
    today = datetime.now()
    target_date = today.strftime('%Y-%m-%d')
    
    # Attempt to fetch today's signals
    signals = fetch_latest_signals(target_date)
    
    # Fallback: If today's scan hasn't run or yielded no data, check yesterday's close
    if not signals:
        logger.info(f"No signals found for {target_date}, falling back to yesterday's data.")
        yesterday = today - timedelta(days=1)
        target_date = yesterday.strftime('%Y-%m-%d')
        signals = fetch_latest_signals(target_date)
        
    if not signals:
        logger.warning("No signals available to generate report for today or yesterday. Aborting dispatch.")
        return False

    # 3. Generate Report and Save to Local Disk
    logger.info(f"Generating Markdown Briefing for {target_date}...")
    generate_markdown_briefing(target_date, signals)
    
    # Construct exact paths (matching quant_screener.py output logic)
    local_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports', f"quant_briefing_{target_date}.md")
    
    remote_folder = "quant_briefings"
    remote_path = f"{remote_folder}/quant_briefing_{target_date}.md"
    
    if not os.path.exists(local_file_path):
        logger.error(f"Report file not found locally at {local_file_path}. Aborting.")
        return False
        
    # 4. Ensure Remote Directory Exists (The Fix)
    logger.info(f"Verifying remote directory '/{remote_folder}' exists...")
    ensure_remote_directory(nc_url, nc_user, nc_pass, remote_folder)
        
    # 5. Upload to Nextcloud via WebDAV
    logger.info(f"Uploading {local_file_path} to Nextcloud WebDAV at {remote_path}...")
    upload_success = upload_file_webdav(
        local_path=local_file_path, 
        remote_path=remote_path, 
        nextcloud_url=nc_url, 
        bot_username=nc_user, 
        app_password=nc_pass, 
        log_message=logger.info
    )
    
    if not upload_success:
        logger.error("Failed to upload Morning Briefing to Nextcloud. Aborting.")
        return False
        
    # 6. Share File to Talk Conversation
    logger.info("Sharing uploaded file to the specified Talk conversation...")
    share_success = share_file_to_talk(
        remote_path=remote_path,
        conversation_token=nc_token,
        nextcloud_url=nc_url,
        bot_username=nc_user,
        app_password=nc_pass,
        log_message=logger.info
    )
    
    if not share_success:
        logger.warning("File uploaded, but failed to share directly to the Talk conversation. Proceeding to text dispatch.")
        
    # 7. Dispatch the Text Notification
    msg = f"📊 *Morning Quant Briefing generated for {target_date}!* Open the attached file to review the latest statistical setups, or visit your dashboard."
    logger.info("Dispatching summary text message to Talk...")
    send_success = send_text_message(msg, config)
    
    if send_success:
        logger.info("Morning Briefing Dispatch completed successfully.")
        return True
    else:
        logger.error("Failed to send summary text message to Talk.")
        return False


if __name__ == "__main__":
    # Standalone execution logic for testing
    push_morning_quant_briefing()