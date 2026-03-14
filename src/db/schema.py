from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    broker TEXT NOT NULL,
    side TEXT NOT NULL,
    symbol TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL NOT NULL,
    volume REAL NOT NULL,
    fee REAL DEFAULT 0,
    pnl REAL,
    pnl_pct REAL,
    executed_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_strategy
    ON trades(strategy_id, executed_at DESC);

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    broker TEXT NOT NULL,
    symbols TEXT NOT NULL,
    capital_allocation REAL NOT NULL,
    current_capital REAL NOT NULL,
    interval_minutes INTEGER DEFAULT 5,
    enabled INTEGER DEFAULT 1,
    params TEXT NOT NULL,
    code_path TEXT NOT NULL,
    code_version TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    date TEXT NOT NULL,
    initial_capital REAL NOT NULL,
    current_capital REAL NOT NULL,
    daily_pnl REAL NOT NULL,
    total_pnl REAL NOT NULL,
    total_pnl_pct REAL NOT NULL,
    win_count INTEGER DEFAULT 0,
    loss_count INTEGER DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    avg_profit REAL,
    avg_loss REAL,
    max_profit REAL,
    max_loss REAL,
    rr_ratio REAL,
    UNIQUE(strategy_id, date)
);

CREATE TABLE IF NOT EXISTS market_data (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (symbol, timeframe, timestamp)
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    role TEXT NOT NULL,
    input_data TEXT NOT NULL,
    output_data TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_used INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, turn)
);

CREATE TABLE IF NOT EXISTS pending_trades (
    id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


async def init_db(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    return db
