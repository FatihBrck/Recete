"""Altın örnek entegrasyon testleri: gerçek vLLM gerekir (READER_VLLM_URL)."""
import os
from pathlib import Path

import pytest
import pytest_asyncio

from reader.config import Settings, load_model_config, load_prompt_set
from reader.llm_client import OpenAICompatClient
from reader.pipeline import ReaderPipeline

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


@pytest_asyncio.fixture
async def pipeline():
    url = os.environ.get("READER_VLLM_URL")
    if not url:
        pytest.skip("READER_VLLM_URL tanımlı değil (çalışan bir vLLM gerekir)")
    cfg = load_model_config(Settings())
    client = OpenAICompatClient(
        base_url=url, model=cfg.model, max_tokens=cfg.max_tokens, timeout_s=cfg.timeout_s
    )
    try:
        yield ReaderPipeline(client, cfg, load_prompt_set(cfg))
    finally:
        await client.aclose()


async def test_a1_handwritten(pipeline):
    r = await pipeline.read((GOLDEN / "a1_handwritten.webp").read_bytes())
    assert r.doc_type == "lens_handwritten"
    assert (r.right.sph.value, r.right.cyl.value, r.right.axis.value) == (-3.0, -0.75, 80)
    assert (r.left.sph.value, r.left.cyl.value, r.left.axis.value) == (-3.0, -0.75, 100)
    assert r.right.bc.value == 8.6 and r.right.bc.shared and r.left.dia.value == 14.5
    assert "Acuvue" in r.brand.raw


async def test_a2_printed(pipeline):
    r = await pipeline.read((GOLDEN / "a2_printed.webp").read_bytes())
    assert r.doc_type == "lens_printed"
    assert r.right.bc.value == 8.4 and r.right.dia.value == 14.0 and r.right.sph.value == -2.25
    assert r.left.sph.value == -2.25 or "left.sph: not_quarter_step" in r.warnings
    assert r.right.cyl is None and r.right.add is None and r.brand is None
    assert r.right.bc.shared is False
