"""
PoC: Print raw Ghostfolio activity data for specific tickers.
Run from project root: python debug_scripts/poc_ghostfolio_activities.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from config import GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN

TICKERS = {"AMD", "GOOGL", "INTC", "MU", "NOK", "NVDA", "TXN", "AAPL", "TSM"}

url = GHOSTFOLIO_URL.rstrip("/")
token = GHOSTFOLIO_TOKEN

# Auth
auth_resp = requests.post(f"{url}/api/v1/auth/anonymous", json={"accessToken": token}, verify=False, timeout=10)
bearer = auth_resp.json().get("authToken")
headers = {"Authorization": f"Bearer {bearer}"}

# Fetch activities
resp = requests.get(f"{url}/api/v1/activities", headers=headers, verify=False, timeout=15)
activities = resp.json().get("activities", [])
print(f"Total activities: {len(activities)}\n")

for act in activities:
    profile = act.get("SymbolProfile") or {}
    ticker = profile.get("symbol", "")
    if ticker not in TICKERS:
        continue

    print(f"Ticker:              {ticker}")
    print(f"  type:              {act.get('type')}")
    print(f"  isDraft:           {act.get('isDraft')}")
    print(f"  date:              {act.get('date', '')[:10]}")
    print(f"  quantity:          {act.get('quantity')}")
    print(f"  unitPrice (GBP):   {act.get('unitPrice')}")
    print(f"  unitPriceInAsset   {act.get('unitPriceInAssetProfileCurrency')}  (USD)")
    print(f"  currency (act):    {act.get('currency')}")
    print(f"  SymbolProfile.currency: {profile.get('currency')}")
    print(f"  dataSource:        {profile.get('dataSource')}")
    implied_gbpusd = None
    usd = act.get("unitPriceInAssetProfileCurrency")
    gbp = act.get("unitPrice")
    if usd and gbp and gbp != 0:
        implied_gbpusd = usd / gbp
    print(f"  implied GBPUSD:    {implied_gbpusd:.4f}" if implied_gbpusd else "  implied GBPUSD:    n/a")
    print()
