"""Sayfa indirme: düz HTTP veya (gerekirse) headless tarayıcı."""

from __future__ import annotations

import logging
import random
import re
import time

import requests

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


class FetchError(RuntimeError):
    pass


def fetch(url: str, *, render: bool = False, timeout: int = 30,
          headers: dict | None = None, wait_selector: str | None = None,
          retries: int = 3) -> str:
    """URL'nin HTML/JSON gövdesini string olarak döndürür."""
    if render:
        return _fetch_rendered(url, timeout=timeout, wait_selector=wait_selector)
    return _fetch_plain(url, timeout=timeout, headers=headers, retries=retries)


_META_CHARSET_RE = re.compile(
    rb'<meta[^>]+charset=["\']?\s*([\w\-]+)', re.IGNORECASE
)


def _best_encoding(resp: requests.Response) -> str:
    """Türkçe karakterlerin bozulmaması için doğru kodlamayı seç.

    requests, Content-Type başlığında charset yoksa HTML için ISO-8859-1
    varsayar; bu da 'Eşyalı' -> 'EÅyalÄ±' bozulmasına yol açar.
    """
    content_type = resp.headers.get("Content-Type", "")
    if "charset=" in content_type.lower():
        return resp.encoding

    match = _META_CHARSET_RE.search(resp.content[:4096])
    if match:
        try:
            candidate = match.group(1).decode("ascii").lower()
            "".encode(candidate)  # kodlama adı geçerli mi
            return candidate
        except (LookupError, UnicodeDecodeError):
            pass

    return resp.apparent_encoding or "utf-8"


def _fetch_plain(url: str, *, timeout: int, headers: dict | None, retries: int) -> str:
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=merged, timeout=timeout)
            # 429 / 5xx geçici — bekleyip tekrar dene
            if resp.status_code in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            resp.encoding = _best_encoding(resp)
            return resp.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                delay = (2 ** attempt) + random.uniform(0, 1)
                log.warning("fetch hata (%s/%s) %s — %.1fs sonra tekrar",
                            attempt, retries, exc, delay)
                time.sleep(delay)
    raise FetchError(f"{url} indirilemedi: {last_error}")


def _fetch_rendered(url: str, *, timeout: int, wait_selector: str | None) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise FetchError(
            "Bu kaynak render:true istiyor ama playwright kurulu değil. "
            "`pip install playwright && playwright install chromium` çalıştır."
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="tr-TR",
            )
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout * 1000)
            else:
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            return page.content()
        finally:
            browser.close()
