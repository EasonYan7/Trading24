"""价格与技术指标：yfinance 取数 + stockstats 算指标 → 落 price_snapshots。"""
from __future__ import annotations

import time

import yfinance as yf
from stockstats import wrap

from .db import database as db
from .db.models import PriceSnapshot


def _fetch_history(ticker: str, period: str = "6mo", retries: int = 3):
    """带重试拉历史（应对 yfinance 429）。"""
    last_err = None
    for i in range(retries):
        try:
            df = yf.Ticker(ticker).history(period=period)
            if not df.empty:
                return df
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(1.5 * (i + 1))
    if last_err:
        raise last_err
    return None


def _round(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def snapshot(conn, ticker: str, recent_days: int = 120) -> int:
    """拉历史、算指标、把最近 recent_days 天写入 price_snapshots。返回写入行数。"""
    df = _fetch_history(ticker)
    if df is None or df.empty:
        return 0

    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    date_col = "date" if "date" in df.columns else df.columns[0]

    # stockstats 会改索引，用 numpy 数组按位置对齐，避免索引标签不一致。
    sdf = wrap(df.copy())
    ind = {
        "ma5": sdf["close_5_sma"].to_numpy(),
        "ma20": sdf["close_20_sma"].to_numpy(),
        "ma60": sdf["close_60_sma"].to_numpy(),
        "rsi": sdf["rsi_14"].to_numpy(),
        "macd": sdf["macd"].to_numpy(),
        "macd_signal": sdf["macds"].to_numpy(),
    }

    total = len(df)
    start = max(0, total - recent_days)
    n = 0
    for pos in range(start, total):
        row = df.iloc[pos]
        p = PriceSnapshot(
            ticker=ticker,
            date=str(row[date_col])[:10],
            open=_round(row["open"]),
            high=_round(row["high"]),
            low=_round(row["low"]),
            close=_round(row["close"]),
            volume=int(row["volume"]) if "volume" in df.columns else None,
            ma5=_round(ind["ma5"][pos]),
            ma20=_round(ind["ma20"][pos]),
            ma60=_round(ind["ma60"][pos]),
            rsi=_round(ind["rsi"][pos]),
            macd=_round(ind["macd"][pos]),
            macd_signal=_round(ind["macd_signal"][pos]),
        )
        db.upsert_price(conn, p)
        n += 1
    return n


def latest_close(conn, ticker: str):
    rows = db.get_prices(conn, ticker, limit=1)
    return rows[-1]["close"] if rows else None
