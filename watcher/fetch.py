"""Sayfa indirme: düz HTTP veya (gerekirse) headless tarayıcı."""

from __future__ import annotations

import logging
import random
import re
import time

import requests

log = logging.getLogger(__name__)

# Tarayıcı sürümü. Bot korumaları ESKİMİŞ sürümleri eler — yılda bir
# güncelle. config.yaml'da `user_agent` yazarak koda dokunmadan da
# değiştirebilirsin. Güncel sürümü öğrenmek için kendi tarayıcında:
#   konsola `navigator.userAgent` yaz.
CHROME_VERSION = "152"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36"
)

_CHROME_SURUM_RE = re.compile(r"Chrome/(\d+)")


def build_headers(user_agent: str | None = None) -> dict:
    """Gerçek bir tarayıcı gezintisine benzeyen başlıkları üretir.

    Sec-CH-UA, User-Agent'taki sürümden türetilir; ikisi birbirinden
    sapamaz (sapması bot korumalarının yakaladığı tipik bir tutarsızlıktır).
    """
    ua = user_agent or DEFAULT_USER_AGENT
    eslesme = _CHROME_SURUM_RE.search(ua)
    surum = eslesme.group(1) if eslesme else CHROME_VERSION
    return {
        "User-Agent": ua,
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        # DİKKAT: Accept-Encoding'i ELLE AYARLAMA. requests yalnızca
        # açabildiği yöntemleri ister (gzip, deflate). Buraya "br" eklenirse
        # site brotli gönderir, requests açamaz ve elimize çözülmemiş ikili
        # veri geçer — sayfa sağlam iner ama hiçbir seçici tutmaz.
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": (f'"Chromium";v="{surum}", "Not(A:Brand";v="24", '
                      f'"Google Chrome";v="{surum}"'),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Connection": "keep-alive",
    }


DEFAULT_HEADERS = build_headers()


def make_session(headers: dict | None = None,
                 user_agent: str | None = None) -> requests.Session:
    """Çerezleri taşıyan bir oturum açar.

    Çerez ŞART olan siteler var (hangiev gibi): ana sayfayı ziyaret edip
    çerez almadan arama sayfası 403 döndürüyor. Aynı oturumun tüm
    sayfalarda kullanılması gerekir, yoksa çerez her istekte kaybolur.
    """
    s = requests.Session()
    s.headers.update(build_headers(user_agent))
    if headers:
        s.headers.update(headers)
    return s


def warmup(session: requests.Session, url: str, timeout: int = 30) -> bool:
    """Ana sayfayı ziyaret edip çerezleri alır, Referer'ı ayarlar.

    Sonucu AÇIKÇA log'lar: ısınmanın kendisi de reddedildiyse engel
    büyük ihtimalle başlık/çerez değil, IP tabanlıdır. Bunu bilmek
    hangi çözümü deneyeceğimizi belirler.
    """
    try:
        resp = session.get(url, timeout=timeout)
        cerez = len(session.cookies)
        session.headers["Referer"] = url
        time.sleep(1.0 + random.uniform(0, 0.8))

        if resp.status_code == 200:
            log.info("ısınma OK: %s → HTTP 200, %d çerez alındı", url, cerez)
            return True
        log.warning("ısınma REDDEDİLDİ: %s → HTTP %s, %d çerez. Ana sayfa bile "
                    "açılmıyorsa engel başlık/çerez kaynaklı DEĞİL, bu sunucunun "
                    "IP'si engelleniyor demektir.", url, resp.status_code, cerez)
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("ısınma isteği başarısız (%s), yine de devam ediliyor", exc)
        return False

# Tekrar denemenin işe yaramayacağı kalıcı hatalar
PERMANENT_STATUSES = (400, 401, 403, 404, 410, 451)


class FetchError(RuntimeError):
    pass


class PermanentFetchError(FetchError):
    """Tekrar denemenin çözmeyeceği hata (403, 404 gibi)."""


def fetch(url: str, *, render: bool = False, timeout: int = 30,
          headers: dict | None = None, wait_selector: str | None = None,
          retries: int = 3, warmup_url: str | None = None,
          session: requests.Session | None = None,
          user_agent: str | None = None) -> str:
    """URL'nin HTML/JSON gövdesini string olarak döndürür.

    warmup_url verilirse önce o sayfa ziyaret edilir; böylece sitenin
    kurduğu çerezler alınır ve asıl istek "sitede gezinen kullanıcı" gibi
    görünür. Bot korumalı sitelerde 403'ü aşmaya yarar.

    session verilirse o oturum kullanılır (çerezler korunur).
    """
    if render:
        return _fetch_rendered(url, timeout=timeout, wait_selector=wait_selector,
                               warmup_url=warmup_url, user_agent=user_agent)
    return _fetch_plain(url, timeout=timeout, headers=headers, retries=retries,
                        warmup_url=warmup_url, session=session,
                        user_agent=user_agent)


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
                 warmup_url: str | None = None,
                 session: requests.Session | None = None,
                 user_agent: str | None = None) -> str:
    # Oturum dışarıdan verilmediyse burada açılır. Dışarıdan verilmesi
    # önemli: çok sayfalı okumada çerezler sayfalar arasında korunmalı.
    kendi_oturumu = session is None
    if kendi_oturumu:
        session = make_session(headers, user_agent)
        if warmup_url:
            warmup(session, warmup_url, timeout)
    elif headers:
        session.headers.update(headers)

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


def _fetch_rendered(url: str, *, timeout: int, wait_selector: str | None,
                    warmup_url: str | None = None,
                    user_agent: str | None = None) -> str:
    """Gerçek Chromium ile açar.

    warmup_url verilirse önce o sayfaya gidilir (aynı tarayıcı bağlamında,
    yani çerezler korunur), sonra asıl adrese geçilir — tıpkı siteye girip
    arama yapan bir kullanıcı gibi.
    """
    try:
        from playwright.sync_api import sync_playwright
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import TimeoutError as PwTimeout
    except ImportError as exc:  # pragma: no cover
        raise FetchError(
            "Bu kaynak render:true istiyor ama playwright kurulu değil. "
            "`pip install playwright && playwright install chromium` çalıştır."
        ) from exc

    ua = user_agent or DEFAULT_USER_AGENT
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            # Headless Chromium kendini varsayılan olarak "HeadlessChrome"
            # diye tanıtır; UA'yı HTTP yolundakiyle aynı tutuyoruz ki iki
            # yol arasında tutarsızlık olmasın.
            context = browser.new_context(
                user_agent=ua,
                locale="tr-TR",
                viewport={"width": 1366, "height": 900},
                extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
            )
            page = context.new_page()

            if warmup_url:
                try:
                    r = page.goto(warmup_url, timeout=timeout * 1000,
                                  wait_until="domcontentloaded")
                    kod = r.status if r else "?"
                    log.info("ısınma (tarayıcı): %s → HTTP %s", warmup_url, kod)
                    page.wait_for_timeout(1500)
                except (PwError, PwTimeout) as exc:
                    log.warning("tarayıcı ısınması başarısız: %s", exc)

            resp = page.goto(url, timeout=timeout * 1000,
                             wait_until="domcontentloaded")
            durum = resp.status if resp else None
            if durum and durum in PERMANENT_STATUSES:
                raise PermanentFetchError(
                    f"HTTP {durum} — gerçek tarayıcıyla da reddedildi. "
                    "Bu, engelin başlık/tarayıcı kaynaklı DEĞİL, bu sunucunun "
                    "IP'sine dayalı olduğunu gösterir. Tek çözüm taramayı "
                    "kendi ağında çalıştırmak."
                )

            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except PwTimeout:
                    log.warning("'%s' beklenirken zaman aşımı — sayfa yine de "
                                "okunuyor", wait_selector)
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                except PwTimeout:
                    pass
            return page.content()
        finally:
            browser.close()
