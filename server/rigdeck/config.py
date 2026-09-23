"""Settings that survive restarts, stored next to the server as config.json."""

from __future__ import annotations

import json
import threading
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS = {
    "port": 8384,
    # auto follows the running game: ETS2 -> metric/EUR, ATS -> imperial/USD
    "units": "auto",       # auto | metric | imperial
    "currency": "auto",    # auto | EUR | USD | GBP | CHF
    "tick_hz": 20,
    "vjoy_device": 1,
    # The vJoy device is switched on when a panel connects and off again shortly
    # after the last one leaves, because its driver bugchecks the machine now and
    # then when anything enumerates it. Set false to leave it permanently on, the
    # way it used to be.
    "vjoy_auto_device": True,
    "pulse_ms": 70,        # how long a virtual button is held for a one-shot press
}

_lock = threading.Lock()
_cache: dict | None = None


def use_scratch_file(path) -> None:
    """Point settings at a throwaway file.

    The tests run the real server on a spare port, and without this they would write
    that port into the settings the panel actually starts with -- which is exactly the
    sort of thing you only notice when the QR code stops matching.
    """
    global CONFIG_PATH, _cache
    with _lock:
        CONFIG_PATH = Path(path)
        _cache = None


def load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            merged = dict(DEFAULTS)
            if CONFIG_PATH.exists():
                try:
                    merged.update(json.loads(CONFIG_PATH.read_text("utf-8")))
                except (OSError, ValueError):
                    pass  # a corrupt file must not stop the panel from starting
            _cache = merged
        return dict(_cache)


def update(changes: dict) -> dict:
    global _cache
    with _lock:
        current = dict(_cache or DEFAULTS)
        for key, value in changes.items():
            if key in DEFAULTS:
                current[key] = value
        _cache = current
        try:
            CONFIG_PATH.write_text(json.dumps(current, indent=2), "utf-8")
        except OSError:
            pass
        return dict(current)


def resolve_units(cfg: dict, game: str) -> tuple[str, str]:
    """Return (units, currency) with 'auto' resolved against the running game."""
    units = cfg.get("units", "auto")
    currency = cfg.get("currency", "auto")
    if units == "auto":
        units = "imperial" if game == "ATS" else "metric"
    if currency == "auto":
        currency = "USD" if game == "ATS" else "EUR"
    return units, currency
