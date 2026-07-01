"""LLM token 统计回调（传给 TradingAgentsGraph 的 LLM）。"""
from __future__ import annotations

import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


class StatsCallbackHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.tokens_in = 0
        self.tokens_out = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            gen = response.generations[0][0]
        except (IndexError, TypeError):
            return
        msg = getattr(gen, "message", None)
        usage = getattr(msg, "usage_metadata", None) if isinstance(msg, AIMessage) else None
        if usage:
            with self._lock:
                self.tokens_in += usage.get("input_tokens", 0)
                self.tokens_out += usage.get("output_tokens", 0)

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def cost(self, price_in_per_1m: float, price_out_per_1m: float) -> float:
        return self.tokens_in / 1e6 * price_in_per_1m + self.tokens_out / 1e6 * price_out_per_1m
