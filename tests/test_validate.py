from decimal import Decimal

from reader.validate import FORMAT_PENALTY, RULES, check_cyl_axis, check_value


def test_quarter_step():
    assert check_value("sph", Decimal("-2.29")) == ("not_quarter_step",)
    assert check_value("sph", Decimal("-2.25")) == ()


def test_ranges():
    assert check_value("axis", 190) == ("out_of_range",)
    assert check_value("axis", 0) == () and check_value("axis", 180) == ()
    assert check_value("bc", Decimal("6.5")) == ("out_of_range",)
    assert check_value("add", Decimal("0.25")) == ("out_of_range",)
    assert check_value("dia", Decimal("14.2")) == ()
    assert set(check_value("sph", Decimal("31.10"))) == {"out_of_range", "not_quarter_step"}


def test_both_codes_in_order():
    assert check_value("sph", Decimal("31.10")) == ("out_of_range", "not_quarter_step")


def test_inclusive_bounds():
    assert check_value("sph", Decimal("-30.00")) == ()
    assert check_value("sph", Decimal("30.00")) == ()
    assert check_value("cyl", Decimal("-10.00")) == ()
    assert check_value("add", Decimal("0.50")) == ()
    assert check_value("add", Decimal("4.00")) == ()
    assert check_value("bc", Decimal("7.0")) == () and check_value("bc", Decimal("10.0")) == ()
    assert check_value("dia", Decimal("12.0")) == () and check_value("dia", Decimal("16.0")) == ()


def test_no_step_fields_never_quarter_step():
    assert check_value("bc", Decimal("8.6")) == ()
    assert check_value("dia", Decimal("14.2")) == ()
    assert check_value("axis", 91) == ()
    assert check_value("axis", -1) == ("out_of_range",)


def test_rules_and_penalty():
    assert FORMAT_PENALTY == 0.2
    assert RULES["axis"].integer is True and RULES["axis"].step is None
    assert RULES["sph"].step == Decimal("0.25")


def test_cyl_axis_pair():
    assert check_cyl_axis(Decimal("-0.75"), None) == "cyl_without_axis"
    assert check_cyl_axis(None, 90) == "axis_without_cyl"
    assert check_cyl_axis(Decimal("0.00"), None) is None
    assert check_cyl_axis(Decimal("-0.75"), 80) is None
    assert check_cyl_axis(None, None) is None
