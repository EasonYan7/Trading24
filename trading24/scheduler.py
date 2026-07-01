"""批量调度：遍历 watchlist 逐个分析，失败隔离续跑。"""
from __future__ import annotations

from datetime import date

from .config import load_settings, load_watchlist
from .db import database as db
from . import runner


def run_batch(trade_date: str | None = None) -> dict:
    """遍历 watchlist 串行跑。单家失败不影响其余。返回汇总。"""
    trade_date = trade_date or date.today().isoformat()
    settings = load_settings()
    _, watch = load_watchlist()
    conn = db.connect()
    db.init_db(conn)

    results = {"date": trade_date, "ok": [], "failed": [], "cost": 0.0}
    for t in watch:
        try:
            print(f"▶ {t.symbol} ({t.name}) ...")
            rid = runner.run_one(t.symbol, trade_date, settings=settings, conn=conn)
            r = db.get_runs(conn, ticker=t.symbol, limit=1)[0]
            results["ok"].append((t.symbol, r.decision, r.cost_usd))
            results["cost"] += r.cost_usd or 0
            print(f"  ✓ {r.decision}  ${r.cost_usd}")
        except Exception as e:  # noqa: BLE001  失败隔离
            results["failed"].append((t.symbol, str(e)[:120]))
            print(f"  ✗ {t.symbol} 失败: {str(e)[:120]}")

    budget = settings.cost_budget_usd_per_day
    if results["cost"] > budget:
        results["over_budget"] = True
        print(f"⚠️ 当日成本 ${results['cost']:.2f} 超预算 ${budget}")

    print(f"\n汇总 {trade_date}: 成功 {len(results['ok'])} / 失败 {len(results['failed'])} "
          f"/ 成本 ${results['cost']:.2f}")
    return results
