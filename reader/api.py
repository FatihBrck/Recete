"""HTTP API. Logs never contain prescription values, raw texts or brand text."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse

from reader.config import Settings, load_model_config, load_prompt_set
from reader.llm_client import OpenAICompatClient, VLMError, VLMTimeout
from reader.pipeline import ReaderPipeline
from reader.preprocess import UnsupportedImageError
from reader.schemas import ReadResponse

_EYE_FIELDS = ("sph", "cyl", "axis", "add", "bc", "dia", "color")
_NEAR_FIELDS = ("sph", "cyl", "axis")

_logging_configured = False


def configure_logging() -> None:
    """Configure structlog once (JSON lines to stdout)."""
    global _logging_configured
    if _logging_configured:
        return
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,  # keep capture_logs() working in tests
    )
    _logging_configured = True


def confidence_map(resp: ReadResponse) -> dict[str, float]:
    """Output path -> confidence for every non-null field. Never any values."""
    out: dict[str, float] = {}
    for side in ("right", "left"):
        eye = getattr(resp, side)
        if eye is None:
            continue
        for name in _EYE_FIELDS:
            fv = getattr(eye, name)
            if fv is not None:
                out[f"{side}.{name}"] = fv.confidence
        if eye.near is not None:
            for name in _NEAR_FIELDS:
                fv = getattr(eye.near, name)
                if fv is not None:
                    out[f"{side}.near.{name}"] = fv.confidence
    if resp.brand is not None:
        out["brand"] = resp.brand.confidence
    return out


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def create_app(
    pipeline: ReaderPipeline | None = None, settings: Settings | None = None
) -> FastAPI:
    configure_logging()
    settings = settings or Settings()
    owned_client: OpenAICompatClient | None = None
    if pipeline is None:
        cfg = load_model_config(settings)
        owned_client = OpenAICompatClient.from_config(cfg)
        pipeline = ReaderPipeline(owned_client, cfg, load_prompt_set(cfg))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if owned_client is not None:
                await owned_client.aclose()

    app = FastAPI(title="recete-okuyucu", lifespan=lifespan)
    log = structlog.get_logger()
    pipe = pipeline

    @app.post("/v1/read", response_model=ReadResponse)
    async def read(file: UploadFile):
        request_id = str(uuid.uuid4())
        started = time.perf_counter()

        def failed(status: int, code: str, **extra) -> JSONResponse:
            log.error(
                "read_failed",
                request_id=request_id,
                error=code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                **extra,
            )
            return _error(status, code)

        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            return failed(413, "too_large")
        try:
            resp = await pipe.read(data)
        except UnsupportedImageError:
            return failed(415, "unsupported_image")
        except VLMTimeout:
            return failed(504, "model_timeout")
        except VLMError:
            return failed(502, "model_error")
        except Exception as exc:  # message may contain data: log the type only
            return failed(500, "internal_error", exception_type=type(exc).__name__)

        resp = resp.model_copy(update={"request_id": request_id})
        log.info(
            "read_completed",
            request_id=request_id,
            doc_type=resp.doc_type,
            doc_type_confidence=resp.doc_type_confidence,
            confidences=confidence_map(resp),
            warnings=resp.warnings,
            model=resp.model,
            prompt_version=resp.prompt_version,
            latency_ms=resp.latency_ms,
        )
        return resp

    @app.get("/health")
    async def health():
        if await pipe.health():
            return {"status": "ok", "model": pipe._cfg.name}
        return JSONResponse({"status": "unavailable"}, status_code=503)

    return app
