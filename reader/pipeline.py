"""Two-stage reader pipeline: classify, then extract with a type-specific prompt."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from reader.assemble import Assembled, assemble_glasses, assemble_lens
from reader.config import ModelConfig, PromptSet, PromptTemplate
from reader.confidence import ConfidenceMap
from reader.llm_client import LLMResult, OutputParseError, VLMClient
from reader.preprocess import fit_max_pixels, load_image, to_jpeg_b64
from reader.schemas import (
    Classification,
    GlassesExtraction,
    LensExtraction,
    ReadResponse,
)

M = TypeVar("M", bound=BaseModel)

CONFIDENCE_UNAVAILABLE = "confidence_unavailable"


def _zero(_path: str) -> float:
    return 0.0


def _parse(model: type[M], result: LLMResult) -> M:
    try:
        return model.model_validate_json(result.text)
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        raise OutputParseError(f"{model.__name__} output could not be parsed") from exc


def _confidence(result: LLMResult) -> ConfidenceMap | None:
    """ConfidenceMap if usable, else None (treated as unavailable)."""
    try:
        cmap = ConfidenceMap(result.text, result.tokens)
    except ValueError:
        return None
    return cmap if cmap.available else None


def _preprocess(image_bytes: bytes, max_pixels: int) -> str:
    img = fit_max_pixels(load_image(image_bytes), max_pixels)
    return to_jpeg_b64(img)


class ReaderPipeline:
    def __init__(self, client: VLMClient, cfg: ModelConfig, prompts: PromptSet):
        self._client = client
        self._cfg = cfg
        self._prompts = prompts

    async def health(self) -> bool:
        return await self._client.health()

    async def _call(
        self, template: PromptTemplate, schema: type[BaseModel], image_b64: str
    ) -> LLMResult:
        return await self._client.complete(
            system=template.system,
            user=template.user,
            image_jpeg_b64=image_b64,
            schema_name=schema.__name__,
            json_schema=schema.model_json_schema(),
        )

    async def read(self, image_bytes: bytes) -> ReadResponse:
        started = time.perf_counter()
        image_b64 = await asyncio.to_thread(
            _preprocess, image_bytes, self._cfg.max_image_pixels
        )
        warnings: list[str] = []
        unavailable = False

        cls_result = await self._call(self._prompts.classify, Classification, image_b64)
        classification = _parse(Classification, cls_result)
        cls_conf = _confidence(cls_result)
        if cls_conf is None:
            unavailable = True
            doc_type_confidence = 0.0
        else:
            doc_type_confidence = cls_conf.get("doc_type", mode="product")

        doc_type = classification.doc_type
        assembled = Assembled(right=None, left=None, brand=None)
        prompt_version = self._prompts.classify.version

        if doc_type != "other":
            if doc_type == "lens_printed":
                template, schema = self._prompts.lens_printed, LensExtraction
            elif doc_type == "lens_handwritten":
                template, schema = self._prompts.lens_handwritten, LensExtraction
            else:
                template, schema = self._prompts.glasses, GlassesExtraction
            prompt_version = f"{self._prompts.classify.version}/{template.version}"

            ext_result = await self._call(template, schema, image_b64)
            extraction = _parse(schema, ext_result)
            ext_conf = _confidence(ext_result)
            if ext_conf is None:
                unavailable = True
                lookup = _zero
            else:
                lookup = ext_conf.get
            if isinstance(extraction, LensExtraction):
                assembled = assemble_lens(extraction, lookup)
            else:
                assert isinstance(extraction, GlassesExtraction)
                assembled = assemble_glasses(extraction, lookup)
            warnings.extend(assembled.warnings)

        if unavailable:
            warnings.append(CONFIDENCE_UNAVAILABLE)

        return ReadResponse(
            request_id=str(uuid.uuid4()),
            doc_type=doc_type,
            doc_type_confidence=doc_type_confidence,
            right=assembled.right,
            left=assembled.left,
            brand=assembled.brand,
            warnings=warnings,
            model=self._cfg.name,
            prompt_version=prompt_version,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
