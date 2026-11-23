"""Test doubles for the VLM client."""

import math

from reader.llm_client import LLMResult, TokenLogprob


def char_tokens(
    text: str, p: float = 0.99, overrides: dict[str, float] | None = None
) -> list[TokenLogprob]:
    """One token per character; `overrides` maps a substring (first occurrence)
    to the probability of each of its characters."""
    probs = [p] * len(text)
    for sub, prob in (overrides or {}).items():
        i = text.find(sub)
        if i < 0 or not sub:
            raise ValueError(f"override substring not found in text: {sub!r}")
        for j in range(i, i + len(sub)):
            probs[j] = prob
    return [TokenLogprob(c, math.log(q)) for c, q in zip(text, probs)]


class FakeVLMClient:
    def __init__(self, results: list[LLMResult | Exception], healthy: bool = True):
        self._results = list(results)
        self._healthy = healthy
        self.calls: list[dict] = []

    async def complete(
        self,
        *,
        system: str,
        user: str,
        image_jpeg_b64: str,
        schema_name: str,
        json_schema: dict,
    ) -> LLMResult:
        self.calls.append(
            dict(
                system=system,
                user=user,
                image_jpeg_b64=image_jpeg_b64,
                schema_name=schema_name,
                json_schema=json_schema,
            )
        )
        if not self._results:
            raise AssertionError(
                f"FakeVLMClient: no more queued results (call #{len(self.calls)})"
            )
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def health(self) -> bool:
        return self._healthy
