"""Sayfadan ilan kayıtlarını çıkarma (HTML CSS seçicileri veya JSON yolları)."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# "div.price::text", "a::attr(href)", "a::own_text" gibi ifadeleri ayrıştırır
_SELECTOR_RE = re.compile(
    r"^(?P<css>.*?)(?:::(?P<mode>own_text|text|attr\((?P<attr>[^)]+)\)))?$"
)

# 1.250.000 TL / £185,000 / 45.000₺ gibi metinlerden sayı çıkarmak için
_NUM_RE = re.compile(r"\d[\d.,\s]*")


def parse_price(text: str | None) -> float | None:
    """'1.250.000 TL' -> 1250000.0 ; '£185,500' -> 185500.0 ; '12,5' -> 12.5"""
    if not text:
        return None
    match = _NUM_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    raw = match.group(0).strip().replace(" ", "")

    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        # Son gelen ayırıcı ondalık ayırıcıdır
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif has_comma:
        # Tek virgül ve sağında 1-2 hane varsa ondalık, değilse binlik
        tail = raw.rsplit(",", 1)[1]
        raw = raw.replace(",", "." if (raw.count(",") == 1 and len(tail) <= 2) else "")
    elif has_dot:
        tail = raw.rsplit(".", 1)[1]
        if not (raw.count(".") == 1 and len(tail) <= 2):
            raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError:
        return None


def _extract_one(element, selector: str, base_url: str) -> str | None:
    """Bir ilan bloğu içinden tek bir alanı çeker."""
    m = _SELECTOR_RE.match(selector.strip())
    css = (m.group("css") or "").strip()
    mode = m.group("mode")
    attr = m.group("attr")

    node = element if not css else element.select_one(css)
    if node is None:
        return None

    if attr:
        value = node.get(attr)
        if isinstance(value, list):
            value = " ".join(value)
        if value and attr in ("href", "src", "data-src") and base_url:
            value = urljoin(base_url, value)
        return value.strip() if value else None

    if mode == "own_text":
        # Sadece elemanın DOĞRUDAN metni; iç elemanların metni alınmaz.
        # Örn. <a>Başlık <span>Konum</span></a> -> "Başlık"
        own = " ".join(
            chunk.strip()
            for chunk in node.find_all(string=True, recursive=False)
            if chunk.strip()
        )
        return own or None

    text = node.get_text(" ", strip=True)
    return text or None


def _dig(obj, path: str):
    """'data.items.0.title' gibi bir yolu JSON nesnesinde takip eder."""
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def make_id(item: dict, source_name: str) -> str:
    """İlan için kalıcı, tekrarlanabilir kimlik üretir."""
    basis = item.get("id") or item.get("link") or (
        f"{item.get('title', '')}|{item.get('price_text', '')}"
    )
    digest = hashlib.sha256(f"{source_name}|{basis}".encode()).hexdigest()
    return digest[:16]


def parse_html(html: str, source: dict) -> list[dict]:
    """CSS seçicilerine göre ilan listesini çıkarır."""
    soup = BeautifulSoup(html, "lxml")
    selectors = source.get("selectors", {})
    item_selector = selectors.get("item")
    if not item_selector:
        raise ValueError(f"[{source['name']}] selectors.item tanımlı değil")

    base_url = source.get("base_url") or source["url"]
    fields = {k: v for k, v in selectors.items() if k != "item"}

    results = []
    for block in soup.select(item_selector):
        item = {}
        for field, selector in fields.items():
            item[field] = _extract_one(block, selector, base_url)
        raw_price = item.get("price")
        item["price_text"] = raw_price
        item["price"] = parse_price(raw_price)
        item["_id"] = make_id(item, source["name"])
        item["_source"] = source["name"]
        results.append(item)
    return results


def parse_json(body: str, source: dict) -> list[dict]:
    """JSON API yanıtından ilan listesini çıkarır."""
    data = json.loads(body)
    items_path = source.get("items_path")
    raw_items = _dig(data, items_path) if items_path else data
    if not isinstance(raw_items, list):
        raise ValueError(f"[{source['name']}] items_path bir liste vermiyor: {items_path}")

    fields = source.get("fields", {})
    base_url = source.get("base_url") or source["url"]

    results = []
    for raw in raw_items:
        item = {}
        for field, path in fields.items():
            value = _dig(raw, path)
            if field == "link" and isinstance(value, str):
                value = urljoin(base_url, value)
            item[field] = value
        price_source = item.get("price")
        item["price_text"] = (
            None if price_source is None else str(price_source)
        )
        item["price"] = (
            float(price_source) if isinstance(price_source, (int, float))
            else parse_price(item["price_text"])
        )
        item["_id"] = make_id(item, source["name"])
        item["_source"] = source["name"]
        results.append(item)
    return results


def parse(body: str, source: dict) -> list[dict]:
    kind = source.get("type", "html")
    if kind == "json":
        return parse_json(body, source)
    return parse_html(body, source)
