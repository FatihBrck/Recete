import base64
import io
import uuid

import pytest
from PIL import Image

from reader.config import Settings, load_model_config, load_prompt_set
from reader.llm_client import LLMResult, OutputParseError
from reader.pipeline import ReaderPipeline
from tests.fakes import FakeVLMClient, char_tokens

CLS_OTHER = '{"doc_type":"other"}'
CLS_GLASSES = '{"doc_type":"glasses"}'
CLS_HW = '{"doc_type":"lens_handwritten"}'
CLS_PRINTED = '{"doc_type":"lens_printed"}'
LENS_EMPTY = '{"right":null,"left":null,"shared_bc":null,"shared_dia":null,"brand":null}'
GLASSES_EMPTY = '{"right":null,"left":null,"shared_bc":null,"shared_dia":null}'
LENS_SPH = (
    '{"right":{"sph":{"raw":"-2.00","handwritten":false},"cyl":null,"axis":null,'
    '"add":null,"bc":null,"dia":null,"color":null},"left":null,'
    '"shared_bc":null,"shared_dia":null,"brand":null}'
)


def res(text: str, **kw) -> LLMResult:
    return LLMResult(text=text, tokens=char_tokens(text, **kw))


def png_bytes(size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def cfg():
    return load_model_config(Settings())


@pytest.fixture
def prompts(cfg):
    return load_prompt_set(cfg)


def make(cfg, prompts, results):
    fake = FakeVLMClient(results)
    return ReaderPipeline(fake, cfg, prompts), fake


async def test_other_stops_after_classification(cfg, prompts):
    p, fake = make(cfg, prompts, [res(CLS_OTHER)])
    r = await p.read(png_bytes())
    assert r.doc_type == "other"
    assert r.right is None and r.left is None and r.brand is None
    assert len(fake.calls) == 1
    assert r.prompt_version == "classify-v1"
    assert fake.calls[0]["schema_name"] == "Classification"
    assert fake.calls[0]["system"] == prompts.classify.system


async def test_glasses_uses_glasses_prompt_and_schema(cfg, prompts):
    p, fake = make(cfg, prompts, [res(CLS_GLASSES), res(GLASSES_EMPTY)])
    r = await p.read(png_bytes())
    assert fake.calls[1]["schema_name"] == "GlassesExtraction"
    assert fake.calls[1]["system"] == prompts.glasses.system
    assert fake.calls[1]["user"] == prompts.glasses.user
    assert r.prompt_version == "classify-v1/glasses-v1"


async def test_lens_handwritten_uses_handwritten_prompt_with_lens_schema(cfg, prompts):
    p, fake = make(cfg, prompts, [res(CLS_HW), res(LENS_EMPTY)])
    r = await p.read(png_bytes())
    assert fake.calls[1]["schema_name"] == "LensExtraction"
    assert fake.calls[1]["system"] == prompts.lens_handwritten.system
    assert r.prompt_version == "classify-v1/lens_handwritten-v1"


async def test_lens_printed_assembles_fields(cfg, prompts):
    p, fake = make(cfg, prompts, [res(CLS_PRINTED), res(LENS_SPH)])
    r = await p.read(png_bytes())
    assert fake.calls[1]["system"] == prompts.lens_printed.system
    assert r.right.sph.value == -2.0
    assert r.right.sph.confidence == pytest.approx(0.99)
    assert r.warnings == []


async def test_doc_type_confidence_is_product_of_label_tokens(cfg, prompts):
    text = CLS_GLASSES
    cls = LLMResult(text, char_tokens(text, overrides={"glasses": 0.9}))
    p, _ = make(cfg, prompts, [cls, res(GLASSES_EMPTY)])
    r = await p.read(png_bytes())
    assert r.doc_type_confidence == pytest.approx(0.9**7)


async def test_invalid_json_raises_output_parse_error(cfg, prompts):
    p, _ = make(cfg, prompts, [res(CLS_GLASSES), res("not json")])
    with pytest.raises(OutputParseError):
        await p.read(png_bytes())


async def test_schema_violation_raises_output_parse_error(cfg, prompts):
    p, _ = make(cfg, prompts, [res('{"doc_type":"banana"}')])
    with pytest.raises(OutputParseError) as ei:
        await p.read(png_bytes())
    assert "banana" not in str(ei.value)


async def test_confidence_unavailable_warning(cfg, prompts):
    cls = LLMResult(CLS_PRINTED, [])
    ext = LLMResult(LENS_SPH, [])
    p, _ = make(cfg, prompts, [cls, ext])
    r = await p.read(png_bytes())
    assert r.warnings.count("confidence_unavailable") == 1
    assert r.warnings[-1] == "confidence_unavailable"
    assert r.doc_type_confidence == 0.0
    assert r.right.sph.confidence == 0.0


async def test_confidence_map_value_error_treated_as_unavailable(cfg, prompts, monkeypatch):
    def boom(text, tokens):
        raise ValueError("bad")

    monkeypatch.setattr("reader.pipeline.ConfidenceMap", boom)
    p, _ = make(cfg, prompts, [res(CLS_PRINTED), res(LENS_SPH)])
    r = await p.read(png_bytes())
    assert r.warnings == ["confidence_unavailable"]
    assert r.doc_type_confidence == 0.0
    assert r.right.sph.confidence == 0.0


async def test_image_downscaled_before_sending(cfg, prompts):
    p, fake = make(cfg, prompts, [res(CLS_OTHER)])
    await p.read(png_bytes((4000, 3000)))
    img = Image.open(io.BytesIO(base64.b64decode(fake.calls[0]["image_jpeg_b64"])))
    assert img.width * img.height <= cfg.max_image_pixels


async def test_response_metadata(cfg, prompts):
    p, _ = make(cfg, prompts, [res(CLS_OTHER)])
    r = await p.read(png_bytes())
    uuid.UUID(r.request_id)
    assert r.model == "qwen3-vl-8b-instruct"
    assert r.latency_ms >= 0 and isinstance(r.latency_ms, int)


async def test_health_delegates(cfg, prompts):
    p = ReaderPipeline(FakeVLMClient([], healthy=False), cfg, prompts)
    assert await p.health() is False
