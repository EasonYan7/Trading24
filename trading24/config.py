"""读取 config/*.yaml 与 .env，集中暴露配置。所有路径相对项目根，便于迁移。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "trading24.sqlite"


def _load_dotenv() -> None:
    """极简 .env 加载（不引第三方），只填充未设置的变量。"""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _read_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


@dataclass
class TickerCfg:
    symbol: str
    name: str = ""
    upper: float | None = None
    lower: float | None = None
    pct_move: float | None = None


@dataclass
class Settings:
    llm_provider: str = "deepseek"
    deep_think_llm: str = "deepseek-reasoner"
    quick_think_llm: str = "deepseek-chat"
    backend_url: str | None = None
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    output_language: str = "English"
    data_vendors: dict = field(default_factory=dict)
    cost_budget_usd_per_day: float = 5.0
    price_per_1m_input: float = 0.27
    price_per_1m_output: float = 1.10
    notify: dict = field(default_factory=dict)
    analysts: list = field(default_factory=lambda: ["market", "social", "news", "fundamentals"])

    def to_ta_config(self) -> dict:
        """转成 TradingAgents 的 config（覆盖 DEFAULT_CONFIG 对应键）。"""
        from tradingagents.default_config import DEFAULT_CONFIG

        cfg = DEFAULT_CONFIG.copy()
        cfg["llm_provider"] = self.llm_provider
        cfg["deep_think_llm"] = self.deep_think_llm
        cfg["quick_think_llm"] = self.quick_think_llm
        if self.backend_url:
            cfg["backend_url"] = self.backend_url
        cfg["max_debate_rounds"] = self.max_debate_rounds
        cfg["max_risk_discuss_rounds"] = self.max_risk_discuss_rounds
        cfg["output_language"] = self.output_language
        if self.data_vendors:
            cfg["data_vendors"] = {**cfg.get("data_vendors", {}), **self.data_vendors}
        return cfg


def load_settings() -> Settings:
    _load_dotenv()
    raw = _read_yaml("settings.yaml")
    return Settings(
        llm_provider=raw.get("llm_provider", "deepseek"),
        deep_think_llm=raw.get("deep_think_llm", "deepseek-reasoner"),
        quick_think_llm=raw.get("quick_think_llm", "deepseek-chat"),
        backend_url=raw.get("backend_url"),
        max_debate_rounds=raw.get("max_debate_rounds", 1),
        max_risk_discuss_rounds=raw.get("max_risk_discuss_rounds", raw.get("max_debate_rounds", 1)),
        output_language=raw.get("output_language", "English"),
        data_vendors=raw.get("data_vendors", {}),
        cost_budget_usd_per_day=raw.get("cost_budget_usd_per_day", 5.0),
        price_per_1m_input=raw.get("price_per_1m_input", 0.27),
        price_per_1m_output=raw.get("price_per_1m_output", 1.10),
        notify=raw.get("notify", {}),
        analysts=raw.get("analysts", ["market", "social", "news", "fundamentals"]),
    )


def save_settings(s: Settings) -> None:
    """写回 config/settings.yaml，保留注释无法保留但字段顺序稳定、中文正常。"""
    out = {
        "llm_provider": s.llm_provider,
        "deep_think_llm": s.deep_think_llm,
        "quick_think_llm": s.quick_think_llm,
        "backend_url": s.backend_url,
        "max_debate_rounds": s.max_debate_rounds,
        "max_risk_discuss_rounds": s.max_risk_discuss_rounds,
        "output_language": s.output_language,
        "data_vendors": s.data_vendors or {},
        "cost_budget_usd_per_day": s.cost_budget_usd_per_day,
        "price_per_1m_input": s.price_per_1m_input,
        "price_per_1m_output": s.price_per_1m_output,
        "notify": s.notify or {},
        "analysts": list(s.analysts or []),
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "settings.yaml").write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False))


def load_watchlist() -> tuple[str, list[TickerCfg]]:
    raw = _read_yaml("watchlist.yaml")
    run_time = raw.get("run_time", "08:30")
    tickers = [
        TickerCfg(
            symbol=t["symbol"],
            name=t.get("name", ""),
            upper=t.get("upper"),
            lower=t.get("lower"),
            pct_move=t.get("pct_move"),
        )
        for t in raw.get("tickers", [])
    ]
    return run_time, tickers


def save_watchlist(run_time: str, tickers: list[TickerCfg]) -> None:
    """写回 config/watchlist.yaml，保留结构，省略空阈值。"""
    out = {"run_time": run_time, "tickers": []}
    for t in tickers:
        d = {"symbol": t.symbol}
        if t.name:
            d["name"] = t.name
        for k in ("upper", "lower", "pct_move"):
            v = getattr(t, k)
            if v is not None:
                d[k] = v
        out["tickers"].append(d)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "watchlist.yaml").write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False))


def market_of(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".SS") or s.endswith(".SZ"):
        return "cn"
    if s.endswith(".HK"):
        return "hk"
    return "us"
