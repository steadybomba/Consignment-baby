import sqlite3
from flask import g

DATABASE = "consignment.db"

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DATABASE)
    with open("schema.sql", "r") as f:
        db.executescript(f.read())
    db.commit()
    db.close()
