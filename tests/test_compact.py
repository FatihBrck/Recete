from reader.compact import (
    GlassesExtractionCompact,
    LensExtractionCompact,
    full_path,
    to_full,
)
from reader.schemas import GlassesExtraction, LensExtraction


def test_lens_compact_to_full_copies_doc_handwritten():
    c = LensExtractionCompact.model_validate({
        "handwritten": True,
        "right": {"sph": "-5,00", "cyl": "-0,75", "axis": "170", "add": None,
                  "bc": None, "dia": None, "color": None},
        "left": None, "shared_bc": "8,6", "shared_dia": None, "brand": "Acuvue"})
    f = to_full(c)
    assert isinstance(f, LensExtraction)
    assert f.right.sph.raw == "-5,00" and f.right.sph.handwritten is True
    assert f.right.add is None and f.left is None
    assert f.shared_bc.raw == "8,6" and f.shared_bc.handwritten is True
    assert f.brand == "Acuvue"


def test_glasses_compact_to_full_with_near():
    c = GlassesExtractionCompact.model_validate({
        "handwritten": False,
        "right": {"sph": "-2.00", "cyl": None, "axis": None, "add": "+2.00", "bc": None, "dia": None,
                  "near": {"sph": "+0.00", "cyl": None, "axis": None}},
        "left": None, "shared_bc": None, "shared_dia": None})
    f = to_full(c)
    assert isinstance(f, GlassesExtraction)
    assert f.right.near.sph.raw == "+0.00" and f.right.near.sph.handwritten is False
    assert f.right.add.raw == "+2.00"


def test_compact_schema_has_no_raw_objects():
    props = LensExtractionCompact.model_json_schema()
    assert "RawField" not in str(props)
    assert "handwritten" in props["properties"]


def test_full_path_maps_raw_leaf_to_compact_leaf():
    assert full_path("right.sph.raw") == "right.sph"
    assert full_path("left.near.axis.raw") == "left.near.axis"
    assert full_path("shared_bc.raw") == "shared_bc"
    assert full_path("brand") == "brand"
