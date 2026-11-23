import pytest

from reader.confidence import ConfidenceMap
from reader.llm_client import LLMResult
from tests.fakes import FakeVLMClient, char_tokens


def test_char_tokens_concat_and_overrides():
    text = '{"a": "xyz"}'
    toks = char_tokens(text, p=0.9, overrides={"xyz": 0.1})
    assert "".join(t.token for t in toks) == text
    ConfidenceMap(text, toks)
    i = text.index("xyz")
    assert all(abs(__import__("math").exp(t.logprob) - 0.1) < 1e-9 for t in toks[i : i + 3])
    assert abs(__import__("math").exp(toks[0].logprob) - 0.9) < 1e-9


def test_char_tokens_missing_override_raises():
    with pytest.raises(ValueError):
        char_tokens("abc", overrides={"zzz": 0.5})


async def test_fake_client_sequence():
    err = RuntimeError("boom")
    fake = FakeVLMClient([LLMResult("t", []), err])
    kw = dict(system="s", user="u", image_jpeg_b64="b", schema_name="n", json_schema={})
    assert (await fake.complete(**kw)).text == "t"
    with pytest.raises(RuntimeError):
        await fake.complete(**kw)
    with pytest.raises(AssertionError, match="no more"):
        await fake.complete(**kw)
    assert fake.calls[0] == kw
    assert len(fake.calls) == 3
    assert await fake.health() is True
