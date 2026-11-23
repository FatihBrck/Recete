"""Format rules for prescription fields. They never reject; codes only lower confidence."""

from dataclasses import dataclass
from decimal import Decimal

FORMAT_PENALTY: float = 0.2

_QUARTER = Decimal("0.25")


@dataclass(frozen=True)
class Rule:
    min: Decimal
    max: Decimal
    step: Decimal | None
    integer: bool = False


RULES: dict[str, Rule] = {
    "sph": Rule(Decimal("-30.00"), Decimal("30.00"), _QUARTER),
    "cyl": Rule(Decimal("-10.00"), Decimal("10.00"), _QUARTER),
    "axis": Rule(Decimal("0"), Decimal("180"), None, integer=True),
    "add": Rule(Decimal("0.50"), Decimal("4.00"), _QUARTER),
    "bc": Rule(Decimal("7.0"), Decimal("10.0"), None),
    "dia": Rule(Decimal("12.0"), Decimal("16.0"), None),
}


def check_value(field: str, value: Decimal | int) -> tuple[str, ...]:
    rule = RULES[field]
    dec = Decimal(value)
    codes: list[str] = []
    if not rule.min <= dec <= rule.max:
        codes.append("out_of_range")
    if rule.step is not None and dec % rule.step != 0:
        codes.append("not_quarter_step")
    return tuple(codes)


def check_cyl_axis(cyl: Decimal | None, axis: int | None) -> str | None:
    if cyl is not None and cyl != 0 and axis is None:
        return "cyl_without_axis"
    if axis is not None and cyl is None:
        return "axis_without_cyl"
    return None
