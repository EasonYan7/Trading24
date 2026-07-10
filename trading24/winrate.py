"""决策胜率复盘：把每个 AI 决策(runs)当作一个信号，用 price_snapshots 计算
未来 N 天的收益，判断方向押对没有，聚合成胜率。

口径（v2，绝对方向 + 止损）：
  增持 (Overweight/BUY)  → 押涨：收益 > 0 记胜
  减持 (Underweight/SELL) → 押跌：收益 < 0 记胜
  中性 (Hold/Neutral)    → 非方向性下注，不计入胜负（单列展示收益）
  破止损                  → 报告给出的硬止损被跌破（增持）/升破（减持），
                           无论之后走势如何，该窗判"负"（计划已失败）
  未到期 / 缺价格         → 待定
胜率 = 胜 /（胜 + 负），只统计"已到期或已破止损的方向性信号"。
一只股票的多次决策各算一个独立信号。
时间窗含 90 天：评级的真实视野多为季度级，7 天窗仅衡量短期择时。

命令行查看：uv run python -m trading24.winrate
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .db import database as db

HORIZONS = (7, 30, 90)  # 复盘时间窗（天）

DIR_LABEL = {"BUY": "增持", "SELL": "减持", "HOLD": "中性"}


def norm_decision(d: str | None) -> str | None:
    """把两套决策词汇归一到 BUY/SELL/HOLD（镜像 dashboard.charts._norm_decision，
    放这里避免 core 反向依赖 dashboard）。"""
    if not d:
        return None
    u = d.strip().upper()
    if any(w in u for w in ("OVERWEIGHT", "BUY", "ACCUMULATE", "LONG")):
        return "BUY"
    if any(w in u for w in ("UNDERWEIGHT", "SELL", "REDUCE", "TRIM", "SHORT")):
        return "SELL"
    if any(w in u for w in ("NEUTRAL", "HOLD", "MARKET-WEIGHT", "MARKETWEIGHT",
                            "EQUAL-WEIGHT", "EQUALWEIGHT")):
        return "HOLD"
    return None


# ── 价格查找（series 为按日期升序的 [(date, close, low, high), ...]）────
def _price_series(conn, ticker: str) -> list[tuple[str, float, float, float]]:
    rows = db.get_prices(conn, ticker, limit=500)  # 升序返回
    out = []
    for r in rows:
        if r.get("date") and r.get("close") is not None:
            c = r["close"]
            out.append((r["date"], c, r.get("low") or c, r.get("high") or c))
    return out


def _on_or_before(series, target: str):
    """最后一个 date <= target 的 (date, close)。"""
    hit = None
    for row in series:
        if row[0] <= target:
            hit = (row[0], row[1])
        else:
            break
    return hit


def _on_or_after(series, target: str):
    """第一个 date >= target 的 (date, close)。未来越界返回 None（=还没到期）。"""
    for row in series:
        if row[0] >= target:
            return (row[0], row[1])
    return None


# ── 止损提取（从 final/trader 报告文本）───────────────────────────
# 数字可能在"止损"前或后，单位 $ / 美元 / 港元 等；"预警线"之类不匹配。
_NUM = r'([\d,]+(?:\.\d+)?)'
_STOP_PATS = [
    # 关键词在前：硬止损设定在$120 / 止损放宽至$780 / 止损：840美元 / 硬止损不破20.0港元
    (re.compile(r'(硬性?止损|止损)[^。\n；;，,]{0,12}?[\$＄]\s*' + _NUM), 1),
    (re.compile(r'(硬性?止损|止损)[^。\n；;，,]{0,12}?' + _NUM + r'\s*(?:美元|美金|港元|港币|元)'), 1),
    # 数字在前：$120硬止损 / 190美元硬性止损线 / $750止损
    (re.compile(r'[\$＄]\s*' + _NUM + r'\s*(?:的)?(硬性?止损|止损)'), 0),
    (re.compile(_NUM + r'\s*(?:美元|美金|港元|港币|元)[^。\n；;，,]{0,8}?(硬性?止损|止损)'), 0),
]


def extract_stop(text: str | None) -> float | None:
    """从报告文本提取硬止损价。多处提及时：优先带"硬"的，再取出现最多的值。"""
    if not text:
        return None
    hard, soft = [], []
    for pat, num_group in _STOP_PATS:
        for m in pat.finditer(text):
            groups = m.groups()
            num = groups[num_group] if num_group < len(groups) else None
            kw = groups[1 - num_group] if len(groups) > 1 else ""
            if not num:
                continue
            try:
                v = float(num.replace(",", ""))
            except ValueError:
                continue
            (hard if (kw and "硬" in kw) else soft).append(v)
    pool = hard or soft
    if not pool:
        return None
    # 取众数（并列取先出现的）
    return max(pool, key=lambda v: (pool.count(v), -pool.index(v)))


def _stop_hit_date(series, entry_date: str, stop: float, direction: str) -> str | None:
    """入场日之后，首个触发止损的交易日（增持看 low 跌破 / 减持看 high 升破）。"""
    for d, _c, lo, hi in series:
        if d <= entry_date:
            continue
        if direction == "BUY" and lo <= stop:
            return d
        if direction == "SELL" and hi >= stop:
            return d
    return None


def _verdict(direction: str, ret: float) -> str:
    if direction == "HOLD":
        return "中性"
    if ret == 0:
        return "平"
    win = ret > 0 if direction == "BUY" else ret < 0
    return "胜" if win else "负"


@dataclass
class HorizonResult:
    days: int
    target_date: str
    exit_date: str | None = None
    exit_price: float | None = None
    ret: float | None = None       # (exit-entry)/entry
    verdict: str = "待定"          # 胜/负/平/中性/待定


@dataclass
class Signal:
    ticker: str
    trade_date: str
    decision_raw: str
    direction: str                 # BUY/SELL/HOLD
    dir_label: str                 # 增持/减持/中性
    entry_date: str | None
    entry_price: float | None
    last_price: float | None       # 最新收盘价
    live_ret: float | None         # 入场 → 最新收盘（"至今"，永远可算）
    horizons: dict[int, HorizonResult] = field(default_factory=dict)
    note: str = ""
    run_id: int | None = None
    tracked: bool = True           # 是否纳入胜率统计（复盘页可切换）
    stop_price: float | None = None    # 报告给出的硬止损（提取自 final/trader 文本）
    stop_hit_date: str | None = None   # 首次触发止损的日期（未触发为 None）
    plan_entry_low: float | None = None    # 报告建议的建/减仓区间（decision_params）
    plan_entry_high: float | None = None


def evaluate(conn, horizons=HORIZONS) -> tuple[list[Signal], dict]:
    """遍历所有成功决策，算出每个信号在各时间窗的结果 + 汇总胜率。"""
    runs = db.get_runs(conn, limit=1000)
    series_cache: dict[str, list] = {}
    signals: list[Signal] = []

    # 按 ticker、trade_date 升序，让同股多决策节点顺序清晰
    for r in sorted(runs, key=lambda x: (x.ticker, x.trade_date)):
        if r.status != "success":
            continue
        direction = norm_decision(r.decision)
        if direction is None:
            continue

        series = series_cache.setdefault(r.ticker, _price_series(conn, r.ticker))
        entry = _on_or_before(series, r.trade_date)
        entry_date, entry_price = entry if entry else (None, None)

        latest = series[-1] if series else None
        live_ret = None
        if entry_price and latest and latest[0] > (entry_date or ""):
            live_ret = (latest[1] - entry_price) / entry_price

        sig = Signal(
            ticker=r.ticker, trade_date=r.trade_date, decision_raw=r.decision or "",
            direction=direction, dir_label=DIR_LABEL[direction],
            entry_date=entry_date, entry_price=entry_price,
            last_price=latest[1] if latest else None, live_ret=live_ret,
            note="" if entry_price else "缺入场价（价格数据未覆盖决策日）",
            run_id=r.id, tracked=bool(getattr(r, "win_tracked", 1)),
        )

        for h in horizons:
            target = (date.fromisoformat(r.trade_date) + timedelta(days=h)).isoformat()
            hr = HorizonResult(days=h, target_date=target)
            if entry_price is not None:
                exitp = _on_or_after(series, target)
                if exitp is not None:                    # 已到期
                    hr.exit_date, hr.exit_price = exitp
                    hr.ret = (hr.exit_price - entry_price) / entry_price
                    hr.verdict = _verdict(direction, hr.ret)
                # exitp is None → 保持"待定"（未来还没到）
            sig.horizons[h] = hr

        dp = db.get_decision_params(conn, r.id) or {}
        sig.plan_entry_low = dp.get("entry_low")
        sig.plan_entry_high = dp.get("entry_high")

        # 止损检查：优先用 decision_params 表的结构化提取值（LLM 固定字段），
        # 缺失时退回正则从 final/trader 文本里找。
        # 方向性信号在窗口内触发止损 → 该窗判"破止损"（计划失败，计负），
        # 未到期的窗口若已破止损也立即判定（不用再等）。
        if direction in ("BUY", "SELL") and entry_price is not None:
            stop = dp.get("stop_loss")
            if stop is None:
                texts = {rep.section: rep.content for rep in db.get_reports(conn, r.id)}
                stop = extract_stop(texts.get("final") or texts.get("trader"))
            # 合理性校验：增持止损须低于入场价、减持须高于入场价（且不离谱）
            if stop is not None and (
                (direction == "BUY" and 0.2 * entry_price < stop < entry_price)
                or (direction == "SELL" and entry_price < stop < 5 * entry_price)
            ):
                sig.stop_price = stop
                hit = _stop_hit_date(series, entry_date, stop, direction)
                if hit:
                    sig.stop_hit_date = hit
                    for h in horizons:
                        hr = sig.horizons[h]
                        if hr.exit_date is None or hit <= hr.exit_date:
                            hr.verdict = "破止损"

        signals.append(sig)

    return signals, _summarize(signals, horizons)


def _summarize(signals: list[Signal], horizons) -> dict:
    """按时间窗汇总；再按方向(增持/减持)细分。"""
    def blank():
        return {"wins": 0, "losses": 0, "flats": 0, "pending": 0,
                "win_rate": None, "avg_dir_ret": None, "n_decided": 0, "_rets": []}

    out = {h: {"all": blank(), "BUY": blank(), "SELL": blank(), "neutral": 0}
           for h in horizons}

    for h in horizons:
        for s in signals:
            if not s.tracked:          # 用户取消跟踪的决策不计入统计
                continue
            hr = s.horizons[h]
            if s.direction == "HOLD":
                out[h]["neutral"] += 1
                continue
            buckets = (out[h]["all"], out[h][s.direction])
            if hr.verdict == "待定":
                for b in buckets:
                    b["pending"] += 1
                continue
            key = {"胜": "wins", "负": "losses", "破止损": "losses"}.get(hr.verdict, "flats")
            sign = 1 if s.direction == "BUY" else -1
            for b in buckets:
                b[key] += 1
                if hr.ret is not None:
                    b["_rets"].append(sign * hr.ret)
        for grp in ("all", "BUY", "SELL"):
            b = out[h][grp]
            decided = b["wins"] + b["losses"]
            b["n_decided"] = decided
            b["win_rate"] = (b["wins"] / decided) if decided else None
            b["avg_dir_ret"] = (sum(b["_rets"]) / len(b["_rets"])) if b["_rets"] else None
            b.pop("_rets")
    return out


# ── 命令行报告 ────────────────────────────────────────────────
def _pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def _print_report() -> None:
    conn = db.connect()
    db.init_db(conn)
    signals, summary = evaluate(conn)

    print("═" * 74)
    print("决策胜率复盘（口径：绝对方向；增持押涨、减持押跌；中性不计胜负）")
    print("═" * 74)

    for h in HORIZONS:
        a = summary[h]["all"]
        wr = "—" if a["win_rate"] is None else f"{a['win_rate'] * 100:.0f}%"
        print(f"\n【{h} 天窗】总胜率 {wr}  "
              f"(胜 {a['wins']} / 负 {a['losses']} / 平 {a['flats']} / 待定 {a['pending']})"
              f"  平均方向收益 {_pct(a['avg_dir_ret'])}")
        for grp, lab in (("BUY", "增持"), ("SELL", "减持")):
            b = summary[h][grp]
            if b["wins"] + b["losses"] + b["pending"] == 0:
                continue
            wr2 = "—" if b["win_rate"] is None else f"{b['win_rate'] * 100:.0f}%"
            print(f"    · {lab}: 胜率 {wr2}  (胜 {b['wins']}/负 {b['losses']}/待定 {b['pending']})"
                  f"  方向收益 {_pct(b['avg_dir_ret'])}")

    print("\n" + "─" * 96)
    print("逐信号明细：")
    head = f"{'标的':<10}{'决策日':<12}{'决策':<7}{'入场价':>9}{'止损':>8}{'至今':>9}"
    for h in HORIZONS:
        head += f"{str(h)+'天':>9}{'判':>5}"
    print(head)
    print("─" * 96)
    for s in signals:
        ep = "—" if s.entry_price is None else f"{s.entry_price:.2f}"
        sp = "—" if s.stop_price is None else f"{s.stop_price:.0f}"
        line = f"{s.ticker:<10}{s.trade_date:<12}{s.dir_label:<7}{ep:>9}{sp:>8}{_pct(s.live_ret):>9}"
        for h in HORIZONS:
            hr = s.horizons[h]
            line += f"{_pct(hr.ret):>9}{hr.verdict:>5}"
        print(line)
    print("─" * 96)
    print("注：'至今'=入场至最新收盘；各窗到期才有判定，未到期=待定；"
          "'破止损'=报告硬止损被触发，该窗计负（含未到期窗）。")


if __name__ == "__main__":
    _print_report()
