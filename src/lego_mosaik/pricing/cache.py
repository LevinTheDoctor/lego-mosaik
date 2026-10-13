"""Lokaler Preis-Cache (JSON), damit nicht jede Schätzung die BrickLink-API belastet.

BrickLink erlaubt 5.000 Abfragen pro Tag; ein Mosaik braucht zwei pro Teil und Farbe.
Die Einstellungen (Zustand, Währung, Region, …) gehören zum Schlüssel, damit
Preise in EUR nie als USD gelesen werden. Geschrieben wird atomar wie im Updater.
"""

import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lego_mosaik.pricing.protocol import PriceGuide, PriceKey

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
TEMP_SUFFIX = ".tmp"


class PriceCache:
    """Preise pro (Teil, Farbe, Einstellungen) mit Zeitstempel.

    Args:
        path: JSON-Datei, z. B. ``daten/bricklink/preise.json``.
        settings: Kennung der Abfrage-Einstellungen, z. B. ``"N|EUR|europe|-|Y"``.
        max_age_days: Ab diesem Alter gilt ein Preis als veraltet.
        now: Uhr (Tests: feste Zeit).
    """

    def __init__(
        self,
        path: Path,
        settings: str,
        max_age_days: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._path = path
        self._settings = settings
        self._max_age = timedelta(days=max_age_days)
        self._now = now

    def get(self, key: PriceKey) -> PriceGuide | None:
        """Gespeicherter Preis oder ``None``; auch veraltete Preise kommen zurück."""
        raw = self._load().get(self._key(key))
        return None if raw is None else _guide_from_json(raw)

    def is_fresh(self, guide: PriceGuide) -> bool:
        """True, wenn der Preis jünger als ``max_age_days`` ist."""
        return self._now() - guide.fetched <= self._max_age

    def update(self, guides: Mapping[PriceKey, PriceGuide]) -> None:
        """Übernimmt neue Preise und schreibt die Datei atomar."""
        if not guides:
            return
        entries = self._load()
        entries.update({self._key(key): _guide_to_json(g) for key, g in guides.items()})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + TEMP_SUFFIX)
        payload = {"version": CACHE_VERSION, "preise": entries}
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")
        temp.replace(self._path)  # Umbenennen ist atomar: nie eine halbe Datei

    def _key(self, key: PriceKey) -> str:
        item, color = key
        return f"{item}|{color}|{self._settings}"

    def _load(self) -> dict[str, dict[str, str | None]]:
        """Liest die Datei; fehlt sie oder ist sie kaputt, beginnt der Cache leer."""
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text("utf-8"))
        except ValueError, OSError:
            logger.warning("Preis-Cache %s ist nicht lesbar und wird neu angelegt.", self._path)
            return {}
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return {}
        entries = payload.get("preise")
        return dict(entries) if isinstance(entries, dict) else {}


def _guide_to_json(guide: PriceGuide) -> dict[str, str | None]:
    return {
        "verkauft": None if guide.sold is None else str(guide.sold),
        "angeboten": None if guide.stock is None else str(guide.stock),
        "stand": guide.fetched.isoformat(),
    }


def _guide_from_json(raw: Mapping[str, str | None]) -> PriceGuide | None:
    """Ein Eintrag aus der Datei; unlesbare Einträge zählen als fehlend."""
    try:
        fetched = datetime.fromisoformat(str(raw["stand"]))
        sold, stock = raw.get("verkauft"), raw.get("angeboten")
        return PriceGuide(
            sold=None if sold is None else Decimal(sold),
            stock=None if stock is None else Decimal(stock),
            fetched=fetched,
        )
    except KeyError, TypeError, ValueError, InvalidOperation:
        return None
