"""Raw prescription text -> normalized values (spec 5.6). Pure Python."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", ",": "."})
_SUFFIXES = ("dioptri", "dpt", "mm", "d", "°", "º")  # longest text unit first
_PLANO = {"plano", "plan", "pl"}
_POWER_RE = re.compile(r"^([+-])?([0-9]{0,2})(?:\.([0-9]{1,2}))?$")
_UNSIGNED_MM_RE = re.compile(r"^([0-9]{0,2})(?:\.([0-9]{1,2}))?$")
_AXIS_RE = re.compile(r"^([0-9]+)(?:\.([0-9]+))?$")
_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class Normalized:
    value: Decimal | int | str | None
    warnings: tuple[str, ...] = ()
    penalize: bool = False
    unparseable: bool = False


def _unparseable(code: str = "unparseable") -> Normalized:
    return Normalized(value=None, warnings=(code,), unparseable=True)


def _clean(raw: str) -> str:
    """Decimal point, ASCII minus, no whitespace, no trailing unit; lower-case."""
    text = "".join(raw.translate(_DASHES).split()).lower()
    for suffix in _SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _has_digit(int_part: str | None, frac_part: str | None) -> bool:
    return bool(int_part or frac_part)


def normalize_power(raw: str, *, field: Literal["sph", "cyl", "add"]) -> Normalized:
    text = _clean(raw)
    if text in _PLANO:
        return Normalized(value=Decimal("0.00"))
    m = _POWER_RE.match(text)
    if not m or not _has_digit(m.group(2), m.group(3)):
        return _unparseable()
    sign, int_part, frac_part = m.groups()
    value = Decimal(f"{int_part or '0'}.{frac_part or '0'}").quantize(_QUANT)
    if sign == "-":
        value = -value
    if sign is None and value != 0 and field in ("sph", "cyl"):
        return Normalized(
            value=value, warnings=("sign_missing_assumed_plus",), penalize=True
        )
    return Normalized(value=value)


def normalize_axis(raw: str) -> Normalized:
    m = _AXIS_RE.match(_clean(raw))
    if not m:
        return _unparseable()
    int_part, frac_part = m.groups()
    if frac_part and frac_part.strip("0"):
        return _unparseable("not_integer")
    return Normalized(value=int(int_part))


def normalize_mm(raw: str) -> Normalized:
    text = _clean(raw)
    m = _UNSIGNED_MM_RE.match(text)
    if not m or not _has_digit(m.group(1), m.group(2)):
        return _unparseable()
    return Normalized(value=Decimal(text))


def normalize_color(raw: str) -> Normalized:
    text = raw.strip().lower()
    if not text:
        return _unparseable()
    return Normalized(value=text)
