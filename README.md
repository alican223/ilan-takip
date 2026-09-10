# İlan Takip Sistemi

Belirlenen ilan sayfalarını düzenli aralıklarla tarar, **filtreye uyan yeni ilanları** Telegram'dan gönderir. Aynı ilanı ikinci kez göndermez.

- Çalışma yeri: **GitHub Actions** (ücretsiz, sunucu bakımı yok)
- Site başına kod yazmak yok — her şey `config.yaml`'da
- Klasik HTML sayfaları, sitenin JSON API'si ve JS ile yüklenen sayfalar desteklenir

> **Bu botu kendine kurmak istiyorsan → [KURULUM.md](KURULUM.md)**
> Orada sıfırdan, tarayıcıdan, adım adım anlatılıyor. Aşağısı sistemin nasıl çalıştığının referansı.

---

## Komutlar

```bash
python main.py                      # normal tarama
python main.py --test-notify        # her kaynaktan EN SON ilanı test olarak gönder
python main.py --dry-run            # tara ama Telegram'a gönderme, ekrana yaz
python main.py --source X           # tek kaynak
python main.py -v                   # ayrıntılı log (eleme sebepleri dahil)
python main.py --reset              # "görülen ilanlar" hafızasını sıfırla
python inspect_site.py "<URL>"      # yeni bir site için seçici keşfi
```

Aynı işlemler GitHub arayüzünden de yapılabilir: **Actions → İlan Takibi → Run workflow** açılır menüsünde `normal`, `test-notify`, `dry-run`, `reset` modları var.

`--test-notify` uçtan uca doğrulama içindir: token geçerliliğini sorar, her kaynağı indirip ayrıştırır ve en son ilanı gerçek bir mesaj olarak gönderir. İlan hafızasına dokunmaz, istediğin kadar çalıştırabilirsin.

---

## Kaynak tanımlama

```yaml
sources:
  - name: girne-kiralik          # dosya adı olarak kullanılır, boşluksuz
    label: "Girne 2+1 Kiralık"   # Telegram mesajında görünen başlık
    enabled: true
    url: "https://site.com/kiralik?bolge=girne&sirala=yeni"
    base_url: "https://site.com" # göreli linkleri tamamlamak için
    type: html                   # html | json
    render: false                # true ise headless tarayıcı kullanılır
    selectors:
      item: "div.listing-card"
      title: "h3.title::text"
      price: "span.price::text"
      link: "a::attr(href)"
    filters:
      max_price: 45000
    notify_on_first_run: false
```

Aramayı olabildiğince **sitenin kendi filtreleriyle** daralt ve sıralamayı "en yeni" yap; sisteme daha az iş düşer.

### Seçici sözdizimi

| Yazım | Ne yapar |
|---|---|
| `.fiyat::text` | Elemanın metnini alır (iç elemanlar boşlukla birleştirilir) |
| `.fiyat` | `::text` yazmasan da aynı şey |
| `a::attr(href)` | Öznitelik değeri. `href`/`src` göreli ise `base_url` ile tamamlanır |
| `::attr(data-id)` | Seçici boş bırakılırsa **ilanın kendi** özniteliği okunur |
| `a::own_text` | Sadece elemanın **doğrudan** metni. `<a>Başlık<span>Konum</span></a>` → "Başlık" |
| `span::text_tight` | İç elemanları **boşluksuz** birleştirir. `315m<sup>2</sup>` → "315m2" |

`item` dışındaki alanların hepsi isteğe bağlıdır ve **istediğin adı verebilirsin**. `title`, `price`, `link`, `location`, `date` özel olarak biçimlendirilir; diğerleri mesajın altına listelenir.

**`id` alanı** varsa tekrar tespiti (dedup) onun üzerinden yapılır — sitenin kendi ilan numarası en sağlam anahtardır. Yoksa `link`, o da yoksa başlık+fiyat kullanılır.

### JSON API kaynağı

Site ilanları bir API'den çekiyorsa (DevTools → Network → Fetch/XHR) HTML kazımak yerine onu kullan; site tasarımı değişince bozulmaz.

```yaml
    type: json
    items_path: "data.results"     # JSON içindeki listenin yolu
    fields:
      id: "id"
      title: "title"
      price: "price.amount"
      link: "detailUrl"
```

### Filtreler

```yaml
filters:
  min_price: 10000
  max_price: 45000
  include: ["girne", "lapta"]   # en az biri geçmeli
  require_all: ["eşyalı"]       # hepsi geçmeli
  exclude: ["stüdyo"]           # biri bile geçerse elenir
  regex: "2\\+1|3\\+1"
```

Kelime aramaları Türkçe karakter ve büyük/küçük harf duyarsızdır. Fiyat okuyucu `32.500 TL`, `£185,500`, `1.250,75` biçimlerinin hepsini anlar. `min_price`/`max_price` varken fiyat okunamıyorsa ilan **elenir**; sebepleri `-v` ile görebilirsin.

---

## Engellenen siteler

Bazı siteler veri merkezi IP'lerinden gelen istekleri reddeder (GitHub Actions da öyle bir IP kullanır). Sırayla denenecekler:

```yaml
    warmup_url: "https://site.com/"   # önce ana sayfayı ziyaret et, çerez al
```

```yaml
    render: true                       # gerçek Chromium ile aç
    wait_selector: ".listing-card"
```

`render: true` kullanacaksan `requirements.txt`'te `playwright` satırının başındaki `#`'i kaldır ve `watch.yml` içindeki "Playwright kur" adımını aç.

İkisi de yetmezse o site veri merkezi IP'lerini tamamen eliyordur; tek çözüm taramayı kendi makinende çalıştırmaktır (aşağıya bak).

> ⚠️ **`Accept-Encoding` başlığını elle ayarlama.** `requests` yalnızca açabildiği sıkıştırmaları ister. Elle `br` eklersen site brotli gönderir, kütüphane açamaz ve elimize çözülmemiş ikili veri geçer: sayfa sağlam inmiş görünür ama hiçbir seçici tutmaz.

---

## Sorun giderme

Sistem 0 kayıt bulduğunda sebebini kendisi teşhis eder ve log'a yazar:

| Log'daki teşhis | Anlamı |
|---|---|
| `... seçicisi hiçbir şeyle eşleşmiyor` | `selectors.item` yanlış → `inspect_site.py` ile bak |
| `Sayfa engellenmiş görünüyor` | IP engeli → `warmup_url` / `render: true` / kendi sunucun |
| `Gövde HTML'e benzemiyor` | Çözülemeyen sıkıştırma → `Accept-Encoding` başlığını kaldır |
| `HTTP 401 / 400` (Telegram) | Token ya da chat id yanlış → `--test-notify` |

Telegram gönderimi başarısız olursa çalıştırma **kırmızıya** düşer — bildirim gelmediği hâlde her şey yolunda sanılmasın diye.

---

## Kendi sunucuna taşımak

Kod olduğu gibi çalışır:

```bash
# /etc/systemd/system/ilan-takip.service
#   ExecStart=/opt/ilan-takip/.venv/bin/python /opt/ilan-takip/main.py
#   EnvironmentFile=/etc/ilan-takip.env      → TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# /etc/systemd/system/ilan-takip.timer
#   OnCalendar=*:0/30
systemctl enable --now ilan-takip.timer
```

`state/` klasörü aynı şekilde çalışır, commit'lemeye gerek kalmaz. Ev/ofis IP'si kullandığın için bot koruması sorunları da genelde ortadan kalkar.

---

## Dosya yapısı

```
├── main.py              # tarama akışı, teşhis, test modu
├── inspect_site.py      # seçici keşif aracı
├── config.yaml          # TAKİPLER BURADA
├── KURULUM.md           # sıfırdan kurulum rehberi
├── watcher/
│   ├── fetch.py         # indirme (HTTP / headless), kodlama, ısınma isteği
│   ├── parse.py         # HTML + JSON ayrıştırma, seçici modları, fiyat okuma
│   ├── filters.py       # eleme kuralları (Türkçe duyarlı)
│   ├── notify.py        # Telegram mesajları ve hata teşhisi
│   └── store.py         # görülen ilan hafızası
├── tests/               # gerçek sitelerden alınmış örnek sayfalar
├── state/               # kaynak başına görülen ilan kayıtları
└── .github/workflows/watch.yml
```

## Dikkat

- Takip ettiğin sitenin `robots.txt` ve kullanım şartlarını kontrol et.
- Tarama sıklığını makul tut. Birkaç saatte bir fazlasıyla yeterli; daha sık tarama IP engeli getirir ve sana bir şey kazandırmaz.
- İlan sahiplerinin telefon/isim gibi kişisel bilgilerini toplayıp saklama.
- Topladığın ilanları başka bir yerde yayınlama — o noktada kişisel takipten çıkıp "ilan toplayıcı" olursun ve birçok site bunu açıkça yasaklar.
