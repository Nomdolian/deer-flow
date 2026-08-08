"""Create all tables. Run once against a fresh Postgres instance:
    python -m scripts.init_db
"""

from app.db.base import init_db

if __name__ == "__main__":
    init_db()
    print("tables created")
