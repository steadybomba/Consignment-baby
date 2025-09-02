"""
database.py
SQLite helper: get_db(), close_connection, init_db(), and new_db_connection()

Notes:
- get_db() returns a connection stored on flask.g for request-scoped usage.
- new_db_connection() returns a brand-new connection suitable for background threads.
  It sets check_same_thread=False so other threads can use the connection safely.
- init_db() will create schema from schema.sql if present.
"""

import sqlite3
import os
from flask import g

DATABASE_PATH = os.environ.get("DATABASE_URL", "tracker.db")

def _connect(**kwargs):
    # ensure detection of datetime types, and allow overriding check_same_thread
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES, **kwargs)
    conn.row_factory = sqlite3.Row
    return conn

def get_db():
    """
    Return a request-local connection (Flask g). Use this inside request handlers.
    """
    db = getattr(g, "_database", None)
    if db is None:
        db = _connect()
        g._database = db
        # lazy init if DB file doesn't exist
        if not os.path.exists(DATABASE_PATH):
            p = os.path.join(os.path.dirname(__file__), "schema.sql")
            if os.path.exists(p):
                with open(p, "r") as f:
                    db.executescript(f.read())
                db.commit()
    return db

def close_connection(e=None):
    db = getattr(g, "_database", None)
    if db is not None:
        try:
            db.close()
        finally:
            g._database = None

def new_db_connection():
    """
    Return a fresh sqlite3.Connection suitable for background threads.
    Uses check_same_thread=False to allow use from other threads.
    Caller is responsible for closing the connection.
    """
    return _connect(check_same_thread=False)

def init_db():
    p = os.path.join(os.path.dirname(__file__), "schema.sql")
    conn = _connect(check_same_thread=False)
    try:
        if os.path.exists(p):
            with open(p, "r") as f:
                conn.executescript(f.read())
            conn.commit()
    finally:
        conn.close()
