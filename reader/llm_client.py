"""VLM client types (extended by later tasks)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenLogprob:
    token: str
    logprob: float
