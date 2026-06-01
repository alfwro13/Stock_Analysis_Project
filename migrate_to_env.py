"""
One-time migration: reads secrets from the old config.json and writes .env
Run this on the production server BEFORE pulling the updated code.

Usage:
    python3 migrate_to_env.py
"""
import json
import secrets
import pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
ENV_PATH = BASE_DIR / ".env"

if not CONFIG_PATH.exists():
    print("[ERROR] config.json not found. Nothing to migrate.")
    exit(1)

if ENV_PATH.exists():
    print("[WARNING] .env already exists. Aborting to avoid overwriting.")
    print("          Delete .env manually first if you want to re-run this.")
    exit(1)

cfg = json.loads(CONFIG_PATH.read_text())

lines = [
    "# Migrated from config.json — do not commit this file",
    f"GHOSTFOLIO_TOKEN={cfg.get('API_TOKEN', '')}",
    f"GHOSTFOLIO_URL={cfg.get('GHOSTFOLIO_URL', '')}",
    f"NEXTCLOUD_URL={cfg.get('NEXTCLOUD_URL', '')}",
    f"NEXTCLOUD_BOT_USERNAME={cfg.get('BOT_USERNAME', '')}",
    f"NEXTCLOUD_APP_PASSWORD={cfg.get('APP_PASSWORD', '')}",
    f"NEXTCLOUD_CONVERSATION_TOKEN={cfg.get('CONVERSATION_TOKEN', '')}",
    f"FRED_API_KEY={cfg.get('FRED_API_KEY', '')}",
    f"APP_SECRET_KEY={secrets.token_hex(32)}",
    "",
    "# Dashboard login (HTTP Basic Auth)",
    "DASHBOARD_USERNAME=andre",
    "DASHBOARD_PASSWORD=changeme",
    "# API key for script/curl access (leave empty to disable)",
    "API_KEY=",
]

ENV_PATH.write_text("\n".join(lines) + "\n")
print(f"[OK] .env written to {ENV_PATH}")
print()
print("Next steps:")
print("  1. git pull  (get the updated config.py / requirements.txt)")
print("  2. pip install -r requirements.txt")
print("  3. Restart the app")
