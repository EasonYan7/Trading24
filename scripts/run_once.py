"""手动跑一家公司。用法：uv run python scripts/run_once.py NVDA [2024-05-10]"""
import sys
from datetime import date

from trading24 import runner
from trading24.db import database as db


def main():
    if len(sys.argv) < 2:
        print("用法: uv run python scripts/run_once.py <ticker> [yyyy-mm-dd]")
        sys.exit(2)
    ticker = sys.argv[1]
    trade_date = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

    print(f"▶ 分析 {ticker} @ {trade_date} ...（几分钟，会调用 LLM）")
    run_id = runner.run_one(ticker, trade_date)

    conn = db.connect()
    r = db.get_runs(conn, ticker=ticker)[0]
    reps = db.get_reports(conn, run_id)
    px = db.get_prices(conn, ticker)
    print(f"\n✅ run #{run_id} {r.status} | 决策: {r.decision}")
    print(f"   报告段: {len(reps)} ({', '.join(x.section for x in reps)})")
    print(f"   价格快照: {len(px)} 天")


if __name__ == "__main__":
    main()
