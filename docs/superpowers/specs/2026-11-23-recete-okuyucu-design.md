# Reçete Okuyucu — Tasarım Dokümanı

Tarih: 2026-11-23
Durum: Onaylandı (2026-11-23)

## 1. Amaç ve neden

Lens siparişlerinde müşteri reçetesini yüklüyor. Bugün müşteri hizmetleri her
reçeteyi elle siparişle karşılaştırıp onaylıyor. Hedef, eşleşen siparişlerin
**otomatik onaylanması**, eşleşmeyen veya güvenle okunamayanların müşteri
hizmetlerine gitmesi.

Bu doküman o zincirin **yalnızca okuma halkasını** tanımlar: reçete görselini
alıp türünü ve göz değerlerini **standart sayı formatında, alan bazında güven
puanıyla** JSON olarak döndüren servis ("okuyucu"). Siparişle karşılaştırma ve
onay/ret kararı başka bir sistemde yapılır.

Hata maliyeti asimetriktir: yanlış okunmuş bir değer otomatik onaya giderse
müşteriye yanlış lens gider; düşük güvenle işaretlenmiş doğru bir değer ise
sadece müşteri hizmetlerine iş çıkarır. Okuyucunun tüm tasarım kararları bu
asimetriye göre verilir: **emin değilsen düşük güven ver ya da boş bırak.**

## 2. Hedefler, hedef dışı olanlar, anti-hedefler

**Hedefler**
- Görseli 4 türden birine sınıflandırmak: `lens_printed` (Lens Reçetesi),
  `lens_handwritten` (Lens Reçetesi El Yazısı), `glasses` (Gözlük Reçetesi),
  `other`.
- Sağ ve sol göz için SPH, CYL, AKS, ADD, BC, DIA, renk değerlerini okumak.
- Gözlük reçetelerinde doktorun **elle eklediği BC** değerini yakalamak
  (iş için en kritik alan).
- Her değeri standart formatta (`value`) ve reçetede yazdığı haliyle (`raw`)
  döndürmek.
- Her alan ve sınıflandırma için güvenilir, modelden hesaplanmış bir güven
  puanı (0–1) döndürmek.
- Kullanılan VLM'in kod değişikliği olmadan (konfigürasyonla) değiştirilebilmesi.

**Hedef dışı (bu sürümde yok)**
- Sipariş değerleriyle karşılaştırma, tolerans uygulama, onay/ret kararı.
- Gözlük → lens vertex dönüşümü, torik yuvarlama.
- Düşük güvenli alanlar için kırpıp yeniden okuma (Yaklaşım C) — ileride B'nin
  üstüne eklenebilir.
- Model fine-tuning.

**Anti-hedefler (çalışsa bile başarısızlık sayılır)**
- Modelin sipariş değerlerini görmesi. Okuyucu sipariş verisini **hiçbir
  şekilde** almaz; aksi halde model "beklenen" değeri okumaya meyleder.
- Eksik/okunamayan bir değeri tahminle doldurmak. Yoksa `null`.
- Format kuralına uymayan bir değeri sessizce "düzeltmek" (ör. 175.5 → 175).
  Kural dışı değer `raw` olarak korunur, güveni düşürülür, uyarı eklenir.
- Silindrin işaretini çevirmek (transpozisyon). Reçetede nasıl yazıyorsa öyle.

## 3. Kısıtlar

- Model kendi sunucumuzda, GPU üzerinde çalışır. Başlangıç modeli
  `Qwen/Qwen3-VL-8B-Instruct`; ileride vLLM'in desteklediği başka bir VLM
  olabilir.
- Reçeteler Türkçe; göz etiketleri "Sağ/Sol", "OD/OS", "R/L" ve benzeri
  biçimlerde gelebilir. Ondalık ayırıcı virgül veya nokta olabilir.
- Sağlık verisi işlenir (KVKK): okuyucu görsel saklamaz; alan değerleri
  normal loga yazılmaz (bkz. 5.11).
- Donanım: **tek NVIDIA L4 (24 GB)**. Yük: günde ~200+ reçete. Yanıt süresi
  için sert sınır yok ama **istek başına < 60 sn** hedeflenir. Bu yük ve
  bellekle 8B model bf16'da sığar, senkron HTTP yeterlidir; kuyruk gerekmez.
- Girdiler telefon fotoğrafıdır: eğik çekim, katlanmış kâğıt, elde tutulan
  reçete, kişisel bilgileri karartılmış alanlar olabilir.

## 4. Değerlendirilen yaklaşımlar

| | Yaklaşım | Artı | Eksi |
|---|---|---|---|
| A | Tek çağrı: tür + tüm değerler tek prompt'ta | En hızlı, en basit | Tek genel prompt; gözlükteki el yazısı BC'ye özel talimat vermek zor, bir türü iyileştirmek diğerini bozabilir |
| **B** | **İki aşama: önce sınıflandırma, sonra türe özel okuma** | Her tür ayrı ayarlanır; sınıflandırma güveni doğrudan etiket olasılığından gelir; `other` erken durur | Ek kısa bir çağrı (~0.3–0.5 sn) |
| C | B + düşük güvenli alanlarda kırpıp büyüterek ikinci okuma | El yazısında en yüksek doğruluk | Daha karmaşık; eşikler veriyle ayarlanmalı |

**Seçilen: B.** Kritik alan (gözlükteki el yazısı BC) türe özel talimat
gerektiriyor; B bunu veriyor. C, gerçek hata dağılımı görüldükten sonra B'nin
üstüne eklenecek bir genişleme olarak bırakıldı.

## 5. Kararlaştırılan tasarım

### 5.1 Genel akış

```
istemci (kendi sunucunuz)
   │  POST /v1/read  (görsel)
   ▼
Okuyucu servisi
   1. Ön işleme: EXIF yönü düzeltme, RGB'ye çevirme, maksimum piksel sınırına
      ölçekleme (model konfigürasyonundan).
   2. Aşama 1 — Sınıflandırma çağrısı → doc_type + doc_type_confidence
        └─ doc_type == other → yalnızca tür bilgisiyle yanıt dön.
   3. Aşama 2 — Türe özel okuma çağrısı (lens prompt'u veya gözlük prompt'u)
   4. Normalizasyon: raw → value
   5. Format kontrolleri: kural dışı → güven düşür + warning
   6. Güven hesabı (logprob'lardan)
   7. JSON yanıt
   ▼
vLLM (OpenAI uyumlu API, GPU)
```

### 5.2 Model servisi ve model bağımsızlık

- Model **vLLM** ile OpenAI uyumlu API olarak servis edilir.
- Okuyucu modele yalnızca bu API üzerinden konuşur; model adına özel kod yoktur.
- Her model için bir konfigürasyon kaydı tutulur: endpoint, model adı, maksimum
  görsel pikseli, kullanılacak prompt seti, sampling parametreleri.
  Model değiştirmek = yeni kayıt + konfigürasyonda aktif modeli değiştirmek.
- Her iki aşamada **structured output** (vLLM guided decoding, JSON şeması /
  sabit seçenek listesi) kullanılır; çıktı her zaman şemaya uygun ve parse
  edilebilir olur.
- Sıcaklık 0 (deterministik), `logprobs` açık.

### 5.3 Aşama 1 — Sınıflandırma

- Çıktı, 4 etiketten biriyle sınırlandırılmış tek bir alan.
- `doc_type_confidence` = seçilen etiketin token olasılığı (etiket birden çok
  token ise olasılıkların çarpımı).
- Tür tanımları prompt'ta açıkça verilir. **Tür, mürekkebe değil belgenin
  kendisine göre belirlenir:**
  - `lens_printed`: Basılı bir kontakt lens reçetesi **formu** (ör. başlığı
    "KONTAKT LENS REÇETESİ", BC/DIA/Sferik satırları ve Sağ Göz/Sol Göz
    sütunları olan tablo). Değerler elle doldurulmuş olsa bile bu türdür.
  - `lens_handwritten`: Form olmadan, boş/düz bir kâğıda (veya reçete
    koçanına) baştan sona elle yazılmış lens reçetesi.
  - `glasses`: Gözlük reçetesi. Üzerine elle BC eklenmiş olması türünü
    değiştirmez.
  - `other`: Bunların hiçbiri.

### 5.4 Aşama 2 — Türe özel okuma

İki prompt/şema seti vardır:

- **Lens seti** (`lens_printed`, `lens_handwritten` için ortak şema; el yazısı
  türünde prompt el yazısı rakam okumaya dair ek talimat içerir):
  SPH, CYL, AKS, ADD, BC, DIA, renk.
- **Gözlük seti** (`glasses`): uzak SPH, CYL, AKS; ADD; ve **"reçetenin
  herhangi bir yerine elle eklenmiş BC (ve varsa DIA) değerini ara"**
  talimatı. Yakın satırı (near) varsa ayrı bir `near` nesnesinde döner;
  ADD hesaplanmaz, yalnızca yazılıysa okunur.

Model her alan için reçetede gördüğü metni (`raw`) ve el yazısı olup
olmadığını (`handwritten`) döndürür. Sayıya çevirme (`value`) modelde değil,
kodda yapılır.

**Tek değer, iki göz (BC ve DIA):** Doktorlar BC'yi (ve sıklıkla DIA'yı) göz
belirtmeden **tek bir değer** olarak yazar ("BC: 8.6", "Tüm çap: 14.5"). Bu
durumda değer **her iki göze de yazılır** ve alanda `"shared": true`
işaretlenir. Bu normal bir durumdur; uyarı eklenmez, güven düşürülmez.
Model bu ayrımı şemada belge seviyesinde bir alanla (`shared_bc`,
`shared_dia`) döndürür; iki göze kopyalama kodda yapılır.

**Prompt'larda yer alması gereken saha gerçekleri** (örnek reçetelerden):
- Türkçe etiketler: "Temel Eğri (BC)", "Tüm Çap (DIA)", "Sferik Değer",
  "sferik", "silindirik", "Cyl", "Aks", "Torik Değer", "Multifokal Değer
  (ADD)", "Sağ / Sol", "Sağ Göz / Sol Göz".
- Silindir sıkça `CYL x AKS` biçiminde tek satırda yazılır: "-0,75 x 80"
  → CYL −0.75, AKS 80.
- Boş alanlar üzerine çekilmiş çapraz/yatay çizgi **"değer yok"** demektir;
  çizgi bir sayı veya eksi işareti olarak okunmamalıdır → `null`.
- El yazısında 5↔9, 4↔9, 1↔7 karışıklıkları beklenir (ör. "-2.25" kolayca
  "-2.29" okunur). Format kontrolü (0.25 adım) bunu yakalar; bkz. 5.7.

### 5.5 Çıktı sözleşmesi (JSON)

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
  "warnings": ["left.axis: kutu kısmen silik"],
  "model": "qwen3-vl-8b-instruct",
  "prompt_version": "glasses-v1",
  "latency_ms": 1840
}
```

Kurallar:
- `null` = reçetede yok veya okunamadı. Okuyucu ikisini ayırt etmeye
  çalışmaz; ikisi de "değer yok" demektir.
- `doc_type == "other"` ise `right` ve `left` `null` döner.
- Bir göz reçetede hiç yoksa o göz `null` döner.
- `value` tipleri: SPH, CYL, ADD, BC, DIA → ondalık sayı; AKS → tam sayı;
  renk → küçük harfli, baştaki/sondaki boşlukları alınmış metin.
- `model` ve `prompt_version` her yanıtta bulunur (model değişiminde
  karşılaştırma ve geriye dönük analiz için).
- `shared` yalnızca BC ve DIA'da bulunur; diğer alanlarda yoktur.
- *(Tasarımcı kararı)* `brand`: reçetede yazılıysa lens marka/model adı,
  `{"raw": "...", "confidence": ...}` biçiminde, normalize edilmeden
  (ör. "Acuvue Oasys for Astigmatism"). Karşılaştırma sistemi kullanmak
  zorunda değildir; okuması ucuz olduğu ve bazı reçetelerde yazdığı için
  eklendi.

### 5.6 Normalizasyon (raw → value)

- Ondalık virgül → nokta; Unicode eksi (−, –) → `-`; boşluklar temizlenir.
- İşaret yoksa ve değer 0 değilse SPH/CYL/ADD için `+` kabul edilir
  (ADD her zaman pozitiftir). "Plano", "PL", "0.00" → 0.0.
  *(Tasarımcı kararı)* İşaretsiz SPH/CYL'de `+` varsayımı yapıldığında alanın
  güveni format cezasıyla aynı oranda düşürülür ve `warnings`'e
  "işaret yazılmamış, + varsayıldı" eklenir. Silik/düşmüş bir eksi işaretinin
  yüksek güvenle otomatik onaya gitmesini engellemek için. ADD bu kuraldan
  muaftır.
- "-2,5" → -2.50 gibi eksik ondalık hane tamamlanır; bu bir yuvarlama değil
  yazım farkıdır.
- AKS'teki "°" vb. işaretler atılır.
- Sayıya çevrilemeyen `raw` → `value: null`, güven 0, uyarı eklenir.

### 5.7 Format kontrolleri

Kontroller **reddetmez**; kurala uymayan alanın güvenini düşürür (ör. 0.2 ile
çarpar — kesin katsayı builder'a bırakıldı) ve `warnings`'e not ekler.

| Alan | Kural |
|---|---|
| SPH | −30.00 … +30.00, 0.25 adım |
| CYL | −10.00 … +10.00, 0.25 adım |
| AKS | 0 … 180 tam sayı |
| ADD | +0.50 … +4.00, 0.25 adım |
| BC  | 7.0 … 10.0 |
| DIA | 12.0 … 16.0 (lens) |
| — | CYL var, AKS yok (veya tersi) → ikisinin de güveni düşer |

### 5.8 Güven puanı

- vLLM'den dönen token logprob'ları, üretilen JSON'daki her alanın `raw`
  değerine ait token'larla eşleştirilir.
- Alan güveni = o değerin token olasılıklarının **minimumu** (en şüpheli
  rakam belirleyicidir; muhafazakâr tercih).
- Ardından format kontrolü cezası uygulanır.
- Bu puan kalibre edilmemiş ham bir sinyaldir; eşik değerini karşılaştırma
  sistemi, değerlendirme seti üzerinde belirler (bkz. 5.10).

### 5.9 Servis arayüzü ve hata durumları

- `POST /v1/read` — multipart, istek başına **tek dosya**. Yanıt: 5.5'teki JSON.
  Kabul edilen formatlar: JPEG, PNG, WEBP, HEIC (iPhone). **PDF desteklenmez.**
  Bir siparişte birden çok görsel varsa her biri ayrı istekle gönderilir;
  sonuçları birleştirmek çağıran sistemin işidir.
- `GET /health` — servis ve vLLM erişilebilirliği.
- Model erişilemez / zaman aşımı / parse hatası → HTTP 5xx ve hata gövdesi.
  Okuyucu hiçbir durumda "kısmi tahmin" uydurmaz; çağıran sistem hata
  durumunda siparişi müşteri hizmetlerine yönlendirir.

### 5.10 Değerlendirme düzeneği

- **Veri mevcut:** elde çok sayıda müşteri hizmetlerince doğrulanmış reçete
  var. *(Tasarımcı kararı)* Bunlardan tür bazında dengeli bir test seti
  ayrılır (başlangıç önerisi: her türden ~100, toplam ~400) ve prompt
  geliştirmede **kullanılmaz**; prompt iterasyonu için ayrı küçük bir
  geliştirme seti kullanılır.
- Doğrulanmış kayıtlarda tür etiketi yoksa tür etiketleri bu set için elle
  eklenir. Doğru alan değerleri olarak onaylanmış sipariş değerleri
  kullanılırsa BC/DIA'daki küçük tolerans farkları yanlış okuma gibi
  görünebilir; raporda BC/DIA hem birebir hem toleranslı doğrulukla verilir.
- *(Tasarımcı kararı)* Sabit bir doğruluk hedefi yok. Asıl ölçü: "yanlış
  okunan alan içeren reçetelerin < %1'i eşik üstünde kalacak şekilde güven
  eşiği seçildiğinde, reçetelerin yüzde kaçı eşik üstünde kalıyor?"
  (= ulaşılabilir otomatik onay oranı). Eşiğin kendisini karşılaştırma
  sistemi belirler.
- Bir komut satırı aracı test setini okuyucudan geçirip raporlar:
  - tür bazında sınıflandırma doğruluğu ve karışıklık matrisi,
  - alan bazında doğruluk (özellikle gözlük reçetelerinde BC),
  - güven puanı aralıklarına göre doğruluk (kalibrasyon tablosu) — otomatik
    onay eşiğinin seçilmesi için,
  - gecikme.
- Model veya prompt değişikliğinde aynı set üzerinde öncekiyle yan yana
  karşılaştırma.

### 5.11 Loglama

*(Tasarımcı kararı)* Okuyucu **durumsuzdur, görseli saklamaz**; görsel
zaten çağıran sistemde (sipariş kaydında) duruyor, KVKK saklama/silme
sorumluluğu orada kalır.
- Uygulama logu: request_id, doc_type, güven puanları, model,
  prompt_version, gecikme, uyarı kodları. **Alan değerleri ve `raw` metinleri
  normal loga yazılmaz** (sağlık verisi).
- Değerlendirme için gereken değerler, çağıran sistemin request_id ile
  eşleştirerek kendi tarafında tuttuğu yanıtlardan alınır.

### 5.12 Teknoloji yığını *(Tasarımcı kararı)*

| Katman | Seçim | Neden |
|---|---|---|
| Model sunucusu | vLLM (`vllm/vllm-openai` Docker imajı), OpenAI uyumlu API | Qwen3-VL desteği, structured output (xgrammar), logprob, L4'te verimli |
| Dil | Python 3.11+ | VLM ekosistemi, ekip için yaygın |
| API | FastAPI + Uvicorn | Async, Pydantic ile doğal entegrasyon, otomatik OpenAPI dokümanı |
| Şemalar | Pydantic v2 | Tek kaynak: aynı model hem API yanıtını hem vLLM'e verilen JSON şemasını üretir |
| VLM istemcisi | `openai` Python SDK (AsyncOpenAI), `base_url` vLLM'i gösterir | Model bağımsız; vLLM'deki herhangi bir VLM aynı kodla çağrılır |
| Görsel işleme | Pillow + pillow-heif | EXIF düzeltme, HEIC, yeniden boyutlandırma |
| Sayısal kontroller | `decimal.Decimal` | 0.25 adım kontrolünde float hatası olmasın |
| Konfigürasyon | YAML (`models.yaml`) + pydantic-settings | Model değişimi = konfig değişikliği |
| Prompt'lar | Model ve türe göre sürümlenmiş dosyalar (`prompts/<model>/<tür>-v1.md`) | `prompt_version` yanıtta raporlanır |
| Test | pytest; birim testlerde sahte VLM istemcisi, entegrasyon testlerinde gerçek vLLM + Ek A örnekleri | Normalizasyon/kontrol/güven mantığı GPU'suz test edilir |
| Değerlendirme | Typer CLI + pandas, Markdown/CSV rapor | 5.10'daki raporlar |
| Dağıtım | Docker Compose: `vllm` (GPU) + `reader` (CPU) iki konteyner | Model sunucusu ve servis bağımsız yeniden başlatılır |
| Log | structlog (JSON) | 5.11 kurallarıyla |

**Bilinçli olarak kullanılmayanlar:** LangChain / LlamaIndex gibi
orkestrasyon çatıları — akış iki çağrıdan ibaret ve logprob ile structured
output parametrelerine doğrudan erişim gerekiyor; ek soyutlama katmanı bunu
zorlaştırır. Ollama / llama.cpp — VLM'lerde structured output ve logprob
desteği vLLM kadar olgun değil.

**Modül yapısı:**

```
reader/
  api.py          # FastAPI uç noktaları (/v1/read, /health)
  pipeline.py     # Akış: ön işleme → sınıflandırma → okuma → normalizasyon → kontrol → güven
  llm_client.py   # VLMClient arayüzü + OpenAI uyumlu uygulama
  schemas.py      # Pydantic: model çıktı şemaları ve API yanıtı
  preprocess.py   # Görsel ön işleme
  normalize.py    # raw → value
  validate.py     # Format kontrolleri ve cezalar
  confidence.py   # Logprob → alan güveni
  prompts/        # Sürümlü prompt dosyaları
config/models.yaml
eval/run_eval.py
tests/
docker-compose.yml
```

## 6. Builder'a bırakılanlar

- Prompt metinlerinin ayrıntılı yazımı ve iterasyonu.
- Logprob → alan eşleştirmesinin uygulama yöntemi.
- Format kontrolü ceza katsayısı.
- vLLM başlatma parametreleri (GPU bellek, maksimum görsel boyutu, eşzamanlılık).
- Dağıtım (container, servis yöneticisi).
- Proje yapısı ve test çerçevesi.

## Ek A — Örnek reçeteler ve beklenen çıktı

Kullanıcının paylaştığı iki örnek (kişisel bilgiler karartılmış). İlk birim
testleri / altın örnekler olarak kullanılabilir.

**A.1 — `lens_handwritten`** (düz kâğıda tamamen el yazısı)

Reçetede yazan: "Tanı: miyopi + astigmatizma", "Sağ → −3,00 (sferik),
−0,75 x 80 (silindirik)", "Sol → −3,00 (sferik), −0,75 x 100 (silindirik)",
"Tüm çap: 14,5", "BC: 8,6", "Acuvue Oasys for Astigmatism", geçerlilik
tarihleri "31.08.26 – 31.08.27".

Beklenen:
- Sağ: SPH −3.00, CYL −0.75, AKS 80, BC 8.6 (shared), DIA 14.5 (shared)
- Sol: SPH −3.00, CYL −0.75, AKS 100, BC 8.6 (shared), DIA 14.5 (shared)
- ADD, renk: null. `brand.raw`: "Acuvue Oasys for Astigmatism"

**A.2 — `lens_printed`** (basılı "KONTAKT LENS REÇETESİ" formu, elle doldurulmuş)

Beklenen:
- Sağ: BC 8.4, DIA 14.0, SPH −2.25
- Sol: BC 8.4, DIA 14.0, SPH −2.25 (el yazısındaki "5" kolayca "9" okunur;
  "−2.29" okunursa 0.25 adım kontrolü güveni düşürür)
- CYL/AKS, ADD, marka: üzerlerine çizgi çekilmiş → null
- `shared`: false (değerler her göz için ayrı sütunda yazılı)

## Ek B — Donanım notları (builder için)

- L4 (24 GB, Ada mimarisi): Qwen3-VL-8B bf16 ağırlıkları ~17 GB; kalan bellek
  tek/düşük eşzamanlı istekler için yeterli. `max_model_len` ve maksimum
  görsel pikseli buna göre sınırlanmalı. Bellek darlığında L4'ün desteklediği
  FP8 ağırlık seçeneği değerlendirilebilir (doğruluk test setiyle
  karşılaştırılarak).
- Günde ~200 istek için tek vLLM örneği yeterli; servis vLLM'den ayrı süreçte
  çalışır.
