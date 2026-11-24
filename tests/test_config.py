from pathlib import Path

import pytest

from reader.config import (
    PromptTemplate,
    Settings,
    load_model_config,
    load_prompt,
    load_prompt_set,
)

YAML = """\
active: qwen3-vl-8b
models:
  qwen3-vl-8b:
    name: qwen3-vl-8b-instruct
    base_url: http://vllm:8000/v1
    model: Qwen/Qwen3-VL-8B-Instruct
    max_image_pixels: 1638400
    max_tokens: 1024
    timeout_s: 50
    prompt_dir: prompts/qwen3-vl
    prompts: {classify: classify-v1, lens_printed: lens_printed-v1,
              lens_handwritten: lens_handwritten-v1, glasses: glasses-v1}
  other-model:
    name: other
    base_url: http://x:8000/v1
    model: Other/Model
    max_image_pixels: 1000
    max_tokens: 10
    timeout_s: 5
    prompt_dir: prompts/other
    prompts: {classify: c, lens_printed: p, lens_handwritten: h, glasses: g}
"""


@pytest.fixture
def models_file(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text(YAML)
    return f


def test_load_model_config_resolves_prompt_dir(models_file, tmp_path):
    cfg = load_model_config(Settings(models_file=models_file))
    assert cfg.model == "Qwen/Qwen3-VL-8B-Instruct"
    assert cfg.name == "qwen3-vl-8b-instruct"
    assert cfg.prompt_dir == tmp_path / "prompts/qwen3-vl"


def test_active_override_via_env(models_file, monkeypatch):
    monkeypatch.setenv("READER_ACTIVE_MODEL", "other-model")
    monkeypatch.setenv("READER_MODELS_FILE", str(models_file))
    cfg = load_model_config(Settings())
    assert cfg.model == "Other/Model"


def test_vllm_base_url_env_overrides_yaml(models_file, monkeypatch):
    monkeypatch.setenv("READER_VLLM_BASE_URL", "http://localhost:8000/v1")
    cfg = load_model_config(Settings(models_file=models_file))
    assert cfg.base_url == "http://localhost:8000/v1"


def test_base_url_from_yaml_without_override(models_file, monkeypatch):
    monkeypatch.delenv("READER_VLLM_BASE_URL", raising=False)
    cfg = load_model_config(Settings(models_file=models_file))
    assert cfg.base_url == "http://vllm:8000/v1"


def test_max_upload_bytes_env(monkeypatch):
    monkeypatch.setenv("READER_MAX_UPLOAD_BYTES", "123")
    assert Settings().max_upload_bytes == 123


def test_unknown_active_raises(models_file):
    with pytest.raises(ValueError):
        load_model_config(Settings(models_file=models_file, active_model="nope"))


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_model_config(Settings(models_file=tmp_path / "yok.yaml"))


def test_invalid_yaml_raises(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("active: [unclosed")
    with pytest.raises(ValueError):
        load_model_config(Settings(models_file=f))


def test_bad_structure_raises(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_model_config(Settings(models_file=f))


def test_invalid_model_fields_raise(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("active: a\nmodels:\n  a:\n    name: x\n")
    with pytest.raises(ValueError):
        load_model_config(Settings(models_file=f))


def test_load_prompt_splits_system_user(tmp_path):
    p = tmp_path / "glasses-v1.md"
    p.write_text("SYS\n=== USER ===\nUSR")
    assert load_prompt(p) == PromptTemplate(version="glasses-v1", system="SYS", user="USR")


def test_load_prompt_without_marker_raises(tmp_path):
    p = tmp_path / "x-v1.md"
    p.write_text("only system")
    with pytest.raises(ValueError):
        load_prompt(p)


def test_load_prompt_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "yok.md")


def test_repo_prompts_cover_required_facts():
    ps = load_prompt_set(load_model_config(Settings()))
    for label in ("lens_printed", "lens_handwritten", "glasses", "other"):
        assert label in ps.classify.system
    for p in (ps.lens_printed, ps.lens_handwritten, ps.glasses):
        assert "shared_bc" in p.system and "Temel Eğri" in p.system and "çizgi" in p.system
    assert "near" in ps.glasses.system and "BC" in ps.glasses.system


def test_repo_prompts_never_mention_order_and_versions():
    ps = load_prompt_set(load_model_config(Settings()))
    assert ps.classify.version == "classify-v1"
    assert ps.glasses.version == "glasses-v2"
    for p in (ps.classify, ps.lens_printed, ps.lens_handwritten, ps.glasses):
        assert "sipariş" not in p.user.lower()
        assert p.user


@pytest.mark.parametrize("text", ["=== USER ===\nUSR", "SYS\n=== USER ===\n  "])
def test_load_prompt_empty_section_raises(tmp_path, text):
    p = tmp_path / "x-v1.md"
    p.write_text(text)
    with pytest.raises(ValueError):
        load_prompt(p)


def test_repo_prompts_have_no_grid_hint_and_keep_signs():
    ps = load_prompt_set(load_model_config(Settings()))
    for p in (ps.lens_printed, ps.lens_handwritten, ps.glasses):
        assert "0,25" not in p.system and "basamak" not in p.system
        assert "çevirme" in p.system
    assert "ayrı ayrı yazılmışsa" in ps.lens_printed.system


def test_repo_read_prompts_v2_rules():
    ps = load_prompt_set(load_model_config(Settings()))
    for p in (ps.lens_printed, ps.lens_handwritten, ps.glasses):
        assert "İmza" in p.system          # signatures/strokes elsewhere do not cancel values
        assert "aralık" in p.system        # ranges like 8.4-8.6 kept as written
        assert "o göz null olamaz" in p.system
    assert "aynı değer olsa bile" in ps.lens_printed.system


def test_output_format_defaults_to_full():
    cfg = load_model_config(Settings())
    assert cfg.output_format == "full"
    assert load_prompt_set(cfg).lens_printed.version == "lens_printed-v2"


def test_output_format_env_selects_compact_prompts(monkeypatch):
    monkeypatch.setenv("READER_OUTPUT_FORMAT", "compact")
    cfg = load_model_config(Settings())
    assert cfg.output_format == "compact"
    ps = load_prompt_set(cfg)
    assert ps.lens_printed.version == "lens_printed-v2c"
    assert ps.glasses.version == "glasses-v2c"
    for p in (ps.lens_printed, ps.lens_handwritten, ps.glasses):
        assert "raw" not in p.system
        assert "Belge için tek" in p.system


def test_compact_without_compact_prompts_raises(models_file):
    with pytest.raises(ValueError):
        load_model_config(Settings(models_file=models_file, output_format="compact"))


@pytest.mark.parametrize("key,model,thinking_off", [
    ("qwen3-vl-8b", "Qwen/Qwen3-VL-8B-Instruct", False),
    ("qwen3.5-9b", "Qwen/Qwen3.5-9B", True),
    ("medgemma-1.5-4b", "google/medgemma-1.5-4b-it", False),
])
def test_repo_models_load(key, model, thinking_off):
    cfg = load_model_config(Settings(active_model=key))
    assert cfg.model == model
    load_prompt_set(cfg)
    kwargs = cfg.extra_body.get("chat_template_kwargs", {})
    assert (kwargs.get("enable_thinking") is False) == thinking_off
    cfg_c = load_model_config(Settings(active_model=key, output_format="compact"))
    assert load_prompt_set(cfg_c).lens_printed.version.endswith("c")


def test_extra_body_defaults_empty(models_file):
    assert load_model_config(Settings(models_file=models_file)).extra_body == {}


def test_validation_and_image_overrides(monkeypatch):
    monkeypatch.setenv("READER_VALIDATION", "false")
    monkeypatch.setenv("READER_MAX_IMAGE_PIXELS", "3240000")
    cfg = load_model_config(Settings())
    assert cfg.validation is False and cfg.max_image_pixels == 3240000
    monkeypatch.delenv("READER_VALIDATION"); monkeypatch.delenv("READER_MAX_IMAGE_PIXELS")
    cfg = load_model_config(Settings())
    assert cfg.validation is True and cfg.max_image_pixels == 1638400


@pytest.mark.parametrize("fmt,suffix", [("full", "-min"), ("compact", "-minc")])
def test_minimal_prompt_variant(monkeypatch, fmt, suffix):
    monkeypatch.setenv("READER_PROMPT_VARIANT", "min")
    monkeypatch.setenv("READER_OUTPUT_FORMAT", fmt)
    ps = load_prompt_set(load_model_config(Settings()))
    assert ps.classify.version == "classify-v1"
    for name in ("lens_printed", "lens_handwritten", "glasses"):
        tpl = getattr(ps, name)
        assert tpl.version == f"{name}{suffix}"
        assert len(tpl.system) < 1500                  # gerçekten kısa
        assert "Tahmin etme" in tpl.system and "sipariş" not in tpl.system.lower()
    if fmt == "compact":
        assert "raw" not in ps.lens_printed.system
