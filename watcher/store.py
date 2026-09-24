"""Görülen ilanların kaydı — tekrar bildirmemek ve fiyat değişimini yakalamak için.

Dosya biçimi:
    {"<ilan_id>": {"t": <ilk görülme zamanı>, "p": <son görülen fiyat|null>}}

Eski biçim ({"<ilan_id>": <zaman>}) okunurken otomatik yeni biçime çevrilir,
yani mevcut hafıza dosyaları sıfırlanmadan çalışmaya devam eder.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
# Bir ilan kaydı bu süre sonunda unutulur (dosya sonsuza kadar büyümesin)
TTL_DAYS = 120


class SeenStore:
    def __init__(self, source_name: str, state_dir: Path | None = None):
        base = state_dir or STATE_DIR
        base.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in source_name)
        self.path = base / f"{safe}.json"
        self._data: dict[str, dict] = {}
        self.is_first_run = not self.path.exists()
        if not self.is_first_run:
            try:
                self._data = _migrate(json.loads(self.path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError, TypeError, AttributeError):
                self._data = {}
                self.is_first_run = True

    # --- okuma ---------------------------------------------------------

    def is_new(self, item_id: str) -> bool:
        return item_id not in self._data

    def price_of(self, item_id: str) -> float | None:
        """Bu ilanı en son gördüğümüzdeki fiyatı."""
        kayit = self._data.get(item_id)
        return kayit.get("p") if kayit else None

    def __len__(self) -> int:
        return len(self._data)

    def last_new_at(self) -> float | None:
        """En son ne zaman YENİ bir ilan kaydedildi (unix zaman)."""
        return max((k["t"] for k in self._data.values()), default=None)

    def days_since_last_new(self) -> float | None:
        last = self.last_new_at()
        return None if last is None else (time.time() - last) / 86400

    def new_since(self, seconds: float) -> int:
        """Son N saniyede kaç yeni ilan kaydedildi."""
        esik = time.time() - seconds
        return sum(1 for k in self._data.values() if k["t"] >= esik)

    # --- yazma ---------------------------------------------------------

    def mark(self, item_id: str, price: float | None = None) -> None:
        """İlanı görülmüş olarak işaretler. İlk görülme zamanı korunur."""
        mevcut = self._data.get(item_id)
        self._data[item_id] = {
            "t": mevcut["t"] if mevcut else time.time(),
            "p": price,
        }

    def update_price(self, item_id: str, price: float | None) -> None:
        """Sadece fiyatı tazeler; 'ilk görülme' zamanına dokunmaz."""
        if item_id in self._data:
            self._data[item_id]["p"] = price

    def save(self) -> None:
        cutoff = time.time() - TTL_DAYS * 86400
        self._data = {k: v for k, v in self._data.items() if v["t"] >= cutoff}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=0, sort_keys=True), "utf-8")
        tmp.replace(self.path)


def _migrate(ham: dict) -> dict[str, dict]:
    """Eski {id: zaman} biçimini {id: {"t": zaman, "p": None}} biçimine çevirir."""
    cikti: dict[str, dict] = {}
    for anahtar, deger in (ham or {}).items():
        if isinstance(deger, dict):
            cikti[anahtar] = {"t": float(deger.get("t", 0)), "p": deger.get("p")}
        else:
            # eski biçim: değer doğrudan zaman damgasıydı, fiyat bilinmiyor
            cikti[anahtar] = {"t": float(deger), "p": None}
    return cikti
