"""SQLite 连接与 CRUD。单文件库，WAL 模式以便看板读、runner 写并发。"""
from __future__ import annotations

import json
import re
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
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：给旧库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "win_tracked" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN win_tracked INTEGER DEFAULT 1")


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


_SECRET_RE = re.compile(
    r'((?:api[_-]?key|apikey|access[_-]?key|secret|token|password|pwd)=)'
    r'([^&\s"\'#]+)', re.IGNORECASE)
_BEARER_RE = re.compile(r'(Bearer\s+)([A-Za-z0-9._\-]{8,})', re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """把 URL/文本里的 api_key/token/密码等值打码，避免明文密钥落库（DB 会进 git）。"""
    text = _SECRET_RE.sub(r'\1***', text)
    text = _BEARER_RE.sub(r'\1***', text)
    return text


def fail_run(conn, run_id, error) -> None:
    conn.execute(
        "UPDATE runs SET status='failed', finished_at=?, error=? WHERE id=?",
        (now_iso(), redact_secrets(str(error))[:1000], run_id),
    )
    conn.commit()


def reset_run(conn, run_id: int, model: str | None = None) -> None:
    """复用已有记录重跑（典型：失败重试）：清空产物、置回 running。
    不新建决策记录——run_id 及其在历史里的位置保持不变。"""
    conn.execute(
        "UPDATE runs SET status='running', started_at=?, finished_at=NULL, "
        "error=NULL, decision=NULL, tokens=0, cost_usd=0, model=COALESCE(?, model) "
        "WHERE id=?",
        (now_iso(), model, run_id),
    )
    conn.execute("DELETE FROM reports WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM decision_params WHERE run_id=?", (run_id,))
    conn.commit()


def set_run_tracked(conn, run_id: int, tracked: bool) -> None:
    """标记某次决策是否纳入胜率统计。记录本身保留，可随时切回。"""
    conn.execute("UPDATE runs SET win_tracked=? WHERE id=?", (1 if tracked else 0, run_id))
    conn.commit()


# ---- decision_params（final 报告的结构化提取）----
_DP_COLS = ("stop_loss", "entry_low", "entry_high", "initial_position_pct",
            "max_position_pct", "price_target", "time_horizon", "time_horizon_days")


def upsert_decision_params(conn, run_id: int, params: dict, model: str | None = None) -> None:
    vals = [params.get(c) for c in _DP_COLS]
    conn.execute(
        f"""INSERT INTO decision_params (run_id,{','.join(_DP_COLS)},model,extracted_at)
        VALUES (?,{','.join('?' * len(_DP_COLS))},?,?)
        ON CONFLICT(run_id) DO UPDATE SET
        {','.join(f'{c}=excluded.{c}' for c in _DP_COLS)},
        model=excluded.model, extracted_at=excluded.extracted_at""",
        [run_id, *vals, model, now_iso()],
    )
    conn.commit()


def get_decision_params(conn, run_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM decision_params WHERE run_id=?", (run_id,)).fetchone()
    return dict(row) if row else None


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


# ---- ai_threads（AI 助手会话）----
def get_ai_threads(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, data FROM ai_threads ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    threads = []
    for r in rows:
        try:
            data = json.loads(r["data"] or "{}")
        except ValueError:
            data = {}
        data["id"] = int(r["id"])
        data["title"] = r["title"]
        threads.append(data)
    return threads


def upsert_ai_thread(conn, thread: dict) -> int:
    """写入/更新一条会话；返回其 id（新建时为自增 id）。"""
    payload = json.dumps(
        {k: v for k, v in thread.items() if k not in ("id", "title")},
        ensure_ascii=False,
    )
    tid = thread.get("id")
    if tid and conn.execute("SELECT 1 FROM ai_threads WHERE id=?", (tid,)).fetchone():
        conn.execute(
            "UPDATE ai_threads SET title=?, data=?, updated_at=? WHERE id=?",
            (thread.get("title", ""), payload, now_iso(), tid),
        )
    else:
        cur = conn.execute(
            "INSERT INTO ai_threads(title, data, created_at, updated_at) VALUES (?,?,?,?)",
            (thread.get("title", ""), payload, now_iso(), now_iso()),
        )
        tid = cur.lastrowid
    conn.commit()
    return int(tid)


def delete_ai_thread(conn, thread_id: int) -> None:
    conn.execute("DELETE FROM ai_threads WHERE id=?", (thread_id,))
    conn.commit()
