"""Token / cost accounting helpers for Stage 2⑥."""

from __future__ import annotations

from dataclasses import dataclass, field

# Rough USD per 1M tokens: (input, output). Unknown models → no dollar estimate.
_PRICE_PER_MTTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5.6-terra": (0.0, 0.0),  # placeholder; still counts tokens
    "deepseek-chat": (0.14, 0.28),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
}


@dataclass
class UsageTotals:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    turns: int = 0
    llm_ms: float = 0.0
    tool_ms: float = 0.0
    model: str = ""
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
    ) -> None:
        self.prompt_tokens += max(0, prompt_tokens)
        self.completion_tokens += max(0, completion_tokens)
        self.llm_calls += 1
        self.llm_ms += max(0.0, latency_ms)
        self.turns = max(self.turns, turn)
        self.per_turn.append(
            {
                "turn": turn,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": round(latency_ms, 1),
                "streamed": streamed,
            }
        )

    def add_tool(self, *, latency_ms: float = 0.0) -> None:
        self.tool_calls += 1
        self.tool_ms += max(0.0, latency_ms)

    def estimate_cost_usd(self) -> float | None:
        key = self.model.lower().strip()
        prices = _PRICE_PER_MTTOK.get(key)
        if prices is None:
            # prefix match (e.g. deepseek-v4-flash-x)
            for name, p in _PRICE_PER_MTTOK.items():
                if key.startswith(name) or name.startswith(key):
                    prices = p
                    break
        if prices is None:
            return None
        inp, out = prices
        return (self.prompt_tokens * inp + self.completion_tokens * out) / 1_000_000

    def summary_line(self) -> str:
        cost = self.estimate_cost_usd()
        cost_s = f" · est. ${cost:.4f}" if cost is not None else ""
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
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "turns": self.turns,
            "llm_ms": round(self.llm_ms, 1),
            "tool_ms": round(self.tool_ms, 1),
            "est_cost_usd": self.estimate_cost_usd(),
            "per_turn": list(self.per_turn),
        }
