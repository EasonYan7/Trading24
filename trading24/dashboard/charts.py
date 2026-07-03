"""趋势图：价格 + MA + 决策标记（plotly，交互式）。"""
from __future__ import annotations

import plotly.graph_objects as go

DEFAULT_THEME = {
    "primary": "#10b981",
    "danger": "#ef4444",
    "muted": "#6b7280",
    "border": "#e5e7eb",
    "subtle": "#9ca3af",
}
DECISION_LABEL = {"BUY": "增持", "SELL": "减持", "HOLD": "中性"}
DECISION_STYLE_KEYS = {
    "BUY": ("triangle-up", "primary"),
    "SELL": ("triangle-down", "danger"),
    "HOLD": ("circle", "muted"),
}


def _rgba(hex_color: str, alpha: float) -> str:
    color = hex_color.lstrip("#")
    if len(color) != 6:
        return f"rgba(0,200,5,{alpha})"
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _norm_decision(d: str | None) -> str | None:
    """兼容两套词汇：买卖(BUY/SELL/HOLD) 与机构评级(增持/中性/减持)。"""
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


def _decision_points(prices: list[dict], runs: list | None) -> dict[str, list[tuple[str, float]]]:
    dates = [p.get("date") for p in prices if p.get("date") and p.get("close") is not None]
    close_by_date = {p["date"]: p["close"] for p in prices if p.get("date") and p.get("close") is not None}
    grouped: dict[str, list[tuple[str, float]]] = {}
    seen: set[tuple[str, str]] = set()
    for r in runs or []:
        if getattr(r, "status", "") != "success":
            continue
        dec = _norm_decision(getattr(r, "decision", None))
        trade_date = getattr(r, "trade_date", None)
        if not dec or not trade_date:
            continue
        point_date = trade_date if trade_date in close_by_date else None
        if point_date is None:
            prior_dates = [d for d in dates if d <= trade_date]
            point_date = prior_dates[-1] if prior_dates else None
        if not point_date or (dec, point_date) in seen:
            continue
        seen.add((dec, point_date))
        grouped.setdefault(dec, []).append((point_date, close_by_date[point_date]))
    return grouped


def _decision_style(theme: dict) -> dict[str, dict]:
    return {
        dec: dict(symbol=symbol, color=theme[color_key])
        for dec, (symbol, color_key) in DECISION_STYLE_KEYS.items()
    }


def build_sparkline(prices: list[dict], theme: dict | None = None, runs: list | None = None) -> go.Figure:
    """卡片用的迷你走势图：主色线 + 低透明填充 + 决策点位，无坐标轴。"""
    t = {**DEFAULT_THEME, **(theme or {})}
    fig = go.Figure()
    if prices:
        dates = [p.get("date") for p in prices]
        close = [p["close"] for p in prices]
        fig.add_trace(go.Scatter(x=dates, y=close, mode="lines",
                                 line=dict(color=t["primary"], width=1.6),
                                 fill="tozeroy", fillcolor=_rgba(t["primary"], 0.10)))
        for dec, points in _decision_points(prices, runs).items():
            style = _decision_style(t)[dec]
            fig.add_trace(go.Scatter(
                x=[p[0] for p in points],
                y=[p[1] for p in points],
                name=DECISION_LABEL.get(dec, dec),
                mode="markers",
                marker=dict(
                    symbol=style["symbol"],
                    color=style["color"],
                    size=9,
                    line=dict(width=1, color=t["border"]),
                ),
                hovertemplate=f"{DECISION_LABEL.get(dec, dec)}<br>%{{x}}<br>%{{y:.2f}}<extra></extra>",
            ))
        vals = [v for v in close if v is not None]
        fig.update_yaxes(range=[min(vals) * 0.98, max(vals) * 1.02])
    fig.update_layout(
        height=72, margin=dict(l=0, r=0, t=4, b=0),
        showlegend=False, plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig


def build_trend_figure(prices: list[dict], runs: list, theme: dict | None = None) -> go.Figure:
    """prices: price_snapshots 行；runs: Run 列表（取 success 的决策打点）。"""
    t = {**DEFAULT_THEME, **(theme or {})}
    decision_style = _decision_style(t)
    fig = go.Figure()
    if not prices:
        fig.add_annotation(text="暂无价格快照，运行一次分析后生成",
                           showarrow=False, font=dict(size=14, color=t["muted"]))
        fig.update_layout(height=360, margin=dict(l=40, r=16, t=20, b=30))
        return fig

    dates = [p["date"] for p in prices]
    close = [p["close"] for p in prices]
    ma20 = [p.get("ma20") for p in prices]
    close_by_date = {p["date"]: p["close"] for p in prices}
    vals = [v for v in close + ma20 if v is not None]
    yrange = [min(vals) * 0.97, max(vals) * 1.03] if vals else None

    fig.add_trace(go.Scatter(x=dates, y=close, name="收盘价", mode="lines",
                             line=dict(color=t["muted"], width=2.2),
                             fill="tozeroy", fillcolor=_rgba(t["muted"], 0.08)))
    if any(v is not None for v in ma20):
        fig.add_trace(go.Scatter(x=dates, y=ma20, name="MA20", mode="lines",
                                 line=dict(color=t["subtle"], width=1.4, dash="dash")))

    # 决策标记：按 trade_date 落在价格曲线上（无当日价格快照时回退到最近的前一交易日）
    for dec, points in _decision_points(prices, runs).items():
        style = decision_style[dec]
        fig.add_trace(go.Scatter(
            x=[d for d, _ in points], y=[y for _, y in points],
            name=DECISION_LABEL.get(dec, dec), mode="markers",
            marker=dict(symbol=style["symbol"], color=style["color"], size=13,
                        line=dict(width=1, color=t["border"])),
        ))

    fig.update_layout(
        height=380, margin=dict(l=40, r=16, t=20, b=30),
        legend=dict(orientation="h", y=1.02, x=0,
                    font=dict(family="Geist Mono, monospace", size=11, color=t["muted"])),
        hovermode="x unified",
        font=dict(family="Geist Mono, monospace", size=10, color=t["muted"]),
        xaxis=dict(showgrid=False, linecolor=t["border"]),
        yaxis=dict(gridcolor=_rgba(t["border"].replace("#", ""), 0.9) if t["border"].startswith("#") else t["border"],
                   range=yrange),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(rangeslider_visible=True, rangeslider_thickness=0.06)
    return fig
