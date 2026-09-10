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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="128", "Not(A:Brand";v="24", "Google Chrome";v="128"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Connection": "keep-alive",
}

# Tekrar denemenin işe yaramayacağı kalıcı hatalar
PERMANENT_STATUSES = (400, 401, 403, 404, 410, 451)


class FetchError(RuntimeError):
    pass


class PermanentFetchError(FetchError):
    """Tekrar denemenin çözmeyeceği hata (403, 404 gibi)."""


def fetch(url: str, *, render: bool = False, timeout: int = 30,
          headers: dict | None = None, wait_selector: str | None = None,
          retries: int = 3, warmup_url: str | None = None) -> str:
    """URL'nin HTML/JSON gövdesini string olarak döndürür.

    warmup_url verilirse önce o sayfa ziyaret edilir; böylece sitenin
    kurduğu çerezler alınır ve asıl istek "sitede gezinen kullanıcı" gibi
    görünür. Bot korumalı sitelerde 403'ü aşmaya yarayabilir.
    """
    if render:
        return _fetch_rendered(url, timeout=timeout, wait_selector=wait_selector)
    return _fetch_plain(url, timeout=timeout, headers=headers, retries=retries,
                        warmup_url=warmup_url)


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


def _fetch_plain(url: str, *, timeout: int, headers: dict | None, retries: int,
                 warmup_url: str | None = None) -> str:
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    session = requests.Session()
    session.headers.update(merged)

    if warmup_url:
        try:
            session.get(warmup_url, timeout=timeout)
            time.sleep(1.0 + random.uniform(0, 0.8))
            session.headers["Referer"] = warmup_url
        except Exception as exc:  # noqa: BLE001
            log.debug("ısınma isteği başarısız (%s), yine de devam ediliyor", exc)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)

            if resp.status_code in PERMANENT_STATUSES:
                raise PermanentFetchError(
                    f"HTTP {resp.status_code} — site bu isteği reddetti. "
                    "Muhtemelen sunucunun IP'si bot koruması tarafından "
                    "engelleniyor. Çözüm: kaynağa warmup_url/headers ekle, "
                    "render: true dene ya da taramayı kendi sunucunda çalıştır."
                )
            # 429 / 5xx geçici — bekleyip tekrar dene
            if resp.status_code in (429, 500, 502, 503, 504):
                raise FetchError(f"HTTP {resp.status_code}")

            resp.raise_for_status()
            resp.encoding = _best_encoding(resp)
            return resp.text

        except PermanentFetchError:
            raise  # tekrar denemenin anlamı yok
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
