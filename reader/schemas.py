"""Pydantic schemas: LLM extraction outputs and public API models."""
from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["lens_printed", "lens_handwritten", "glasses", "other"]
DOC_TYPES: tuple[str, ...] = ("lens_printed", "lens_handwritten", "glasses", "other")

# --- LLM output models (every field required, nullable, no defaults) ---


class RawField(BaseModel):
    raw: str
    handwritten: bool


class RawNear(BaseModel):
    sph: RawField | None
    cyl: RawField | None
    axis: RawField | None


class LensEyeRaw(BaseModel):
    sph: RawField | None
    cyl: RawField | None
    axis: RawField | None
    add: RawField | None
    bc: RawField | None
    dia: RawField | None
    color: RawField | None


class GlassesEyeRaw(BaseModel):
    sph: RawField | None
    cyl: RawField | None
    axis: RawField | None
    add: RawField | None
    bc: RawField | None
    dia: RawField | None
    near: RawNear | None


class LensExtraction(BaseModel):
    right: LensEyeRaw | None
    left: LensEyeRaw | None
    shared_bc: RawField | None
    shared_dia: RawField | None
    brand: str | None


class GlassesExtraction(BaseModel):
    right: GlassesEyeRaw | None
    left: GlassesEyeRaw | None
    shared_bc: RawField | None
    shared_dia: RawField | None


class Classification(BaseModel):
    doc_type: DocType


# --- API models ---


class FieldValue(BaseModel):
    value: float | int | str | None
    raw: str
    confidence: float = Field(ge=0, le=1)
    handwritten: bool


class SharedFieldValue(FieldValue):
    shared: bool


class NearValues(BaseModel):
    sph: FieldValue | None
    cyl: FieldValue | None
    axis: FieldValue | None


class EyeResult(BaseModel):
    sph: FieldValue | None
    cyl: FieldValue | None
    axis: FieldValue | None
    add: FieldValue | None
    bc: SharedFieldValue | None
    dia: SharedFieldValue | None
    color: FieldValue | None
    near: NearValues | None


class BrandValue(BaseModel):
    raw: str
    confidence: float = Field(ge=0, le=1)


class ReadResponse(BaseModel):
    request_id: str
    doc_type: DocType
    doc_type_confidence: float = Field(ge=0, le=1)
    right: EyeResult | None
    left: EyeResult | None
    brand: BrandValue | None
    warnings: list[str]
    model: str
    prompt_version: str
    latency_ms: int
