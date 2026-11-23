"""Model configuration and versioned prompt loading."""
from pathlib import Path

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="READER_")

    models_file: Path = Path("config/models.yaml")
    active_model: str | None = None
    vllm_base_url: str | None = None  # READER_VLLM_BASE_URL overrides the YAML base_url
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
    names = cfg.prompts
    return PromptSet(
        **{
            field: load_prompt(cfg.prompt_dir / f"{getattr(names, field)}.md")
            for field in PromptNames.model_fields
        }
    )
