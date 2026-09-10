"""İlanları config'deki kurallara göre eleme."""

from __future__ import annotations

import re
import unicodedata


def _normalize(text: str) -> str:
    """Türkçe karakterleri ve büyük/küçük harf farkını yok sayan arama metni."""
    text = text.casefold()
    text = (text.replace("ı", "i").replace("İ".casefold(), "i")
                .replace("ş", "s").replace("ğ", "g")
                .replace("ü", "u").replace("ö", "o").replace("ç", "c"))
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _haystack(item: dict) -> str:
    parts = [
        str(value)
        for key, value in item.items()
        if not key.startswith("_") and key != "price" and value
    ]
    return _normalize(" ".join(parts))


def matches(item: dict, rules: dict | None) -> tuple[bool, str]:
    """(geçti_mi, sebep) döndürür. Sebep sadece elenenler için log'da kullanılır."""
    if not rules:
        return True, ""

    price = item.get("price")

    min_price = rules.get("min_price")
    if min_price is not None:
        if price is None:
            return False, "fiyat okunamadı (min_price kuralı var)"
        if price < min_price:
            return False, f"fiyat {price:,.0f} < min {min_price:,.0f}"

    max_price = rules.get("max_price")
    if max_price is not None:
        if price is None:
            return False, "fiyat okunamadı (max_price kuralı var)"
        if price > max_price:
            return False, f"fiyat {price:,.0f} > max {max_price:,.0f}"

    text = _haystack(item)

    # include: listedeki kelimelerden EN AZ BİRİ geçmeli
    include = rules.get("include") or []
    if include and not any(_normalize(str(word)) in text for word in include):
        return False, "include kelimelerinden hiçbiri yok"

    # require_all: listedeki kelimelerin HEPSİ geçmeli
    require_all = rules.get("require_all") or []
    missing = [w for w in require_all if _normalize(str(w)) not in text]
    if missing:
        return False, f"eksik zorunlu kelime: {', '.join(missing)}"

    # exclude: listedeki kelimelerden HERHANGİ BİRİ geçerse elenir
    for word in rules.get("exclude") or []:
        if _normalize(str(word)) in text:
            return False, f"dışlanan kelime: {word}"

    # regex: serbest desen (isteğe bağlı)
    pattern = rules.get("regex")
    if pattern and not re.search(pattern, text, re.IGNORECASE):
        return False, f"regex eşleşmedi: {pattern}"

    return True, ""


def apply(items: list[dict], rules: dict | None) -> tuple[list[dict], list[tuple[dict, str]]]:
    passed, rejected = [], []
    for item in items:
        ok, reason = matches(item, rules)
        (passed if ok else rejected).append(item if ok else (item, reason))
    return passed, rejected
