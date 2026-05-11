# ghostfolio_sync.py
import json
import requests
import urllib3
from slugify import slugify
from typing import Dict, Any

from config import (
    GHOSTFOLIO_URL, 
    GHOSTFOLIO_TOKEN, 
    PORTFOLIO_PATH, 
    WATCHLIST_PATH, 
    SECRETS_PATH,
    GHOSTFOLIO_ACCOUNTS
)

# Disable insecure request warnings for self-hosted instances using IP addresses
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class GhostfolioSyncEngine:
    def __init__(self):
        """
        Initializes the Sync Engine using raw REST APIs to mirror the HA integration logic.
        Provides granular control over account extraction and token lifecycles.
        """
        self.url = GHOSTFOLIO_URL.rstrip("/")
        self.token = GHOSTFOLIO_TOKEN
        self.headers = {}
        
        if not self.url or not self.token:
            print("[ERROR] Ghostfolio credentials missing. Please check config.json.")
            self.is_configured = False
        else:
            self.is_configured = True

    def authenticate(self) -> bool:
        """
        Authenticates anonymously via the Access Token to receive a short-lived Bearer Token.
        """
        auth_url = f"{self.url}/api/v1/auth/anonymous"
        payload = {"accessToken": self.token}
        
        try:
            response = requests.post(auth_url, json=payload, verify=False, timeout=10)
            response.raise_for_status()
            
            bearer_token = response.json().get("authToken")
            if not bearer_token:
                print("[ERROR] Authentication succeeded but no Bearer token returned.")
                return False
                
            self.headers = {"Authorization": f"Bearer {bearer_token}"}
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to authenticate with Ghostfolio: {e}")
            return False

    def discover_accounts(self) -> list:
        """
        Queries the Ghostfolio server for all active accounts and updates the config.json.
        This enables the Opt-In Sync Boundary feature.
        """
        print("[SYNC] Discovering active accounts on Ghostfolio server...")
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
            
            # --- Safely Update config.json ---
            try:
                with open(SECRETS_PATH, 'r') as f:
                    config_data = json.load(f)
                    
                if "GHOSTFOLIO_ACCOUNTS" not in config_data:
                    config_data["GHOSTFOLIO_ACCOUNTS"] = {"discovered": [], "active": []}
                    
                config_data["GHOSTFOLIO_ACCOUNTS"]["discovered"] = discovered_accounts
                
                # Auto-whitelist all discovered accounts if the active list is totally empty
                # This ensures a seamless transition for legacy users without breaking their dashboards
                if not config_data["GHOSTFOLIO_ACCOUNTS"].get("active"):
                    config_data["GHOSTFOLIO_ACCOUNTS"]["active"] = [acc["id"] for acc in discovered_accounts]
                    print("[SYNC] First-time discovery detected. Auto-activating all accounts.")
                
                with open(SECRETS_PATH, 'w') as f:
                    json.dump(config_data, f, indent=4)
                    
                # Update runtime state
                self.active_account_ids = config_data["GHOSTFOLIO_ACCOUNTS"]["active"]
                self.discovered_accounts = discovered_accounts
                
                print(f"[SUCCESS] Discovered {len(discovered_accounts)} accounts. {len(self.active_account_ids)} are set to Active.")
                return discovered_accounts
                
            except Exception as e:
                print(f"[ERROR] Failed to update config.json with discovered accounts: {e}")
                self.active_account_ids = GHOSTFOLIO_ACCOUNTS.get("active", [])
                return discovered_accounts

        except Exception as e:
            print(f"[ERROR] Account discovery failed: {e}")
            self.active_account_ids = GHOSTFOLIO_ACCOUNTS.get("active", [])
            return []

    def sync_portfolio(self) -> bool:
        """
        Executes the Hierarchical Macro-to-Micro data extraction.
        Queries ONLY active accounts and builds a VWAP-adjusted global portfolio.
        """
        if not self.active_account_ids:
            print("[SYNC] No active accounts configured to sync. Aborting portfolio pull.")
            return False

        output_json: Dict[str, Any] = {}

        try:
            print(f"[SYNC] Extracting holdings from {len(self.active_account_ids)} active accounts...")
            
            # 1. Loop over each explicitly whitelisted account
            for acc_id in self.active_account_ids:
                
                # Find the account name from our discovery payload for nice UI mapping
                acc_name = next((acc["name"] for acc in self.discovered_accounts if acc["id"] == acc_id), acc_id)
                
                # Fetch holdings strictly for this account
                holdings_url = f"{self.url}/api/v1/portfolio/holdings?accounts={acc_id}"
                resp = requests.get(holdings_url, headers=self.headers, verify=False, timeout=15)
                
                if resp.status_code != 200:
                    print(f"[WARNING] Failed to fetch holdings for account {acc_name}. HTTP {resp.status_code}")
                    continue
                    
                holdings_list = resp.json().get("holdings", [])
                
                # 2. Process and aggregate the holdings
                for asset in holdings_list:
                    symbol = asset.get('symbol', '')
                    quantity = float(asset.get('quantity', 0))
                    name = asset.get('name', '')
                    currency = asset.get('currency', '')
                    total_investment = float(asset.get('investment', 0))
                    
                    if quantity <= 0:
                        continue

                    # Exact cost basis for THIS specific account
                    acc_avg_buy_price = total_investment / quantity
                    key = slugify(name, separator='_')
                    is_pence = (currency == 'GBp')

                    # 3. Build or update the Global Hierarchical Object
                    if key not in output_json:
                        # First time encountering this asset across any account
                        output_json[key] = {
                            "ticker": symbol,
                            "price_in_pence": is_pence,
                            "global_shares": quantity,
                            "global_total_investment": total_investment, 
                            "global_buy_price": round(acc_avg_buy_price, 4), # Will recalculate below
                            "accounts": []
                        }
                    else:
                        # We already saw this asset in a previous account. Aggregate the macro data.
                        output_json[key]["global_shares"] += quantity
                        output_json[key]["global_total_investment"] += total_investment
                        
                        # Recalculate the VWAP (Volume Weighted Average Price) for the Macro view
                        new_global_shares = output_json[key]["global_shares"]
                        new_global_investment = output_json[key]["global_total_investment"]
                        output_json[key]["global_buy_price"] = round(new_global_investment / new_global_shares, 4)

                    # Append the Micro (Account-level) ledger entry
                    output_json[key]["accounts"].append({
                        "id": acc_id,
                        "name": acc_name,
                        "shares": quantity,
                        "buy_price": round(acc_avg_buy_price, 4),
                        "total_investment": round(total_investment, 2)
                    })

            # 4. Clean up standard output (Remove the temp global_total_investment key)
            for k in output_json.keys():
                output_json[k].pop("global_total_investment", None)

            # 5. Persist to Disk
            with open(PORTFOLIO_PATH, 'w') as f:
                json.dump(output_json, f, indent=4)
                
            print(f"[SUCCESS] Synced {len(output_json)} unique macro assets to portfolio.json.")
            return True

        except Exception as e:
            print(f"[ERROR] Failed to sync portfolio via Opt-In pipeline: {e}")
            return False

    def sync_watchlist(self) -> bool:
        """
        Fetches the watchlist from Ghostfolio and saves the raw tickers 
        to watchlist.json for the system to scan.
        """
        try:
            print("[SYNC] Fetching watchlist from Ghostfolio...")
            response = requests.get(f"{self.url}/api/v1/watchlist", headers=self.headers, verify=False, timeout=10)
            response.raise_for_status()
            
            # The API might return a dict with a 'watchlist' key, or a direct list
            resp_data = response.json()
            watchlist_items = resp_data.get('watchlist', []) if isinstance(resp_data, dict) else resp_data
            
            tickers = [item.get('symbol') for item in watchlist_items if item.get('symbol')]
            output_data = {"watchlist": tickers}

            # Safely overwrite the existing watchlist.json
            with open(WATCHLIST_PATH, 'w') as f:
                json.dump(output_data, f, indent=4)
                
            print(f"[SUCCESS] Synced {len(tickers)} tickers to watchlist.json.")
            return True

        except Exception as e:
            print(f"[ERROR] Failed to sync watchlist: {e}")
            return False

    def run_full_sync(self) -> bool:
        """Executes the complete Ghostfolio extraction pipeline sequentially."""
        print("\n--- INITIATING INSTITUTIONAL GHOSTFOLIO SYNC ---")
        
        if not self.is_configured:
            print("[ABORT] Sync engine is not properly configured.")
            return False
            
        if not self.authenticate():
            print("[ABORT] Could not secure Bearer token.")
            return False
            
        self.discover_accounts()
        p_success = self.sync_portfolio()
        w_success = self.sync_watchlist()
        
        print("--- GHOSTFOLIO SYNC COMPLETE ---\n")
        return p_success and w_success


if __name__ == "__main__":
    # Test block to execute the sync manually via the terminal
    engine = GhostfolioSyncEngine()
    engine.run_full_sync()