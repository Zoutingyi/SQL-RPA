"""Shared fixtures for RPA backend tests."""

import os
import sys
from pathlib import Path

# Ensure backend/ is on sys.path
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Disable rate limiting for tests
os.environ["SQL_RPA_RATE_LIMIT"] = "9999"
# Disable auth for tests (SecurityMiddleware still enforces body size limit)
os.environ["API_KEY"] = ""
os.environ["APP_ENV"] = "development"

import aiosqlite
import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text

TEST_DB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT,
    age INTEGER,
    role TEXT DEFAULT 'user',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product_name TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    amount REAL,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER DEFAULT 0,
    category TEXT,
    description TEXT
);
"""

TEST_DB_SEED_SQL = """
DELETE FROM orders;
DELETE FROM users;
DELETE FROM products;

INSERT INTO users (name, email, age, role) VALUES
  ('Alice',   'alice@example.com',   28, 'admin'),
  ('Bob',     'bob@example.com',     35, 'user'),
  ('Charlie', 'charlie@example.com', 42, 'user'),
  ('Diana',   '',                     0, 'user'),
  ('Eve',     'eve@example.com',    150, 'user'),
  ('Frank',   NULL,                  25, 'user'),
  ('Grace',   'grace@example.com',   30, 'user'),
  ('Henry',   'henry@example.com',   55, 'admin'),
  ('Ivy',     'ivy@example.com',     22, 'user'),
  ('Jack',    'jack@example.com',    19, 'user');

INSERT INTO orders (user_id, product_name, quantity, status, amount, created_at) VALUES
  (1,  'Laptop',     1, 'completed', 5999.00, '2026-06-01'),
  (1,  'Mouse',      2, 'completed',  199.00, '2026-06-15'),
  (2,  'Keyboard',   1, 'pending',    299.00, '2026-07-01'),
  (3,  'Monitor',    1, 'shipped',   2499.00, '2026-06-20'),
  (3,  'USB Cable',  3, 'pending',     59.00, '2026-07-02'),
  (4,  'Webcam',     1, 'completed',  399.00, '2026-05-01'),
  (5,  'Headphones', 1, 'expired',    199.00, '2025-01-01'),
  (6,  'Charger',    2, 'pending',    149.00, '2026-06-30'),
  (7,  'Tablet',     1, 'shipped',   3499.00, '2026-06-28'),
  (8,  'Phone Case', 1, 'completed',   49.00, '2026-06-25'),
  (NULL, 'Orphan Product', 1, 'pending', 99.00, '2026-07-01'),
  (1,  'Monitor',    1, 'expired',   2499.00, '2024-12-31'),
  (2,  'Mouse Pad',  1, 'pending',     29.00, '2026-07-02'),
  (NULL, 'Gift Card', 5, 'completed', 500.00, '2026-06-01'),
  (9,  'Speaker',    1, 'pending',    399.00, '2026-07-01');

INSERT INTO products (name, price, stock, category) VALUES
  ('Laptop',      5999.00,  50, 'Electronics'),
  ('Mouse',        199.00, 200, 'Electronics'),
  ('Keyboard',     299.00, 150, 'Electronics'),
  ('Monitor',     2499.00,  30, 'Electronics'),
  ('USB Cable',     59.00,   0, 'Accessories'),
  ('Webcam',       399.00,  25, 'Electronics'),
  ('Gift Card',     -50.00, 100, 'Other'),
  ('Test Product',    0.00,   0, 'Test');
"""


def _split_sql_script(script: str) -> list[str]:
    """Split a SQL script into individual statements, skipping empty lines."""
    statements = []
    for stmt in script.split(";"):
        stmt = stmt.strip()
        if stmt:
            statements.append(stmt)
    return statements


@pytest_asyncio.fixture
async def test_rpa_db(tmp_path):
    """Create a fresh test database with schema and seed data, isolated per test."""
    # Reset singleton connector
    from db_connector.factory import close_connector
    await close_connector()

    # Use a unique temp path per test to eliminate file-level interference
    db_path = str(tmp_path / "test_rpa.db")

    # Override pydantic-settings singleton
    from config import settings
    settings.db_type = "sqlite"
    settings.db_sqlite_path = db_path

    # Create and seed database using individual executes (more reliable than executescript)
    async with aiosqlite.connect(db_path) as conn:
        for stmt in _split_sql_script(TEST_DB_SCHEMA_SQL):
            await conn.execute(stmt)
        for stmt in _split_sql_script(TEST_DB_SEED_SQL):
            await conn.execute(stmt)
        await conn.commit()

    yield db_path

    # Cleanup
    await close_connector()


@pytest_asyncio.fixture
async def db_connector(test_rpa_db):
    """Return a connected SqliteConnector backed by the test DB (no WAL)."""
    from db_connector.sqlite_impl import SqliteConnector

    conn = SqliteConnector(test_rpa_db)
    await conn.connect()
    # Disable WAL — WAL files can survive connection close and corrupt the next
    # test's fresh database when they share the same file path.
    await conn._conn.execute("PRAGMA journal_mode=DELETE")
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def init_rag_db():
    """Initialize RAG Agent internal database tables."""
    from models.database import init_db

    await init_db()
