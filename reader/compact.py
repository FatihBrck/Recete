"""Compact model-output schemas: values as bare strings, one document-level `handwritten`.

The model writes ~35-40% fewer tokens than with per-field {"raw","handwritten"} objects.
`to_full` converts a compact extraction into the regular extraction schemas so that
assembly, normalization and the API response stay identical.
"""

from __future__ import annotations

from pydantic import BaseModel

from reader.schemas import (
    GlassesExtraction,
    GlassesEyeRaw,
    LensExtraction,
    LensEyeRaw,
    RawField,
    RawNear,
)


class NearCompact(BaseModel):
    sph: str | None
    cyl: str | None
    axis: str | None


class LensEyeCompact(BaseModel):
    sph: str | None
    cyl: str | None
    axis: str | None
    add: str | None
    bc: str | None
    dia: str | None
    color: str | None


class GlassesEyeCompact(BaseModel):
    sph: str | None
    cyl: str | None
    axis: str | None
    add: str | None
    bc: str | None
    dia: str | None
    near: NearCompact | None


class LensExtractionCompact(BaseModel):
    handwritten: bool
    right: LensEyeCompact | None
    left: LensEyeCompact | None
    shared_bc: str | None
    shared_dia: str | None
    brand: str | None


class GlassesExtractionCompact(BaseModel):
    handwritten: bool
    right: GlassesEyeCompact | None
    left: GlassesEyeCompact | None
    shared_bc: str | None
    shared_dia: str | None


def _field(raw: str | None, handwritten: bool) -> RawField | None:
    return None if raw is None else RawField(raw=raw, handwritten=handwritten)


def _fields(model: BaseModel, names: tuple[str, ...], hw: bool) -> dict:
    return {n: _field(getattr(model, n), hw) for n in names}


_LENS_EYE = ("sph", "cyl", "axis", "add", "bc", "dia", "color")
_GLASSES_EYE = ("sph", "cyl", "axis", "add", "bc", "dia")
_NEAR = ("sph", "cyl", "axis")


def to_full(ext: LensExtractionCompact | GlassesExtractionCompact) -> LensExtraction | GlassesExtraction:
    hw = ext.handwritten
    shared = {"shared_bc": _field(ext.shared_bc, hw), "shared_dia": _field(ext.shared_dia, hw)}
    if isinstance(ext, LensExtractionCompact):
        def eye(e):
            return None if e is None else LensEyeRaw(**_fields(e, _LENS_EYE, hw))
        return LensExtraction(right=eye(ext.right), left=eye(ext.left), brand=ext.brand, **shared)

    def geye(e):
        if e is None:
            return None
        near = None if e.near is None else RawNear(**_fields(e.near, _NEAR, hw))
        return GlassesEyeRaw(near=near, **_fields(e, _GLASSES_EYE, hw))
    return GlassesExtraction(right=geye(ext.right), left=geye(ext.left), **shared)


def full_path(path: str) -> str:
    """Map an assembly confidence path ("right.sph.raw") to the compact JSON leaf ("right.sph")."""
    return path[: -len(".raw")] if path.endswith(".raw") else path
