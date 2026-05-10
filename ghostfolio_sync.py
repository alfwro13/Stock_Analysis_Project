# ghostfolio_sync.py
import json
from ghostfolio import Ghostfolio
from slugify import slugify
from config import GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN, PORTFOLIO_PATH, WATCHLIST_PATH

class GhostfolioSyncEngine:
    def __init__(self):
        """
        Initializes the Ghostfolio client using credentials from config.json.
        Fails gracefully if credentials are missing or incorrect.
        """
        self.url = GHOSTFOLIO_URL
        self.token = GHOSTFOLIO_TOKEN
        
        if not self.url or not self.token:
            print("[ERROR] Ghostfolio credentials missing. Please check config.json.")
            self.client = None
        else:
            # verify_ssl=False is required to bypass SSL errors when using local IP addresses
            self.client = Ghostfolio(token=self.token, host=self.url, verify_ssl=False)

    def sync_portfolio(self):
        """
        Fetches active holdings from Ghostfolio, calculates the average cost basis,
        and saves them to portfolio.json for the dashboard to ingest.
        """
        if not self.client:
            return False

        try:
            print("Fetching portfolio holdings from Ghostfolio...")
            data = self.client.holdings()
            holdings_list = data.get('holdings', [])
            
            output_json = {}

            for asset in holdings_list:
                # 1. Extract Core Data
                symbol = asset.get('symbol', '')
                quantity = float(asset.get('quantity', 0))
                name = asset.get('name', '')
                currency = asset.get('currency', '')
                
                # Exclude empty or closed positions so they don't clutter the dashboard
                if quantity <= 0:
                    continue

                # 2. Calculate Exact Cost Basis
                # Ghostfolio returns the total monetary investment in the 'investment' key.
                # We divide this by quantity to get the average buy price per share.
                total_investment = float(asset.get('investment', 0))
                avg_buy_price = total_investment / quantity if quantity > 0 else 0

                # 3. Create the JSON Key (Slugified Name)
                # e.g., "Apple Inc." -> "apple_inc"
                key = slugify(name, separator='_')

                # 4. Handle London Stock Exchange Currency Quirk
                # Yahoo Finance reports LSE in pence (GBp), Ghostfolio reports in pounds (GBP).
                # We flag this so the math engine in main.py knows to multiply by 100.
                is_pence = (currency == 'GBp')

                # 5. Build Clean Output Object (Legacy "sensor" string removed)
                output_json[key] = {
                    "shares": quantity,
                    "buy_price": round(avg_buy_price, 4),
                    "price_in_pence": is_pence,
                    "ticker": symbol
                }

            # Safely overwrite the existing portfolio.json in the data/ directory
            with open(PORTFOLIO_PATH, 'w') as f:
                json.dump(output_json, f, indent=2)
                
            print(f"[SUCCESS] Synced {len(output_json)} holdings to portfolio.json.")
            return True

        except Exception as e:
            print(f"[ERROR] Failed to sync portfolio from Ghostfolio: {e}")
            return False

    def sync_watchlist(self):
        """
        Fetches the watchlist from Ghostfolio and saves the raw tickers 
        to watchlist.json for the system to scan.
        """
        if not self.client:
            return False

        try:
            print("Fetching watchlist from Ghostfolio...")
            response = self.client.get("watchlist")
            
            # The API might return a dict with a 'watchlist' key, or a direct list
            watchlist_items = response.get('watchlist', []) if isinstance(response, dict) else response
            
            tickers = []
            for item in watchlist_items:
                symbol = item.get('symbol')
                if symbol:
                    tickers.append(symbol)

            output_data = {"watchlist": tickers}

            # Safely overwrite the existing watchlist.json
            with open(WATCHLIST_PATH, 'w') as f:
                json.dump(output_data, f, indent=2)
                
            print(f"[SUCCESS] Synced {len(tickers)} tickers to watchlist.json.")
            return True

        except Exception as e:
            print(f"[ERROR] Failed to sync watchlist from Ghostfolio: {e}")
            return False

    def run_full_sync(self):
        """Executes the complete Ghostfolio extraction pipeline sequentially."""
        print("\n--- INITIATING GHOSTFOLIO SYNC ---")
        p_success = self.sync_portfolio()
        w_success = self.sync_watchlist()
        print("--- GHOSTFOLIO SYNC COMPLETE ---\n")
        return p_success and w_success

if __name__ == "__main__":
    # Test block to execute the sync manually via the terminal
    engine = GhostfolioSyncEngine()
    engine.run_full_sync()