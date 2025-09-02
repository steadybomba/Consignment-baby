DROP TABLE IF EXISTS shipments;
DROP TABLE IF EXISTS checkpoints;
DROP TABLE IF EXISTS subscribers;

CREATE TABLE shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking TEXT UNIQUE NOT NULL,
    title TEXT,
    origin_lat REAL,
    origin_lng REAL,
    dest_lat REAL,
    dest_lng REAL,
    status TEXT DEFAULT 'In Transit'
);

CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL,
    lat REAL,
    lng REAL,
    label TEXT,
    note TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (shipment_id) REFERENCES shipments (id)
);

CREATE TABLE subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL,
    email TEXT,
    FOREIGN KEY (shipment_id) REFERENCES shipments (id)
);
