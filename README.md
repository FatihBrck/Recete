# Reçete okuyucu

Göz reçetesi fotoğraflarını (lens basılı, lens el yazısı, gözlük) okuyup yapılandırılmış JSON döndüren
durumsuz bir servis. Görüntüyü, vLLM üzerinde sunulan **Qwen3-VL-8B-Instruct** ile okur; tek bir
NVIDIA L4 (24 GB) yeterlidir. Okuyucu sipariş verisi almaz: yalnızca tek bir görsel dosyası alır.

Girdi: JPEG, PNG, WEBP, HEIC (PDF desteklenmez → 415). En fazla 15 MB (aşılırsa 413).

## Çalıştırma

Gereksinim: NVIDIA sürücüsü + NVIDIA Container Toolkit olan bir Docker sunucusu.

```bash
docker compose up -d
curl -s localhost:8080/health          # {"status":"ok","model":"qwen3-vl-8b-instruct"}
curl -F file=@recete.jpg http://localhost:8080/v1/read
```

vLLM ilk açılışta modeli indirir (HF önbelleği `hf-cache` volume'ünde tutulur); `reader` servisi
vLLM sağlıklı olana kadar başlamaz. vLLM imajı `docker-compose.yml` içinde sabit etikete
(`v0.34.0`, PyPI'deki en yeni kararlı sürüm) bağlıdır; etiketi değiştirirken bayrak sözdizimini
`docker run --rm vllm/vllm-openai:<etiket> --help` ile doğrulayın.

### Ters vekil (reverse proxy)

15 MB sınırı, yükleme sunucuya alındıktan **sonra** uygulanır. Bu yüzden önüne mutlaka bir gövde
boyutu sınırı koyun, örn. nginx'te `client_max_body_size 15m;`.

## Yanıt

```json
{
  "request_id": "uuid",
  "doc_type": "glasses",
  "doc_type_confidence": 0.96,
  "right": {
    "sph":   {"value": -2.50, "raw": "-2,5",  "confidence": 0.97, "handwritten": false},
    "cyl":   {"value": -0.75, "raw": "-0.75", "confidence": 0.93, "handwritten": false},
    "axis":  {"value": 180,   "raw": "180",   "confidence": 0.90, "handwritten": false},
    "add":   null,
    "bc":    {"value": 8.6,   "raw": "8.6",   "confidence": 0.81, "handwritten": true, "shared": true},
    "dia":   null,
    "color": null,
    "near":  null
  },
  "left":  { "...": "aynı yapı" },
  "brand": null,
  "warnings": ["left.sph: not_quarter_step"],
  "model": "qwen3-vl-8b-instruct",
  "prompt_version": "classify-v1/glasses-v2",
  "stage_latency_ms": {"preprocess": 40, "classify": 900, "extract": 9000},
  "latency_ms": 1840
}
```

- `doc_type`: `lens_printed`, `lens_handwritten`, `glasses`, `other`. `other` ise `right` ve `left` `null`.
- `null` = reçetede yok veya okunamadı. Kural dışı değerler düzeltilmez; `raw` korunur, güven düşer, uyarı eklenir.
- `shared` yalnızca BC ve DIA'da bulunur. `brand` reçetede yazılıysa lens marka adıdır (`raw` + `confidence`).
- Hata durumunda (model erişilemez, zaman aşımı, parse hatası) kısmi sonuç dönmez; HTTP 5xx döner.

### Uyarı kodları

Uyarılar `"<yol>: <kod>"` biçimindedir ve değer içermez.

| Kod | Anlamı |
|---|---|
| `sign_missing_assumed_plus` | SPH/CYL'de işaret yok, `+` varsayıldı (güven düşürülür) |
| `unparseable` | `raw` sayıya çevrilemedi, `value` null |
| `not_integer` | Aks tam sayı değil |
| `out_of_range` | Değer izin verilen aralığın dışında |
| `not_quarter_step` | Değer 0.25 adımına uymuyor |
| `cyl_without_axis` | CYL var, aks yok |
| `axis_without_cyl` | Aks var, CYL yok |
| `shared_conflict` | Bir gözün kendi BC/DIA değeri ile ortak (shared) değer birlikte var ve farklı; iki gözün güveni de düşürülür |
| `confidence_unavailable` | Model logprob döndürmedi veya token hizalaması kurulamadı, güven hesaplanamadı |
| `no_values_read` | Tür lens/gözlük olduğu halde iki gözde de hiçbir değer okunamadı (müşteri hizmetlerine) |

### Güven puanı

`confidence`, token olasılıklarının minimumundan (ve format cezasından) türeyen **kalibre edilmemiş
ham bir sinyaldir**; "%97 doğru" anlamına gelmez. Otomatik onay eşiği, değerlendirme raporu
(aşağıya bakın) ile seçilir ve eşiği çağıran sistem belirler.

Değerlendirmedeki otomatik onay eşiği, belge güveninin
`min(doc_type_confidence, karşılaştırılan her alanın confidence değeri)` olduğunu varsayar. Bu nedenle
alt akıştaki kapı da aynı toplulaştırmayı uygulamalıdır: `doc_type_confidence` ve beklenen belge türü
(`doc_type`) dahil, yalnızca alan güvenlerine bakmak eşiğin kalibrasyonunu geçersiz kılar.

## Yapılandırma

Ortam değişkenleri:

| Değişken | Varsayılan | Anlamı |
|---|---|---|
| `READER_MODELS_FILE` | `config/models.yaml` | Model kayıt dosyası |
| `READER_ACTIVE_MODEL` | dosyadaki `active` | Kullanılacak model anahtarı |
| `READER_MAX_UPLOAD_BYTES` | `15000000` | Yükleme sınırı |
| `READER_OUTPUT_FORMAT` | dosyadaki `output_format` (`full`) | `compact`: model değerleri düz metin ve belge bazında tek `handwritten` ile yazar (~%35-40 daha az çıktı, daha hızlı); API yanıtı aynı kalır |
| `READER_VLLM_BASE_URL` | dosyadaki `base_url` | vLLM adresini geçersiz kılar (ör. Colab'da `http://localhost:8000/v1`) |

### Model değiştirme

1. `config/models.yaml` içine yeni bir kayıt ekleyin (`name`, `base_url`, `model`, `max_image_pixels`,
   `max_tokens`, `timeout_s`, `prompt_dir`, `prompts`).
2. Dosyadaki `active` değerini değiştirin veya `READER_ACTIVE_MODEL=<anahtar>` verin.
3. vLLM'in `docker-compose.yml`'deki `--model` değerini aynı modele çevirin ve yeniden başlatın.

Her yanıttaki `model` ve `prompt_version` alanları, değişiklik sonrası karşılaştırma içindir.

### Yeni prompt sürümü ekleme

1. `config/prompts/<model-dizini>/` altına yeni dosyayı ekleyin, ör. `glasses-v2.md`
   (sistem metni, ardından `=== USER ===` satırı, ardından kullanıcı metni). Eski sürümü silmeyin.
2. `config/models.yaml` içinde ilgili modelin `prompts:` alanını yeni ada çevirin (`glasses: glasses-v2`).
3. Servisi yeniden başlatın; yanıttaki `prompt_version` yeni sürümü gösterir.
4. Eski ve yeni sürümü aynı test seti üzerinde `compare` ile karşılaştırın.

## Değerlendirme

Manifest, satır başına bir JSON olan bir `manifest.jsonl` dosyasıdır; `image` yolu manifest dosyasına göredir.
Gerçek değerler doğrulanmış reçeteden gelir. Reçetede **olmayan** bir alan `null` olarak yazılır ve
puanlanır (orada değer tahmin etmek yanlıştır; reçetede olmayan bir göz için de aynı kural geçerlidir:
göz `null` ise tahmin edilen her null olmayan alan yanlış sayılır). Yalnızca siparişte bulunan ama
reçete türüyle ilgisiz alanlar (ör. DIA'sız bir gözlük reçetesinde DIA) atlanabilir.

```json
{"id": "r001", "image": "images/r001.jpg", "doc_type": "glasses",
 "right": {"sph": -2.5, "cyl": -0.75, "axis": 180, "bc": 8.6},
 "left":  {"sph": -2.0, "cyl": -0.5,  "axis": 170, "bc": 8.6}}
```

```bash
# 1) Seti çalışan servisten geçir, tahminleri yaz
python -m eval.run_eval run --manifest testset/manifest.jsonl --url http://localhost:8080 --out reports/run1.jsonl
# 2) Rapor üret (report.md + fields.csv)
python -m eval.run_eval report --manifest testset/manifest.jsonl --predictions reports/run1.jsonl --out reports/run1
# 3) İki koşuyu (model/prompt değişikliği) yan yana karşılaştır
python -m eval.run_eval compare reports/run1.jsonl reports/run2.jsonl [reports/run3.jsonl ...] --manifest testset/manifest.jsonl
```

Rapor: tür bazında sınıflandırma ve karışıklık matrisi, alan doğruluğu (gözlük BC ayrıca; BC/DIA hem birebir
hem ±0.2 toleranslı), güven aralığına göre kalibrasyon tablosu, otomatik onay oranı ve gecikme (p50/p95).
Güven eşiği bu rapora bakılarak seçilir.

**Test seti kuralı:** müşteri hizmetlerince doğrulanmış reçetelerden tür başına yaklaşık 100'lük (toplam ~400)
dengeli bir test seti ayrılır ve **prompt geliştirmede kullanılmaz**. Prompt iterasyonu ayrı, küçük bir
geliştirme setiyle yapılır; test seti yalnızca sonucu ölçmek içindir.

## Testler

```bash
pytest                      # birim testleri (entegrasyon testleri hariç)
```

Altın örnek entegrasyon testleri çalışan bir vLLM gerektirir (GPU sunucusunda):

```bash
READER_VLLM_URL=http://localhost:8000/v1 pytest -m integration -v
```

`READER_VLLM_URL` tanımlı değilse bu testler atlanır. Her isteğin 60 sn altında kalması hedeflenir
(`latency_ms` alanına bakın). Bu yüzden `config/models.yaml` içinde `timeout_s` 28 sn'dir: iki ardışık
model çağrısı (sınıflandırma + okuma) en kötü durumda bile 60 sn hedefinin altında kalır.

### Hız

L4'te süreyi belirleyen, modelin çıktıyı token token üretmesidir (bf16 8B için saniyede ~15-20 token).
Yanıttaki `stage_latency_ms` sürenin ön işleme, sınıflandırma ve okuma arasında nasıl dağıldığını gösterir.
İki hızlandırma seçeneği vardır, ikisi de açılıp kapatılabilir:

- **FP8:** vLLM'e `--quantization fp8` (bkz. `docker-compose.yml`). ~1,6-2× daha hızlı üretim.
- **Kısa çıktı:** `READER_OUTPUT_FORMAT=compact`. Okuma çıktısı ~%35-40 kısalır; kayıp: `handwritten` alan
  bazında değil belge bazında olur.

Açmadan önce aynı değerlendirme setinde `python -m eval.run_eval compare` ile doğruluğu karşılaştırın.

### Model karşılaştırma

`config/models.yaml` üç model içerir; hepsi aynı prompt'ları ve aynı işleme adımlarını kullanır:

| Anahtar | Model | Not |
|---|---|---|
| `qwen3-vl-8b` (varsayılan) | `Qwen/Qwen3-VL-8B-Instruct` | |
| `qwen3.5-9b` | `Qwen/Qwen3.5-9B` | "Düşünme" modu `extra_body` ile kapalı; bf16'da L4'e sınırda sığar, gerekirse FP8 |
| `medgemma-1.5-4b` | `google/medgemma-1.5-4b-it` | Erişimi kısıtlı: Hugging Face'te şartları kabul edin, vLLM için `HF_TOKEN` gerekir |

Bir modeli kullanmak için vLLM'i o modelle başlatın (`vllm serve <model>`) ve servise
`READER_ACTIVE_MODEL=<anahtar>` verin. Her model aynı test setinden geçirilip sonuçlar
`python -m eval.run_eval compare a.jsonl b.jsonl c.jsonl --manifest ...` ile yan yana konur.
Colab notebook'undaki **8d** hücresi bunu üç model için sırayla yapar.

### Colab ile test

`colab/test_l4.ipynb` notebook'u sunucudaki GPU'nun aynısıyla (Colab Pro, **L4**) deneme yapar: vLLM'i ayrı
bir sanal ortamda başlatır, iki örnek reçeteyle entegrasyon testlerini çalıştırır, okuyucu servisini açar ve
kendi görselinizi yüklemenize izin verir. Colab'da Docker olmadığı için compose kullanılmaz; ayarlar
`docker-compose.yml` ile aynıdır. T4'te 8B model sığmaz. Colab'a yüklenen görseller Google'ın bulutuna gider
(KVKK): yalnızca karartılmış örneklerle deneyin.

## KVKK notu

Reçeteler sağlık verisidir. Okuyucu **görseli saklamaz** (durumsuzdur; yalnızca bellekte işler) ve alan
değerleri ile `raw`/marka metinleri **hiçbir zaman loglanmaz**. Loga yalnızca `request_id`, `doc_type`,
güven puanları, model, `prompt_version`, `latency_ms` ve uyarı kodları yazılır. Görsel çağıran sistemde
durur; saklama/silme sorumluluğu orada kalır. Değerlendirme için gereken değerler, çağıran sistemin
`request_id` ile eşleştirdiği kendi yanıt kayıtlarından alınır.
