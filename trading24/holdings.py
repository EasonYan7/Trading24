"""投资者真实持仓 → 注入分析上下文。

数据在 ``data/holdings.yaml``（被 .gitignore 忽略，含真实仓位/盈亏，不进 Git）。
用户定期粘贴 Robinhood 持仓截图，由助手更新该文件。

runner 在跑某只票时调用 :func:`position_note`，把对应持仓拼成一段英文
``[Investor position]`` 追加到 ``instrument_context`` 后面。这样 trader、
多空研究员、风险辩论、研究/组合经理等 agent 都能针对"已持仓、成本 X、
当前盈亏 Y"给出加/减/持/清的建议，而不是把每只票当成全新入场判断。

同一经济仓位在 Robinhood 与 watchlist 里可能用不同代码（如小米 ADR ``XIACY``
对应港股 ``1810.HK``），用每条持仓的 ``aliases`` 对上。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import DATA_DIR

HOLDINGS_PATH = DATA_DIR / "holdings.yaml"


def _norm(sym: str | None) -> str:
    return (sym or "").strip().upper()


@dataclass
class Position:
    symbol: str
    name: str = ""
    shares: float = 0.0
    avg_cost: float | None = None
    price: float | None = None
    total_return: float | None = None
    equity: float | None = None
    aliases: list[str] = field(default_factory=list)

    def cost_basis(self) -> float | None:
        if self.avg_cost is not None and self.shares:
            return self.avg_cost * self.shares
        return None

    def return_pct(self) -> float | None:
        basis = self.cost_basis()
        if basis and self.total_return is not None:
            return self.total_return / basis * 100.0
        return None

    def keys(self) -> set[str]:
        return {_norm(self.symbol), *(_norm(a) for a in self.aliases)} - {""}


def load_positions(path: Path | None = None) -> list[Position]:
    """读取 holdings.yaml；文件不存在或为空时返回 []（不报错，退化为无持仓）。"""
    path = path or HOLDINGS_PATH
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    out: list[Position] = []
    for p in raw.get("positions", []) or []:
        try:
            out.append(
                Position(
                    symbol=str(p["symbol"]),
                    name=p.get("name", ""),
                    shares=float(p.get("shares") or 0),
                    avg_cost=p.get("avg_cost"),
                    price=p.get("price"),
                    total_return=p.get("total_return"),
                    equity=p.get("equity"),
                    aliases=[str(a) for a in (p.get("aliases") or [])],
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # 单条格式坏了跳过，不影响其它持仓
    return out


def find_position(ticker: str, positions: list[Position] | None = None) -> Position | None:
    """按 symbol 或 aliases 匹配持仓（大小写/空格不敏感）；无匹配返回 None。"""
    positions = positions if positions is not None else load_positions()
    key = _norm(ticker)
    if not key:
        return None
    for p in positions:
        if key in p.keys():
            return p
    return None


def _money(v: float | int | None) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def position_note(ticker: str, positions: list[Position] | None = None) -> str:
    """返回一段英文 ``[Investor position]``，拼到 instrument_context 后；无持仓返回空串。"""
    p = find_position(ticker, positions)
    if not p or not p.shares:
        return ""

    parts = [f"{p.shares:g} shares"]
    if p.avg_cost is not None:
        parts.append(f"average cost {_money(p.avg_cost)}")
    if p.price is not None:
        parts.append(f"latest price {_money(p.price)}")
    if p.equity is not None:
        parts.append(f"current market value {_money(p.equity)}")

    pnl = ""
    if p.total_return is not None:
        pct = p.return_pct()
        pct_str = f" ({pct:+.1f}%)" if pct is not None else ""
        kind = "unrealized gain" if p.total_return >= 0 else "unrealized loss"
        pnl = f", {kind} of {_money(abs(p.total_return))}{pct_str}"

    label = p.name or p.symbol
    return (
        f"[Investor position] The investor ALREADY HOLDS {label} ({p.symbol}): "
        f"{', '.join(parts)}{pnl}. "
        "This is an existing position, not a hypothetical fresh entry. Frame the "
        "recommendation as add / trim / hold / exit for a holder with this cost basis "
        "and P/L, and factor the average cost and unrealized P/L into the "
        "risk and position-sizing guidance."
    )
