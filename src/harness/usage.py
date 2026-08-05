"""Token / cost accounting — DeepSeek CNY rates with context-cache split.

Prices from https://api-docs.deepseek.com/zh-cn/quick_start/pricing
(元 / 百万 tokens). Peak-hour 2× (Beijing 09:00–12:00, 14:00–18:00) applied
when estimating, per the same docs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# CNY per 1M tokens: (cache_hit_input, cache_miss_input, output)
# Source: https://api-docs.deepseek.com/zh-cn/quick_start/pricing
_PRICE_CNY_PER_M: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.02, 1.0, 2.0),
    "deepseek-v4-flash-max": (0.02, 1.0, 2.0),
    "deepseek-v4-pro": (0.025, 3.0, 6.0),
    "deepseek-v4-pro-max": (0.025, 3.0, 6.0),
    # Legacy aliases (retired; keep rough mapping if still configured)
    "deepseek-chat": (0.02, 1.0, 2.0),
    "deepseek-reasoner": (0.025, 3.0, 6.0),
}

# Fixed UTC+8 — avoids Windows tzdata dependency for Beijing wall clock.
_BEIJING = timezone(timedelta(hours=8))


def _resolve_cny_rates(model: str) -> tuple[float, float, float] | None:
    key = model.lower().strip()
    if key in _PRICE_CNY_PER_M:
        return _PRICE_CNY_PER_M[key]
    for name, rates in _PRICE_CNY_PER_M.items():
        if key.startswith(name) or name.startswith(key):
            return rates
    return None


def peak_multiplier(when: datetime | None = None) -> float:
    """DeepSeek peak windows: Beijing 09:00–12:00 and 14:00–18:00 → 2×."""
    now = when or datetime.now(_BEIJING)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_BEIJING)
    else:
        now = now.astimezone(_BEIJING)
    minutes = now.hour * 60 + now.minute
    # [09:00, 12:00) and [14:00, 18:00)
    if 9 * 60 <= minutes < 12 * 60 or 14 * 60 <= minutes < 18 * 60:
        return 2.0
    return 1.0


@dataclass
class UsageTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    turns: int = 0
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    model: str = ""
    # Sum of (cost_at_1x * peak_mult) for each call, for accurate mixed peak/off-peak.
    cost_cny: float = 0.0
    per_turn: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add_llm(
        self,
        *,
        turn: int,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        streamed: bool = False,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        when: datetime | None = None,
    ) -> None:
        prompt_tokens = max(0, prompt_tokens)
        completion_tokens = max(0, completion_tokens)
        hit = max(0, cache_hit_tokens)
        miss = max(0, cache_miss_tokens)
        # If provider omitted cache split, bill all prompt as miss (upper bound).
        if prompt_tokens and hit == 0 and miss == 0:
            miss = prompt_tokens
        elif hit + miss < prompt_tokens:
            miss = max(miss, prompt_tokens - hit)

        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cache_hit_tokens += hit
        self.cache_miss_tokens += miss
        self.llm_calls += 1
        self.llm_ms += max(0.0, latency_ms)
        self.turns = max(self.turns, turn)

        call_cost = self._cost_cny_for(hit, miss, completion_tokens)
        mult = peak_multiplier(when)
        self.cost_cny += call_cost * mult

        self.per_turn.append(
            {
                "turn": turn,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_hit_tokens": hit,
                "cache_miss_tokens": miss,
                "latency_ms": round(latency_ms, 1),
                "streamed": streamed,
                "peak_mult": mult,
                "est_cost_cny": round(call_cost * mult, 6),
            }
        )

    def add_tool(self, *, latency_ms: float = 0.0) -> None:
        self.tool_calls += 1
        self.tool_ms += max(0.0, latency_ms)

    def _cost_cny_for(self, hit: int, miss: int, completion: int) -> float:
        rates = _resolve_cny_rates(self.model)
        if rates is None:
            return 0.0
        hit_p, miss_p, out_p = rates
        return (hit * hit_p + miss * miss_p + completion * out_p) / 1_000_000

    def estimate_cost_cny(self) -> float | None:
        """Session total in CNY; None if model has no CNY price table."""
        if _resolve_cny_rates(self.model) is None:
            return None
        return self.cost_cny

    def cache_hit_rate(self) -> float | None:
        if self.prompt_tokens <= 0:
            return None
        return self.cache_hit_tokens / self.prompt_tokens

    def summary_line(self) -> str:
        cost = self.estimate_cost_cny()
        if cost is not None:
            rate = self.cache_hit_rate()
            rate_s = f" · cache {rate:.0%}" if rate is not None else ""
            cost_s = f" · est. ¥{cost:.4f}{rate_s}"
        else:
            cost_s = ""
        return (
            f"tokens {self.prompt_tokens}+{self.completion_tokens}"
            f"={self.total_tokens} · llm {self.llm_calls} · tools {self.tool_calls}"
            f" · {self.llm_ms/1000:.1f}s llm / {self.tool_ms/1000:.1f}s tools"
            f"{cost_s}"
        )

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "turns": self.turns,
            "llm_ms": round(self.llm_ms, 1),
            "tool_ms": round(self.tool_ms, 1),
            "est_cost_cny": self.estimate_cost_cny(),
            "cache_hit_rate": self.cache_hit_rate(),
            "per_turn": list(self.per_turn),
        }
