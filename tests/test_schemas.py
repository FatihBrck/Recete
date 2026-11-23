import pytest
from pydantic import ValidationError

from reader.schemas import (
    DOC_TYPES,
    Classification,
    EyeResult,
    FieldValue,
    GlassesEyeRaw,
    GlassesExtraction,
    LensEyeRaw,
    LensExtraction,
    SharedFieldValue,
)


def test_shared_only_on_bc_dia_and_nulls_kept():
    eye = EyeResult(
        sph=FieldValue(value=-2.5, raw="-2,5", confidence=0.9, handwritten=False),
        cyl=None, axis=None, add=None,
        bc=SharedFieldValue(value=8.6, raw="8.6", confidence=0.8, handwritten=True, shared=True),
        dia=None, color=None, near=None)
    d = eye.model_dump(mode="json")
    assert "shared" not in d["sph"]
    assert d["bc"]["shared"] is True
    assert "add" in d and d["add"] is None


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        FieldValue(value=1.0, raw="1", confidence=1.2, handwritten=False)


def test_classification_schema_enum():
    assert Classification.model_json_schema()["properties"]["doc_type"]["enum"] == list(DOC_TYPES)


def test_llm_schemas_require_all_keys():
    for cls in (LensExtraction, GlassesExtraction, LensEyeRaw, GlassesEyeRaw):
        s = cls.model_json_schema()
        assert set(s["required"]) == set(s["properties"])
    with pytest.raises(ValidationError):
        LensExtraction.model_validate_json("{}")


def test_glasses_eye_has_near_not_color():
    props = GlassesEyeRaw.model_json_schema()["properties"]
    assert "near" in props and "color" not in props
