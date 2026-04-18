import sqlite3
import os

db_path = os.path.join('instance', 'rental.db')
print(f'Database path: {db_path}')
print(f'Database exists: {os.path.exists(db_path)}')

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all tables
    cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
    tables = cursor.fetchall()
    print(f'Tables: {[t[0] for t in tables]}')

    # Check booking table columns
    cursor.execute('PRAGMA table_info(booking)')
    columns = cursor.fetchall()
    print(f'Booking columns: {[col[1] for col in columns]}')

    # Check user table columns
    cursor.execute('PRAGMA table_info(user)')
    user_columns = cursor.fetchall()
    print(f'User columns: {[col[1] for col in user_columns]}')

    conn.close()

except Exception as e:
    print(f'Error: {e}')