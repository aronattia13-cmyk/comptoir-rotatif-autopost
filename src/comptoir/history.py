"""Publication history — the system's memory (data/history.json, committed).

Each entry records what was planned/published, so the planner can avoid
repeating products, articles, service messages, photos and layouts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HISTORY_FILE = ROOT / "data" / "history.json"

DONE = {"published", "simulated", "partial", "uncertain"}


@dataclass
class Entry:
    uid: str                  # unique key: <date>-<slot>  (double-publish guard)
    date: str                 # ISO date, Europe/Paris
    slot: str                 # mon-product, tue-service-story, ...
    family: str               # product | blog | service
    surface: str              # post | story
    subject: str              # product model key / article handle / service message id
    reference: str            # handle of the exact product / article URL / service id
    layout: str
    photo: str = ""
    category: str = ""
    status: str = "planned"   # planned | simulated | published | failed | skipped
    platforms: dict = field(default_factory=dict)   # {"instagram": {"id":..,"permalink":..}}
    price: str = ""
    error: str = ""


class History:
    def __init__(self, entries: list[Entry] | None = None, path: Path = HISTORY_FILE):
        self.entries = entries or []
        self.path = path

    @classmethod
    def load(cls, path: Path = HISTORY_FILE) -> "History":
        if not path.exists():
            return cls([], path)
        raw = json.loads(path.read_text() or "[]")
        return cls([Entry(**e) for e in raw], path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(e) for e in self.entries], ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ #
    def add(self, e: Entry) -> None:
        if self.has(e.uid):
            raise ValueError(f"doublon refusé: {e.uid} déjà dans l'historique")
        self.entries.append(e)

    def has(self, uid: str) -> bool:
        return any(x.uid == uid for x in self.entries)

    def get(self, uid: str) -> Entry | None:
        return next((x for x in self.entries if x.uid == uid), None)

    def is_published(self, uid: str) -> bool:
        e = self.get(uid)
        return bool(e and e.status == "published")

    def _relevant(self):
        return [e for e in self.entries if e.status in DONE | {"planned"}]

    def last_date(self, **match) -> date | None:
        ds = [date.fromisoformat(e.date) for e in self._relevant()
              if all(getattr(e, k) == v for k, v in match.items())]
        return max(ds) if ds else None

    def used_since(self, since: date, **match) -> bool:
        d = self.last_date(**match)
        return d is not None and d >= since

    def recent(self, n: int, family: str | None = None) -> list[Entry]:
        es = [e for e in self._relevant() if family is None or e.family == family]
        return sorted(es, key=lambda e: e.date)[-n:]

    def days_since(self, today: date, **match) -> int:
        d = self.last_date(**match)
        return 10_000 if d is None else (today - d).days

    def window(self, start: date, days: int) -> list[Entry]:
        end = start + timedelta(days=days)
        return [e for e in self._relevant() if start <= date.fromisoformat(e.date) < end]
