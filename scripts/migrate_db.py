import sqlite3
import os
import time

# Path to the database
db_path = os.path.join(os.path.dirname(__file__), '..', 'instance', 'rental.db')

def add_due_date_column():
    """Add due_date column to booking table if it doesn't exist"""
    max_attempts = 5
    attempt = 0

    while attempt < max_attempts:
        try:
            print(f"Attempt {attempt + 1} to connect to database...")
            # Use timeout to wait for lock release
            conn = sqlite3.connect(db_path, timeout=10.0)
            cursor = conn.cursor()

            # Check if due_date column exists
            cursor.execute("PRAGMA table_info(booking)")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]

            if 'due_date' not in column_names:
                print("Adding due_date column to booking table...")
                cursor.execute("ALTER TABLE booking ADD COLUMN due_date DATETIME")
                conn.commit()
                print("Successfully added due_date column!")
            else:
                print("due_date column already exists.")

            # Add licence_proof column if it doesn't exist
            if 'licence_proof' not in column_names:
                print("Adding licence_proof column to booking table...")
                cursor.execute("ALTER TABLE booking ADD COLUMN licence_proof VARCHAR(200)")
                conn.commit()
                print("Successfully added licence_proof column!")
            else:
                print("licence_proof column already exists.")

            # Add proof_type column if it doesn't exist
            if 'proof_type' not in column_names:
                print("Adding proof_type column to booking table...")
                cursor.execute("ALTER TABLE booking ADD COLUMN proof_type VARCHAR(20) DEFAULT 'License'")
                conn.commit()
                print("Successfully added proof_type column!")
            else:
                print("proof_type column already exists.")

            # Add location column to vehicle table if it doesn't exist
            cursor.execute("PRAGMA table_info(vehicle)")
            vcols = cursor.fetchall()
            vcol_names = [col[1] for col in vcols]
            if 'location' not in vcol_names:
                print("Adding location column to vehicle table...")
                cursor.execute("ALTER TABLE vehicle ADD COLUMN location VARCHAR(100)")
                conn.commit()
                print("Successfully added location column!")
            else:
                print("location column already exists in vehicle table.")

            conn.close()
            print("Database migration completed successfully!")
            return True

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                print(f"Database is locked, waiting... (attempt {attempt + 1}/{max_attempts})")
                time.sleep(2)
                attempt += 1
            else:
                print(f"Operational error: {e}")
                return False
        except Exception as e:
            print(f"Error during migration: {e}")
            return False

    print("Failed to migrate database after multiple attempts")
    return False

if __name__ == "__main__":
    success = add_due_date_column()
    if not success:
        print("Migration failed. You may need to stop the Flask app and try again.")