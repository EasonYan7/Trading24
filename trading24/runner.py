"""核心运行器：调 TradingAgentsGraph 跑一家公司，落库 reports/runs/price_snapshots。

Phase 1 用 propagate() 一次跑完后抽取各段落库（稳健）。
Phase 3 做实时进度时再改造为 graph.stream() 逐节点写入。
"""
from __future__ import annotations

import threading

from .config import Settings, load_settings, market_of
from .db import database as db
from . import prices


# ── 协作式取消 ──────────────────────────────────────────────
# app 侧点「取消运行」时登记 run_id；_run_streamed 在每个节点边界检查后停止，
# 这样能真正停下、不再烧 token（而不是杀线程）。run_one 捕获后删除本次记录。
_cancel_lock = threading.Lock()
_cancel_run_ids: set[int] = set()


class AnalysisCancelled(Exception):
    """用户在运行中取消。run_one 捕获后删除本次运行、不标记为失败。"""


def request_cancel(run_id: int) -> None:
    with _cancel_lock:
        _cancel_run_ids.add(int(run_id))


def clear_cancel(run_id: int) -> None:
    with _cancel_lock:
        _cancel_run_ids.discard(int(run_id))


def is_cancelled(run_id: int | None) -> bool:
    if run_id is None:
        return False
    with _cancel_lock:
        return int(run_id) in _cancel_run_ids


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


def _run_streamed(ta, ticker: str, trade_date: str, conn, run_id: int):
    """用 graph.stream() 逐节点跑，报告段一产出就落库；返回 (final_state, decision)。

    复用 TradingAgentsGraph 自身的初始化/收尾方法，只额外插入分段落库。
    开启 checkpoint 或内部 API 不兼容时，回退到稳健的一次性 propagate()。
    """
    def _persist_all(final_state: dict) -> None:
        for section, content in _section_map(final_state).items():
            db.set_report(conn, run_id, section, str(content))

    if ta.config.get("checkpoint_enabled"):
        final_state, decision = ta.propagate(ticker, trade_date)
        _persist_all(final_state)
        return final_state, decision

    try:
        ta.ticker = ticker
        ta._resolve_pending_entries(ticker)
        past_context = ta.memory_log.get_past_context(ticker)
        instrument_context = ta.resolve_instrument_context(ticker, "stock")
        try:  # 注入投资者真实持仓（有则拼上，无则原样）；失败不阻塞主流程
            from . import holdings
            note = holdings.position_note(ticker)
            if note:
                instrument_context = f"{instrument_context}\n\n{note}"
        except Exception:  # noqa: BLE001
            pass
        init_state = ta.propagator.create_initial_state(
            ticker, trade_date, asset_type="stock",
            past_context=past_context, instrument_context=instrument_context,
        )
        args = ta.propagator.get_graph_args()
    except Exception:  # noqa: BLE001  内部 API 变动时回退，保证仍能跑
        final_state, decision = ta.propagate(ticker, trade_date)
        _persist_all(final_state)
        return final_state, decision

    final_state: dict = {}
    written: dict[str, str] = {}
    for chunk in ta.graph.stream(init_state, **args):
        if is_cancelled(run_id):  # 用户取消：在节点边界停下，不再继续跑
            raise AnalysisCancelled()
        final_state.update(chunk)
        for section, content in _section_map(final_state).items():
            text = str(content)
            if written.get(section) != text:
                db.set_report(conn, run_id, section, text)
                written[section] = text

    ta.curr_state = final_state
    try:  # 记忆日志/反射失败不影响主结果
        ta._log_state(trade_date, final_state)
        ta.memory_log.store_decision(
            ticker=ticker, trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )
    except Exception:  # noqa: BLE001
        pass
    return final_state, ta.process_signal(final_state["final_trade_decision"])


def run_one(ticker: str, trade_date: str, settings: Settings | None = None,
            conn=None, analysts: list[str] | None = None,
            run_id: int | None = None) -> int:
    """跑一家公司并落库，返回 run_id。异常时标记 failed 并向上抛。

    analysts: 选择参与的分析师子集，None 时用 settings.analysts。
    run_id: 传入时复用该记录重跑（典型：失败重试），不新建决策记录。
    """
    settings = settings or load_settings()
    analysts = analysts or settings.analysts
    own_conn = conn is None
    conn = conn or db.connect()
    if own_conn:
        db.init_db(conn)

    market = market_of(ticker)
    if run_id is None:
        run_id = db.create_run(conn, ticker, market, trade_date, settings.deep_think_llm)
    else:
        db.reset_run(conn, run_id, settings.deep_think_llm)

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
        # 流式跑图：各段报告一产出就落库，前端可边跑边看
        final_state, decision = _run_streamed(ta, ticker, trade_date, conn, run_id)

        # 价格快照（趋势图用）
        try:
            prices.snapshot(conn, ticker)
        except Exception:  # noqa: BLE001  价格失败不阻塞主流程
            pass

        cost = stats.cost(settings.price_per_1m_input, settings.price_per_1m_output)
        db.finish_run(conn, run_id, decision=str(decision).strip(),
                      tokens=stats.tokens, cost_usd=round(cost, 4))

        # 决策参数结构化提取（止损/建仓区/仓位等，复盘用；quick 模型，约 $0.001）
        try:
            from . import extract
            final_text = final_state.get("final_trade_decision") if final_state else None
            params = extract.extract_decision_params(final_text)
            if params:
                db.upsert_decision_params(conn, run_id, params, model=settings.quick_think_llm)
        except Exception:  # noqa: BLE001  提取失败不影响主流程
            pass
        return run_id
    except AnalysisCancelled:
        # 用户取消：删除本次运行及其报告，不标记为失败（幂等，app 侧可能已删）
        try:
            db.delete_runs(conn, [run_id])
        finally:
            clear_cancel(run_id)
        return run_id
    except Exception as e:  # noqa: BLE001
        db.fail_run(conn, run_id, e)
        raise
