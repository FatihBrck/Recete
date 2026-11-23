"""Per-field confidence from token logprobs.

Tokens are aligned back to character offsets of the emitted JSON text; each
JSON leaf value gets min (or product) of the probabilities of the tokens that
overlap its character span.
"""

import math
from typing import Literal

from reader.llm_client import TokenLogprob

_WS = " \t\n\r"
_DELIMS = _WS + ",}]"
_SYNC_WINDOW = 4


def leaf_spans(text: str) -> dict[str, tuple[int, int]]:
    """Map dotted path -> [start, end) of every leaf value in valid JSON.

    String spans exclude the quotes. Array elements use their index as the
    path segment.
    """
    spans: dict[str, tuple[int, int]] = {}
    n = len(text)

    def skip_ws(i: int) -> int:
        while i < n and text[i] in _WS:
            i += 1
        return i

    def scan_string(i: int) -> int:
        """i at opening quote; return index just past the closing quote."""
        i += 1
        while i < n:
            c = text[i]
            if c == "\\":
                i += 2
            elif c == '"':
                return i + 1
            else:
                i += 1
        return n

    def join(path: str, seg: str) -> str:
        return f"{path}.{seg}" if path else seg

    def value(i: int, path: str) -> int:
        i = skip_ws(i)
        if i >= n:
            return i
        c = text[i]
        if c == "{":
            i = skip_ws(i + 1)
            while i < n and text[i] != "}":
                kend = scan_string(i)
                key = _unescape(text[i + 1 : kend - 1])
                i = skip_ws(kend)
                i = skip_ws(i + 1)  # ':'
                i = value(i, join(path, key))
                i = skip_ws(i)
                if i < n and text[i] == ",":
                    i = skip_ws(i + 1)
            return i + 1
        if c == "[":
            i = skip_ws(i + 1)
            idx = 0
            while i < n and text[i] != "]":
                i = value(i, join(path, str(idx)))
                idx += 1
                i = skip_ws(i)
                if i < n and text[i] == ",":
                    i = skip_ws(i + 1)
            return i + 1
        if c == '"':
            end = scan_string(i)
            spans[path] = (i + 1, end - 1)
            return end
        j = i
        while j < n and text[j] not in _DELIMS:
            j += 1
        spans[path] = (i, j)
        return j

    value(0, "")
    return spans


def _unescape(s: str) -> str:
    if "\\" not in s:
        return s
    import json

    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s


class ConfidenceMap:
    def __init__(self, text: str, tokens: list[TokenLogprob]):
        self._spans = leaf_spans(text)
        self._tokens = tokens
        self._offsets: list[tuple[int, int]] = []
        self.available = self._align(text, tokens)

    def _align(self, text: str, tokens: list[TokenLogprob]) -> bool:
        if not tokens:
            return False
        offsets: list[tuple[int, int]] = [(0, 0)] * len(tokens)
        pos = 0
        pending: list[int] = []
        pending_start = 0
        for i, t in enumerate(tokens):
            tok = t.token
            if not pending and text.startswith(tok, pos):
                offsets[i] = (pos, pos + len(tok))
                pos += len(tok)
                continue
            f = text.find(tok, pos, pos + _SYNC_WINDOW + len(tok))
            if f >= 0:
                if not pending:
                    return False  # characters skipped, no token covers them
                for k in pending:
                    offsets[k] = (pending_start, f)
                pending = []
                offsets[i] = (f, f + len(tok))
                pos = f + len(tok)
            else:
                if not pending:
                    pending_start = pos
                pending.append(i)
        if pending:
            if len(text) - pending_start > _SYNC_WINDOW:
                return False
            for k in pending:
                offsets[k] = (pending_start, len(text))
        elif pos != len(text):
            return False
        self._offsets = offsets
        return True

    def get(self, path: str, mode: Literal["min", "product"] = "min") -> float:
        if not self.available:
            return 0.0
        span = self._spans.get(path)
        if span is None or span[1] <= span[0]:
            return 0.0
        s, e = span
        probs = []
        for t, (ts, te) in zip(self._tokens, self._offsets):
            if te > ts and ts < e and te > s:
                p = math.exp(t.logprob) if not math.isnan(t.logprob) else 0.0
                probs.append(min(1.0, max(0.0, p)))
        if not probs:
            return 0.0
        if mode == "product":
            return min(1.0, max(0.0, math.prod(probs)))
        return min(probs)
