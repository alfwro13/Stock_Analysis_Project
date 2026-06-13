# 🔄 System State & Database Recovery Guide

The Quantamental Dashboard utilizes a **Dual-Storage Architecture** to maximize performance and minimize API calls. Understanding the separation between the Relational Database and the Local File Storage is critical for system maintenance and disaster recovery.

## 🏗️ The Dual-Storage Architecture

### 1. The Relational Database (`data/analysis.db`)
This SQLite database stores all structured, queryable data:
* **System Verdicts & Scores:** The 0-100 composite scores and "STRONG BUY" / "BEARISH" signals.
* **Algorithmic Breakdown:** The text-based explanations of why an asset received its score.
* **Setup Tags:** The UI badges (e.g., `🔥 VCP Breakout`, `🐂 Bullish Engulfing`, `🤖 AI Buy`).
* **Fundamental Data:** P/E ratios, Debt-to-Equity, Sector classifications.
* **Risk Metrics:** Value at Risk (VaR), CVaR, and Machine Learning Confidence Scores.

### 2. Local File Storage (`data/historical/*.parquet`)
These highly compressed files store raw time-series data:
* **Daily OHLCV Data:** 2 years of daily Open, High, Low, Close, and Volume data.
* **Intraday Data:** 5-minute interval data for the current trading session.
* **Chart Rendering:** The interactive Plotly charts are drawn *directly* from these Parquet files, bypassing the SQLite database entirely.

---

## ⚠️ What Happens During a Database Reset?

If you delete or reset `analysis.db` (e.g., using `clean_db.py` or manually deleting the file), you will observe the following UI behaviors:

1. **Charts Still Work:** Because the `.parquet` files survive the DB wipe, your interactive charts will continue to render seamlessly.
2. **Missing Tags & Verdicts:** The UI will default to a "UNIVERSE SCAN ONLY" placeholder state. You will lose the "System Verdict" box, the "Algorithmic Breakdown", and all colorful setup tags.
3. **Missing Chart Annotations (▲ / ▼):** While the charts render, the specific up/down triangle markers for candlestick patterns may disappear. These markers are calculated on-the-fly using the last 14 days of data, but rely on the `QuantEngine` having processed the data properly to confirm the setups.

---

## 🛠️ The System Rebuild Protocol

To restore the dashboard to its full institutional capacity after a database wipe, you must trigger the core background engines to recalculate the mathematical models and repopulate `analysis.db`.

Navigate to the **⚙️ Settings** page in the Web UI and execute the following steps strictly in order:

### Step 1: Rebuild the Quantitative Models
* **Action:** Under the *Manual Actions* card, click **"↻ Force Update Analysis Models"**.
* **What it does:** 1. Triggers `DataEngine.update_all_data()` to ensure your `.parquet` files are perfectly synced.
  2. Executes `QuantEngine.run_all()` to recalculate every moving average, RSI, setup, score, and candlestick pattern.
  3. Repopulates the `stock_signals` table in the database.

### Step 2: Rebuild the AI & Risk Metrics
* **Action:** Scroll down to the *Machine Learning & AI Engine* card and click **"⚙️ Initialize AI Engine (Backfill & Train)"**.
* **What it does:** 1. Securely rebuilds the 2-year historical vectors required for machine learning.
  2. Retrains the XGBoost/RandomForest soft-voting ensemble (`models/ml_ensemble.joblib`).
  3. Recalculates the Parametric VaR (Value at Risk) and ML Confidence scores for all tracked assets.

Once the background notifications confirm these tasks are complete, refresh your dashboard. The System Verdict, Algorithmic Breakdown, and all mathematical tags will be fully restored.
---

## Password Reset Procedures

The dashboard uses PBKDF2-SHA256 password hashing. The hash is stored as `DASHBOARD_PASSWORD_HASH` in `.env`; the plaintext `DASHBOARD_PASSWORD` key is cleared after the first password change.

There are three ways to reset the admin password if you are locked out:

---

### Option 1 — Console Script (recommended)

Run the following from the project root on the server:

```bash
source venv/bin/activate
python reset_admin_password.py
```

The script prompts for a new password, hashes it, and writes it to `.env`. **Restart the app afterwards for the change to take effect.**

---

### Option 2 — `FORCE_PASSWORD_RESET` flag in `config.json`

This method does not require a server restart.

1. Open `config.json` in any text editor and set:

   ```json
   "FORCE_PASSWORD_RESET": true
   ```

2. Navigate to `http://<server>:<port>/admin-reset-password` in a browser.
   The page is accessible without authentication when the flag is enabled.

3. Enter and confirm a new password. On success the flag is automatically cleared.

> Security note: The `FORCE_PASSWORD_RESET` flag bypasses login. Only set it from a trusted network connection and clear it immediately after resetting.

---

### Option 3 — Direct `.env` edit

If neither of the above is feasible you can edit `.env` directly:

1. Run this Python one-liner on the server to generate a hash:

   ```bash
   python3 -c "
   import hashlib, os
   pw = input('New password: ')
   salt = os.urandom(32)
   dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, 600000)
   print('pbkdf2:sha256:600000:' + salt.hex() + ':' + dk.hex())
   "
   ```

2. Paste the output as the value of `DASHBOARD_PASSWORD_HASH` in `.env`, and set `DASHBOARD_PASSWORD=`.

3. Restart the app.

---

### Self-Service Reset (for users who know their email)

Users can initiate a reset from the login page by clicking **Forgot password?**.

Prerequisites — configure in **Settings → User Account**:
- `ACCOUNT_EMAIL` — the registered email address
- For email delivery: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` in `.env`
- Without SMTP: the reset link is sent via **Nextcloud Talk** if configured, otherwise it is logged at `INFO` level in the application logs.

Reset links expire after **1 hour** and are single-use.
