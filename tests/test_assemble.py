import pytest

from reader.assemble import assemble_glasses, assemble_lens
from reader.schemas import (
    GlassesEyeRaw,
    GlassesExtraction,
    LensEyeRaw,
    LensExtraction,
    RawField,
    RawNear,
)


def rf(raw, hw=True):
    return RawField(raw=raw, handwritten=hw)


def lens_eye(**kw):
    base = dict(sph=None, cyl=None, axis=None, add=None, bc=None, dia=None, color=None)
    base.update(kw)
    return LensEyeRaw(**base)


def glasses_eye(**kw):
    base = dict(sph=None, cyl=None, axis=None, add=None, bc=None, dia=None, near=None)
    base.update(kw)
    return GlassesEyeRaw(**base)


def lens(right=None, left=None, bc=None, dia=None, brand=None):
    return LensExtraction(right=right, left=left, shared_bc=bc, shared_dia=dia, brand=brand)


def fake_conf(table, default=0.9):
    calls = []

    def conf(path):
        calls.append(path)
        return table.get(path, default)

    conf.calls = calls
    return conf


def test_golden_a1_handwritten():
    ex = LensExtraction(
        right=lens_eye(sph=rf("-3,00"), cyl=rf("-0,75"), axis=rf("80")),
        left=lens_eye(sph=rf("-3,00"), cyl=rf("-0,75"), axis=rf("100")),
        shared_bc=rf("8,6"),
        shared_dia=rf("14,5"),
        brand="Acuvue Oasys for Astigmatism",
    )
    a = assemble_lens(ex, lambda p: 0.9)
    assert a.right.sph.value == -3.0 and a.right.cyl.value == -0.75 and a.right.axis.value == 80
    assert a.left.axis.value == 100 and isinstance(a.left.axis.value, int)
    assert a.right.bc.value == 8.6 and a.right.bc.shared
    assert a.left.dia.value == 14.5 and a.left.dia.shared
    assert a.brand.raw == "Acuvue Oasys for Astigmatism" and a.warnings == []


def test_shared_bc_uses_shared_confidence_path():
    conf = fake_conf({"shared_bc.raw": 0.7})
    a = assemble_lens(lens(right=lens_eye(sph=rf("-1.00")), bc=rf("8.6")), conf)
    assert a.right.bc.confidence == 0.7
    assert "shared_bc.raw" in conf.calls


def test_per_eye_bc_not_shared():
    a = assemble_lens(lens(right=lens_eye(bc=rf("8.4"))), fake_conf({"right.bc.raw": 0.8}))
    assert a.right.bc.shared is False and a.right.bc.confidence == 0.8


def test_per_eye_and_shared_conflict():
    a = assemble_lens(lens(right=lens_eye(bc=rf("8.4")), bc=rf("8.6")), lambda p: 0.9)
    assert "right.bc: shared_conflict" in a.warnings
    assert a.right.bc.value == 8.4 and a.right.bc.shared is False


def test_shared_not_applied_to_missing_eye():
    a = assemble_lens(lens(right=lens_eye(sph=rf("-1.00")), bc=rf("8.6")), lambda p: 0.9)
    assert a.left is None


def test_eye_with_only_shared_bc_is_kept():
    a = assemble_lens(lens(right=lens_eye(), bc=rf("8.6")), lambda p: 0.9)
    assert a.right is not None and a.right.bc.value == 8.6


def test_format_violation_penalizes_confidence():
    a = assemble_lens(lens(left=lens_eye(sph=rf("-2.29"))), lambda p: 0.9)
    assert a.left.sph.confidence == pytest.approx(0.18)
    assert "left.sph: not_quarter_step" in a.warnings


def test_penalty_applied_once():
    a = assemble_lens(lens(right=lens_eye(sph=rf("2.29"))), lambda p: 0.9)
    assert a.right.sph.confidence == pytest.approx(0.18)
    assert a.warnings == ["right.sph: sign_missing_assumed_plus", "right.sph: not_quarter_step"]


def test_unparseable_field_value_none_confidence_zero():
    a = assemble_lens(lens(right=lens_eye(sph=rf("abc"))), lambda p: 0.9)
    assert a.right.sph.value is None and a.right.sph.confidence == 0.0
    assert a.right.sph.raw == "abc"
    assert a.warnings == ["right.sph: unparseable"]


def test_empty_raw_is_null():
    a = assemble_lens(lens(right=lens_eye(sph=rf("  "), cyl=rf("-1.00"), axis=rf("10"))), lambda p: 0.9)
    assert a.right.sph is None


def test_raw_kept_unstripped():
    a = assemble_lens(lens(right=lens_eye(sph=rf(" -1.00 "))), lambda p: 0.9)
    assert a.right.sph.raw == " -1.00 " and a.right.sph.value == -1.0


def test_eye_with_no_fields_is_none():
    a = assemble_lens(lens(right=lens_eye(sph=rf("")), left=lens_eye()), lambda p: 0.9)
    assert a.right is None and a.left is None


def test_cyl_without_axis_penalizes_cyl():
    a = assemble_lens(lens(right=lens_eye(cyl=rf("-1.25"))), lambda p: 0.9)
    assert "right.cyl: cyl_without_axis" in a.warnings
    assert a.right.cyl.confidence == pytest.approx(0.18)


def test_axis_without_cyl_penalizes_axis():
    a = assemble_lens(lens(right=lens_eye(axis=rf("90"))), lambda p: 0.9)
    assert "right.axis: axis_without_cyl" in a.warnings
    assert a.right.axis.confidence == pytest.approx(0.18)


def test_pair_penalty_not_stacked_with_format():
    a = assemble_lens(lens(right=lens_eye(cyl=rf("-1.30"))), lambda p: 0.9)
    assert a.right.cyl.confidence == pytest.approx(0.18)
    assert a.warnings == ["right.cyl: not_quarter_step", "right.cyl: cyl_without_axis"]


def test_blank_brand_is_none_and_brand_stripped():
    assert assemble_lens(lens(right=lens_eye(sph=rf("1.00")), brand="  "), lambda p: 0.9).brand is None
    a = assemble_lens(lens(right=lens_eye(sph=rf("1.00")), brand=" X "), fake_conf({"brand": 0.5}))
    assert a.brand.raw == "X" and a.brand.confidence == 0.5


def test_color_field():
    a = assemble_lens(lens(right=lens_eye(color=rf(" Blue "))), lambda p: 0.9)
    assert a.right.color.value == "blue" and a.right.color.raw == " Blue "


def test_glasses_near_and_shared_bc():
    ex = GlassesExtraction(
        right=glasses_eye(
            sph=rf("+1.00"),
            near=RawNear(sph=rf("+3.00"), cyl=rf("-0.50"), axis=None),
        ),
        left=glasses_eye(sph=rf("+1.00")),
        shared_bc=rf("8.6"),
        shared_dia=None,
    )
    conf = fake_conf({})
    a = assemble_glasses(ex, conf)
    assert a.right.near.sph.value == 3.0
    assert a.right.bc.shared and a.left.bc.shared
    assert "right.near.sph.raw" in conf.calls
    assert "right.near.cyl: cyl_without_axis" in a.warnings
    assert a.right.near.cyl.confidence == pytest.approx(0.18)
    assert a.left.near is None and a.brand is None


def test_empty_near_is_none():
    ex = GlassesExtraction(
        right=glasses_eye(sph=rf("1.00"), near=RawNear(sph=None, cyl=rf(" "), axis=None)),
        left=None, shared_bc=None, shared_dia=None,
    )
    assert assemble_glasses(ex, lambda p: 0.9).right.near is None


def test_warning_order_right_before_left_near_last():
    ex = GlassesExtraction(
        right=glasses_eye(
            near=RawNear(sph=rf("x"), cyl=None, axis=None), sph=rf("y"), bc=rf("z")
        ),
        left=glasses_eye(sph=rf("q")),
        shared_bc=None, shared_dia=None,
    )
    a = assemble_glasses(ex, lambda p: 0.9)
    assert a.warnings == [
        "right.sph: unparseable", "right.bc: unparseable",
        "right.near.sph: unparseable", "left.sph: unparseable",
    ]
