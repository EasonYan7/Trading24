"""决策参数结构化提取：把 final 报告文本 → 固定 JSON 字段，落 decision_params 表。

为什么不改 TradingAgents 的 PortfolioDecision schema：项目铁律是不改框架源码。
这里用便宜的 quick_think 模型（deepseek-v4-flash）做一次独立提取调用，
字段完全按复盘（winrate）需求定义，老决策也可回填。单次成本约 $0.001。
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .config import load_settings

# 提取字段与含义（description 同时就是给模型的输出说明）
_FIELDS = {
    "stop_loss": "硬止损价（数字）。只取明确标注为硬止损/止损的最终采纳值；预警线、观察位、辩论中被否掉的方案不算。没有则 null。",
    "entry_low": "建仓区间下限（数字）。报告建议的首笔买入/加仓价格区间；没有则 null。",
    "entry_high": "建仓区间上限（数字）。没有则 null。",
    "initial_position_pct": "初始仓位占组合百分比（数字，如 1.5-2% 取中值 1.75）。没有则 null。",
    "max_position_pct": "仓位上限占组合百分比（数字）。没有则 null。",
    "price_target": "目标价（数字）。没有则 null。",
    "time_horizon": "时间视野原文（字符串，如 '6-12个月'）。没有则 null。",
    "time_horizon_days": "时间视野换算为天数（整数，区间取中值，如 6-12个月→270）。没有则 null。",
}

_SYSTEM = (
    "你是金融报告的结构化提取器。从用户给出的最终投资决策报告中提取字段，"
    "严格输出 JSON 对象（无其他文字），键与含义如下，找不到的字段填 null，"
    "绝不编造：\n"
    + "\n".join(f"- {k}: {v}" for k, v in _FIELDS.items())
)


# 新版 TradingAgents（本地 fork df045a3+）的 final 报告带固定标签，
# 可零成本确定性解析；解析不到再退回 LLM 提取（老报告/自由文本）。
_NUM_RE = r'(-?[\d,]+(?:\.\d+)?)'
_LABELS = {
    "stop_loss": re.compile(r'\*\*Stop Loss\*\*:\s*' + _NUM_RE),
    "price_target": re.compile(r'\*\*Price Target\*\*:\s*' + _NUM_RE),
    "time_horizon": re.compile(r'\*\*Time Horizon\*\*:\s*([^\n*]+)'),
    "_entry": re.compile(r'\*\*Entry Zone\*\*:\s*' + _NUM_RE + r'(?:\s*[-–~]\s*' + _NUM_RE + r')?'),
    "_position": re.compile(
        r'\*\*Position\*\*:\s*(?:initial\s*' + _NUM_RE + r'%)?\s*/?\s*(?:max\s*' + _NUM_RE + r'%)?'),
}


def _f(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_rendered_params(text: str) -> dict | None:
    """从固定标签的 final 报告确定性解析。至少解析到止损或建仓区才算成功。"""
    if not text:
        return None
    out = {k: None for k in _FIELDS}
    m = _LABELS["stop_loss"].search(text)
    if m:
        out["stop_loss"] = _f(m.group(1))
    m = _LABELS["_entry"].search(text)
    if m:
        out["entry_low"] = _f(m.group(1))
        out["entry_high"] = _f(m.group(2)) if m.group(2) else out["entry_low"]
    m = _LABELS["_position"].search(text)
    if m:
        out["initial_position_pct"] = _f(m.group(1))
        out["max_position_pct"] = _f(m.group(2))
    m = _LABELS["price_target"].search(text)
    if m:
        out["price_target"] = _f(m.group(1))
    m = _LABELS["time_horizon"].search(text)
    if m:
        th = m.group(1).strip()
        out["time_horizon"] = th[:60]
        out["time_horizon_days"] = _horizon_days(th)
    if out["stop_loss"] is None and out["entry_low"] is None:
        return None
    return out


def _horizon_days(text: str) -> int | None:
    """'6-12个月' / '3-6 months' / '90天' → 天数（区间取中值）。"""
    m = re.search(r'(\d+)\s*(?:[-–~到至]\s*(\d+))?\s*(个月|月|months?|天|days?|周|weeks?|年|years?)',
                  text, re.I)
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    unit = m.group(3).lower()
    factor = 30 if ("月" in unit or "month" in unit) else \
        7 if ("周" in unit or "week" in unit) else \
        365 if ("年" in unit or "year" in unit) else 1
    return round((lo + hi) / 2 * factor)


def extract_decision_params(final_text: str, timeout: float = 60.0) -> dict | None:
    """先零成本确定性解析（新版固定标签）；不行再调 quick_think 模型提取。
    失败（网络/解析/无 key）返回 None，不抛异常。"""
    if not final_text or not final_text.strip():
        return None
    parsed = parse_rendered_params(final_text)
    if parsed is not None:
        return parsed
    settings = load_settings()
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = (settings.backend_url or "https://api.deepseek.com/v1").rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": settings.quick_think_llm,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": final_text[:12000]},
                ],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(raw)
    except Exception:  # noqa: BLE001  提取失败不影响主流程
        return None
    return _sanitize(data)


def _sanitize(data: dict) -> dict:
    """只保留已知字段并做类型清洗，脏值置 null。"""
    out = {}
    for k in _FIELDS:
        v = data.get(k)
        if k == "time_horizon":
            out[k] = str(v)[:60] if v else None
        elif k == "time_horizon_days":
            try:
                out[k] = int(v) if v is not None else None
            except (TypeError, ValueError):
                out[k] = None
        else:
            try:
                out[k] = float(v) if v is not None else None
            except (TypeError, ValueError):
                out[k] = None
    return out
