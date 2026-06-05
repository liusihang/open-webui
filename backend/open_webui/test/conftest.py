import os
import sqlite3
import tempfile

os.environ.setdefault("WEBUI_SECRET_KEY", "test-secret")
os.environ.setdefault("ENABLE_DB_MIGRATIONS", "false")

_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db_file.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_file.name}")

if os.environ["DATABASE_URL"] == f"sqlite:///{_db_file.name}":
    with sqlite3.connect(_db_file.name) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY,
                data JSON NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME
            )
            """
        )
