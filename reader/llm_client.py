"""OpenAI-compatible VLM client (vLLM) with token logprobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx
import openai

if TYPE_CHECKING:
    from reader.config import ModelConfig


@dataclass(frozen=True)
class TokenLogprob:
    token: str
    logprob: float


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens: list[TokenLogprob]


class VLMError(Exception):
    """Any failure talking to the VLM."""


class VLMTimeout(VLMError):
    """The VLM did not answer within the timeout."""


class OutputParseError(VLMError):
    """The VLM answered, but the output is unusable (truncated or empty)."""


class VLMClient(Protocol):
    async def complete(
        self,
        *,
        system: str,
        user: str,
        image_jpeg_b64: str,
        schema_name: str,
        json_schema: dict,
    ) -> LLMResult: ...

    async def health(self) -> bool: ...


class OpenAICompatClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        max_tokens: int,
        timeout_s: float,
        api_key: str = "EMPTY",
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        # Explicit httpx client: openai>=3 otherwise uses its vendored httpx2,
        # which respx cannot intercept; one transport for prod and tests.
        self._client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
            http_client=httpx.AsyncClient(timeout=timeout_s),
        )

    @classmethod
    def from_config(cls, cfg: ModelConfig) -> OpenAICompatClient:
        return cls(
            base_url=cfg.base_url,
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            timeout_s=cfg.timeout_s,
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        image_jpeg_b64: str,
        schema_name: str,
        json_schema: dict,
    ) -> LLMResult:
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64," + image_jpeg_b64
                                },
                            },
                            {"type": "text", "text": user},
                        ],
                    },
                ],
                temperature=0,
                logprobs=True,
                max_tokens=self._max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": json_schema,
                        "strict": True,
                    },
                },
            )
        except openai.APITimeoutError as exc:
            raise VLMTimeout("VLM request timed out") from exc
        except openai.APIConnectionError as exc:
            raise VLMError("VLM connection failed") from exc
        except openai.APIStatusError as exc:
            raise VLMError(f"VLM returned HTTP {exc.status_code}") from exc
        except openai.APIError as exc:
            raise VLMError("VLM request failed") from exc

        if not resp.choices:
            raise OutputParseError("VLM response has no choices")
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise OutputParseError("VLM output truncated (finish_reason=length)")
        text = choice.message.content
        if text is None:
            raise OutputParseError("VLM response has no content")
        content = choice.logprobs.content if choice.logprobs else None
        tokens = [TokenLogprob(t.token, t.logprob) for t in content or []]
        return LLMResult(text=text, tokens=tokens)

    async def aclose(self) -> None:
        await self._client.close()

    async def health(self) -> bool:
        try:
            await self._client.models.list()
        except Exception:
            return False
        return True
