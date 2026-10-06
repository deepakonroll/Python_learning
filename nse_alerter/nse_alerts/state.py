"""Tiny JSON persistence - dedupe state so a signal is alerted exactly once.

Java equivalent: a ConcurrentHashMap written with Jackson to a temp file and
atomically moved over the old one (java.nio.file.Files.move with REPLACE).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class SymbolState:
    last_side: str                          # "UP" | "DOWN" at last evaluation
    last_processed_bar: str = ""            # ISO start of last completed bar seen
    last_event_side: str = ""               # "BUY" | "SELL" | ""
    last_event_bar: str = ""
    updated_at: str = ""
    last_seen_date: str = ""                # YYYY-MM-DD of last live run (heartbeat)
    history: list[dict] = field(default_factory=list)   # rolling tail of events (log)
    prep_fired: bool = False                # env pre-flip: one heads-up per approach

    def record_event(self, side: str, bar_iso: str) -> None:
        self.last_event_side = side
        self.last_event_bar = bar_iso
        self.history.append({"side": side, "bar": bar_iso})
        self.history = self.history[-50:]               # keep the file small


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, SymbolState]:
        raw = self._load_raw()
        out: dict[str, SymbolState] = {}
        for symbol, data in raw.items():
            if symbol == "__control__":
                continue                        # reserved for the enable/disable flag
            try:
                out[symbol] = SymbolState(**data)
            except TypeError:
                continue                        # unknown/extra keys - skip entry
        return out

    def _load_raw(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt/unreadable state: keep a backup, start clean rather than crash.
            try:
                self.path.replace(self.path.with_suffix(".bak"))
            except OSError:
                pass
            return {}
        return raw if isinstance(raw, dict) else {}

    def get_control(self) -> dict:
        control = self._load_raw().get("__control__")
        return control if isinstance(control, dict) else {}

    def set_control(self, control: dict) -> None:
        """Persist the alerts on/off flag alongside the signal state."""
        data = self._load_raw()
        data["__control__"] = control
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, symbol: str) -> SymbolState | None:
        return self.load().get(symbol)

    def put(self, symbol: str, state: SymbolState) -> None:
        state.updated_at = datetime.now().isoformat(timespec="seconds")
        data = self._load_raw()                 # preserves __control__ entries
        data[symbol] = asdict(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)              # atomic on the same volume
