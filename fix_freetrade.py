from database import get_connection
conn = get_connection()
cursor = conn.cursor()

# Artificially age the exact assets that pass the firewall
cursor.execute("""
    UPDATE asset_profiles 
    SET last_verified_date = '2020-01-01 00:00:00' 
    WHERE ticker IN (SELECT ticker FROM market_universe WHERE is_index = 1 AND is_freetrade = 1)
""")

print(f"Successfully aged {cursor.rowcount} profiles!")
conn.commit()
conn.close()