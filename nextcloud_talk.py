# nextcloud_talk.py
import logging
import os
import requests

logger = logging.getLogger(__name__)

def __noop_logger(*args, **kwargs):
    """A logger that does nothing."""
    pass

def upload_file_webdav(local_path, remote_path, nextcloud_url, bot_username, app_password, log_message=__noop_logger):
    """Uploads the file using a WebDAV PUT request."""
    webdav_url = f"{nextcloud_url}/remote.php/dav/files/{bot_username}/{remote_path}"

    try:
        if not os.path.exists(local_path):
            log_message(f"❌ FATAL: Local file not found: {local_path}. Cannot upload.")
            return False

        with open(local_path, 'rb') as f:
            response = requests.put(
                webdav_url,
                data=f,
                auth=(bot_username, app_password),
                headers={"OCS-APIRequest": "true"},
                timeout=30
            )
            response.raise_for_status()

            if response.status_code in [200, 201, 204]:
                log_message(f"✅ File uploaded successfully via WebDAV to /files/{bot_username}/{remote_path}")
                return True
            else:
                log_message(f"❌ WebDAV Upload Failed: HTTP {response.status_code}")
                return False

    except requests.exceptions.RequestException as e:
        log_message(f"❌ FATAL WebDAV Upload Error: {e}")
        return False


def share_file_to_talk(remote_path, conversation_token, nextcloud_url, bot_username, app_password, log_message=__noop_logger):
    """Shares the uploaded file into the target Talk conversation."""
    share_endpoint = f"{nextcloud_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"

    share_path_clean = remote_path if remote_path.startswith('/') else f'/{remote_path}'

    share_payload = {
        "path": share_path_clean,
        "shareType": 10, # 10 = Talk Conversation
        "shareWith": conversation_token,  
    }

    headers = {
        "OCS-APIRequest": "true",
        "Accept": "application/json"
    }

    try:
        response = requests.post(
            share_endpoint,
            data=share_payload,  
            auth=(bot_username, app_password),
            headers=headers,
            timeout=15
        )
        response.raise_for_status()

        response_data = response.json()
        if response_data.get('ocs', {}).get('meta', {}).get('statuscode') == 200:
            log_message(f"✅ File successfully shared to Talk.")
            return True
        else:
            message = response_data.get('ocs', {}).get('meta', {}).get('message', 'Unknown OCS share error.')
            log_message(f"❌ File Share Failed (OCS Error): {message}")
            return False

    except requests.exceptions.RequestException as e:
        log_message(f"❌ FATAL File Share Error: {e}")
        return False

def send_text_message(message_text: str, config_data: dict) -> bool:
    """Sends a direct text payload to Nextcloud Talk using dynamic configurations."""
    # Env vars take precedence — credentials are sensitive and are never written to
    # config.json, only to .env / os.environ. config_data (from load_config()) is
    # kept as a fallback for callers that resolve credentials themselves.
    url = os.environ.get("NEXTCLOUD_URL") or config_data.get("NEXTCLOUD_URL", "")
    token = os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN") or config_data.get("CONVERSATION_TOKEN", "")
    user = os.environ.get("NEXTCLOUD_BOT_USERNAME") or config_data.get("BOT_USERNAME", "")
    pwd = os.environ.get("NEXTCLOUD_APP_PASSWORD") or config_data.get("APP_PASSWORD", "")
    
    if not all([url, token, user, pwd]):
        return False

    api_endpoint = f"{url}/ocs/v2.php/apps/spreed/api/v1/chat/{token}"
    payload = {"message": message_text}
    headers = {
        "OCS-APIRequest": "true", 
        "Content-Type": "application/json", 
        "Accept": "application/json"
    }
    
    try:
        response = requests.post(
            api_endpoint, 
            headers=headers, 
            json=payload, 
            auth=(user, pwd), 
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error("Failed to send Nextcloud text message: %s", e)
        return False