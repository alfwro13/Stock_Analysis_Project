"""
Admin utility: reset the dashboard password directly from the console.

Usage:
    python reset_admin_password.py

The script prompts for a new password, hashes it, and writes DASHBOARD_PASSWORD_HASH
to the .env file.  It also bumps SESSION_EPOCH (signing out every browser) and clears
any failed-login lockout.  The running app must be restarted to pick up the change.
"""
import getpass
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    print("Quantamental — Admin Password Reset")
    print("====================================")
    print("This will overwrite the stored password hash in .env.")
    print("Restart the app after completing this step.\n")

    from auth import password_policy_error

    while True:
        pw = getpass.getpass("New password: ")
        policy_error = password_policy_error(pw)
        if policy_error:
            print(f"{policy_error} Try again.")
            continue
        confirm = getpass.getpass("Confirm new password: ")
        if pw != confirm:
            print("Passwords do not match. Try again.")
            continue
        break

    from auth import bump_session_epoch, clear_login_failures, hash_password
    from dotenv import set_key

    env_path = PROJECT_ROOT / ".env"
    env_path.touch(exist_ok=True)

    new_hash = hash_password(pw)
    set_key(str(env_path), "DASHBOARD_PASSWORD_HASH", new_hash)
    set_key(str(env_path), "DASHBOARD_PASSWORD", "")
    bump_session_epoch()
    try:
        clear_login_failures()
    except sqlite3.OperationalError:
        pass  # login_throttle is created on the first boot after upgrade; no lockout can exist before it

    print("\nPassword updated successfully.")
    print("Restart the app for the change to take effect.")


if __name__ == "__main__":
    main()
