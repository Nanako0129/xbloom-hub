"""Brew records: one JSON file per brew.

No database: nothing asks a question across brews that a directory listing
cannot answer, and a file per brew can be written while the brew is running.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

_ID = re.compile(r"\d{8}-\d{6}-[a-z0-9]{4}")
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


class BadBrewId(ValueError):
    pass


class BrewStore:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._last_stamp = 0

    def new_id(self) -> str:
        # Milliseconds, then one random character, and never the same
        # millisecond twice: ids sort in the order the brews happened.
        stamp = max(int(time.time() * 1000), self._last_stamp + 1)
        self._last_stamp = stamp
        tail = f"{stamp % 1000:03d}" + secrets.choice(_ALPHABET)
        return time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp / 1000)) + "-" + tail

    def _path(self, brew_id: str) -> Path:
        # The id is the file name; only the hub's own pattern is accepted.
        if not isinstance(brew_id, str) or not _ID.fullmatch(brew_id):
            raise BadBrewId(str(brew_id))
        return self._dir / f"{brew_id}.json"

    def write(self, record: dict) -> None:
        path = self._path(record["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)               # never leave a half-written record

    def get(self, brew_id: str) -> dict | None:
        path = self._path(brew_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        """Newest first, without the readings."""
        out = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            samples = record.get("samples") or {}
            water = samples.get("water") or []
            out.append({
                "id": record.get("id"), "started_at": record.get("started_at"),
                "ended_at": record.get("ended_at"), "reason": record.get("reason"),
                "duration_s": record.get("duration_s"),
                "recipe_name": (record.get("recipe") or {}).get("name"),
                "slug": record.get("slug"),
                "water_ml": water[-1][1] if water else None,
            })
        return out
