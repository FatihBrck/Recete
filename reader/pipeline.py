"""Two-stage reader pipeline: classify, then extract with a type-specific prompt."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from reader.assemble import Assembled, assemble_glasses, assemble_lens
from reader.compact import (
    GlassesExtractionCompact,
    LensExtractionCompact,
    full_path,
    to_full,
)
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
NO_VALUES_READ = "no_values_read"


def _ms_since(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


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
        stages = {"preprocess": _ms_since(started)}
        warnings: list[str] = []
        unavailable = False

        t = time.perf_counter()
        cls_result = await self._call(self._prompts.classify, Classification, image_b64)
        stages["classify"] = _ms_since(t)
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
            compact = self._cfg.output_format == "compact"
            is_glasses = doc_type == "glasses"
            if is_glasses:
                template = self._prompts.glasses
                schema = GlassesExtractionCompact if compact else GlassesExtraction
            else:
                template = getattr(self._prompts, doc_type)
                schema = LensExtractionCompact if compact else LensExtraction
            prompt_version = f"{self._prompts.classify.version}/{template.version}"

            t = time.perf_counter()
            ext_result = await self._call(template, schema, image_b64)
            stages["extract"] = _ms_since(t)
            extraction = _parse(schema, ext_result)
            if compact:
                extraction = to_full(extraction)
            ext_conf = _confidence(ext_result)
            if ext_conf is None:
                unavailable = True
                lookup = _zero
            elif compact:
                lookup = lambda path, _c=ext_conf: _c.get(full_path(path))  # noqa: E731
            else:
                lookup = ext_conf.get
            if is_glasses:
                assembled = assemble_glasses(extraction, lookup)
            else:
                assembled = assemble_lens(extraction, lookup)
            warnings.extend(assembled.warnings)
            if assembled.right is None and assembled.left is None:
                # A lens/glasses prescription with nothing read must be visibly flagged.
                warnings.append(NO_VALUES_READ)

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
            latency_ms=_ms_since(started),
            stage_latency_ms=stages,
        )
