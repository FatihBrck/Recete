from decimal import Decimal

import pytest

from reader.normalize import (
    normalize_axis,
    normalize_color,
    normalize_mm,
    normalize_power,
)


@pytest.mark.parametrize("raw,expected", [
    ("-2,5", "-2.50"), ("−3,00", "-3.00"), ("+1.25", "1.25"), ("-.75", "-0.75"),
    ("Plano", "0.00"), ("PL", "0.00"), ("-2.25 D", "-2.25"), (" - 0,75 ", "-0.75"), ("0", "0.00")])
def test_power_parses(raw, expected):
    r = normalize_power(raw, field="sph")
    assert r.value == Decimal(expected) and not r.penalize and r.warnings == ()


def test_power_quantizes_to_two_places():
    assert str(normalize_power("-2.5", field="sph").value) == "-2.50"


@pytest.mark.parametrize("raw", ["-2.25dpt", "-2.25 Dioptri", "-2.25DPT", "–2.25 d"])
def test_power_unit_suffixes_and_dashes(raw):
    assert normalize_power(raw, field="sph").value == Decimal("-2.25")


def test_unsigned_sph_assumes_plus_and_penalizes():
    r = normalize_power("2.25", field="sph")
    assert r.value == Decimal("2.25") and r.penalize
    assert r.warnings == ("sign_missing_assumed_plus",)


def test_unsigned_cyl_penalizes():
    r = normalize_power("0,75", field="cyl")
    assert r.value == Decimal("0.75") and r.penalize
    assert r.warnings == ("sign_missing_assumed_plus",)


def test_unsigned_add_not_penalized():
    r = normalize_power("2.00", field="add")
    assert r.value == Decimal("2.00") and not r.penalize and r.warnings == ()


def test_unsigned_zero_not_penalized():
    r = normalize_power("0.00", field="cyl")
    assert r.value == Decimal("0.00") and not r.penalize and r.warnings == ()


def test_combined_cyl_axis_notation_is_unparseable():
    r = normalize_power("-0,75 x 80", field="cyl")
    assert r.value is None and r.unparseable and r.warnings == ("unparseable",)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "-", "+.", "123", "1.234"])
def test_power_unparseable(raw):
    r = normalize_power(raw, field="sph")
    assert r.value is None and r.unparseable and r.warnings == ("unparseable",)


@pytest.mark.parametrize("raw,expected", [("80", 80), ("180°", 180), (" 5 ", 5), ("90º", 90)])
def test_axis_parses(raw, expected):
    assert normalize_axis(raw).value == expected


def test_axis_non_integer_not_rounded():
    r = normalize_axis("175.5")
    assert r.value is None and r.unparseable and r.warnings == ("not_integer",)


@pytest.mark.parametrize("raw", ["", "abc", "-5"])
def test_axis_unparseable(raw):
    r = normalize_axis(raw)
    assert r.value is None and r.unparseable and r.warnings == ("unparseable",)


def test_mm():
    assert normalize_mm("8,6").value == Decimal("8.6")
    assert normalize_mm("14.0mm").value == Decimal("14.0")
    assert normalize_mm("abc").unparseable


def test_mm_empty_unparseable():
    r = normalize_mm(" ")
    assert r.value is None and r.warnings == ("unparseable",)


def test_color():
    assert normalize_color("  Mavi ").value == "mavi"
