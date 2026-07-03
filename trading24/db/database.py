"""SQLite 连接与 CRUD。单文件库，WAL 模式以便看板读、runner 写并发。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH
from .models import PriceSnapshot, Report, Run

SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect(db_path: Path | None = None, check_same_thread: bool = True) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA.read_text())
    conn.commit()


# ---- runs ----
def create_run(conn, ticker, market, trade_date, model) -> int:
    cur = conn.execute(
        "INSERT INTO runs(ticker, market, trade_date, started_at, status, model) "
        "VALUES (?,?,?,?, 'running', ?)",
        (ticker, market, trade_date, now_iso(), model),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, decision, tokens, cost_usd) -> None:
    conn.execute(
        "UPDATE runs SET status='success', finished_at=?, decision=?, tokens=?, cost_usd=? WHERE id=?",
        (now_iso(), decision, tokens, cost_usd, run_id),
    )
    conn.commit()


def fail_run(conn, run_id, error) -> None:
    conn.execute(
        "UPDATE runs SET status='failed', finished_at=?, error=? WHERE id=?",
        (now_iso(), str(error)[:1000], run_id),
    )
    conn.commit()


def get_runs(conn, ticker=None, limit=100) -> list[Run]:
    if ticker:
        rows = conn.execute(
            "SELECT * FROM runs WHERE ticker=? ORDER BY id DESC LIMIT ?", (ticker, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [Run(**dict(r)) for r in rows]


def get_run(conn, run_id: int) -> Run | None:
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return Run(**dict(row)) if row else None


def delete_runs(conn, run_ids: list[int]) -> None:
    """删除运行记录及其关联报告/通知。用于看板历史页手动清理误跑记录。"""
    ids = [int(rid) for rid in run_ids]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM reports WHERE run_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM notifications WHERE run_id IN ({placeholders})", ids)
    conn.execute(f"DELETE FROM runs WHERE id IN ({placeholders})", ids)
    conn.commit()


# ---- reports ----
def add_report(conn, run_id, section, content) -> None:
    conn.execute(
        "INSERT INTO reports(run_id, section, content, created_at) VALUES (?,?,?,?)",
        (run_id, section, content, now_iso()),
    )
    conn.commit()


def set_report(conn, run_id, section, content) -> None:
    """幂等写入某段报告：同 (run_id, section) 覆盖为最新内容（供流式逐段更新用）。"""
    conn.execute(
        "DELETE FROM reports WHERE run_id=? AND section=?", (run_id, section)
    )
    conn.execute(
        "INSERT INTO reports(run_id, section, content, created_at) VALUES (?,?,?,?)",
        (run_id, section, content, now_iso()),
    )
    conn.commit()


def get_reports(conn, run_id) -> list[Report]:
    rows = conn.execute(
        "SELECT * FROM reports WHERE run_id=? ORDER BY id", (run_id,)
    ).fetchall()
    return [Report(**dict(r)) for r in rows]


# ---- price_snapshots ----
def upsert_price(conn, p: PriceSnapshot) -> None:
    conn.execute(
        """INSERT INTO price_snapshots
        (ticker,date,open,high,low,close,volume,ma5,ma20,ma60,rsi,macd,macd_signal)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker,date) DO UPDATE SET
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
        volume=excluded.volume,ma5=excluded.ma5,ma20=excluded.ma20,ma60=excluded.ma60,
        rsi=excluded.rsi,macd=excluded.macd,macd_signal=excluded.macd_signal""",
        (p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume,
         p.ma5, p.ma20, p.ma60, p.rsi, p.macd, p.macd_signal),
    )
    conn.commit()


def get_prices(conn, ticker, limit=250) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM price_snapshots WHERE ticker=? ORDER BY date DESC LIMIT ?",
        (ticker, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]
