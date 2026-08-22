import sqlite3
import os
from datetime import date, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "kettle.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    card_last4 TEXT,
    card_balance REAL NOT NULL DEFAULT 4000.0,
    loyalty_points INTEGER NOT NULL DEFAULT 0,
    subscribed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kettles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL, -- 'household' or 'trip'
    created_by INTEGER NOT NULL,
    start_date TEXT,
    end_date TEXT,
    total_amount REAL, -- trip pool amount
    landlord_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kettle_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kettle_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    join_date TEXT NOT NULL,
    left_date TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active, leaving, left
    UNIQUE(kettle_id, user_id)
);

CREATE TABLE IF NOT EXISTS charges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kettle_id INTEGER NOT NULL,
    category TEXT NOT NULL, -- rent, utilities, groceries, other
    amount REAL NOT NULL DEFAULT 0,
    UNIQUE(kettle_id, category)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kettle_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS covers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kettle_id INTEGER NOT NULL,
    payer_id INTEGER NOT NULL,
    debtor_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kettle_id INTEGER,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    sound_preset TEXT,
    custom_nudge_id INTEGER
);

CREATE TABLE IF NOT EXISTS nudges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kettle_id INTEGER NOT NULL,
    to_user_id INTEGER NOT NULL,
    from_user_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pay_by_eom (
    kettle_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    month TEXT NOT NULL,
    pressed_at TEXT NOT NULL,
    PRIMARY KEY (kettle_id, user_id, month)
);

CREATE TABLE IF NOT EXISTS custom_nudges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    price_qr REAL NOT NULL DEFAULT 7.0,
    times_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    audio_data TEXT
);

CREATE TABLE IF NOT EXISTS dining_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id TEXT NOT NULL,
    label TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- open, closed
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS table_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'dining', -- dining, late_arrival, left_early
    joined_at TEXT NOT NULL,
    left_at TEXT,
    spending_cap REAL,
    exclude_drinks INTEGER NOT NULL DEFAULT 0,
    UNIQUE(table_id, user_id)
);

CREATE TABLE IF NOT EXISTS table_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'food',
    price_qr REAL NOT NULL,
    shared_with TEXT, -- comma-separated user ids, includes the orderer
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS table_covers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    payer_id INTEGER NOT NULL,
    debtor_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn):
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "subscription_status" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'none'")
    if "trial_ends_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_ends_at TEXT")
    if "next_billing_date" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN next_billing_date TEXT")
    if "location_prompted" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN location_prompted INTEGER NOT NULL DEFAULT 0")

    notif_cols = [r["name"] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()]
    if "sound_preset" not in notif_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN sound_preset TEXT")
    if "custom_nudge_id" not in notif_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN custom_nudge_id INTEGER")

    nudge_cols = [r["name"] for r in conn.execute("PRAGMA table_info(custom_nudges)").fetchall()]
    if "audio_data" not in nudge_cols:
        conn.execute("ALTER TABLE custom_nudges ADD COLUMN audio_data TEXT")

    tmember_cols = [r["name"] for r in conn.execute("PRAGMA table_info(table_members)").fetchall()]
    if "left_at" not in tmember_cols:
        conn.execute("ALTER TABLE table_members ADD COLUMN left_at TEXT")
    if "spending_cap" not in tmember_cols:
        conn.execute("ALTER TABLE table_members ADD COLUMN spending_cap REAL")
    if "exclude_drinks" not in tmember_cols:
        conn.execute("ALTER TABLE table_members ADD COLUMN exclude_drinks INTEGER NOT NULL DEFAULT 0")

    torder_cols = [r["name"] for r in conn.execute("PRAGMA table_info(table_orders)").fetchall()]
    if "category" not in torder_cols:
        conn.execute("ALTER TABLE table_orders ADD COLUMN category TEXT NOT NULL DEFAULT 'food'")

    conn.commit()


def init_db():
    fresh = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)
    if fresh:
        seed(conn)
    conn.close()


def seed(conn):
    from werkzeug.security import generate_password_hash

    demo_users = [
        ("Alex Rivera", "alex@demo.kettle"),
        ("Sam Okafor", "sam@demo.kettle"),
        ("Jordan Lee", "jordan@demo.kettle"),
    ]
    pw = generate_password_hash("demo1234", method="pbkdf2:sha256")
    today = date.today().isoformat()
    for name, email in demo_users:
        conn.execute(
            "INSERT INTO users (name, email, password_hash, card_last4, card_balance, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, email, pw, "4242", 4000.0, today),
        )
    conn.commit()
