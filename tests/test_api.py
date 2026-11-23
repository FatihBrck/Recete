import io

import httpx
import pytest
import structlog
from PIL import Image

from reader.api import create_app
from reader.config import Settings, load_model_config, load_prompt_set
from reader.llm_client import LLMResult, VLMError, VLMTimeout
from reader.pipeline import ReaderPipeline
from tests.fakes import FakeVLMClient, char_tokens

CLS_PRINTED = '{"doc_type":"lens_printed"}'
LENS = (
    '{"right":{"sph":{"raw":"-2,29","handwritten":false},"cyl":null,"axis":null,'
    '"add":null,"bc":{"raw":"8.6","handwritten":false},"dia":null,"color":null},'
    '"left":{"sph":{"raw":"-2.25","handwritten":false},"cyl":{"raw":"-0.75","handwritten":false},'
    '"axis":{"raw":"180","handwritten":false},"add":null,"bc":null,"dia":null,"color":null},'
    '"shared_bc":null,"shared_dia":null,"brand":"Acuvue Oasys"}'
)
LENS_PLAIN = (
    '{"right":{"sph":{"raw":"-2.25","handwritten":false},"cyl":null,"axis":null,'
    '"add":null,"bc":null,"dia":null,"color":null},"left":null,'
    '"shared_bc":null,"shared_dia":null,"brand":null}'
)


def res(text: str) -> LLMResult:
    return LLMResult(text=text, tokens=char_tokens(text))


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buf, format="PNG")
    return buf.getvalue()


def make_app(results, healthy=True, settings=None):
    cfg = load_model_config(Settings())
    pipeline = ReaderPipeline(
        FakeVLMClient(results, healthy=healthy), cfg, load_prompt_set(cfg)
    )
    return create_app(pipeline=pipeline, settings=settings or Settings())


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def post(app, content: bytes):
    async with client_for(app) as c:
        return await c.post("/v1/read", files={"file": ("x.bin", content)})


async def test_read_returns_response():
    r = await post(make_app([res(CLS_PRINTED), res(LENS_PLAIN)]), png_bytes())
    assert r.status_code == 200
    body = r.json()
    assert body["doc_type"] == "lens_printed"
    assert body["right"]["sph"]["value"] == -2.25


async def test_pdf_returns_415():
    r = await post(make_app([]), b"%PDF-1.4\n%fake")
    assert r.status_code == 415
    assert r.json() == {"error": "unsupported_image"}


async def test_empty_file_returns_415():
    r = await post(make_app([]), b"")
    assert r.status_code == 415
    assert r.json() == {"error": "unsupported_image"}


async def test_too_large_returns_413():
    app = make_app([], settings=Settings(max_upload_bytes=10))
    r = await post(app, b"x" * 11)
    assert r.status_code == 413
    assert r.json() == {"error": "too_large"}


async def test_exactly_at_limit_is_not_too_large():
    app = make_app([], settings=Settings(max_upload_bytes=10))
    r = await post(app, b"x" * 10)
    assert r.status_code == 415


async def test_vlm_error_returns_502_without_partial_body():
    r = await post(make_app([res(CLS_PRINTED), VLMError("boom")]), png_bytes())
    assert r.status_code == 502
    assert r.json() == {"error": "model_error"}


async def test_timeout_returns_504():
    r = await post(make_app([VLMTimeout("slow")]), png_bytes())
    assert r.status_code == 504
    assert r.json() == {"error": "model_timeout"}


async def test_unexpected_error_returns_500_and_logs_type_only():
    app = make_app([RuntimeError("secret -2,29 Acuvue")])
    with structlog.testing.capture_logs() as logs:
        r = await post(app, png_bytes())
    assert r.status_code == 500
    assert r.json() == {"error": "internal_error"}
    failed = [e for e in logs if e["event"] == "read_failed"]
    assert failed and failed[0]["error"] == "internal_error"
    assert failed[0]["exception_type"] == "RuntimeError"
    assert "secret" not in str(logs) and "Acuvue" not in str(logs)


async def test_health_ok_and_unavailable():
    async with client_for(make_app([])) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model": load_model_config(Settings()).name}
    async with client_for(make_app([], healthy=False)) as c:
        r = await c.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable"}


async def test_logs_contain_no_values():
    app = make_app([res(CLS_PRINTED), res(LENS)])
    with structlog.testing.capture_logs() as logs:
        r = await post(app, png_bytes())
    assert r.status_code == 200
    text = str(logs)
    for forbidden in ("-2,29", "-2.29", "Acuvue", "-2.25", "-0.75", "8.6"):
        assert forbidden not in text
    done = [e for e in logs if e["event"] == "read_completed"]
    assert len(done) == 1
    ev = done[0]
    for key in (
        "request_id", "doc_type", "doc_type_confidence", "confidences",
        "warnings", "model", "prompt_version", "latency_ms",
    ):
        assert key in ev
    assert ev["request_id"] == r.json()["request_id"]
    conf = ev["confidences"]
    assert {"right.sph", "right.bc", "left.sph", "left.cyl", "left.axis", "brand"} <= set(conf)
    assert all(isinstance(v, float) and 0 <= v <= 1 for v in conf.values())


async def test_failure_is_logged_as_read_failed():
    app = make_app([VLMTimeout("slow")])
    with structlog.testing.capture_logs() as logs:
        await post(app, png_bytes())
    failed = [e for e in logs if e["event"] == "read_failed"]
    assert len(failed) == 1 and failed[0]["error"] == "model_timeout"
    assert "request_id" in failed[0]


async def test_lifespan_does_not_close_injected_pipeline():
    app = make_app([])
    async with app.router.lifespan_context(app):
        pass  # no aclose on FakeVLMClient; must not raise


@pytest.mark.parametrize("name", ["right.near.axis"])
def test_confidence_paths_cover_near(name):
    from reader.api import confidence_map
    from reader.schemas import EyeResult, FieldValue, NearValues, ReadResponse

    fv = FieldValue(value=10, raw="10", confidence=0.5, handwritten=False)
    eye = EyeResult(sph=None, cyl=None, axis=None, add=None, bc=None, dia=None,
                    color=None, near=NearValues(sph=None, cyl=None, axis=fv))
    resp = ReadResponse(request_id="r", doc_type="glasses", doc_type_confidence=1,
                        right=eye, left=None, brand=None, warnings=[], model="m",
                        prompt_version="p", latency_ms=1)
    assert confidence_map(resp) == {name: 0.5}
