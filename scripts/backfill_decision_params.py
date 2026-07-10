"""给已有决策回填结构化参数（decision_params）。

对每个 status=success 且还没有 decision_params 的 run，取 final（缺则 trader）
报告文本，调 quick 模型提取固定字段落库。幂等：已有参数的 run 跳过；
加 --force 重新提取全部。

用法：uv run python scripts/backfill_decision_params.py [--force]
成本：每条约 $0.001（quick_think 模型）。
"""
import sys

from trading24.config import load_settings
from trading24.db import database as db
from trading24 import extract


def main() -> int:
    force = "--force" in sys.argv
    settings = load_settings()
    conn = db.connect()
    db.init_db(conn)

    runs = [r for r in db.get_runs(conn, limit=1000) if r.status == "success"]
    done = skipped = failed = 0
    for r in sorted(runs, key=lambda x: x.id or 0):
        if not force and db.get_decision_params(conn, r.id):
            skipped += 1
            continue
        texts = {rep.section: rep.content for rep in db.get_reports(conn, r.id)}
        final_text = texts.get("final") or texts.get("trader")
        params = extract.extract_decision_params(final_text)
        if params is None:
            failed += 1
            print(f"  ✗ run#{r.id} {r.ticker} {r.trade_date}: 提取失败/无文本")
            continue
        db.upsert_decision_params(conn, r.id, params, model=settings.quick_think_llm)
        done += 1
        brief = {k: v for k, v in params.items() if v is not None}
        print(f"  ✓ run#{r.id} {r.ticker} {r.trade_date}: {brief}")

    print(f"\n完成：提取 {done} / 跳过 {skipped} / 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
