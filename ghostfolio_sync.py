import json
import logging
import requests
import urllib3
from slugify import slugify
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

from config import (
    load_config,
    update_config_atomic,
    GHOSTFOLIO_URL,
    GHOSTFOLIO_TOKEN,
    PORTFOLIO_PATH,
    WATCHLIST_PATH,
    GHOSTFOLIO_ACCOUNTS,
)

# Disable insecure request warnings for self-hosted instances using IP addresses
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def purge_ghostfolio_files() -> int:
    """Deletes portfolio.json/watchlist.json; called when Ghostfolio integration is disabled."""
    deleted = 0
    for path in (PORTFOLIO_PATH, WATCHLIST_PATH):
        try:
            if path.exists():
                path.unlink()
                deleted += 1
        except OSError as e:
            logger.error("Failed to delete %s: %s", path, e)
    return deleted


class GhostfolioSyncEngine:
    def __init__(self):
        self.url = GHOSTFOLIO_URL.rstrip("/")
        self.token = GHOSTFOLIO_TOKEN
        self.headers = {}
        self.active_account_ids = GHOSTFOLIO_ACCOUNTS.get("active", [])
        self.discovered_accounts = []

        if not self.url or not self.token:
            logger.error("Ghostfolio credentials missing. Please check config.json.")
            self.is_configured = False
        else:
            self.is_configured = True

    def authenticate(self) -> bool:
        auth_url = f"{self.url}/api/v1/auth/anonymous"
        payload = {"accessToken": self.token}
        
        try:
            response = requests.post(auth_url, json=payload, verify=False, timeout=10)
            response.raise_for_status()
            
            bearer_token = response.json().get("authToken")
            if not bearer_token:
                logger.error("Authentication succeeded but no Bearer token returned.")
                return False

            self.headers = {"Authorization": f"Bearer {bearer_token}"}
            return True

        except Exception as e:
            logger.error(f"Failed to authenticate with Ghostfolio: {e}")
            return False

    def discover_accounts(self) -> list:
        logger.info("Discovering active accounts on Ghostfolio server...")
        try:
            response = requests.get(f"{self.url}/api/v1/account", headers=self.headers, verify=False, timeout=10)
            response.raise_for_status()
            
            accounts_list = response.json().get("accounts", [])
            discovered_accounts = []
            
            for acc in accounts_list:
                if acc.get("isExcluded"):
                    continue
                discovered_accounts.append({
                    "id": acc["id"],
                    "name": acc["name"],
                    "currency": acc.get("currency", "Unknown")
                })
            
            try:
                current_cfg = load_config()
                current_active = current_cfg.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])

                # Auto-activate all on first discovery so existing users don't need to reconfigure
                if not current_active:
                    current_active = [acc["id"] for acc in discovered_accounts]
                    logger.info("First-time discovery: auto-activating all accounts.")

                updated_accounts = {"discovered": discovered_accounts, "active": current_active}
                update_config_atomic({"GHOSTFOLIO_ACCOUNTS": updated_accounts})

                self.active_account_ids = current_active
                self.discovered_accounts = discovered_accounts

                logger.info(f"Discovered {len(discovered_accounts)} accounts; {len(self.active_account_ids)} active.")
                return discovered_accounts

            except Exception as e:
                logger.error(f"Failed to update config.json with discovered accounts: {e}")
                self.active_account_ids = GHOSTFOLIO_ACCOUNTS.get("active", [])
                return discovered_accounts

        except Exception as e:
            logger.error(f"Account discovery failed: {e}")
            self.active_account_ids = GHOSTFOLIO_ACCOUNTS.get("active", [])
            return []

    def sync_portfolio(self) -> bool:
        if not self.active_account_ids:
            logger.warning("No active accounts configured to sync.")
            return False

        output_json: Dict[str, Any] = {}

        try:
            logger.info(f"Extracting holdings from {len(self.active_account_ids)} active accounts...")
            
            for acc_id in self.active_account_ids:
                acc_name = next((acc["name"] for acc in self.discovered_accounts if acc["id"] == acc_id), acc_id)
                holdings_url = f"{self.url}/api/v1/portfolio/holdings?accounts={acc_id}"
                resp = requests.get(holdings_url, headers=self.headers, verify=False, timeout=15)

                try:
                    resp.raise_for_status()
                except requests.exceptions.HTTPError:
                    logger.warning(f"Failed to fetch holdings for account {acc_name}: HTTP {resp.status_code}")
                    continue

                holdings_list = resp.json().get("holdings", [])
                for asset in holdings_list:
                    profile = asset.get('assetProfile') or asset  # API v1 nests symbol/name/currency under assetProfile
                    symbol = profile.get('symbol', '') or asset.get('symbol', '')
                    quantity = float(asset.get('quantity') or 0)
                    name = profile.get('name', '') or asset.get('name', '')
                    currency = profile.get('currency', '') or asset.get('currency', '')
                    total_investment = float(asset.get('investment') or 0)
                    
                    if quantity <= 0:
                        continue

                    acc_avg_buy_price = total_investment / quantity
                    key = slugify(name, separator='_')
                    is_pence = (currency == 'GBp')

                    if key not in output_json:
                        output_json[key] = {
                            "ticker": symbol,
                            "price_in_pence": is_pence,
                            "global_shares": quantity,
                            "global_total_investment": total_investment,
                            "global_buy_price": round(acc_avg_buy_price, 4),
                            "accounts": []
                        }
                    else:
                        output_json[key]["global_shares"] += quantity
                        output_json[key]["global_total_investment"] += total_investment
                        new_global_shares = output_json[key]["global_shares"]
                        new_global_investment = output_json[key]["global_total_investment"]
                        output_json[key]["global_buy_price"] = round(new_global_investment / new_global_shares, 4)

                    output_json[key]["accounts"].append({
                        "id": acc_id,
                        "name": acc_name,
                        "shares": quantity,
                        "buy_price": round(acc_avg_buy_price, 4),
                        "total_investment": round(total_investment, 2)
                    })

            for k in output_json.keys():
                output_json[k].pop("global_total_investment", None)

            with open(PORTFOLIO_PATH, 'w') as f:
                json.dump(output_json, f, indent=4)
                
            logger.info(f"Synced {len(output_json)} unique assets to portfolio.json.")
            return True

        except Exception as e:
            logger.error(f"Failed to sync portfolio: {e}")
            return False

    def fetch_activities(self, account_id: Optional[str] = None) -> list[dict]:
        if not self.headers:
            if not self.authenticate():
                return []
        try:
            url = f"{self.url}/api/v1/activities"
            if account_id:
                url += f"?accounts={account_id}"
            response = requests.get(
                url,
                headers=self.headers,
                verify=False,
                timeout=15,
            )
            response.raise_for_status()
            return response.json().get("activities", [])
        except Exception as e:
            logger.error("Failed to fetch activities from Ghostfolio: %s", e)
            return []

    def run_full_sync(self) -> bool:
        logger.info("Ghostfolio sync starting...")

        if not load_config().get("GHOSTFOLIO_ENABLED", True):
            logger.info("Ghostfolio integration disabled; skipping sync.")
            return False

        if not self.is_configured:
            logger.error("Sync aborted: engine not configured.")
            return False

        if not self.authenticate():
            logger.error("Sync aborted: could not authenticate.")
            return False

        self.discover_accounts()
        p_success = self.sync_portfolio()

        logger.info("Ghostfolio sync complete.")
        return p_success


if __name__ == "__main__":
    engine = GhostfolioSyncEngine()
    engine.run_full_sync()