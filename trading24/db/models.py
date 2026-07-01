"""数据库行的轻量 dataclass 表示。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Run:
    id: int | None
    ticker: str
    market: str
    trade_date: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    decision: str | None = None
    model: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


@dataclass
class Report:
    run_id: int
    section: str
    content: str
    created_at: str
    id: int | None = None


@dataclass
class PriceSnapshot:
    ticker: str
    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
