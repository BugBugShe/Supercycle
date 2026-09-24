"""Persisted record of purchases so restarts never re-buy or overspend."""
from __future__ import annotations

import json
import time
from pathlib import Path


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"spent": 0.0, "items": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    @property
    def spent(self) -> float:
        return float(self.data.get("spent", 0.0))

    def is_done(self, url: str) -> bool:
        return url in self.data["items"]

    def record(self, url: str, status: str, amount: float) -> None:
        self.data["items"][url] = {"status": status, "amount": amount, "ts": time.time()}
        self.data["spent"] = self.spent + amount
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
