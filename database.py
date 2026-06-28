"""
Database SQLite leggero per il sistema di paper trading.
Usiamo SQLite invece di Postgres per semplicita' di avvio locale:
e' un singolo file, zero setup, perfetto per uso personale a due.
Se in futuro volete passare a Postgres, lo schema e' identico
e basta cambiare la connection string in production.
"""
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "trading.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            symbol TEXT DEFAULT 'BTCUSDT',
            timeframe TEXT DEFAULT '5m',
            max_capital REAL DEFAULT 1000,
            risk_per_trade_pct REAL DEFAULT 1.0,
            max_daily_loss_pct REAL DEFAULT 5.0,
            max_trades_per_day INTEGER DEFAULT 10,
            stop_loss_pct REAL DEFAULT 0.8,
            take_profit_pct REAL DEFAULT 1.5,
            trailing_stop_pct REAL DEFAULT 0.5,
            max_slippage_pct REAL DEFAULT 0.3,
            bot_active INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            quantity REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT,            -- open / closed
            exit_reason TEXT,       -- take_profit / stop_loss / trailing_stop / manual
            opened_at TEXT,
            closed_at TEXT,
            signal_source TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT,
            category TEXT,
            message TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equity REAL,
            ts TEXT
        )
    """)

    # Inserisce la riga di config di default se non esiste
    cur.execute("INSERT OR IGNORE INTO config (id) VALUES (1)")

    conn.commit()
    conn.close()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_event(level: str, category: str, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO logs (level, category, message, created_at) VALUES (?, ?, ?, ?)",
            (level, category, message, now_iso()),
        )


def get_config():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM config WHERE id = 1").fetchone()
        return dict(row)


def update_config(**kwargs):
    if not kwargs:
        return get_config()
    fields = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values())
    with get_conn() as conn:
        conn.execute(f"UPDATE config SET {fields} WHERE id = 1", values)
    return get_config()


def get_open_trade():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trades WHERE status = 'open' LIMIT 1").fetchone()
        return dict(row) if row else None


def open_trade(symbol, side, entry_price, quantity, signal_source):
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (symbol, side, entry_price, quantity, status, opened_at, signal_source)
               VALUES (?, ?, ?, ?, 'open', ?, ?)""",
            (symbol, side, entry_price, quantity, now_iso(), signal_source),
        )
        return cur.lastrowid


def close_trade(trade_id, exit_price, pnl, pnl_pct, exit_reason):
    with get_conn() as conn:
        conn.execute(
            """UPDATE trades SET exit_price = ?, pnl = ?, pnl_pct = ?,
               status = 'closed', exit_reason = ?, closed_at = ? WHERE id = ?""",
            (exit_price, pnl, pnl_pct, exit_reason, now_iso(), trade_id),
        )


def get_trades(limit=100):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_logs(limit=200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def record_equity(equity):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO balance_history (equity, ts) VALUES (?, ?)",
            (equity, now_iso()),
        )


def get_equity_history(limit=500):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM balance_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows][::-1]


def get_daily_pnl():
    """Somma il pnl dei trade chiusi oggi (UTC)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pnl FROM trades WHERE status='closed' AND closed_at LIKE ?",
            (f"{today}%",),
        ).fetchall()
        return sum(r["pnl"] for r in rows) if rows else 0.0


def count_trades_today():
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM trades WHERE opened_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return row["c"]
