#!/usr/bin/env python3
"""
İlan takip sistemi — config.yaml'daki kaynakları tarar, filtreye uyan
YENİ ilanları Telegram'a gönderir.

Kullanım:
    python main.py                    # normal çalıştırma
    python main.py --dry-run          # Telegram'a göndermeden ekrana yaz
    python main.py --source emlak-101 # tek kaynağı çalıştır
    python main.py --reset            # görülen ilan kayıtlarını sıfırla
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from watcher import filters, notify, parse
from watcher.fetch import FetchError, fetch
from watcher.store import STATE_DIR, SeenStore

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("watcher")


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    if not config.get("sources"):
        raise SystemExit(f"{path} içinde 'sources' listesi yok.")
    return config


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_BLOCK_WORDS = ("captcha", "cloudflare", "access denied", "are you a robot",
                "attention required", "erişim engellendi", "bot detection",
                "just a moment", "payment required")


def _diagnose_empty(name: str, body: str, source: dict) -> None:
    """0 kayıt çıktığında sebebi log'dan anlaşılsın: engel mi, yanlış seçici mi?"""
    title_match = _TITLE_RE.search(body)
    title = " ".join(title_match.group(1).split())[:90] if title_match else "(başlık yok)"
    log.warning("[%s] TEŞHİS → sayfa boyutu: %s karakter | <title>: %s",
                name, f"{len(body):,}", title)

    # HTML'de en az %0.5 oranında '<' bulunur. Çok altındaysa gövde
    # çözülmemiş (brotli gibi) ya da ikili veridir.
    sample = body[:20000]
    if sample and sample.count("<") < len(sample) * 0.005:
        log.warning("[%s] Gövde HTML'e benzemiyor — muhtemelen çözülemeyen bir "
                    "sıkıştırma (brotli) geldi. Accept-Encoding başlığını elle "
                    "ayarlama, requests'e bırak.", name)
        return

    lowered = body[:6000].lower()
    hit = next((w for w in _BLOCK_WORDS if w in lowered), None)
    if hit or len(body) < 5000:
        log.warning("[%s] Sayfa engellenmiş görünüyor (ipucu: %s). Site bu sunucunun "
                    "IP'sini elemiş olabilir → render: true dene ya da kendi sunucuna taşı.",
                    name, hit or "sayfa çok küçük")
        return

    item_sel = (source.get("selectors") or {}).get("item", "?")
    log.warning("[%s] Sayfa normal indirilmiş ama '%s' seçicisi hiçbir şeyle eşleşmiyor "
                "→ selectors.item yanlış. `python inspect_site.py \"%s\"` ile kontrol et.",
                name, item_sel, source["url"])


def _sayfa_url(url: str, param: str, sayfa: int) -> str:
    """URL'deki sayfa parametresini verilen değere ayarlar (yoksa ekler)."""
    parcalar = urlsplit(url)
    sorgu = parse_qsl(parcalar.query, keep_blank_values=True)
    sorgu = [(k, v) for k, v in sorgu if k != param]
    sorgu.append((param, str(sayfa)))
    return urlunsplit(parcalar._replace(query=urlencode(sorgu)))


def _fetch_pages(source: dict, defaults: dict) -> tuple[list[dict], str]:
    """Kaynağın ilk N sayfasını okur. (ilanlar, ilk_sayfanın_gövdesi) döner.

    Fiyat takibi için önemli: indirimler genelde bir süredir bekleyen,
    1. sayfadan düşmüş ilanlarda olur. `pages` bunları da kapsar.
    """
    name = source["name"]
    kac = max(1, int(source.get("pages", defaults.get("pages", 1))))
    param = source.get("page_param", defaults.get("page_param", "page"))
    bekleme = float(source.get("delay_between_pages",
                               defaults.get("delay_between_pages", 2)))

    hepsi: list[dict] = []
    gorulen: set[str] = set()
    ilk_govde = ""

    for sayfa in range(1, kac + 1):
        url = source["url"] if kac == 1 else _sayfa_url(source["url"], param, sayfa)
        govde = fetch(
            url,
            render=source.get("render", False),
            timeout=source.get("timeout", defaults.get("timeout", 30)),
            headers=source.get("headers"),
            wait_selector=source.get("wait_selector"),
            warmup_url=source.get("warmup_url") if sayfa == 1 else None,
        )
        if sayfa == 1:
            ilk_govde = govde

        sayfa_ilanlari = parse.parse(govde, source)
        if not sayfa_ilanlari:
            if sayfa > 1:
                log.debug("[%s] %d. sayfa boş, durduruluyor", name, sayfa)
            break

        # Site sayfa parametresini yok sayıyorsa aynı ilanlar gelir — tekrarı at
        yeni = [i for i in sayfa_ilanlari if i["_id"] not in gorulen]
        if sayfa > 1 and not yeni:
            log.warning("[%s] %d. sayfa 1. sayfayla aynı geldi — sayfalama "
                        "çalışmıyor olabilir (page_param yanlış?)", name, sayfa)
            break

        gorulen.update(i["_id"] for i in yeni)
        hepsi.extend(yeni)

        if sayfa < kac:
            time.sleep(bekleme)

    if kac > 1:
        log.info("[%s] %d sayfadan toplam %d ilan okundu", name, kac, len(hepsi))
    return hepsi, ilk_govde


def run_source(source: dict, notifier: notify.TelegramNotifier,
               defaults: dict, stats_out: list | None = None) -> tuple[int, int]:
    """Tek bir kaynağı tarar. (yeni_ilan_sayısı, gönderilen_mesaj) döner.

    stats_out verilirse günlük özet için kaynak durumu oraya eklenir.
    """
    name = source["name"]
    label = source.get("label", name)
    store = SeenStore(name)

    durum = {
        "name": name, "label": label, "sayfa": 0, "hafiza": 0,
        "gun_gecti": None, "yeni_24s": 0,
        "stale_limit": source.get("stale_after_days",
                                  defaults.get("stale_after_days", 7)),
    }

    def _kaydet():
        durum["hafiza"] = len(store)
        durum["gun_gecti"] = store.days_since_last_new()
        durum["yeni_24s"] = store.new_since(86400)
        if stats_out is not None:
            stats_out.append(durum)

    items, body = _fetch_pages(source, defaults)
    durum["sayfa"] = len(items)
    log.info("[%s] sayfada %d kayıt bulundu", name, len(items))
    if not items:
        _diagnose_empty(name, body, source)
        durum["hata"] = "sayfada hiç ilan bulunamadı (seçici ya da engel sorunu)"
        _kaydet()
        return 0, 0

    passed, rejected = filters.apply(items, source.get("filters"))
    log.info("[%s] filtreden geçen: %d, elenen: %d", name, len(passed), len(rejected))
    for item, reason in rejected[:3]:
        log.debug("[%s] elendi: %s (%s)", name, item.get("title"), reason)

    new_items = [item for item in passed if store.is_new(item["_id"])]

    # İlk çalıştırmada mevcut ilanlar "yeni" sayılmaz — 200 mesajlık sel olmasın
    first_run_silent = store.is_first_run and not source.get("notify_on_first_run", False)
    if first_run_silent and new_items:
        log.info("[%s] ilk çalıştırma: %d ilan sessizce kaydedildi", name, len(new_items))
        for item in new_items:
            store.mark(item["_id"], item.get("price"))
        store.save()
        _kaydet()
        return 0, 0

    # Bu kaynak sadece fiyat değişimi için mi? (örn. "güncellenenler" ya da
    # "fiyatı düşenler" beslemesi) O zaman ilk kez görülen ilanlar "yeni ilan"
    # değildir — sadece o beslemeye yeni düşmüşlerdir. Sessizce kaydedilir.
    if not source.get("notify_new", True):
        # ÖNCE fiyat değişimlerini topla: site eski fiyatı kendisi veriyorsa
        # (prev_price_field) ilanı ilk kez görsek bile indirimi yakalayalım.
        price_changes = _collect_price_changes(passed, store, source,
                                               defaults, name)
        if new_items:
            log.info("[%s] %d ilan bu beslemeye ilk kez düştü, sessizce "
                     "kaydedildi (notify_new: false)", name, len(new_items))
            for item in new_items:
                store.mark(item["_id"], item.get("price"))
        sent = 0
        for item, eski, yeni in price_changes:
            if notifier.send(notify.format_price_change(item, eski, yeni, label)):
                sent += 1
                store.update_price(item["_id"], yeni)
                time.sleep(1)
        for item in passed:
            if not store.is_new(item["_id"]) and store.price_of(item["_id"]) is None:
                store.update_price(item["_id"], item.get("price"))
        store.save()
        _kaydet()
        return 0, sent

    # EMNİYET SUPABI: bir anda çok fazla "yeni" ilan çıktıysa bu gerçek bir
    # akın değil, kapsam değişikliğidir (sayfa sayısı arttı, filtre gevşedi,
    # dedup anahtarı değişti...). 150 mesajla boğmak yerine sessizce yut.
    max_new = int(source.get("max_new_per_run",
                             defaults.get("max_new_per_run", 25)))
    if len(new_items) > max_new:
        log.warning("[%s] tek seferde %d yeni ilan çıktı (sınır %d) — kapsam "
                    "değişmiş olmalı, hepsi sessizce kaydedildi.",
                    name, len(new_items), max_new)
        for item in new_items:
            store.mark(item["_id"], item.get("price"))
        store.save()
        notifier.send(
            f"ℹ️ <b>{notify._esc(label)}</b>\n"
            f"Tek taramada {len(new_items)} yeni ilan çıktı — bu normal bir akın "
            f"değil, kapsam değişikliği gibi görünüyor (sayfa sayısı artmış "
            f"olabilir).\nHepsi sessizce kaydedildi; bundan sonraki yeni ilanlar "
            f"normal şekilde bildirilecek."
        )
        _kaydet()
        return len(new_items), 1

    # Zaten bildiğimiz ilanların fiyatı değişmiş mi?
    price_changes = _collect_price_changes(passed, store, source, defaults, name)

    sent = 0
    if new_items:
        digest_threshold = source.get("digest_threshold",
                                      defaults.get("digest_threshold", 6))
        if len(new_items) >= digest_threshold:
            if notifier.send(notify.format_digest(new_items, label)):
                sent += 1
                for item in new_items:
                    store.mark(item["_id"], item.get("price"))
        else:
            for item in new_items:
                if notifier.send(notify.format_item(item, label)):
                    sent += 1
                    store.mark(item["_id"], item.get("price"))
                    time.sleep(1)  # Telegram'ı yormayalım

    for item, eski, yeni in price_changes:
        if notifier.send(notify.format_price_change(item, eski, yeni, label)):
            sent += 1
            store.update_price(item["_id"], yeni)
            time.sleep(1)

    # Fiyatı değişmemiş ilanların kaydını da tazele (eski biçimden gelen
    # fiyatsız kayıtlara fiyat yazılsın diye)
    for item in passed:
        if not store.is_new(item["_id"]) and store.price_of(item["_id"]) is None:
            store.update_price(item["_id"], item.get("price"))

    store.save()
    _kaydet()

    # Uzun süredir yeni ilan gelmiyorsa bu bir arıza olabilir — sessizce
    # geçme. En sık sebebi arama adresinin "en yeni" sıralı olmaması.
    if not new_items:
        gecen = store.days_since_last_new()
        limit = source.get("stale_after_days", defaults.get("stale_after_days", 7))
        if gecen is not None and gecen > limit:
            log.warning("[%s] %.0f gündür yeni ilan yok. Arama adresi 'en yeni' "
                        "sıralı mı? `python main.py --doctor` ile kontrol et.",
                        name, gecen)

    return len(new_items), sent


DAILY_STATE = STATE_DIR / "_gunluk_ozet.json"


def _maybe_send_daily_summary(config: dict, notifier: notify.TelegramNotifier,
                              stats: list[dict], dry_run: bool) -> None:
    """Günde bir kez 'sistem çalışıyor' özeti gönderir.

    Tarama 4 saatte bir çalıştığı için özet, ayarlanan saatten sonraki
    İLK taramada gider. Gönderim tarihi state/_gunluk_ozet.json'da tutulur.
    """
    defaults = config.get("defaults", {})
    if not defaults.get("daily_summary", False) or not stats:
        return

    hedef_saat = int(defaults.get("daily_summary_hour", 11))
    offset = float(defaults.get("timezone_offset", 0))
    simdi = datetime.now(timezone.utc) + timedelta(hours=offset)
    bugun = simdi.strftime("%Y-%m-%d")

    try:
        kayit = json.loads(DAILY_STATE.read_text("utf-8")) if DAILY_STATE.exists() else {}
    except (json.JSONDecodeError, OSError):
        kayit = {}

    if kayit.get("son") == bugun:
        return                                  # bugün zaten gönderildi
    if simdi.hour < hedef_saat:
        return                                  # saati henüz gelmedi

    metin = notify.format_daily_summary(stats, simdi.strftime("%d.%m.%Y %H:%M"))
    if notifier.send(metin):
        log.info("Günlük özet gönderildi.")
        if not dry_run:
            try:
                DAILY_STATE.parent.mkdir(parents=True, exist_ok=True)
                DAILY_STATE.write_text(json.dumps({"son": bugun}), "utf-8")
            except OSError as exc:
                log.warning("Günlük özet tarihi yazılamadı: %s", exc)


def _collect_price_changes(passed: list[dict], store: SeenStore, source: dict,
                           defaults: dict, name: str) -> list[tuple[dict, float, float]]:
    """Daha önce görülmüş ilanlardan fiyatı değişenleri bulur.

    `notify_price_changes`: "any" (varsayılan) | "down" | "none"
    `price_change_min_pct`: bu yüzdenin altındaki oynamalar yok sayılır.
    """
    mod = str(source.get("notify_price_changes",
                         defaults.get("notify_price_changes", "any"))).lower()
    if mod in ("none", "false", "hayir", "hayır"):
        return []
    min_pct = float(source.get("price_change_min_pct",
                               defaults.get("price_change_min_pct", 0.5)))
    # Bazı siteler indirimli ilanda ESKİ fiyatı da yazar (hangiev'in
    # "Fiyatı Düşenler" beslemesi gibi). Böyle bir alan tanımlıysa, ilanı
    # ilk kez görsek bile indirimi bildirebiliriz.
    onceki_alan = source.get("prev_price_field")

    degisenler = []
    for item in passed:
        yeni = item.get("price")
        eski = store.price_of(item["_id"])
        if eski is None and onceki_alan:
            eski = parse.parse_price(item.get(onceki_alan))
        if eski is None and store.is_new(item["_id"]):
            continue                      # karşılaştıracak eski fiyat yok
        if eski is None or yeni is None or eski <= 0 or eski == yeni:
            continue
        if abs(yeni - eski) / eski * 100 < min_pct:
            continue          # kur/yuvarlama kaynaklı ufak oynamalar
        if mod == "down" and yeni > eski:
            continue
        degisenler.append((item, eski, yeni))

    if degisenler:
        log.info("[%s] fiyatı değişen %d ilan", name, len(degisenler))
    return degisenler


def run_doctor(config: dict, defaults: dict) -> int:
    """Her kaynağın sağlığını raporlar: sessizlik hata mı, yoksa gerçekten
    yeni ilan mı yok — ayırt etmeye yarar. Hiçbir şeyi değiştirmez."""
    stale_days = config.get("stale_after_days", 7)
    sources = [s for s in config["sources"] if s.get("enabled", True)]
    sorunlu = 0

    print()
    for source in sources:
        name, label = source["name"], source.get("label", source["name"])
        store = SeenStore(name)
        print("=" * 68)
        print(f"  {label}   [{name}]")
        print("=" * 68)

        gecen = store.days_since_last_new()
        print(f"  Hafıza      : {len(store)} ilan kayıtlı", end="")
        print(f" | son yeni ilan {gecen:.1f} gün önce" if gecen is not None
              else " | henüz hiç kayıt yok")

        try:
            items, body = _fetch_pages(source, defaults)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ SAYFA İNDİRİLEMEDİ: {exc}\n")
            sorunlu += 1
            continue

        if not items:
            print("  ✗ Sayfada hiç ilan bulunamadı.")
            _diagnose_empty(name, body, source)
            print()
            sorunlu += 1
            continue

        passed, _ = filters.apply(items, source.get("filters"))
        yeni = [i for i in passed if store.is_new(i["_id"])]
        gorulmus = len(passed) - len(yeni)

        print(f"  Canlı sayfa : {len(items)} ilan | filtreden geçen {len(passed)}"
              f" | bunların {gorulmus}'i zaten görülmüş, {len(yeni)}'i yeni")
        if passed:
            print("  Sayfadaki ilk 5 ilan:")
            for sira, it in enumerate(passed[:5], 1):
                isaret = "★ YENİ     " if store.is_new(it["_id"]) else "✓ görülmüş "
                no = f"{it['id']:>9}  " if it.get("id") else ""
                tarih = f"  [{it['date']}]" if it.get("date") else ""
                print(f"    {sira}. {isaret}{no}{(it.get('title') or '?')[:38]}{tarih}")
            print("    (Site tarihi 'güncelleme' tarihi olabilir — emlakçı ilanı")
            print("     yukarı taşıyınca tarih tazelenir ama ilan aynı ilandır.)")

        # TEŞHİS
        if yeni:
            print(f"  → SAĞLIKLI. {len(yeni)} yeni ilan var, sıradaki taramada gelecek.")
        elif gecen is not None and gecen > stale_days:
            print(f"  → {gecen:.0f} gündür yeni ilan yok; sayfadaki {len(passed)} ilanın")
            print("    hepsi zaten görülmüş. İki olasılık var:")
            print("    1) Filtren dar ve siteye gerçekten yeni ilan girmemiş.")
            print("       NORMALDİR. Yukarıdaki listede ilk sıradaki ilan uzun")
            print("       süredir aynıysa ve sitede de o en üstteyse durum budur.")
            print("    2) Arama adresi 'en yeni' sıralı değil — o zaman yeni")
            print("       ilanlar 1. sayfaya hiç düşmez ve sistem onları göremez.")
            print("       Siteye gir, sıralamayı 'Yeni Eklenenler' yap, adres")
            print("       çubuğundaki YENİ adresi config.yaml'daki url'ye koy.")
            sorunlu += 1
        else:
            print("  → NORMAL. Sayfa okunuyor, şu an yeni ilan yok.")
        print()

    print("=" * 68)
    if sorunlu:
        print(f"  {sorunlu} kaynakta dikkat edilmesi gereken durum var.")
    else:
        print("  Tüm kaynaklar sağlıklı.")
    print()
    return 1 if sorunlu else 0


def run_test_notify(config: dict, notifier: notify.TelegramNotifier) -> int:
    """Her kaynaktaki EN SON ilanı Telegram'a gönderir.

    Zinciri baştan sona dener: indirme → ayrıştırma → biçimlendirme → gönderim.
    "Görülen ilanlar" hafızasına dokunmaz, yani normal takibi bozmaz.
    """
    if not notifier.check_token():
        log.error("TELEGRAM TESTİ BAŞARISIZ — token sorunu.")
        return 1

    sources = [s for s in config["sources"] if s.get("enabled", True)]
    if not sources:
        log.error("Etkin kaynak yok.")
        return 1

    problems = 0
    for index, source in enumerate(sources):
        name, label = source["name"], source.get("label", source["name"])
        try:
            body = fetch(
                source["url"],
                render=source.get("render", False),
                timeout=source.get("timeout", 30),
                headers=source.get("headers"),
                wait_selector=source.get("wait_selector"),
                warmup_url=source.get("warmup_url"),
            )
            items = parse.parse(body, source)
            if not items:
                log.error("[%s] sayfada hiç ilan bulunamadı — seçicileri kontrol et.",
                          name)
                problems += 1
                continue

            newest = items[0]   # sıralama "en yeni" olduğu için ilk kayıt
            log.info("[%s] en son ilan: %s", name, (newest.get("title") or "?")[:60])

            text = ("🧪 <b>TEST</b> — aşağıdaki, bu sitedeki en son ilan\n\n"
                    + notify.format_item(newest, label))
            if not notifier.send(text):
                problems += 1
            time.sleep(1)

        except Exception as exc:  # noqa: BLE001
            log.error("[%s] test başarısız: %s", name, exc)
            problems += 1

        if index < len(sources) - 1:
            time.sleep(2)

    if problems:
        log.error("TELEGRAM TESTİ BAŞARISIZ — %d kaynakta sorun var.", problems)
        return 1
    log.info("TELEGRAM TESTİ BAŞARILI — %d mesaj gönderildi, Telegram'ı kontrol et.",
             len(sources))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="İlan takip sistemi")
    ap.add_argument("--config", default="config.yaml", help="config dosyası")
    ap.add_argument("--dry-run", action="store_true",
                    help="Telegram'a göndermeden ekrana yaz")
    ap.add_argument("--source", action="append",
                    help="sadece bu kaynağı çalıştır (birden fazla verilebilir)")
    ap.add_argument("--reset", action="store_true",
                    help="görülen ilan kayıtlarını sil ve çık")
    ap.add_argument("--test-notify", action="store_true",
                    help="Telegram ayarlarını dene: test mesajı gönder ve çık")
    ap.add_argument("--doctor", action="store_true",
                    help="kaynakların sağlığını raporla (hiçbir şeyi değiştirmez)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.reset:
        removed = 0
        for f in STATE_DIR.glob("*.json"):
            f.unlink()
            removed += 1
        log.info("%d durum dosyası silindi.", removed)
        return 0

    config = load_config(ROOT / args.config)

    if args.doctor:
        return run_doctor(config, config.get("defaults", {}))

    if args.test_notify:
        try:
            tester = notify.TelegramNotifier(dry_run=args.dry_run)
        except RuntimeError as exc:
            log.error("%s", exc)
            return 1
        return run_test_notify(config, tester)
    defaults = config.get("defaults", {})
    sources = config["sources"]
    if args.source:
        wanted = set(args.source)
        sources = [s for s in sources if s["name"] in wanted]
        if not sources:
            log.error("Eşleşen kaynak yok: %s", ", ".join(wanted))
            return 1

    notifier = notify.TelegramNotifier(dry_run=args.dry_run)

    total_new = total_sent = 0
    failures: list[str] = []
    stats: list[dict] = []

    for index, source in enumerate(sources):
        if not source.get("enabled", True):
            log.info("[%s] devre dışı, atlanıyor", source["name"])
            continue
        try:
            new_count, sent = run_source(source, notifier, defaults, stats)
            total_new += new_count
            total_sent += sent
        except (FetchError, ValueError) as exc:
            log.error("[%s] HATA: %s", source["name"], exc)
            failures.append(f"{source['name']}: {exc}")
            stats.append({"name": source["name"],
                          "label": source.get("label", source["name"]),
                          "hata": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] beklenmeyen hata", source["name"])
            failures.append(f"{source['name']}: {exc}")
            stats.append({"name": source["name"],
                          "label": source.get("label", source["name"]),
                          "hata": str(exc)})

        if index < len(sources) - 1:
            time.sleep(defaults.get("delay_between_sources", 3) + random.uniform(0, 2))

    log.info("Bitti — %d yeni ilan, %d mesaj gönderildi.", total_new, total_sent)

    # Hataları da bildir ki sistem sessizce ölmesin
    if failures and config.get("notify_errors", True) and not args.dry_run:
        notifier.send("⚠️ <b>Takip hatası</b>\n" + "\n".join(f"• {f}" for f in failures[:5]))

    # Günde bir kez "sistem çalışıyor" özeti
    _maybe_send_daily_summary(config, notifier, stats, args.dry_run)

    # Telegram gönderimi patladıysa çalıştırma YEŞİL görünmemeli —
    # yoksa bildirim gitmediği hâlde her şey yolunda sanılır.
    if notifier.failures:
        log.error("%d bildirim gönderilemedi. `python main.py --test-notify` "
                  "ile Telegram ayarlarını kontrol et.", notifier.failures)
        return 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
