"""
database.py
SQLite helper: get_db(), close_connection, init_db()
"""

import sqlite3
import os
from flask import g

DATABASE_PATH = os.environ.get("DATABASE_URL", "tracker.db")

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        need_init = not os.path.exists(DATABASE_PATH)
        db = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        g._database = db
        if need_init:
            # lazy init
            from pathlib import Path
            p = Path(__file__).parent / "schema.sql"
            if p.exists():
                with open(p, "r") as f:
                    db.executescript(f.read())
                db.commit()
    return db

def close_connection(e=None):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()
        g._database = None

def init_db():
    db = sqlite3.connect(DATABASE_PATH)
    p = os.path.join(os.path.dirname(__file__), "schema.sql")
    if os.path.exists(p):
        with open(p, "r") as f:
            db.executescript(f.read())
        db.commit()
    db.close()
