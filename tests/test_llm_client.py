import json

import httpx
import pytest
import respx

from reader.llm_client import (
    LLMResult,
    OpenAICompatClient,
    OutputParseError,
    TokenLogprob,
    VLMError,
    VLMTimeout,
)

BASE = "http://vllm.test/v1"
MODEL = "Qwen/Qwen3-VL-8B-Instruct"
TEXT = '{"a": 1}'


def make_client() -> OpenAICompatClient:
    return OpenAICompatClient(base_url=BASE, model=MODEL, max_tokens=256, timeout_s=5.0)


def completion(content, logprobs, finish_reason="stop") -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "logprobs": logprobs,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def lp_content(pairs):
    return {
        "content": [
            {"token": t, "logprob": lp, "bytes": list(t.encode()), "top_logprobs": []}
            for t, lp in pairs
        ]
    }


async def call(client):
    return await client.complete(
        system="sys",
        user="usr",
        image_jpeg_b64="QUJD",
        schema_name="LensExtraction",
        json_schema={"type": "object"},
    )


async def test_request_shape():
    pairs = [('{"a"', -0.01), (": 1}", -0.5)]
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").respond(
            200, json=completion(TEXT, lp_content(pairs))
        )
        result = await call(make_client())
    body = json.loads(route.calls.last.request.content)
    assert body["temperature"] == 0
    assert body["logprobs"] is True
    assert body["max_tokens"] == 256
    assert body["model"] == MODEL
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "LensExtraction"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    parts = body["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[1] == {"type": "text", "text": "usr"}
    assert result == LLMResult(
        text=TEXT, tokens=[TokenLogprob(t, lp) for t, lp in pairs]
    )


async def test_missing_logprobs_gives_empty_tokens():
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(200, json=completion(TEXT, None))
        result = await call(make_client())
    assert result.text == TEXT
    assert result.tokens == []


async def test_length_finish_raises_output_parse_error():
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(
            200, json=completion('{"a"', None, finish_reason="length")
        )
        with pytest.raises(OutputParseError):
            await call(make_client())


async def test_empty_choices_and_null_content_raise_output_parse_error():
    empty = completion(TEXT, None)
    empty["choices"] = []
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(200, json=empty)
        with pytest.raises(OutputParseError):
            await call(make_client())
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").respond(200, json=completion(None, None))
        with pytest.raises(OutputParseError):
            await call(make_client())


async def test_timeout_raises_vlm_timeout():
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").mock(side_effect=httpx.ReadTimeout("t"))
        with pytest.raises(VLMTimeout):
            await call(make_client())
    assert route.call_count == 1


async def test_server_error_raises_vlm_error():
    with respx.mock(base_url=BASE) as mock:
        route = mock.post("/chat/completions").respond(500, json={"error": "boom"})
        with pytest.raises(VLMError) as ei:
            await call(make_client())
    assert not isinstance(ei.value, VLMTimeout)
    assert route.call_count == 1
    assert "QUJD" not in str(ei.value)


async def test_connection_error_raises_vlm_error():
    with respx.mock(base_url=BASE) as mock:
        mock.post("/chat/completions").mock(side_effect=httpx.ConnectError("x"))
        with pytest.raises(VLMError) as ei:
            await call(make_client())
    assert not isinstance(ei.value, VLMTimeout)


async def test_health_true_and_false():
    with respx.mock(base_url=BASE) as mock:
        mock.get("/models").respond(
            200, json={"object": "list", "data": [{"id": MODEL, "object": "model", "created": 1, "owned_by": "x"}]}
        )
        assert await make_client().health() is True
    with respx.mock(base_url=BASE) as mock:
        mock.get("/models").mock(side_effect=httpx.ConnectError("x"))
        assert await make_client().health() is False


async def test_aclose_closes_http_client():
    client = make_client()
    assert not client._client.is_closed()
    await client.aclose()
    assert client._client.is_closed()
