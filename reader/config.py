"""Model configuration and versioned prompt loading."""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

USER_MARKER = "=== USER ==="


class PromptNames(BaseModel):
    classify: str
    lens_printed: str
    lens_handwritten: str
    glasses: str


class PromptTemplate(BaseModel):
    version: str
    system: str
    user: str


class PromptSet(BaseModel):
    classify: PromptTemplate
    lens_printed: PromptTemplate
    lens_handwritten: PromptTemplate
    glasses: PromptTemplate


class ModelConfig(BaseModel):
    name: str  # written to the response `model` field
    base_url: str
    model: str  # served model name in vLLM
    max_image_pixels: int
    max_tokens: int
    timeout_s: float
    prompt_dir: Path
    prompts: PromptNames
    output_format: Literal["full", "compact"] = "full"
    compact_prompts: PromptNames | None = None  # required when output_format == "compact"
    # Extra request fields sent to the OpenAI-compatible API, e.g.
    # {"chat_template_kwargs": {"enable_thinking": false}} for models that think by default.
    extra_body: dict = {}
    validation: bool = True  # False: rules do not lower confidence (test only, never production)
    prompt_variant: Literal["default", "min"] = "default"  # "min": short *-min / *-minc prompts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="READER_")

    models_file: Path = Path("config/models.yaml")
    active_model: str | None = None
    vllm_base_url: str | None = None  # READER_VLLM_BASE_URL overrides the YAML base_url
    output_format: Literal["full", "compact"] | None = None  # READER_OUTPUT_FORMAT overrides the YAML
    validation: bool | None = None  # READER_VALIDATION=false: test only
    prompt_variant: Literal["default", "min"] | None = None  # READER_PROMPT_VARIANT
    max_image_pixels: int | None = None  # READER_MAX_IMAGE_PIXELS
    max_upload_bytes: int = 15_000_000


def load_model_config(settings: Settings) -> ModelConfig:
    path = settings.models_file
    if not path.is_file():
        raise FileNotFoundError(f"models file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        raise ValueError(f"{path}: expected a mapping with a 'models' mapping")

    key = settings.active_model or data.get("active")
    if not isinstance(key, str) or not key:
        raise ValueError(f"{path}: no active model (set 'active' or READER_ACTIVE_MODEL)")
    entry = data["models"].get(key)
    if entry is None:
        known = ", ".join(sorted(map(str, data["models"])))
        raise ValueError(f"unknown model '{key}' in {path} (known: {known})")
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: model '{key}' must be a mapping")
    try:
        cfg = ModelConfig(**entry)
    except ValidationError as exc:
        raise ValueError(f"{path}: invalid model '{key}': {exc}") from exc
    if settings.output_format:
        cfg = cfg.model_copy(update={"output_format": settings.output_format})
    if settings.validation is not None:
        cfg = cfg.model_copy(update={"validation": settings.validation})
    if settings.prompt_variant:
        cfg = cfg.model_copy(update={"prompt_variant": settings.prompt_variant})
    if settings.max_image_pixels:
        cfg = cfg.model_copy(update={"max_image_pixels": settings.max_image_pixels})
    if cfg.output_format == "compact" and cfg.compact_prompts is None:
        raise ValueError(f"{path}: model '{key}' has output_format compact but no compact_prompts")
    if settings.vllm_base_url:
        cfg = cfg.model_copy(update={"base_url": settings.vllm_base_url})
    if not cfg.prompt_dir.is_absolute():
        cfg = cfg.model_copy(update={"prompt_dir": path.parent / cfg.prompt_dir})
    return cfg


def load_prompt(path: Path) -> PromptTemplate:
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    text = path.read_text(encoding="utf-8")
    system, sep, user = text.partition(USER_MARKER)
    if not sep:
        raise ValueError(f"{path}: missing '{USER_MARKER}' separator")
    system, user = system.strip(), user.strip()
    if not system or not user:
        raise ValueError(f"{path}: system and user sections must both be non-empty")
    return PromptTemplate(version=path.stem, system=system, user=user)


def load_prompt_set(cfg: ModelConfig) -> PromptSet:
    names = cfg.compact_prompts if cfg.output_format == "compact" else cfg.prompts
    if names is None:
        raise ValueError("output_format is compact but compact_prompts is not configured")
    if cfg.prompt_variant == "min":
        suffix = "-minc" if cfg.output_format == "compact" else "-min"
        names = PromptNames(
            classify=names.classify,
            lens_printed=f"lens_printed{suffix}",
            lens_handwritten=f"lens_handwritten{suffix}",
            glasses=f"glasses{suffix}",
        )
    return PromptSet(
        **{
            field: load_prompt(cfg.prompt_dir / f"{getattr(names, field)}.md")
            for field in PromptNames.model_fields
        }
    )
