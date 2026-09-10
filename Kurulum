# Kendi İlan Takip Botunu Kur

Bu repo, belirlediğin ilan aramalarını düzenli tarayıp **yeni ilanları Telegram'dan sana gönderen** bir sistem. Kendi kopyanı çıkarıp kendi aramalarınla çalıştırabilirsin.

- Ücretsiz — GitHub Actions üzerinde çalışır, sunucu gerekmez
- Kod yazman gerekmez — aramalarını bir ayar dosyasına yazıyorsun
- Kurulum yaklaşık **20 dakika**

Her şey tarayıcıdan yapılır; bilgisayarına bir şey kurmana gerek yok.

---

## 1. Kendi kopyanı çıkar

Repo sayfasının üstünde **"Use this template"** düğmesi varsa onu kullan → **Create a new repository**. (Yoksa sağ üstteki **Fork**.)

- **Repository name:** `ilan-takip` (ne istersen)
- **Public / Private:**
  - **Public** seçersen GitHub Actions dakikaları sınırsız olur, ama takip ettiğin arama adresleri (hangi bölge, hangi fiyat) herkese görünür.
  - **Private** seçersen kimse göremez, ama aylık 2000 dakikalık ücretsiz kotayı kullanırsın. Varsayılan 4 saatlik tarama ayda ~180 dakika tutar, yani private repoda da rahat sığar. Sıklığı çok artırırsan (15 dakikada bir gibi) kotayı zorlarsın.

> Şifreler kopyalanmaz. Kaynak reponun Telegram bilgileri sana geçmez, sen kendi botunu kuracaksın.

---

## 2. Telegram botunu oluştur

1. Telegram'da **@BotFather**'ı bul, `/newbot` yaz.
2. Bir isim ve `_bot` ile biten bir kullanıcı adı ver.
3. Sana verdiği **token**'ı kopyala — `123456789:AAE...` şeklinde uzun bir metin.
4. **Kendi botunu aç ve ona bir mesaj at** (`START` düğmesi + "merhaba"). Bu adım şart: Telegram, kendisine hiç yazmadığın bir bottan sana mesaj gelmesine izin vermez.
5. Kendi numaranı öğrenmek için Telegram'da **@userinfobot**'a `/start` yaz. Sana `Id: 123456789` diye bir sayı verir — bu senin **chat id**'in.

---

## 3. Telegram bilgilerini GitHub'a gir

Kendi repona git: **Settings** → sol menüde **Secrets and variables** → **Actions** → **New repository secret**.

İki tane ekle:

| Name | Secret |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
| `TELEGRAM_CHAT_ID` | @userinfobot'un verdiği sayı |

İsimleri **birebir** böyle yaz, büyük harflerle.

---

## 4. Actions'ı aç ve yazma izni ver

**a) Actions'ı etkinleştir.** Üstteki **Actions** sekmesine geç. "I understand my workflows, go ahead and enable them" yazan yeşil düğme çıkarsa ona bas.

> Kopyalanan repolarda zamanlanmış görevler güvenlik gereği kapalı gelir. Bu düğmeye basmazsan bot hiç çalışmaz.

**b) Yazma izni ver.** **Settings** → sol menüde **Actions** → **General** → sayfanın altındaki **Workflow permissions** → **Read and write permissions** seç → **Save**.

> Bu izin olmadan sistem "hangi ilanları gördüm" bilgisini kaydedemez ve **her taramada aynı ilanları tekrar tekrar gönderir.**

---

## 5. Devraldığın ilan hafızasını temizle

Kopyaladığın repo, önceki sahibinin gördüğü ilanların kaydını da taşıyor. Temizle:

**Actions** → sol taraftan **İlan Takibi** → sağdaki **Run workflow** → "Ne yapılsın?" menüsünden **`reset`** → **Run workflow**.

Tek seferlik bir işlem.

---

## 6. Kendi aramalarını gir

Repo ana sayfasında **`config.yaml`** dosyasına tıkla → sağ üstteki **kalem** ikonu.

Dosyada hazır tanımlı aramalar var. Her biri şöyle görünür:

```yaml
  - name: 101evler-lefkosa-satilik
    label: "101evler — Lefkoşa Satılık 200m²+"
    enabled: true
    url: "https://www.101evler.com/kibris/satilik-konut/lefkosa?min_m2=200&..."
```

**Yapman gereken tek şey `url` satırını değiştirmek:**

1. Siteye gir, aramanı yap — bölge, oda sayısı, fiyat aralığı, ne istiyorsan.
2. Sıralamayı **"en yeni"** yap.
3. Tarayıcının adres çubuğundaki adresi kopyala.
4. `url:` satırındaki tırnak içine yapıştır.
5. `label:` satırını da kendine göre değiştir — bu, Telegram mesajlarının başlığı olacak.

`selectors:` bölümüne **dokunma** — orası sitenin sayfa yapısını tarif ediyor, aynı sitede kalıyorsan değişmez.

İlgilenmediğin bir aramayı kapatmak için `enabled: true` yerine `enabled: false` yaz.

Bitince sayfanın altındaki yeşil **Commit changes** → açılan pencerede tekrar **Commit changes**.

### Tarama sıklığı (isteğe bağlı)

`.github/workflows/watch.yml` dosyasındaki şu satır:

```yaml
    - cron: "0 */4 * * *"    # 4 saatte bir
```

Saatlik için `0 * * * *`, günde iki kez için `0 8,20 * * *`, 30 dakikada bir için `*/30 * * * *`.

> GitHub yoğun saatlerde taramayı 5–15 dakika geciktirebilir; saat gibi düzenli çalışmaz.

---

## 7. Çalışıyor mu, kontrol et

**Actions** → **İlan Takibi** → **Run workflow** → menüden **`test-notify`** → **Run workflow**.

Bu mod her aramadan **en son ilanı** Telegram'a gönderir. Yani hem botun, hem sitelerin okunması, hem mesaj görünümü tek seferde denenmiş olur. İlan hafızasına dokunmaz.

Telegram'a 🧪 TEST etiketli mesajlar düştüyse **kurulum tamam.**

Düşmediyse log'a bak, sistem sana ne olduğunu söyler:

| Log'da yazan | Anlamı ve çözümü |
|---|---|
| `HTTP 401 — Unauthorized` | Token yanlış. Secret'ı yeniden kopyala |
| `HTTP 400 — chat not found` | Chat id yanlış, ya da botuna hiç mesaj atmadın (2. adım, 4. madde) |
| `sayfada 0 kayıt bulundu` | Arama adresi yanlış ya da o sitenin yapısı değişmiş |
| `Sayfa engellenmiş görünüyor` | Site GitHub sunucularını elemiş — o siteyi `enabled: false` yap |

---

## 8. Normal işleyiş

Kurulum bitti; artık kendi kendine çalışır.

- **İlk normal taramada mesaj gelmez.** Sistem o anda sitede duran ilanları sessizce kaydeder — yoksa 30 ilan birden yağardı.
- Sonrasında siteye düşen **her yeni ilan** bir sonraki taramada (varsayılan: 4 saat) Telegram'a gelir.
- Aynı anda çok ilan çıkarsa hepsini tek bir özet mesajda toplar.
- Bir şey bozulursa (site yapısı değişti, bot çalışmıyor) Actions sekmesinde çalıştırma **kırmızı** görünür ve hata Telegram'a da bildirilir.

Ara sıra Actions sekmesine göz at: GitHub, 60 gün boyunca hiç el değmeyen repolarda zamanlanmış görevleri otomatik durduruyor. Elle bir çalıştırma yapmak bunu sıfırlar.

---

## Sık sorulanlar

**Başka bir siteyi takip edebilir miyim?**
Evet, ama o sitenin sayfa yapısını tarif etmen gerekir. Bilgisayarında şunu çalıştır:

```bash
pip install -r requirements.txt
python inspect_site.py "https://site.com/arama-adresi"
```

Araç sayfadaki ilan bloklarını bulur ve `config.yaml`'a yapıştırabileceğin bir `selectors:` bloğu önerir. Genelde **[1] numaralı aday** doğru olandır.

**Fiyat sınırı koyabilir miyim?**
Aramayı sitede yaparken fiyat filtresini kullanmak en temizi. Ek olarak `config.yaml`'da:

```yaml
    filters:
      max_price: 500000
      exclude: ["devren", "paylaşımlı"]
```

Kelime aramaları Türkçe karakter duyarsızdır (`esyali` yazsan "Eşyalı" bulur). Fiyatların hangi para biriminde olduğuna dikkat et — Kıbrıs emlak siteleri genelde sterlin gösterir.

**Bildirimler bir gruba düşsün istiyorum.**
Botu gruba ekle, grupta bir mesaj at, sonra tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` adresini aç. Grubun id'si negatif bir sayıdır (`-100...`); onu `TELEGRAM_CHAT_ID` secret'ına yaz.

**Aynı ilan iki kez geldi.**
İlan sahibi ilanı silip yeniden yayınlamıştır — site ona yeni bir numara verir, sistem de yeni ilan sayar.

**Bu yasal mı?**
Kişisel takip amacıyla, aralıklı olarak herkese açık arama sayfalarını okumak makul bir kullanım. Ancak: takip ettiğin sitenin kullanım şartlarını ve `site.com/robots.txt` dosyasını kontrol et, tarama sıklığını makul tut (birkaç saatte bir fazlasıyla yeterli), ilan sahiplerinin telefon/isim gibi bilgilerini toplayıp saklama ve topladığın ilanları başka bir yerde yayınlama. Yayınlamaya başladığın anda kişisel takipten çıkıp "ilan toplayıcı" konumuna geçersin — birçok site bunu açıkça yasaklar.
