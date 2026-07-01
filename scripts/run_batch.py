"""手动触发整批分析。用法：uv run python scripts/run_batch.py [yyyy-mm-dd]"""
import sys

from trading24.scheduler import run_batch

if __name__ == "__main__":
    trade_date = sys.argv[1] if len(sys.argv) > 1 else None
    run_batch(trade_date)
