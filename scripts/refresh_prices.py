"""只刷新行情快照，不跑 LLM 分析（近零成本）。

给关注列表里每只股票拉最新价格 + 技术指标，写入 price_snapshots，
让看板的趋势图/最新价保持新鲜。不调用 TradingAgents，不产生 LLM 费用。

用法：
    uv run python scripts/refresh_prices.py
"""
from __future__ import annotations

import sys

from trading24.config import load_watchlist
from trading24.db import database as db
from trading24 import prices


def main() -> int:
    _, watch = load_watchlist()
    if not watch:
        print("关注列表为空，无可刷新标的。")
        return 0

    conn = db.connect()
    db.init_db(conn)

    ok, failed, total_rows = [], [], 0
    for t in watch:
        try:
            n = prices.snapshot(conn, t.symbol)  # 失败隔离：单只出错不影响其余
            if n == 0:
                failed.append((t.symbol, "无数据（可能被限流或代码错误）"))
                print(f"  ✗ {t.symbol}: 0 行")
            else:
                ok.append((t.symbol, n))
                total_rows += n
                print(f"  ✓ {t.symbol}: {n} 天")
        except Exception as e:  # noqa: BLE001
            failed.append((t.symbol, str(e)[:120]))
            print(f"  ✗ {t.symbol}: {str(e)[:120]}")

    print(f"\n行情刷新完成：成功 {len(ok)} / 失败 {len(failed)} / 共写 {total_rows} 行")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
