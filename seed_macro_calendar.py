import random
import uuid
import logging
from datetime import datetime, timedelta, timezone
from database import get_connection

logger = logging.getLogger(__name__)

def seed_calendar():
    # get_connection() guarantees WAL mode, correct file path, and row_factory
    conn = get_connection()
    cursor = conn.cursor()
    try:
        logger.info("🌱 Seeding historical Macro Calendar events for AI Training...")

        records = []
        base_date = datetime.now(timezone.utc) - timedelta(days=180)

        for i in range(50):
            event_date = (base_date + timedelta(days=i*3)).strftime("%Y-%m-%d 14:00:00")
            currency = random.choice(['USD', 'GBP'])
            impact = 'High'
            event_name = "Fed Interest Rate Decision" if currency == 'USD' else "BoE Official Bank Rate"

            prev_val = round(random.uniform(4.0, 5.5), 2)
            forecast_val = prev_val + random.choice([0.0, 0.25, -0.25])
            # Synthetic ground-truth labels for AI training
            actual_val = forecast_val + random.choice([0.0, 0.25, -0.25])

            if actual_val != forecast_val:
                post_event_spy_gap = round(random.uniform(0.5, 3.5), 2)
            else:
                post_event_spy_gap = round(random.uniform(0.0, 0.8), 2)

            records.append((
                str(uuid.uuid4()), event_date, currency, impact, event_name,
                forecast_val, prev_val, actual_val, post_event_spy_gap, 1, 1
            ))

        cursor.executemany('''
            INSERT OR REPLACE INTO macro_calendar (
                event_id, event_date, currency, impact, event_name,
                forecast_val, previous_val, actual_val, post_event_spy_gap, is_event_passed, alert_dispatched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)

        conn.commit()

        cursor.execute("SELECT COUNT(*) as count FROM macro_calendar WHERE is_event_passed = 1 AND actual_val IS NOT NULL")
        row = cursor.fetchone()
        count = row['count'] if row else 0

        logger.info("✅ Successfully seeded %d historical events with AI training targets. Verified %d valid rows in DB.", len(records), count)
    finally:
        conn.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_calendar()