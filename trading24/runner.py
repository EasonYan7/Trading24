"""核心运行器：调 TradingAgentsGraph 跑一家公司，落库 reports/runs/price_snapshots。

Phase 1 用 propagate() 一次跑完后抽取各段落库（稳健）。
Phase 3 做实时进度时再改造为 graph.stream() 逐节点写入。
"""
from __future__ import annotations

from .config import Settings, load_settings, market_of
from .db import database as db
from . import prices


def _section_map(final_state: dict) -> dict[str, str]:
    """从最终 state 抽取各段报告 → {section: content}。"""
    inv = final_state.get("investment_debate_state", {}) or {}
    risk = final_state.get("risk_debate_state", {}) or {}
    raw = {
        "market": final_state.get("market_report"),
        "sentiment": final_state.get("sentiment_report"),
        "news": final_state.get("news_report"),
        "fundamentals": final_state.get("fundamentals_report"),
        "bull": inv.get("bull_history"),
        "bear": inv.get("bear_history"),
        "research_mgr": inv.get("judge_decision") or final_state.get("investment_plan"),
        "trader": final_state.get("trader_investment_plan"),
        "risk": risk.get("judge_decision"),
        "final": final_state.get("final_trade_decision"),
    }
    return {k: v for k, v in raw.items() if v}


def run_one(ticker: str, trade_date: str, settings: Settings | None = None,
            conn=None, analysts: list[str] | None = None) -> int:
    """跑一家公司并落库，返回 run_id。异常时标记 failed 并向上抛。

    analysts: 选择参与的分析师子集，None 时用 settings.analysts。
    """
    settings = settings or load_settings()
    analysts = analysts or settings.analysts
    own_conn = conn is None
    conn = conn or db.connect()
    if own_conn:
        db.init_db(conn)

    market = market_of(ticker)
    run_id = db.create_run(conn, ticker, market, trade_date, settings.deep_think_llm)

    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from .stats import StatsCallbackHandler

        stats = StatsCallbackHandler()
        ta = TradingAgentsGraph(
            selected_analysts=tuple(analysts),
            debug=False,
            config=settings.to_ta_config(),
            callbacks=[stats],
        )
        final_state, decision = ta.propagate(ticker, trade_date)

        for section, content in _section_map(final_state).items():
            db.add_report(conn, run_id, section, str(content))

        # 价格快照（趋势图用）
        try:
            prices.snapshot(conn, ticker)
        except Exception:  # noqa: BLE001  价格失败不阻塞主流程
            pass

        cost = stats.cost(settings.price_per_1m_input, settings.price_per_1m_output)
        db.finish_run(conn, run_id, decision=str(decision).strip(),
                      tokens=stats.tokens, cost_usd=round(cost, 4))
        return run_id
    except Exception as e:  # noqa: BLE001
        db.fail_run(conn, run_id, e)
        raise
