"""Telegram bildirimi."""

from __future__ import annotations

import html
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000  # Telegram sınırı 4096; pay bırakıyoruz


# Telegram'ın döndürdüğü hataların Türkçe karşılıkları
_ERROR_HINTS = {
    401: "Token geçersiz. TELEGRAM_BOT_TOKEN secret'ını kontrol et — "
         "başında/sonunda boşluk ya da eksik karakter olabilir.",
    404: "Token biçimi bozuk (API adresi bulunamadı). Token'ı BotFather'dan "
         "yeniden kopyala.",
    400: "İstek reddedildi. En sık sebebi: chat id yanlış, ya da bota hiç "
         "START mesajı atılmamış. Telegram'da botunu açıp bir mesaj gönder.",
    403: "Bot bu sohbete yazamıyor — botu engellemiş olabilirsin ya da "
         "gruptan çıkarılmış olabilir.",
}


class TelegramSendError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 dry_run: bool = False):
        # GitHub secrets'a kopyalarken görünmez boşluk kalması çok sık olur
        self.token = (token or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
        self.dry_run = dry_run
        self.failures = 0          # başarısız gönderim sayısı
        self.successes = 0
        if not self.dry_run and not (self.token and self.chat_id):
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID tanımlı değil "
                "(denemek için --dry-run kullan)."
            )

    def _describe(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            desc = data.get("description", "")
        except ValueError:
            desc = resp.text[:200]
        hint = _ERROR_HINTS.get(resp.status_code, "")
        return f"HTTP {resp.status_code} — {desc}" + (f" | {hint}" if hint else "")

    def send(self, text: str) -> bool:
        if self.dry_run:
            print("\n--- [DRY RUN] gönderilecek mesaj ---")
            print(text)
            self.successes += 1
            return True

        last_reason = "bilinmiyor"
        for attempt in range(1, 4):
            try:
                resp = requests.post(
                    API.format(token=self.token),
                    json={
                        "chat_id": self.chat_id,
                        "text": text[:MAX_LEN],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                    },
                    timeout=20,
                )
                if resp.status_code == 429:
                    wait = resp.json().get("parameters", {}).get("retry_after", 5)
                    log.warning("Telegram hız sınırı, %ss bekleniyor", wait)
                    time.sleep(wait + 1)
                    continue

                if resp.status_code == 200:
                    self.successes += 1
                    return True

                last_reason = self._describe(resp)
                # 4xx kalıcı hatadır, tekrar denemenin anlamı yok
                if 400 <= resp.status_code < 500:
                    log.error("Telegram gönderimi başarısız: %s", last_reason)
                    self.failures += 1
                    return False
                raise TelegramSendError(last_reason)

            except Exception as exc:  # noqa: BLE001
                last_reason = str(exc)
                log.warning("Telegram gönderim hatası (%s/3): %s", attempt, exc)
                if attempt < 3:
                    time.sleep(2 * attempt)

        log.error("Telegram gönderimi 3 denemede de başarısız: %s", last_reason)
        self.failures += 1
        return False

    def check_token(self) -> bool:
        """Token geçerli mi, bot kim? (mesaj göndermez)"""
        if self.dry_run:
            return True

        log.info("Token uzunluğu: %s karakter | chat id: %s",
                 len(self.token), self.chat_id)
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.token}/getMe", timeout=20
            )
            if resp.status_code != 200:
                log.error("Bot kimliği alınamadı: %s", self._describe(resp))
                return False
            me = resp.json().get("result", {})
            log.info("✓ Token geçerli — bot: @%s (%s)",
                     me.get("username", "?"), me.get("first_name", "?"))
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Telegram'a ulaşılamadı: %s", exc)
            return False


def _esc(value) -> str:
    """Metin içeriği için: Telegram HTML'inin istediği yalnızca & < > kaçışı.

    quote=True kullanılırsa kesme işareti &#x27; olur ve Telegram bunu
    düz metin olarak gösterebilir ("Gönyeli&#x27;de" gibi).
    """
    return html.escape(str(value), quote=False) if value is not None else ""


def _esc_attr(value) -> str:
    """href gibi öznitelik değerleri için: tırnak da kaçırılmalı."""
    return html.escape(str(value), quote=True) if value is not None else ""


def format_item(item: dict, source_label: str) -> str:
    """Tek ilanı Telegram HTML mesajına çevirir."""
    title = _esc(item.get("title") or "(başlıksız ilan)")
    link = item.get("link")

    lines = [f"🔔 <b>{source_label}</b>"]
    lines.append(f"<b>{title}</b>" if not link else f'<b><a href="{_esc_attr(link)}">{title}</a></b>')

    if item.get("price_text"):
        lines.append(f"💰 {_esc(item['price_text'])}")
    if item.get("location"):
        lines.append(f"📍 {_esc(item['location'])}")
    if item.get("date"):
        lines.append(f"🗓 {_esc(item['date'])}")

    # Config'de tanımlanmış diğer serbest alanlar
    skip = {"title", "link", "price", "price_text", "location", "date", "image", "id"}
    extras = [
        f"• {_esc(k)}: {_esc(v)}"
        for k, v in item.items()
        if v and not k.startswith("_") and k not in skip
    ]
    lines.extend(extras[:5])

    if link:
        lines.append(f"\n{_esc(link)}")
    return "\n".join(lines)


def format_digest(items: list[dict], source_label: str) -> str:
    """Çok sayıda ilanı tek özet mesajda toplar."""
    lines = [f"🔔 <b>{source_label}</b> — {len(items)} yeni ilan\n"]
    for item in items:
        title = _esc(item.get("title") or "(başlıksız)")
        link = item.get("link")
        price = f" — {_esc(item['price_text'])}" if item.get("price_text") else ""
        lines.append(
            f'• <a href="{_esc_attr(link)}">{title}</a>{price}' if link
            else f"• {title}{price}"
        )
    return "\n".join(lines)
