# İlan Takip Sistemi

Belirlediğin ilan sayfalarını düzenli aralıklarla tarar, **filtrene uyan yeni ilanları** Telegram'dan sana gönderir. Aynı ilanı ikinci kez göndermez.

- Çalışma yeri: **GitHub Actions** (ücretsiz, sunucu bakımı yok)
- Site başına kod yazmak yok — her şey `config.yaml`'da
- Klasik HTML sayfaları, sitenin JSON API'si ve JS ile yüklenen sayfalar desteklenir

---

## 1. Kurulum (~15 dakika)

### a) Telegram botunu oluştur

1. Telegram'da **@BotFather**'a yaz → `/newbot` → isim ver.
2. Sana verdiği **token**'ı sakla (`123456789:AAE...` şeklinde).
3. Kendi botunla sohbet başlat, ona bir mesaj at (**bu şart** — bot sana ilk mesajı atamaz).
4. Chat ID'ni öğren:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

Yanıttaki `"chat":{"id":123456789}` değeri senin **chat id**'in.

> Bildirimlerin bir gruba düşmesini istersen botu gruba ekle, grupta bir mesaj at ve aynı komutu çalıştır. Grup ID'leri negatiftir (`-100...`).

### b) Repoyu kur

```bash
git init && git add . && git commit -m "ilk kurulum"
gh repo create ilan-takip --public --source=. --push
```

> **Public repo öner(il)ir:** GitHub Actions dakikaları public repolarda sınırsız. Private repoda 30 dakikada bir tarama aylık ücretsiz kotayı zorlar. Repoda gizli bilgi yok — token ve chat id koda değil, **Secrets**'a girilir.

### c) Secrets tanımla

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| İsim | Değer |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
| `TELEGRAM_CHAT_ID` | yukarıda bulduğun id |

### d) Yerel deneme

```bash
pip install -r requirements.txt
python main.py --dry-run       # Telegram'a göndermez, ekrana yazar
```

---

## 2. Takip edilecek siteyi ekleme

### Adım 1 — Aramayı sitede yap

Siteye gir, filtreni uygula (bölge, oda sayısı, fiyat aralığı), **"en yeni" sıralamasını** seç. Tarayıcıdaki URL'yi kopyala. Ne kadar çok filtreyi sitenin kendisine yaptırırsan sistem o kadar az iş yapar.

### Adım 2 — Seçicileri bul

```bash
python inspect_site.py "https://site.com/kiralik?bolge=girne&sirala=yeni"
```

Araç sayfadaki tekrar eden blokları bulur ve doğrudan `config.yaml`'a yapıştırabileceğin bir `selectors:` bloğu önerir. Genelde **[1] numaralı aday** doğru olandır.

Çıktı boşsa veya "tekrar eden blok bulunamadı" derse sayfa JavaScript ile yükleniyordur:

```bash
python inspect_site.py "https://..." --render
```

Araç ayrıca sayfada bir **JSON API** izi bulursa onu da söyler. API varsa onu kullan — HTML kazımaktan çok daha sağlamdır, site tasarımı değişince bozulmaz.

### Adım 3 — `config.yaml`'a ekle

```yaml
sources:
  - name: girne-kiralik          # dosya adı olarak kullanılır, boşluksuz
    label: "Girne 2+1 Kiralık"   # Telegram mesajında görünen başlık
    enabled: true
    url: "https://site.com/kiralik?bolge=girne&sirala=yeni"
    type: html
    selectors:
      item: "div.listing-card"
      title: "h3.listing-title::text"
      price: "span.listing-price::text"
      link: "a.listing-link::attr(href)"
      location: "span.listing-location::text"
    filters:
      max_price: 45000
      include: ["girne", "alsancak", "lapta"]
      exclude: ["stüdyo", "devren", "paylaşımlı"]
```

### Adım 4 — Test et

```bash
python main.py --source girne-kiralik --dry-run
```

`sayfada 0 kayıt bulundu` görüyorsan `item` seçicisi tutmuyor demektir → 2. adıma dön.

---

## 3. Seçici sözdizimi

| Yazım | Ne yapar |
|---|---|
| `.fiyat::text` | elemanın metnini alır |
| `a.link::attr(href)` | bağlantı adresini alır (göreli adresler otomatik tamamlanır) |
| `img::attr(data-src)` | herhangi bir HTML özniteliğini alır |
| `.baslik` | `::text` yazmazsan da metin alınır |

`item` dışındaki her alan isteğe bağlıdır ve **istediğin adı verebilirsin**. `title`, `price`, `link`, `location`, `date` alanları Telegram mesajında özel olarak biçimlendirilir; diğerleri listenin altına eklenir.

## 4. Filtreler

```yaml
filters:
  min_price: 10000            # price alanından okunan sayıya göre
  max_price: 45000
  include: ["girne", "lapta"] # en az biri geçmeli
  require_all: ["eşyalı"]     # hepsi geçmeli
  exclude: ["stüdyo"]         # biri bile geçerse elenir
  regex: "2\\+1|3\\+1"        # serbest desen
```

Kelime aramaları **Türkçe karakter ve büyük/küçük harf farkını yok sayar** — `esyali` yazsan da "Eşyalı" bulunur. Fiyat okuyucusu `32.500 TL`, `£185,500`, `1.250,75` gibi biçimlerin hepsini anlar.

> `min_price`/`max_price` kullanıyorsan ve fiyat okunamıyorsa ilan **elenir**. `python main.py --source X --dry-run -v` ile eleme sebeplerini görebilirsin.

## 5. Tarama sıklığı

`.github/workflows/watch.yml` içindeki cron satırı:

```yaml
- cron: "*/30 * * * *"    # 30 dakikada bir
```

Sık kullanılanlar: `*/15 * * * *` (15 dk), `0 * * * *` (saatlik), `0 6-22 * * *` (06:00–22:00 arası saat başı, UTC).

> GitHub zamanlanmış işleri yoğunlukta **5–15 dakika geciktirebilir**; saniye hassasiyeti yoktur. Ayrıca repo 60 gün hiç el değmeden kalırsa zamanlanmış workflow'lar otomatik durdurulur — ara sıra Actions sekmesinden elle bir çalıştırma yapman yeterli.

## 6. JS ile yüklenen siteler

`config.yaml`'da:

```yaml
    render: true
    wait_selector: ".listing-card"   # bu eleman görünene kadar bekle
```

Sonra `requirements.txt`'te `playwright` satırının başındaki `#`'i kaldır ve `watch.yml` içindeki "Playwright kur" adımını aç. Tarama süresi uzar ama sonuç değişmez.

## 7. Komutlar

```bash
python main.py                      # normal çalıştırma
python main.py --dry-run            # göndermeden dene
python main.py --source X           # tek kaynak
python main.py -v                   # ayrıntılı log (eleme sebepleri)
python main.py --reset              # "görülen ilanlar" hafızasını sıfırla
python inspect_site.py "<URL>"      # seçici keşfi
```

## 8. Sorun giderme

| Belirti | Sebep / çözüm |
|---|---|
| `sayfada 0 kayıt bulundu` | `item` seçicisi tutmuyor ya da sayfa JS ile yükleniyor → `inspect_site.py`, gerekirse `--render` |
| Türkçe karakterler bozuk | Site kodlamayı bildirmiyor; `fetch.py` bunu zaten telafi eder, sürmesi hâlinde `headers` ile `Accept-Charset` dene |
| HTTP 403 | Site bot trafiğini engelliyor → `render: true` dene, tarama sıklığını düşür; ısrarla engelliyorsa o siteyi zorlama |
| İlk çalıştırmada mesaj gelmedi | Normal. İlk tarama mevcut ilanları sessizce kaydeder. Hemen görmek için `notify_on_first_run: true` |
| Aynı ilan tekrar geliyor | Sitenin linki her seferinde değişiyor (takip parametresi) → `selectors`'a sabit bir `id` alanı ekle |
| Actions çalışıyor ama mesaj yok | Secrets adlarını kontrol et; bota **sen** ilk mesajı attın mı? |

## 9. Dikkat

- Her sitenin `robots.txt` ve kullanım şartlarını kontrol et; bazı siteler otomatik erişimi açıkça yasaklar.
- Tarama sıklığını makul tut (15–30 dk fazlasıyla yeterli). Dakikada bir tarama hem IP engeli getirir hem de sana bir şey kazandırmaz.
- Kişisel veri (ilan sahibi telefon/isim) toplayıp saklama; sadece ilanın kendisini takip et.

## 10. Sonraki adım: kendi sunucuna taşımak

GitHub Actions'tan Rocky Linux sunucuna taşımak istersen kod aynen çalışır:

```bash
# /etc/systemd/system/ilan-takip.service  →  ExecStart=/opt/ilan-takip/.venv/bin/python /opt/ilan-takip/main.py
# /etc/systemd/system/ilan-takip.timer    →  OnCalendar=*:0/30
systemctl enable --now ilan-takip.timer
```

Token'ları `EnvironmentFile=/etc/ilan-takip.env` ile ver. `state/` klasörü aynı şekilde çalışır, commit'lemeye gerek kalmaz.

---

## Dosya yapısı

```
├── main.py              # tarama akışı
├── inspect_site.py      # seçici keşif aracı
├── config.yaml          # TAKİPLERİN BURADA
├── watcher/
│   ├── fetch.py         # indirme (HTTP / headless)
│   ├── parse.py         # HTML + JSON ayrıştırma, fiyat okuma
│   ├── filters.py       # eleme kuralları (Türkçe duyarlı)
│   ├── notify.py        # Telegram mesajları
│   └── store.py         # görülen ilan hafızası
├── state/               # kaynak başına görülen ilan kayıtları
└── .github/workflows/watch.yml
```
