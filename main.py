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
import logging
import random
import re
import sys
import time
from pathlib import Path

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


def run_source(source: dict, notifier: notify.TelegramNotifier,
               defaults: dict) -> tuple[int, int]:
    """Tek bir kaynağı tarar. (yeni_ilan_sayısı, gönderilen_mesaj) döner."""
    name = source["name"]
    label = source.get("label", name)
    store = SeenStore(name)

    body = fetch(
        source["url"],
        render=source.get("render", False),
        timeout=source.get("timeout", defaults.get("timeout", 30)),
        headers=source.get("headers"),
        wait_selector=source.get("wait_selector"),
        warmup_url=source.get("warmup_url"),
    )

    items = parse.parse(body, source)
    log.info("[%s] sayfada %d kayıt bulundu", name, len(items))
    if not items:
        _diagnose_empty(name, body, source)
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
            store.mark(item["_id"])
        store.save()
        return 0, 0

    sent = 0
    if new_items:
        digest_threshold = source.get("digest_threshold",
                                      defaults.get("digest_threshold", 6))
        if len(new_items) >= digest_threshold:
            if notifier.send(notify.format_digest(new_items, label)):
                sent += 1
                for item in new_items:
                    store.mark(item["_id"])
        else:
            for item in new_items:
                if notifier.send(notify.format_item(item, label)):
                    sent += 1
                    store.mark(item["_id"])
                    time.sleep(1)  # Telegram'ı yormayalım

    store.save()
    return len(new_items), sent


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

    for index, source in enumerate(sources):
        if not source.get("enabled", True):
            log.info("[%s] devre dışı, atlanıyor", source["name"])
            continue
        try:
            new_count, sent = run_source(source, notifier, defaults)
            total_new += new_count
            total_sent += sent
        except (FetchError, ValueError) as exc:
            log.error("[%s] HATA: %s", source["name"], exc)
            failures.append(f"{source['name']}: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] beklenmeyen hata", source["name"])
            failures.append(f"{source['name']}: {exc}")

        if index < len(sources) - 1:
            time.sleep(defaults.get("delay_between_sources", 3) + random.uniform(0, 2))

    log.info("Bitti — %d yeni ilan, %d mesaj gönderildi.", total_new, total_sent)

    # Hataları da bildir ki sistem sessizce ölmesin
    if failures and config.get("notify_errors", True) and not args.dry_run:
        notifier.send("⚠️ <b>Takip hatası</b>\n" + "\n".join(f"• {f}" for f in failures[:5]))

    # Telegram gönderimi patladıysa çalıştırma YEŞİL görünmemeli —
    # yoksa bildirim gitmediği hâlde her şey yolunda sanılır.
    if notifier.failures:
        log.error("%d bildirim gönderilemedi. `python main.py --test-notify` "
                  "ile Telegram ayarlarını kontrol et.", notifier.failures)
        return 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
