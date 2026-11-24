"""Parsed LLM output + confidence lookup -> API eye results (spec 5.6). Pure Python."""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from reader.normalize import (
    Normalized,
    normalize_axis,
    normalize_color,
    normalize_mm,
    normalize_power,
)
from reader.schemas import (
    BrandValue,
    EyeResult,
    FieldValue,
    GlassesEyeRaw,
    GlassesExtraction,
    LensEyeRaw,
    LensExtraction,
    NearValues,
    RawField,
    RawNear,
    SharedFieldValue,
)
from reader.validate import FORMAT_PENALTY, check_cyl_axis, check_value

ConfidenceLookup = Callable[[str], float]

# False = "validation off" (test only): rule codes are still listed as warnings, but they do
# not lower confidence and unparseable values keep the model's confidence.
_APPLY_RULES: ContextVar[bool] = ContextVar("apply_rules", default=True)

Kind = Literal["sph", "cyl", "axis", "add", "bc", "dia", "color"]


@dataclass
class Assembled:
    right: EyeResult | None
    left: EyeResult | None
    brand: BrandValue | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Prepared:
    raw: RawField
    norm: Normalized
    codes: list[str]  # normalize codes + check_value codes (pair code appended later)
    penalize: bool

    @property
    def number(self) -> Decimal | int | None:
        v = self.norm.value
        return None if self.norm.unparseable or isinstance(v, str) else v


def _present(raw: RawField | None) -> RawField | None:
    if raw is None or raw.raw.strip() == "":
        return None
    return raw


def _prepare(raw: RawField | None, kind: Kind) -> _Prepared | None:
    raw = _present(raw)
    if raw is None:
        return None
    if kind in ("sph", "cyl", "add"):
        norm = normalize_power(raw.raw, field=kind)
    elif kind == "axis":
        norm = normalize_axis(raw.raw)
    elif kind in ("bc", "dia"):
        norm = normalize_mm(raw.raw)
    else:
        norm = normalize_color(raw.raw)
    codes = list(norm.warnings)
    penalize = norm.penalize
    if not norm.unparseable and kind != "color" and norm.value is not None:
        checked = check_value(kind, norm.value)  # type: ignore[arg-type]
        codes.extend(checked)
        penalize = penalize or bool(checked)
    return _Prepared(raw, norm, codes, penalize)


def _pair_codes(
    cyl: _Prepared | None, axis: _Prepared | None
) -> tuple[str | None, str | None]:
    """Return (code for cyl, code for axis)."""
    code = check_cyl_axis(
        cyl.number if cyl else None,  # type: ignore[arg-type]
        axis.number if axis else None,  # type: ignore[arg-type]
    )
    if code == "cyl_without_axis" and cyl is not None:
        return code, None
    if code == "axis_without_cyl" and axis is not None:
        return None, code
    return None, None


def _build(
    prep: _Prepared | None,
    out_path: str,
    conf_path: str,
    conf: ConfidenceLookup,
    warnings: list[str],
    pair_code: str | None = None,
    shared: bool | None = None,
) -> FieldValue | SharedFieldValue | None:
    if prep is None:
        return None
    codes = list(prep.codes)
    penalize = prep.penalize
    if pair_code:
        codes.append(pair_code)
        penalize = True
    warnings.extend(f"{out_path}: {c}" for c in codes)
    rules = _APPLY_RULES.get()
    if prep.norm.unparseable:
        confidence = 0.0 if rules else float(conf(conf_path))
        value: float | int | str | None = None
    else:
        confidence = float(conf(conf_path))
        if penalize and rules:
            confidence *= FORMAT_PENALTY
        v = prep.norm.value
        value = float(v) if isinstance(v, Decimal) else v
    confidence = min(1.0, max(0.0, confidence))
    kwargs = dict(
        value=value,
        raw=prep.raw.raw,
        confidence=confidence,
        handwritten=prep.raw.handwritten,
    )
    if shared is None:
        return FieldValue(**kwargs)
    return SharedFieldValue(**kwargs, shared=shared)


def _near(
    near: RawNear | None, eye: str, conf: ConfidenceLookup, warnings: list[str]
) -> NearValues | None:
    if near is None:
        return None
    sph = _prepare(near.sph, "sph")
    cyl = _prepare(near.cyl, "cyl")
    axis = _prepare(near.axis, "axis")
    cyl_code, axis_code = _pair_codes(cyl, axis)
    base = f"{eye}.near"
    result = NearValues(
        sph=_build(sph, f"{base}.sph", f"{base}.sph.raw", conf, warnings),
        cyl=_build(cyl, f"{base}.cyl", f"{base}.cyl.raw", conf, warnings, cyl_code),
        axis=_build(axis, f"{base}.axis", f"{base}.axis.raw", conf, warnings, axis_code),
    )
    if result.sph is None and result.cyl is None and result.axis is None:
        return None
    return result


def _shared_conflict(
    own: RawField | None, shared_raw: RawField | None, kind: Literal["bc", "dia"]
) -> bool:
    """True when an eye's own value and the shared value both exist and differ."""
    own_prep = _prepare(own, kind)
    shared_prep = _prepare(shared_raw, kind)
    if own_prep is None or shared_prep is None:
        return False
    if own_prep.norm.unparseable or shared_prep.norm.unparseable:
        return True
    return own_prep.norm.value != shared_prep.norm.value


def _mm_field(
    own: RawField | None,
    shared_raw: RawField | None,
    kind: Literal["bc", "dia"],
    eye: str,
    conf: ConfidenceLookup,
    warnings: list[str],
    conflict: bool = False,
) -> SharedFieldValue | None:
    own_prep = _prepare(own, kind)
    shared_prep = _prepare(shared_raw, kind)
    out_path = f"{eye}.{kind}"
    if own_prep is not None:
        return _build(  # type: ignore[return-value]
            own_prep, out_path, f"{eye}.{kind}.raw", conf, warnings,
            pair_code="shared_conflict" if conflict else None, shared=False,
        )
    return _build(  # type: ignore[return-value]
        shared_prep, out_path, f"shared_{kind}.raw", conf, warnings,
        pair_code="shared_conflict" if conflict else None, shared=True,
    )


def _assemble_eye(
    eye_raw: LensEyeRaw | GlassesEyeRaw | None,
    eye: str,
    ex: LensExtraction | GlassesExtraction,
    conf: ConfidenceLookup,
    warnings: list[str],
    conflicts: frozenset[str] = frozenset(),
) -> EyeResult | None:
    if eye_raw is None:
        return None
    sph = _prepare(eye_raw.sph, "sph")
    cyl = _prepare(eye_raw.cyl, "cyl")
    axis = _prepare(eye_raw.axis, "axis")
    add = _prepare(eye_raw.add, "add")
    cyl_code, axis_code = _pair_codes(cyl, axis)

    def build(prep: _Prepared | None, name: str, pair: str | None = None):
        return _build(prep, f"{eye}.{name}", f"{eye}.{name}.raw", conf, warnings, pair)

    f_sph = build(sph, "sph")
    f_cyl = build(cyl, "cyl", cyl_code)
    f_axis = build(axis, "axis", axis_code)
    f_add = build(add, "add")
    f_bc = _mm_field(eye_raw.bc, ex.shared_bc, "bc", eye, conf, warnings, "bc" in conflicts
    )
    f_dia = _mm_field(
        eye_raw.dia, ex.shared_dia, "dia", eye, conf, warnings, "dia" in conflicts
    )
    if isinstance(eye_raw, LensEyeRaw):
        f_color = build(_prepare(eye_raw.color, "color"), "color")
        f_near = None
    else:
        f_color = None
        f_near = _near(eye_raw.near, eye, conf, warnings)
    result = EyeResult(
        sph=f_sph,
        cyl=f_cyl,
        axis=f_axis,
        add=f_add,
        bc=f_bc,
        dia=f_dia,
        color=f_color,
        near=f_near,
    )
    if all(v is None for v in result.model_dump().values()):
        return None
    return result


def _assemble(ex: LensExtraction | GlassesExtraction, conf: ConfidenceLookup) -> Assembled:
    warnings: list[str] = []
    conflicts = frozenset(
        kind
        for kind in ("bc", "dia")
        if any(
            e is not None and _shared_conflict(getattr(e, kind), getattr(ex, f"shared_{kind}"), kind)  # type: ignore[arg-type]
            for e in (ex.right, ex.left)
        )
    )
    right = _assemble_eye(ex.right, "right", ex, conf, warnings, conflicts)
    left = _assemble_eye(ex.left, "left", ex, conf, warnings, conflicts)
    brand = None
    if isinstance(ex, LensExtraction) and ex.brand is not None and ex.brand.strip():
        brand = BrandValue(raw=ex.brand.strip(), confidence=float(conf("brand")))
    return Assembled(right=right, left=left, brand=brand, warnings=warnings)


def _run(ex: LensExtraction | GlassesExtraction, conf: ConfidenceLookup, apply_rules: bool) -> Assembled:
    token = _APPLY_RULES.set(apply_rules)
    try:
        return _assemble(ex, conf)
    finally:
        _APPLY_RULES.reset(token)


def assemble_lens(ex: LensExtraction, conf: ConfidenceLookup, *, apply_rules: bool = True) -> Assembled:
    return _run(ex, conf, apply_rules)


def assemble_glasses(ex: GlassesExtraction, conf: ConfidenceLookup, *, apply_rules: bool = True) -> Assembled:
    return _run(ex, conf, apply_rules)
