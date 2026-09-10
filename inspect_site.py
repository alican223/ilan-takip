#!/usr/bin/env python3
"""
Site keşif aracı — bir ilan listesi sayfasındaki tekrar eden blokları bulur
ve config.yaml'a yapıştırabileceğin seçici önerileri çıkarır.

Kullanım:
    python inspect_site.py "https://site.com/kiralik?bolge=girne"
    python inspect_site.py "https://site.com/..." --render      # JS ile yüklenen sayfa
    python inspect_site.py "https://site.com/..." --save sayfa.html
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict

from bs4 import BeautifulSoup

from watcher.fetch import fetch
from watcher.parse import parse_price

PRICE_HINT = re.compile(
    r"(₺|tl|stg|£|\$|€|eur|usd|gbp)\s*[\d.,]{3,}|[\d.,]{3,}\s*(₺|tl|stg|£|\$|€)",
    re.IGNORECASE,
)
DATE_HINT = re.compile(
    r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|bugün|dün|dakika|saat önce|gün önce",
    re.IGNORECASE,
)


def signature(tag) -> str | None:
    """Bir elemanın 'tipi': etiket + sınıfları. Aynı imzalı elemanlar aynı şablondandır."""
    classes = tag.get("class") or []
    if not classes:
        return None
    # Sıralı ve tekrarsız — sınıf sırası değişse de aynı imza çıksın
    return f"{tag.name}." + ".".join(sorted(set(classes)))


def css_from_signature(sig: str) -> str:
    tag, _, rest = sig.partition(".")
    return tag + "".join(f".{c}" for c in rest.split(".") if c)


def field_suggestion(block, pattern: re.Pattern) -> str | None:
    """Blok içinde verilen desene uyan metni taşıyan en dar elemanın seçicisini bulur."""
    best = None
    for node in block.find_all(True):
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 60 or not pattern.search(text):
            continue
        if node.find(True):  # çocuğu varsa daha dar bir aday olabilir
            continue
        sig = signature(node) or node.name
        best = (css_from_signature(sig) if "." in sig else sig, text)
        break
    return f'{best[0]}::text   # örn: "{best[1]}"' if best else None


def title_suggestion(block) -> str | None:
    for node in block.find_all(["h1", "h2", "h3", "h4", "a", "span", "div"]):
        text = node.get_text(" ", strip=True)
        if 12 <= len(text) <= 140 and not PRICE_HINT.search(text):
            sig = signature(node) or node.name
            css = css_from_signature(sig) if "." in sig else node.name
            return f'{css}::text   # örn: "{text[:70]}"'
    return None


def link_suggestion(block) -> str | None:
    anchor = block.find("a", href=True)
    if not anchor:
        return None
    sig = signature(anchor)
    css = css_from_signature(sig) if sig else "a"
    return f'{css}::attr(href)   # örn: {anchor["href"][:70]}'


def score(blocks: list) -> float:
    """Adayın 'ilan kartı' olma ihtimali."""
    sample = blocks[:8]
    has_link = sum(1 for b in sample if b.find("a", href=True))
    has_price = sum(1 for b in sample if PRICE_HINT.search(b.get_text(" ", strip=True)))
    avg_len = sum(len(b.get_text(" ", strip=True)) for b in sample) / max(len(sample), 1)

    value = len(blocks) * 1.0
    value += has_link / max(len(sample), 1) * 40
    value += has_price / max(len(sample), 1) * 60
    if 40 <= avg_len <= 600:
        value += 30
    elif avg_len > 2000:
        value -= 40
    return value


def analyze(html: str, top: int = 5) -> None:
    soup = BeautifulSoup(html, "lxml")
    for junk in soup(["script", "style", "noscript", "svg"]):
        junk.decompose()

    groups: dict[str, list] = defaultdict(list)
    for tag in soup.find_all(True):
        sig = signature(tag)
        if sig:
            groups[sig].append(tag)

    candidates = [(sig, blocks) for sig, blocks in groups.items() if len(blocks) >= 3]
    if not candidates:
        print("⚠️  Tekrar eden sınıflı blok bulunamadı.")
        print("    Sayfa muhtemelen JavaScript ile yükleniyor → --render ile tekrar dene,")
        print("    ya da aşağıdaki API ipuçlarına bak.")
        return

    candidates.sort(key=lambda pair: score(pair[1]), reverse=True)

    print(f"\n{'='*72}\n  EN OLASI İLAN BLOKLARI\n{'='*72}")
    for rank, (sig, blocks) in enumerate(candidates[:top], 1):
        css = css_from_signature(sig)
        sample = blocks[0]
        text = sample.get_text(" ", strip=True)
        print(f"\n[{rank}] {len(blocks)} adet  —  puan {score(blocks):.0f}")
        print(f"    örnek metin: {text[:150]}{'…' if len(text) > 150 else ''}")
        print("\n    # config.yaml için:")
        print("    selectors:")
        print(f'      item: "{css}"')
        for field, suggestion in (
            ("title", title_suggestion(sample)),
            ("price", field_suggestion(sample, PRICE_HINT)),
            ("link", link_suggestion(sample)),
            ("date", field_suggestion(sample, DATE_HINT)),
        ):
            if suggestion:
                sel, _, comment = suggestion.partition("#")
                print(f'      {field}: "{sel.strip()}"' + (f"   # {comment.strip()}" if comment else ""))

        price_text = PRICE_HINT.search(text)
        if price_text:
            parsed = parse_price(price_text.group(0))
            print(f"    → fiyat okuması: {price_text.group(0)!r} = {parsed}")

    hint_api(html)


def hint_api(html: str) -> None:
    """Sayfa içinde gömülü JSON / API izleri var mı?"""
    hints = []
    if "__NEXT_DATA__" in html:
        hints.append("Next.js sayfası: <script id=\"__NEXT_DATA__\"> içinde tüm veri JSON olarak var.")
    if "window.__NUXT__" in html:
        hints.append("Nuxt sayfası: window.__NUXT__ içinde veri JSON olarak var.")
    api_urls = set(re.findall(r'["\'](/api/[\w\-/.]+)["\']', html))
    if api_urls:
        hints.append("Sayfada geçen API yolları: " + ", ".join(sorted(api_urls)[:6]))

    if hints:
        print(f"\n{'='*72}\n  API İPUÇLARI (varsa HTML kazımak yerine bunu kullan — çok daha sağlam)\n{'='*72}")
        for hint in hints:
            print(f"  • {hint}")
    print("\n  ℹ️  Tarayıcıda F12 → Network → Fetch/XHR sekmesinde sayfayı yenile;")
    print("     ilanları getiren bir JSON isteği görürsen config'de type: json kullan.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="İlan sayfası seçici keşif aracı")
    ap.add_argument("url")
    ap.add_argument("--render", action="store_true", help="headless tarayıcı ile aç")
    ap.add_argument("--save", help="indirilen HTML'i bu dosyaya yaz")
    ap.add_argument("--top", type=int, default=5, help="kaç aday gösterilsin")
    ap.add_argument("--file", help="URL yerine yerel HTML dosyasını incele")
    args = ap.parse_args()

    if args.file:
        html = open(args.file, encoding="utf-8").read()
    else:
        print(f"İndiriliyor: {args.url}")
        html = fetch(args.url, render=args.render)
        print(f"{len(html):,} karakter alındı.")

    if args.save:
        open(args.save, "w", encoding="utf-8").write(html)
        print(f"HTML kaydedildi: {args.save}")

    analyze(html, top=args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
