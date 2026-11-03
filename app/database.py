import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    price_cents INTEGER NOT NULL CHECK(price_cents > 0),
                    stock INTEGER NOT NULL CHECK(stock >= 0)
                );
                CREATE TABLE IF NOT EXISTS customers (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    status TEXT NOT NULL DEFAULT 'pending',
                    customer_category TEXT NOT NULL,
                    address TEXT NOT NULL,
                    city TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    shipping TEXT NOT NULL,
                    subtotal_cents INTEGER NOT NULL,
                    discount_cents INTEGER NOT NULL,
                    shipping_cents INTEGER NOT NULL,
                    total_cents INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents > 0),
                    UNIQUE(order_id, product_id)
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    order_id INTEGER NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    recipient TEXT NOT NULL,
                    message TEXT NOT NULL
                );
            """)
