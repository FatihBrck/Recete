# Reçete Okuyucu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reçete görselini alıp türünü (4 sınıf) ve sağ/sol göz değerlerini standart sayı formatında, alan bazında logprob'dan hesaplanmış güven puanıyla JSON döndüren HTTP servisi ve onu ölçen değerlendirme aracı.

**Architecture:** FastAPI servisi (`reader`, CPU) görseli ön işler, vLLM'de (GPU) çalışan VLM'e OpenAI uyumlu API ile iki çağrı yapar: (1) JSON şemasıyla sınıflandırma, (2) türe özel JSON şemasıyla okuma. Model yalnızca `raw` metin döndürür; normalizasyon, format kontrolü, BC/DIA paylaştırma ve token-logprob → alan güveni eşleştirmesi saf Python modüllerinde yapılır ve GPU'suz test edilir.

**Tech Stack:** Python ≥3.11, FastAPI, Pydantic v2, pydantic-settings, `openai` SDK (AsyncOpenAI), Pillow + pillow-heif, PyYAML, structlog, Typer, pytest + pytest-asyncio + respx; vLLM (`vllm/vllm-openai`), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-23-recete-okuyucu-design.md`

## Global Constraints

- Python ≥ 3.11; Pydantic v2. vLLM imajı **tam bir etiketle sabitlenir** (≥ `v0.11.0`, Qwen3-VL desteği); `latest` kullanılmaz.
- Okuyucu sipariş verisi almaz: `/v1/read` yalnızca tek bir dosya alanı (`file`) kabul eder, başka form alanı tanımlanmaz.
- Eksik/okunamayan değer → `null`. Kural dışı değer düzeltilmez; `raw` korunur, güven düşer, uyarı eklenir. CYL işareti çevrilmez.
- Hata durumunda kısmi sonuç yok: model erişilemez / zaman aşımı / parse hatası → 5xx.
- Alan değerleri ve `raw` metinleri, marka metni loga **yazılmaz**. Log: request_id, doc_type, güven puanları, model, prompt_version, latency_ms, uyarılar.
- Doc type değerleri tam olarak: `lens_printed`, `lens_handwritten`, `glasses`, `other`. Alan adları: `sph`, `cyl`, `axis`, `add`, `bc`, `dia`, `color`, `near`.
- Sıcaklık 0, `logprobs=True`, structured output = OpenAI standardı `response_format: {"type": "json_schema", ...}` (vLLM'e özel parametre kullanılmaz → model/sunucu bağımsız).
- İstek başına < 60 sn hedef: VLM istemci zaman aşımı 50 sn (config), `max_retries=0`.
- Desteklenen girdiler: JPEG, PNG, WEBP, HEIC. PDF ve diğerleri → 415. Maksimum yükleme 15 MB → 413.
- Güven puanları her zaman [0, 1].
- Uyarılar `"<yol>: <kod>"` biçiminde, kodlar İngilizce snake_case (loglanabilir, değer içermez): `sign_missing_assumed_plus`, `unparseable`, `not_integer`, `out_of_range`, `not_quarter_step`, `cyl_without_axis`, `axis_without_cyl`, `shared_conflict`, `confidence_unavailable`.
- Format kontrolü cezası: güven × `0.2`, alan başına **bir kez** (birden fazla ihlal katlanmaz).

## Review Focus

1. **Model CYL ve AKS'ı tek `raw`'a koyar** (`"-0,75 x 80"` cyl alanında) → değer `null`, güven 0, `unparseable`; asla yanlış sayı üretmez. Test: Task 2 `test_combined_cyl_axis_notation_is_unparseable`.
2. **Model çıktısında ASCII olmayan karakter** (Unicode eksi `−`, `ş`) byte parçası token'lara bölünür → hizalama çökmez, eksi işaretinin olasılığı alan güvenine yine katılır. Test: Task 4 `test_byte_fragment_tokens_get_span_of_skipped_chars`.
3. **Model hem göz bazında BC hem `shared_bc` döndürür** → göz bazındaki kazanır, `shared_conflict` uyarısı. Test: Task 8 `test_per_eye_and_shared_conflict`.
4. **PDF, boş dosya, rastgele bayt veya 15 MB üstü yükleme** → 415/413, 500 değil. Test: Task 10 `test_pdf_returns_415`, `test_empty_file_returns_415`, `test_too_large_returns_413`.
5. **12 MP telefon fotoğrafı / EXIF ile döndürülmüş fotoğraf** → piksel sınırına küçültülür, doğru yöne çevrilir; küçük görsel büyütülmez. Test: Task 5 `test_exif_rotation_applied`, `test_fit_max_pixels_downscales`, `test_no_upscale`.

---

## Dosya yapısı

```
pyproject.toml
reader/
  __init__.py
  schemas.py      # LLM çıktı şemaları + API yanıt modelleri
  normalize.py    # raw → value
  validate.py     # format kuralları
  confidence.py   # JSON yaprak span'leri + token hizalama + olasılık
  preprocess.py   # görsel yükleme/küçültme/kodlama
  config.py       # Settings, ModelConfig, prompt yükleme
  llm_client.py   # VLMClient protokolü + OpenAICompatClient + hatalar
  assemble.py     # LLM çıktısı + güven → EyeResult'lar (saf)
  pipeline.py     # akış orkestrasyonu
  api.py          # FastAPI + loglama
config/
  models.yaml
  prompts/qwen3-vl/{classify,lens_printed,lens_handwritten,glasses}-v1.md
eval/
  __init__.py
  manifest.py  metrics.py  run_eval.py
tests/
  fakes.py  test_*.py
  integration/test_golden.py
  fixtures/golden/{a1_handwritten.webp,a2_printed.webp}
Dockerfile  docker-compose.yml  README.md
```

Not (spec'ten sapmalar, tasarımcı kararı): prompt'lar `reader/prompts/` yerine `config/prompts/` altında (model konfigürasyonuyla birlikte değişirler); değerlendirmede pandas yerine düz Python (metrikler basit); `prompt_version` iki aşamayı da gösterir: `"classify-v1/glasses-v1"` (`other` için yalnızca `"classify-v1"`).

---

### Task 1: Proje iskeleti ve şemalar

**Files:**
- Create: `pyproject.toml`, `reader/__init__.py`, `reader/schemas.py`, `.gitignore`
- Test: `tests/test_schemas.py`

**Interfaces:**
- Produces (`reader/schemas.py`):
  - `DocType = Literal["lens_printed", "lens_handwritten", "glasses", "other"]`, `DOC_TYPES: tuple[str, ...]` (aynı sırada)
  - LLM çıktı modelleri (tüm alanlar **zorunlu**, `X | None` tipinde, varsayılan yok):
    - `RawField(raw: str, handwritten: bool)`
    - `RawNear(sph, cyl, axis: RawField | None)`
    - `LensEyeRaw(sph, cyl, axis, add, bc, dia, color: RawField | None)`
    - `GlassesEyeRaw(sph, cyl, axis, add, bc, dia: RawField | None, near: RawNear | None)`
    - `LensExtraction(right: LensEyeRaw | None, left: LensEyeRaw | None, shared_bc: RawField | None, shared_dia: RawField | None, brand: str | None)`
    - `GlassesExtraction(right: GlassesEyeRaw | None, left: GlassesEyeRaw | None, shared_bc: RawField | None, shared_dia: RawField | None)`
    - `Classification(doc_type: DocType)`
  - API modelleri:
    - `FieldValue(value: float | int | str | None, raw: str, confidence: float [0..1], handwritten: bool)`
    - `SharedFieldValue(FieldValue)` + `shared: bool`
    - `NearValues(sph, cyl, axis: FieldValue | None)`
    - `EyeResult(sph, cyl, axis, add: FieldValue | None; bc, dia: SharedFieldValue | None; color: FieldValue | None; near: NearValues | None)`
    - `BrandValue(raw: str, confidence: float [0..1])`
    - `ReadResponse(request_id: str, doc_type: DocType, doc_type_confidence: float [0..1], right: EyeResult | None, left: EyeResult | None, brand: BrandValue | None, warnings: list[str], model: str, prompt_version: str, latency_ms: int)`

- [ ] **Step 1: Repo ve paket iskeleti**

`/home/user` içinde `git init`. `pyproject.toml`: paket `reader` + `eval`; bağımlılıklar `fastapi`, `uvicorn[standard]`, `python-multipart`, `pydantic>=2`, `pydantic-settings`, `openai>=1.40`, `pillow`, `pillow-heif`, `pyyaml`, `structlog`, `typer`, `httpx`; `dev` extra: `pytest`, `pytest-asyncio`, `respx`. `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`, `markers = ["integration: needs a running vLLM (READER_VLLM_URL)"]`, `addopts = "-m 'not integration'"`. `.gitignore`: `.venv/`, `__pycache__/`, `reports/`.

Run: `uv venv && uv pip install -e ".[dev]"` → başarılı.

- [ ] **Step 2: Failing testleri yaz**

```python
def test_shared_only_on_bc_dia_and_nulls_kept():
    eye = EyeResult(
        sph=FieldValue(value=-2.5, raw="-2,5", confidence=0.9, handwritten=False),
        cyl=None, axis=None, add=None,
        bc=SharedFieldValue(value=8.6, raw="8.6", confidence=0.8, handwritten=True, shared=True),
        dia=None, color=None, near=None)
    d = eye.model_dump(mode="json")
    assert "shared" not in d["sph"]
    assert d["bc"]["shared"] is True
    assert "add" in d and d["add"] is None

def test_confidence_bounds():
    with pytest.raises(ValidationError):
        FieldValue(value=1.0, raw="1", confidence=1.2, handwritten=False)

def test_classification_schema_enum():
    assert Classification.model_json_schema()["properties"]["doc_type"]["enum"] == list(DOC_TYPES)

def test_llm_schemas_require_all_keys():
    for cls in (LensExtraction, GlassesExtraction, LensEyeRaw, GlassesEyeRaw):
        s = cls.model_json_schema()
        assert set(s["required"]) == set(s["properties"])
    with pytest.raises(ValidationError):
        LensExtraction.model_validate_json("{}")

def test_glasses_eye_has_near_not_color():
    props = GlassesEyeRaw.model_json_schema()["properties"]
    assert "near" in props and "color" not in props
```

- [ ] **Step 3:** Run `pytest tests/test_schemas.py -v` → FAIL (import error).
- [ ] **Step 4:** `reader/schemas.py`'yi Interfaces'teki gibi yaz (`Field(ge=0, le=1)` ile güven sınırları).
- [ ] **Step 5:** Run `pytest tests/test_schemas.py -v` → PASS.
- [ ] **Step 6: Commit** (spec ve plan dahil)

```bash
git add pyproject.toml .gitignore reader/ tests/test_schemas.py docs/
git commit -m "feat: project skeleton and schemas"
```

---

### Task 2: Normalizasyon (raw → value)

**Files:**
- Create: `reader/normalize.py`
- Test: `tests/test_normalize.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Normalized: value: Decimal | int | str | None; warnings: tuple[str, ...] = (); penalize: bool = False; unparseable: bool = False`
  - `normalize_power(raw: str, *, field: Literal["sph", "cyl", "add"]) -> Normalized`
  - `normalize_axis(raw: str) -> Normalized`
  - `normalize_mm(raw: str) -> Normalized` (BC, DIA)
  - `normalize_color(raw: str) -> Normalized`

Kurallar (spec 5.6): `,`→`.`; `−` `–` `—` → `-`; tüm boşluklar silinir; sondaki `D`, `dpt`, `dioptri`, `mm`, `°`, `º` atılır (büyük/küçük harf duyarsız). Güç regex'i: `^([+-])?(\d{0,2})(?:\.(\d{1,2}))?$` (en az bir rakam). `plano`/`pl`/`plan` → `Decimal("0.00")`. Güç değerleri `Decimal("0.01")`'e quantize edilir (`-2.5` → `-2.50`). İşaretsiz ve sıfır olmayan SPH/CYL → `+`, `penalize=True`, `sign_missing_assumed_plus`; ADD ve sıfır muaf. Sayıya çevrilemeyen → `value=None, unparseable=True, warnings=("unparseable",)`. AKS tam sayı değilse → `value=None, unparseable=True, warnings=("not_integer",)`. Renk: `strip().lower()`.

- [ ] **Step 1: Failing testleri yaz**

```python
@pytest.mark.parametrize("raw,expected", [
    ("-2,5", "-2.50"), ("−3,00", "-3.00"), ("+1.25", "1.25"), ("-.75", "-0.75"),
    ("Plano", "0.00"), ("PL", "0.00"), ("-2.25 D", "-2.25"), (" - 0,75 ", "-0.75"), ("0", "0.00")])
def test_power_parses(raw, expected):
    r = normalize_power(raw, field="sph")
    assert r.value == Decimal(expected) and not r.penalize and r.warnings == ()

def test_unsigned_sph_assumes_plus_and_penalizes():
    r = normalize_power("2.25", field="sph")
    assert r.value == Decimal("2.25") and r.penalize
    assert r.warnings == ("sign_missing_assumed_plus",)

def test_unsigned_add_not_penalized():
    r = normalize_power("2.00", field="add")
    assert r.value == Decimal("2.00") and not r.penalize and r.warnings == ()

def test_combined_cyl_axis_notation_is_unparseable():
    r = normalize_power("-0,75 x 80", field="cyl")
    assert r.value is None and r.unparseable and r.warnings == ("unparseable",)

@pytest.mark.parametrize("raw,expected", [("80", 80), ("180°", 180), (" 5 ", 5)])
def test_axis_parses(raw, expected):
    assert normalize_axis(raw).value == expected

def test_axis_non_integer_not_rounded():
    r = normalize_axis("175.5")
    assert r.value is None and r.unparseable and r.warnings == ("not_integer",)

def test_mm():
    assert normalize_mm("8,6").value == Decimal("8.6")
    assert normalize_mm("14.0mm").value == Decimal("14.0")
    assert normalize_mm("abc").unparseable

def test_color():
    assert normalize_color("  Mavi ").value == "mavi"
```

- [ ] **Step 2:** Run `pytest tests/test_normalize.py -v` → FAIL.
- [ ] **Step 3:** `reader/normalize.py`'yi yaz.
- [ ] **Step 4:** Run `pytest tests/test_normalize.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add reader/normalize.py tests/test_normalize.py && git commit -m "feat: raw value normalization"`

---

### Task 3: Format kontrolleri

**Files:**
- Create: `reader/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Produces:
  - `FORMAT_PENALTY: float = 0.2`
  - `@dataclass(frozen=True) class Rule: min: Decimal; max: Decimal; step: Decimal | None; integer: bool = False`
  - `RULES: dict[str, Rule]` — spec 5.7: `sph` −30.00…+30.00 adım 0.25; `cyl` −10.00…+10.00 adım 0.25; `axis` 0…180 tam sayı; `add` 0.50…4.00 adım 0.25; `bc` 7.0…10.0; `dia` 12.0…16.0
  - `check_value(field: str, value: Decimal | int) -> tuple[str, ...]` — kodlar `out_of_range`, `not_quarter_step` (sınırlar dahil)
  - `check_cyl_axis(cyl: Decimal | None, axis: int | None) -> str | None` — `cyl` sıfır değil ve `axis` yok → `"cyl_without_axis"`; `axis` var ve `cyl` yok → `"axis_without_cyl"`

- [ ] **Step 1: Failing testleri yaz**

```python
def test_quarter_step():
    assert check_value("sph", Decimal("-2.29")) == ("not_quarter_step",)
    assert check_value("sph", Decimal("-2.25")) == ()

def test_ranges():
    assert check_value("axis", 190) == ("out_of_range",)
    assert check_value("axis", 0) == () and check_value("axis", 180) == ()
    assert check_value("bc", Decimal("6.5")) == ("out_of_range",)
    assert check_value("add", Decimal("0.25")) == ("out_of_range",)
    assert check_value("dia", Decimal("14.2")) == ()
    assert set(check_value("sph", Decimal("31.10"))) == {"out_of_range", "not_quarter_step"}

def test_cyl_axis_pair():
    assert check_cyl_axis(Decimal("-0.75"), None) == "cyl_without_axis"
    assert check_cyl_axis(None, 90) == "axis_without_cyl"
    assert check_cyl_axis(Decimal("0.00"), None) is None
    assert check_cyl_axis(Decimal("-0.75"), 80) is None
```

- [ ] **Step 2:** Run `pytest tests/test_validate.py -v` → FAIL.
- [ ] **Step 3:** `reader/validate.py`'yi yaz (adım kontrolü: `value % step == 0` Decimal ile).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: format rules"` (ilgili dosyalar)

---

### Task 4: Güven puanı (token logprob → alan)

**Files:**
- Create: `reader/confidence.py`
- Test: `tests/test_confidence.py`

**Interfaces:**
- Consumes: `TokenLogprob` — bu task'ta `reader/llm_client.py` içinde tanımlanır (Task 7 dosyayı genişletir): `@dataclass(frozen=True) class TokenLogprob: token: str; logprob: float`
- Produces:
  - `leaf_spans(text: str) -> dict[str, tuple[int, int]]` — geçerli JSON metnindeki her yaprak (string/number/bool/null) için noktalı yol → karakter aralığı `[start, end)`. String'lerde tırnaklar hariç. Yollar: `"doc_type"`, `"right.sph.raw"`, `"right.near.sph.raw"`, `"shared_bc.raw"`, `"brand"`. (JSON çıktısı structured output ile garanti geçerli; küçük bir recursive-descent tarayıcı yeterli, kaçış dizilerini atlar.)
  - `class ConfidenceMap: __init__(self, text: str, tokens: list[TokenLogprob]); available: bool; get(self, path: str, mode: Literal["min", "product"] = "min") -> float` — yol yoksa, span boşsa veya `available=False` ise `0.0`.

Hizalama algoritması (imzanın belirlemediği kısım):

```
pos = 0; pending = []           # konumu bilinmeyen token'lar
for tok in tokens:
    if text.startswith(tok.token, pos):
        if pending:              # önceki eşleşmeyenler [pending_start, pos) aralığını paylaşır
            assign all pending -> (pending_start, pos); pending = []
        offsets[tok] = (pos, pos+len(tok.token)); pos += len(tok.token)
    else:
        if not pending: pending_start = pos
        pending.append(tok)
        # sonraki token eşleşmeden önce text içinde ileri senkronizasyon:
        # bir sonraki token için text.find(next.token, pos, pos + 4 + len(next.token));
        # bulunursa pos = bulunan konum (atlanan karakterler pending'e atanır).
pending sonda kalırsa -> (pending_start, len(text))
Senkronizasyon başarısızsa veya tokens boşsa -> available = False
```

Bir token span ile kesişiyorsa (`ts < e and te > s`) o alana katılır. `min` → kesişen token olasılıklarının (`exp(logprob)`) minimumu; `product` → çarpımı.

- [ ] **Step 1: Failing testleri yaz**

```python
def toks(*pairs):
    return [TokenLogprob(t, math.log(p)) for t, p in pairs]

def test_leaf_spans_nested():
    text = '{"right":{"sph":{"raw":"-2,5","handwritten":true}},"brand":null}'
    s = leaf_spans(text)
    assert text[slice(*s["right.sph.raw"])] == "-2,5"
    assert text[slice(*s["right.sph.handwritten"])] == "true"
    assert "brand" in s

def test_min_and_product():
    text = '{"raw":"-2,5"}'
    cm = ConfidenceMap(text, toks(('{"', .99), ("raw", .99), ('":"', .99), ("-", .6),
                                  ("2", .95), (",", .9), ("5", .8), ('"}', .99)))
    assert cm.get("raw") == pytest.approx(.6)
    assert cm.get("raw", mode="product") == pytest.approx(.6 * .95 * .9 * .8)

def test_byte_fragment_tokens_get_span_of_skipped_chars():
    text = '{"raw":"−2"}'
    cm = ConfidenceMap(text, toks(('{"', .99), ("raw", .99), ('":"', .99),
                                  ("�", .5), ("�", .7), ("2", .95), ('"}', .99)))
    assert cm.available
    assert cm.get("raw") == pytest.approx(.5)

def test_unalignable_tokens_mark_unavailable():
    cm = ConfidenceMap('{"raw":"12"}', toks(("garbage", .9)))
    assert not cm.available and cm.get("raw") == 0.0

def test_empty_tokens_unavailable_and_missing_path_zero():
    assert not ConfidenceMap('{"a":"1"}', []).available
    cm = ConfidenceMap('{"a":"1"}', toks(('{"a":"', .9), ("1", .9), ('"}', .9)))
    assert cm.get("nope") == 0.0
```

- [ ] **Step 2:** Run `pytest tests/test_confidence.py -v` → FAIL.
- [ ] **Step 3:** `TokenLogprob`'u `reader/llm_client.py`'ye, kalanını `reader/confidence.py`'ye yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: logprob-based field confidence"`

---

### Task 5: Görsel ön işleme

**Files:**
- Create: `reader/preprocess.py`
- Test: `tests/test_preprocess.py`

**Interfaces:**
- Produces:
  - `class UnsupportedImageError(Exception)`
  - `load_image(data: bytes) -> PIL.Image.Image` — `pillow_heif.register_heif_opener()`; yalnızca formatı `JPEG`, `PNG`, `WEBP`, `HEIF` olanları kabul eder, diğerleri (PDF, boş, bozuk) → `UnsupportedImageError`; `ImageOps.exif_transpose`; RGB'ye çevirir.
  - `fit_max_pixels(img: Image.Image, max_pixels: int) -> Image.Image` — `w*h > max_pixels` ise en-boy oranını koruyarak küçültür (LANCZOS); asla büyütmez.
  - `to_jpeg_b64(img: Image.Image) -> str` — JPEG kalite 90, base64 (önek yok).

- [ ] **Step 1: Failing testleri yaz** — `test_exif_rotation_applied` (200×100 JPEG, EXIF orientation=6 → `load_image(...).size == (100, 200)`), `test_heic_supported` (pillow-heif ile `format="HEIF"` kaydedilmiş görsel yüklenir), `test_rgba_png_becomes_rgb` (`mode == "RGB"`), `test_pdf_rejected` (`b"%PDF-1.4\n..."` → `UnsupportedImageError`), `test_empty_rejected` (`b""`), `test_fit_max_pixels_downscales` (4000×3000, `max_pixels=1_638_400` → `w*h <= 1_638_400` ve `abs(w/h - 4/3) < 0.01`), `test_no_upscale` (500×400 → aynı boyut), `test_jpeg_b64_roundtrip` (decode edilen görselin boyutu aynı).
- [ ] **Step 2:** Run `pytest tests/test_preprocess.py -v` → FAIL.
- [ ] **Step 3:** `reader/preprocess.py`'yi yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: image preprocessing"`

---

### Task 6: Konfigürasyon ve prompt'lar

**Files:**
- Create: `reader/config.py`, `config/models.yaml`, `config/prompts/qwen3-vl/classify-v1.md`, `lens_printed-v1.md`, `lens_handwritten-v1.md`, `glasses-v1.md`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class PromptNames(BaseModel): classify: str; lens_printed: str; lens_handwritten: str; glasses: str`
  - `class PromptTemplate(BaseModel): version: str; system: str; user: str`
  - `class PromptSet(BaseModel): classify, lens_printed, lens_handwritten, glasses: PromptTemplate`
  - `class ModelConfig(BaseModel): name: str; base_url: str; model: str; max_image_pixels: int; max_tokens: int; timeout_s: float; prompt_dir: Path; prompts: PromptNames` (`name` yanıttaki `model` alanına yazılır; `model` vLLM'deki servis adı)
  - `class Settings(BaseSettings)` — `env_prefix="READER_"`; `models_file: Path = Path("config/models.yaml")`; `active_model: str | None = None`; `max_upload_bytes: int = 15_000_000`
  - `load_model_config(settings: Settings) -> ModelConfig` — YAML `{active: str, models: {anahtar: ModelConfig alanları}}`; `settings.active_model` doluysa onu seçer; bilinmeyen anahtar → `ValueError`; `prompt_dir` YAML dosyasının klasörüne göre çözülür.
  - `load_prompt(path: Path) -> PromptTemplate` — `version` = dosya adı gövdesi (`glasses-v1`); içerik `=== USER ===` satırından bölünür (üstü system, altı user, `strip()`).
  - `load_prompt_set(cfg: ModelConfig) -> PromptSet` — `cfg.prompt_dir / f"{name}.md"`.

`config/models.yaml`:

```yaml
active: qwen3-vl-8b
models:
  qwen3-vl-8b:
    name: qwen3-vl-8b-instruct
    base_url: http://vllm:8000/v1
    model: Qwen/Qwen3-VL-8B-Instruct
    max_image_pixels: 1638400   # ~1280x1280
    max_tokens: 1024
    timeout_s: 50
    prompt_dir: prompts/qwen3-vl
    prompts: {classify: classify-v1, lens_printed: lens_printed-v1,
              lens_handwritten: lens_handwritten-v1, glasses: glasses-v1}
```

Prompt içerik gereksinimleri (metin yazımı uygulayıcıya ait; spec 5.3–5.4):
- **classify-v1:** dört etiketin tanımı spec 5.3'teki gibi; "tür mürekkebe değil belgeye göre" kuralı; basılı "KONTAKT LENS REÇETESİ" formu elle doldurulsa da `lens_printed`; gözlük reçetesine elle BC eklenmesi türü değiştirmez.
- **Üç okuma prompt'unda ortak:** sipariş/beklenen değer yoktur, yalnızca görselde yazanı oku; `raw` = yazıldığı gibi (virgül, işaret dahil), tahmin yok, yoksa `null`; Türkçe etiket listesi (spec 5.4); `"-0,75 x 80"` → `cyl.raw="-0,75"`, `axis.raw="80"` (ayrı alanlara böl); boş alan üzerindeki çizgi = değer yok, eksi işareti değildir; göz belirtilmeden tek yazılan BC/DIA → `shared_bc`/`shared_dia`, göz bazındaki `bc`/`dia` `null`; `handwritten` alanının anlamı.
- **lens_printed-v1:** form tablosu yapısı (Sağ Göz / Sol Göz sütunları, Temel Eğri, Tüm Çap, Sferik, Torik Cyl/Aks, Multifokal ADD, Materyal/Marka) → `brand`.
- **lens_handwritten-v1:** düz kâğıt, "Sağ →"/"Sol →" düzeni, "(sferik)", "(silindirik)", "Tüm çap"; el yazısı rakam karışıklıkları (5↔9, 4↔9, 1↔7) için dikkat; marka satırı → `brand`.
- **glasses-v1:** uzak satırı → sph/cyl/axis; yakın satırı → `near`; ADD yalnızca yazılıysa (hesaplama yok); **sayfanın herhangi bir yerine elle eklenmiş "BC: 8.6" (ve varsa DIA) değerini mutlaka ara.**

- [ ] **Step 1: Failing testleri yaz**

```python
def test_load_model_config_resolves_prompt_dir(tmp_path):  # tmp YAML yaz
    cfg = load_model_config(Settings(models_file=tmp_path / "models.yaml"))
    assert cfg.model == "Qwen/Qwen3-VL-8B-Instruct"
    assert cfg.prompt_dir == tmp_path / "prompts/qwen3-vl"

def test_active_override_via_env(tmp_path, monkeypatch): ...  # READER_ACTIVE_MODEL ikinci modeli seçer
def test_unknown_active_raises(tmp_path): ...                 # pytest.raises(ValueError)

def test_load_prompt_splits_system_user(tmp_path):
    p = tmp_path / "glasses-v1.md"; p.write_text("SYS\n=== USER ===\nUSR")
    assert load_prompt(p) == PromptTemplate(version="glasses-v1", system="SYS", user="USR")

def test_repo_prompts_cover_required_facts():
    ps = load_prompt_set(load_model_config(Settings()))
    for label in ("lens_printed", "lens_handwritten", "glasses", "other"):
        assert label in ps.classify.system
    for p in (ps.lens_printed, ps.lens_handwritten, ps.glasses):
        assert "shared_bc" in p.system and "Temel Eğri" in p.system and "çizgi" in p.system
    assert "near" in ps.glasses.system and "BC" in ps.glasses.system
```

- [ ] **Step 2:** Run `pytest tests/test_config.py -v` → FAIL.
- [ ] **Step 3:** `reader/config.py`, `config/models.yaml` ve dört prompt dosyasını yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: model config and v1 prompts"`

---

### Task 7: VLM istemcisi

**Files:**
- Modify: `reader/llm_client.py` (Task 4'teki `TokenLogprob`'a ekle)
- Create: `tests/fakes.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Produces (`reader/llm_client.py`):
  - `@dataclass(frozen=True) class LLMResult: text: str; tokens: list[TokenLogprob]`
  - `class VLMError(Exception)`, `class VLMTimeout(VLMError)`, `class OutputParseError(VLMError)`
  - `class VLMClient(Protocol)`: `async def complete(self, *, system: str, user: str, image_jpeg_b64: str, schema_name: str, json_schema: dict) -> LLMResult`; `async def health(self) -> bool`
  - `class OpenAICompatClient(VLMClient)`: `__init__(self, *, base_url: str, model: str, max_tokens: int, timeout_s: float, api_key: str = "EMPTY")` — `AsyncOpenAI(base_url=..., api_key=..., timeout=timeout_s, max_retries=0)`. İstek: `messages=[{"role":"system","content":system}, {"role":"user","content":[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+b64}}, {"type":"text","text":user}]}]`, `temperature=0`, `logprobs=True`, `max_tokens`, `response_format={"type":"json_schema","json_schema":{"name":schema_name,"schema":json_schema,"strict":True}}`. `choices[0].logprobs.content` → `list[TokenLogprob]` (`token`, `logprob` alanlarından); `None` ise `[]`. `finish_reason == "length"` → `OutputParseError`. `openai.APITimeoutError` → `VLMTimeout`; diğer `openai.APIError`/`APIConnectionError` → `VLMError`. `health()` = `models.list()` başarılıysa `True`, hata → `False`.
  - `classmethod from_config(cfg: ModelConfig) -> OpenAICompatClient`
- Produces (`tests/fakes.py`):
  - `char_tokens(text: str, p: float = 0.99, overrides: dict[str, float] | None = None) -> list[TokenLogprob]` — her karakter bir token; `overrides` alt-dizenin ilk geçtiği yerdeki karakterlere verilen olasılık.
  - `class FakeVLMClient(VLMClient)`: `__init__(self, results: list[LLMResult | Exception], healthy: bool = True)`; `calls: list[dict]` (complete'in kwargs'ı); sıradaki sonucu döndürür / istisnayı fırlatır.

- [ ] **Step 1: Failing testleri yaz** (respx ile `POST {base_url}/chat/completions` taklidi)
  - `test_request_shape` — yakalanan gövdede `body["temperature"] == 0`, `body["logprobs"] is True`, `body["response_format"]["type"] == "json_schema"`, `body["response_format"]["json_schema"]["name"] == "LensExtraction"`, `body["model"] == "Qwen/Qwen3-VL-8B-Instruct"`, kullanıcı içeriğindeki URL `startswith("data:image/jpeg;base64,")`; dönen `LLMResult.text` ve `tokens` taklitteki ile aynı.
  - `test_missing_logprobs_gives_empty_tokens`
  - `test_length_finish_raises_output_parse_error`
  - `test_timeout_raises_vlm_timeout` (`side_effect=httpx.ReadTimeout`) ve `route.call_count == 1` (yeniden deneme yok)
  - `test_server_error_raises_vlm_error` (500)
  - `test_health_true_and_false` (`GET /models` 200 / bağlantı hatası)
- [ ] **Step 2:** Run `pytest tests/test_llm_client.py -v` → FAIL.
- [ ] **Step 3:** `reader/llm_client.py` ve `tests/fakes.py`'yi yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: OpenAI-compatible VLM client"`

---

### Task 8: Montaj (LLM çıktısı → göz sonuçları)

**Files:**
- Create: `reader/assemble.py`
- Test: `tests/test_assemble.py`

**Interfaces:**
- Consumes: `schemas` (Task 1), `normalize` (Task 2), `validate.check_value / check_cyl_axis / FORMAT_PENALTY` (Task 3)
- Produces:
  - `ConfidenceLookup = Callable[[str], float]` (LLM JSON yolu → [0,1])
  - `@dataclass class Assembled: right: EyeResult | None; left: EyeResult | None; brand: BrandValue | None; warnings: list[str]`
  - `assemble_lens(ex: LensExtraction, conf: ConfidenceLookup) -> Assembled`
  - `assemble_glasses(ex: GlassesExtraction, conf: ConfidenceLookup) -> Assembled`

Kurallar:
- Alan: `raw` `None` veya `raw.strip() == ""` → `None`. Normalize et; güven = `conf("<llm yolu>.raw")`. `unparseable` → `value=None`, güven `0.0`. `penalize` veya `check_value` kodu veya çift kontrolü kodu varsa güven × `FORMAT_PENALTY` (bir kez). `value`: `Decimal` → `float`, AKS `int`, renk `str`. Uyarılar `f"{çıktı yolu}: {kod}"` (çıktı yolu `right.sph`, `left.near.axis`, …).
- BC/DIA: göz nesnesi varsa ve gözün kendi `bc`'si yoksa `shared_bc` kopyalanır → `SharedFieldValue(shared=True)`, güven yolu `"shared_bc.raw"`. Gözün kendi değeri varsa `shared=False`; ikisi birden varsa göz bazındaki kalır + `"<göz>.bc: shared_conflict"`. Göz nesnesi `None` ise paylaşılan değer uygulanmaz. DIA aynı.
- CYL/AKS çifti (göz ve `near` için): `cyl_without_axis` → cyl güveni düşer; `axis_without_cyl` → axis güveni düşer.
- Tüm alanları `None` olan göz → `None`; tüm alanları `None` olan `near` → `None`.
- `brand` boş değilse `BrandValue(raw=brand.strip(), confidence=conf("brand"))`.

- [ ] **Step 1: Failing testleri yaz** (yardımcı: `conf = lambda path: 0.9`, ya da sözlükten okuyan ve çağrılan yolları kaydeden bir sahte)

```python
def test_golden_a1_handwritten():   # spec Ek A.1
    ex = LensExtraction(
        right=LensEyeRaw(sph=rf("-3,00"), cyl=rf("-0,75"), axis=rf("80"), add=None, bc=None, dia=None, color=None),
        left=LensEyeRaw(sph=rf("-3,00"), cyl=rf("-0,75"), axis=rf("100"), add=None, bc=None, dia=None, color=None),
        shared_bc=rf("8,6"), shared_dia=rf("14,5"), brand="Acuvue Oasys for Astigmatism")
    a = assemble_lens(ex, lambda p: 0.9)
    assert a.right.sph.value == -3.0 and a.right.cyl.value == -0.75 and a.right.axis.value == 80
    assert a.left.axis.value == 100 and isinstance(a.left.axis.value, int)
    assert a.right.bc.value == 8.6 and a.right.bc.shared and a.left.dia.value == 14.5 and a.left.dia.shared
    assert a.brand.raw == "Acuvue Oasys for Astigmatism" and a.warnings == []

def test_shared_bc_uses_shared_confidence_path(): ...      # conf sahte: {"shared_bc.raw": 0.7} → right.bc.confidence == 0.7
def test_per_eye_bc_not_shared(): ...                       # right.bc.shared is False
def test_per_eye_and_shared_conflict():
    ...; assert "right.bc: shared_conflict" in a.warnings and a.right.bc.value == 8.4
def test_format_violation_penalizes_confidence():
    ...  # left.sph raw "-2.29", conf 0.9
    assert a.left.sph.confidence == pytest.approx(0.18) and "left.sph: not_quarter_step" in a.warnings
def test_penalty_applied_once():   # sph "2.29": işaretsiz + adım dışı
    assert a.right.sph.confidence == pytest.approx(0.18)
def test_unparseable_field_value_none_confidence_zero(): ...
def test_empty_raw_is_null(): ...                           # raw "  " → field None
def test_eye_with_no_fields_is_none(): ...
def test_cyl_without_axis_penalizes_cyl(): ...              # "right.cyl: cyl_without_axis", cyl güveni 0.18
def test_glasses_near_and_shared_bc(): ...                  # near.sph değeri, right/left.bc shared
```

- [ ] **Step 2:** Run `pytest tests/test_assemble.py -v` → FAIL.
- [ ] **Step 3:** `reader/assemble.py`'yi yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: assemble eye results with shared BC/DIA and penalties"`

---

### Task 9: Pipeline

**Files:**
- Create: `reader/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `preprocess` (Task 5), `config.ModelConfig/PromptSet` (Task 6), `VLMClient/LLMResult/OutputParseError` (Task 7), `ConfidenceMap` (Task 4), `assemble_*` (Task 8)
- Produces:
  - `class ReaderPipeline: __init__(self, client: VLMClient, cfg: ModelConfig, prompts: PromptSet)`; `async def read(self, image_bytes: bytes) -> ReadResponse`; `async def health(self) -> bool` (istemciye delege)

Akış: `load_image` → `fit_max_pixels(cfg.max_image_pixels)` → `to_jpeg_b64` → sınıflandırma çağrısı (`schema_name="Classification"`, `Classification.model_json_schema()`, `prompts.classify`) → `Classification.model_validate_json`; Pydantic `ValidationError` veya JSON hatası → `OutputParseError`. `doc_type_confidence = ConfidenceMap(...).get("doc_type", mode="product")`. `other` → `right=left=brand=None`, `prompt_version=classify.version`. Aksi halde: `lens_printed` → `prompts.lens_printed` + `LensExtraction`; `lens_handwritten` → `prompts.lens_handwritten` + `LensExtraction`; `glasses` → `prompts.glasses` + `GlassesExtraction`; parse, `ConfidenceMap(...).get` ile `assemble_*`. Herhangi bir çağrıda `ConfidenceMap.available` False ise `"confidence_unavailable"` uyarısı (yol öneki yok) eklenir. `prompt_version = f"{classify.version}/{extraction.version}"`. `request_id = str(uuid4())`, `model = cfg.name`, `latency_ms` = toplam süre.

- [ ] **Step 1: Failing testleri yaz** (`FakeVLMClient`, `char_tokens`, küçük bir PNG baytı)
  - `test_other_stops_after_classification` — `right is None and left is None`, `len(fake.calls) == 1`, `prompt_version == "classify-v1"`
  - `test_glasses_uses_glasses_prompt_and_schema` — `fake.calls[1]["schema_name"] == "GlassesExtraction"`, `fake.calls[1]["system"] == prompts.glasses.system`, `prompt_version == "classify-v1/glasses-v1"`
  - `test_lens_handwritten_uses_handwritten_prompt_with_lens_schema`
  - `test_doc_type_confidence_is_product_of_label_tokens` — `char_tokens('{"doc_type":"glasses"}', overrides={"glasses": 0.9})` → `doc_type_confidence == pytest.approx(0.9 ** 7)`
  - `test_invalid_json_raises_output_parse_error` — okuma çağrısı `"not json"` döndürür
  - `test_confidence_unavailable_warning` — `tokens=[]` → `"confidence_unavailable" in warnings` ve tüm alan güvenleri `0.0`
  - `test_image_downscaled_before_sending` — 4000×3000 görsel; gönderilen b64 decode edilince `w*h <= cfg.max_image_pixels`
  - `test_response_metadata` — `request_id` UUID, `model == "qwen3-vl-8b-instruct"`, `latency_ms >= 0`
- [ ] **Step 2:** Run `pytest tests/test_pipeline.py -v` → FAIL.
- [ ] **Step 3:** `reader/pipeline.py`'yi yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: two-stage reader pipeline"`

---

### Task 10: HTTP API ve loglama

**Files:**
- Create: `reader/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `ReaderPipeline` (Task 9), `Settings/load_model_config/load_prompt_set` (Task 6), `OpenAICompatClient.from_config` (Task 7), `UnsupportedImageError` (Task 5), `VLMError/VLMTimeout` (Task 7)
- Produces:
  - `create_app(pipeline: ReaderPipeline | None = None, settings: Settings | None = None) -> FastAPI` — `pipeline` verilmezse konfigürasyondan kurar. uvicorn ile `--factory reader.api:create_app`.
  - `POST /v1/read` (multipart, tek alan `file`) → 200 `ReadResponse`
    - boyut > `settings.max_upload_bytes` → 413 `{"error": "too_large"}`
    - `UnsupportedImageError` → 415 `{"error": "unsupported_image"}`
    - `VLMTimeout` → 504 `{"error": "model_timeout"}`; diğer `VLMError` → 502 `{"error": "model_error"}`
  - `GET /health` → 200 `{"status": "ok", "model": cfg.name}` / 503 `{"status": "unavailable"}`
  - structlog JSON (`JSONRenderer`), her istek için bir `read_completed` olayı: `request_id`, `doc_type`, `doc_type_confidence`, `confidences` (`{"right.sph": 0.97, ...}`), `warnings`, `model`, `prompt_version`, `latency_ms`. Hatalarda `read_failed` + `error` kodu. **`value`, `raw`, `brand.raw` asla loglanmaz.**

- [ ] **Step 1: Failing testleri yaz** (`httpx.AsyncClient(transport=ASGITransport(app))`, `FakeVLMClient` ile kurulmuş pipeline)
  - `test_read_returns_response` — 200, `body["doc_type"] == "lens_printed"`, `body["right"]["sph"]["value"] == -2.25`
  - `test_pdf_returns_415`, `test_empty_file_returns_415`
  - `test_too_large_returns_413` — `Settings(max_upload_bytes=10)`
  - `test_vlm_error_returns_502_without_partial_body` — `body == {"error": "model_error"}`
  - `test_timeout_returns_504`
  - `test_health_ok_and_unavailable`
  - `test_logs_contain_no_values` — `structlog.testing.capture_logs()`; sahte yanıtta `raw="-2,29"` ve `brand="Acuvue Oasys"`; `str(logs)` içinde `"-2,29"`, `"-2.29"` ve `"Acuvue"` geçmez; `read_completed` olayında yukarıdaki anahtarların hepsi var.
- [ ] **Step 2:** Run `pytest tests/test_api.py -v` → FAIL.
- [ ] **Step 3:** `reader/api.py`'yi yaz.
- [ ] **Step 4:** Run `pytest -v` (tüm birim testleri) → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: HTTP API with value-free structured logging"`

---

### Task 11: Değerlendirme aracı

**Files:**
- Create: `eval/__init__.py`, `eval/manifest.py`, `eval/metrics.py`, `eval/run_eval.py`
- Test: `tests/test_eval.py`

**Interfaces:**
- Produces:
  - Manifest JSONL (her satır bir reçete; görsel yolu manifest dosyasına göreli):
    `{"id": "r001", "image": "images/r001.jpg", "doc_type": "glasses", "right": {"sph": -2.5, "cyl": -0.75, "axis": 180, "bc": 8.6}, "left": {...}}`
    **Anahtar yoksa o alan puanlanmaz; anahtar `null` ise "reçetede yok" beklenir.** (Siparişte olup reçetede olmayan alanlar — ör. gözlük reçetesinde DIA — manifest'e yazılmamalı.) `near` puanlanmaz.
  - `@dataclass class ManifestRow: id: str; image: Path; doc_type: str; right: dict | None; left: dict | None`; `load_manifest(path: Path) -> list[ManifestRow]`
  - Tahmin JSONL: `{"id": ..., "response": <ReadResponse JSON> | null, "error": str | null}`; `@dataclass class Prediction: id: str; response: dict | None; error: str | None`; `load_predictions(path: Path) -> list[Prediction]`
  - `metrics.py`:
    - `TOLERANCE = {"bc": Decimal("0.2"), "dia": Decimal("0.2")}`
    - `classification(rows, preds) -> tuple[float, dict[tuple[str, str], int]]` — doğruluk, karışıklık `(gerçek, tahmin) → sayı` (hata → tahmin `"error"`)
    - `field_accuracy(rows, preds) -> dict[str, dict[str, float]]` — anahtar `"<doc_type>.<alan>"` ve `"all.<alan>"`; değer `{"n", "exact", "tolerant"}`; `Decimal(str(x))` ile karşılaştır; tolerant yalnızca bc/dia için farklı
    - `calibration(rows, preds, bins=(0, .5, .8, .9, .95, .99, 1.0)) -> list[dict]` — alan düzeyinde `{"bin", "n", "accuracy"}`
    - `auto_approve(rows, preds, max_wrong_share=0.01) -> dict` — belge güveni = `min(doc_type_confidence, puanlanan alanlardaki tahmin güvenleri)`; belge doğru = tür doğru ve puanlanan tüm alanlar doğru (bc/dia toleranslı); hata satırı → güven 0, yanlış. Eşik `t` = aday güvenlerden (sıralı) **en küçüğü** öyle ki `(t ve üstündeki yanlış sayısı) / (toplam yanlış) < max_wrong_share` (yanlış yoksa en küçük güven). Döner: `{"threshold", "auto_approve_rate", "wrong_above", "n_wrong", "n"}`
    - `latency(preds) -> dict` — `{"p50", "p95"}` ms
  - `run_eval.py` (Typer):
    - `run --manifest PATH --url http://localhost:8080 --out PATH` — her görseli sırayla `POST {url}/v1/read`'e gönderir (httpx, 120 sn zaman aşımı), tahmin JSONL yazar; HTTP hatası → `error` = durum kodu/hata.
    - `report --manifest PATH --predictions PATH --out DIR` — `DIR/report.md` (başlıklar: "Sınıflandırma", "Alan doğruluğu", "Kalibrasyon", "Otomatik onay oranı", "Gecikme"; gözlük BC satırı ayrıca vurgulanır) ve `DIR/fields.csv`.
    - `compare --manifest PATH A.jsonl B.jsonl` — iki tahmin dosyası için başlık metriklerini yan yana Markdown tablo olarak yazdırır.

- [ ] **Step 1: Failing testleri yaz**

```python
def test_bc_tolerance():   # truth bc 8.6, pred 8.7
    acc = field_accuracy(rows, preds)
    assert acc["all.bc"]["exact"] == 0.0 and acc["all.bc"]["tolerant"] == 1.0

def test_absent_key_not_scored(): ...       # truth'ta "dia" yok → "all.dia" yok veya n == 0

def test_auto_approve_threshold():
    # (belge güveni, doğru mu): (.99,T) (.97,T) (.95,F) (.90,T) (.50,F)
    r = auto_approve(rows, preds)
    assert r["threshold"] == pytest.approx(.97) and r["auto_approve_rate"] == pytest.approx(.4)
    assert r["wrong_above"] == 0 and r["n_wrong"] == 2

def test_error_rows_count_wrong_and_low_confidence(): ...
def test_classification_confusion(): ...
def test_report_cli_writes_markdown(tmp_path):   # typer.testing.CliRunner
    assert "Otomatik onay oranı" in (tmp_path / "out/report.md").read_text()
def test_run_cli_posts_images(tmp_path): ...     # respx ile /v1/read taklidi; tahmin JSONL satır sayısı == manifest
```

- [ ] **Step 2:** Run `pytest tests/test_eval.py -v` → FAIL.
- [ ] **Step 3:** `eval/` modüllerini yaz.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: evaluation CLI with calibration and auto-approve rate"`

---

### Task 12: Dağıtım, altın örnek entegrasyon testi, README

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `README.md`, `tests/integration/test_golden.py`, `tests/fixtures/golden/a1_handwritten.webp`, `tests/fixtures/golden/a2_printed.webp`

**Interfaces:**
- Consumes: `create_app` (Task 10), `config/models.yaml` (Task 6)

İçerik:
- `Dockerfile`: `python:3.11-slim`, `pip install .`, `config/` kopyalanır, `CMD ["uvicorn", "--factory", "reader.api:create_app", "--host", "0.0.0.0", "--port", "8080"]`.
- `docker-compose.yml`:
  - `vllm`: `vllm/vllm-openai:<sabit etiket ≥ v0.11.0>`; GPU rezervasyonu (`deploy.resources.reservations.devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]`); HF önbellek volume'ü; komut: `--model Qwen/Qwen3-VL-8B-Instruct --dtype bfloat16 --max-model-len 8192 --gpu-memory-utilization 0.92 --max-num-seqs 4 --limit-mm-per-prompt '{"image":1,"video":0}'` (bayrak sözdizimini seçilen etiketin `--help` çıktısıyla doğrula); healthcheck `GET /v1/models`.
  - `reader`: build `.`, port `8080:8080`, `depends_on: vllm (service_healthy)`.
- `README.md` (Türkçe): servis nedir; `docker compose up -d`; `curl -F file=@recete.jpg http://localhost:8080/v1/read`; yanıt örneği (spec 5.5) ve uyarı kodları tablosu; model değiştirme (`models.yaml`'a kayıt + `active` veya `READER_ACTIVE_MODEL`, vLLM `--model`); yeni prompt sürümü ekleme; manifest formatı ve `python -m eval.run_eval run/report/compare` kullanımı; test seti kuralı (spec 5.10: doğrulanmış reçetelerden tür başına ~100'lük test seti ayrılır ve prompt geliştirmede kullanılmaz, iterasyon ayrı küçük bir geliştirme setiyle yapılır); KVKK notu (görsel saklanmaz, değerler loglanmaz).
- Altın görseller: kullanıcının paylaştığı iki örnek, kişisel bilgiler zaten karartılmış halde, `/tmp/claude-0/-home-user/fd071800-f6b8-5536-99b4-ea5c20bc0c85/images/1.webp` → `a1_handwritten.webp`, `.../2.webp` → `a2_printed.webp`.

- [ ] **Step 1: Entegrasyon testini yaz** — `@pytest.mark.integration`; `READER_VLLM_URL` yoksa `pytest.skip`. Gerçek `OpenAICompatClient` (base_url = env) + repo konfigürasyonu ile `ReaderPipeline`.

```python
async def test_a1_handwritten(pipeline):
    r = await pipeline.read(Path("tests/fixtures/golden/a1_handwritten.webp").read_bytes())
    assert r.doc_type == "lens_handwritten"
    assert (r.right.sph.value, r.right.cyl.value, r.right.axis.value) == (-3.0, -0.75, 80)
    assert (r.left.sph.value, r.left.cyl.value, r.left.axis.value) == (-3.0, -0.75, 100)
    assert r.right.bc.value == 8.6 and r.right.bc.shared and r.left.dia.value == 14.5
    assert "Acuvue" in r.brand.raw

async def test_a2_printed(pipeline):
    r = await pipeline.read(Path("tests/fixtures/golden/a2_printed.webp").read_bytes())
    assert r.doc_type == "lens_printed"
    assert r.right.bc.value == 8.4 and r.right.dia.value == 14.0 and r.right.sph.value == -2.25
    assert r.left.sph.value == -2.25 or "left.sph: not_quarter_step" in r.warnings
    assert r.right.cyl is None and r.right.add is None and r.brand is None
    assert r.right.bc.shared is False
```

- [ ] **Step 2:** Görselleri kopyala; `Dockerfile`, `docker-compose.yml`, `README.md`'yi yaz.
- [ ] **Step 3: Doğrula (bu ortamda)**
  - Run: `pytest -v` → tüm birim testleri PASS, entegrasyon testleri deselected.
  - Run: `docker compose config -q` → çıkış kodu 0.
  - Run: `docker build -t recete-reader .` → başarılı.
- [ ] **Step 4: Doğrula (L4 sunucusunda — bu ortamda GPU yok)**
  - Run: `docker compose up -d && curl -s localhost:8080/health` → `{"status":"ok",...}`
  - Run: `READER_VLLM_URL=http://localhost:8000/v1 pytest -m integration -v` → 2 PASS; ayrıca her isteğin < 60 sn sürdüğünü (`latency_ms`) not et.
- [ ] **Step 5: Commit** — `git add Dockerfile docker-compose.yml README.md tests/integration tests/fixtures && git commit -m "feat: deployment, golden integration tests, README"`
