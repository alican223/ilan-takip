"""Daha önce görülen ilanların kaydı — aynı ilanın tekrar bildirilmemesi için."""

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
        self._data: dict[str, float] = {}
        self.is_first_run = not self.path.exists()
        if not self.is_first_run:
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
                self.is_first_run = True

    def is_new(self, item_id: str) -> bool:
        return item_id not in self._data

    def mark(self, item_id: str) -> None:
        self._data[item_id] = time.time()

    def save(self) -> None:
        cutoff = time.time() - TTL_DAYS * 86400
        self._data = {k: v for k, v in self._data.items() if v >= cutoff}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=0, sort_keys=True), "utf-8")
        tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self._data)
