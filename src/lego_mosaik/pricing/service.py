"""Preisschätzung für ein fertiges Mosaik: ein Weg für CLI, interaktiven Modus und API.

Ablauf: Teileliste aus ``ergebnis.json`` lesen → Preise aus dem Cache nehmen und
fehlende oder veraltete bei BrickLink abfragen → ``preise.json``,
``preise_bricklink.csv`` und die Wanted List schreiben → ``ergebnis.json`` ergänzen.
"""

import json
import logging
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path

from lego_mosaik.config import PricingConfig
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import OrderItem
from lego_mosaik.outputs.official_parts_lists import BRICKLINK_FILE_NAME, write_bricklink_xml
from lego_mosaik.outputs.price_list import (
    CSV_FILE_NAME,
    JSON_FILE_NAME,
    UNIT_PLACES,
    price_payload,
    price_summary,
    write_price_files,
)
from lego_mosaik.outputs.result_json import FILE_NAME as RESULT_FILE
from lego_mosaik.outputs.result_json import find_mosaic, order_item_from_json, update_result
from lego_mosaik.pricing.estimate import (
    NO_PRICE_DATA,
    NOT_CACHED,
    PriceEstimate,
    estimate_prices,
)
from lego_mosaik.pricing.protocol import PriceCacheProtocol, PriceGuide, PriceKey, PriceSource

logger = logging.getLogger(__name__)

type SourceFactory = Callable[[], PriceSource]


class PriceService:
    """Schätzt den Preis eines Mosaiks und schreibt die Preisdateien in dessen Ordner.

    Args:
        pricing: Einstellungen (Währung, Zustand, Höchstpreis-Faktor, …).
        output_dir: Ordner mit allen Mosaiken (für IDs und „das neueste“).
        cache: Preis-Cache (lokal JSON, in der Cloud PostgreSQL).
        source_factory: Erzeugt die Preisquelle erst, wenn wirklich abgefragt wird.
            So braucht ``--offline`` keine Zugangsdaten.
    """

    def __init__(
        self,
        pricing: PricingConfig,
        *,
        output_dir: Path,
        cache: PriceCacheProtocol,
        source_factory: SourceFactory,
    ) -> None:
        self.pricing = pricing
        self._output_dir = output_dir
        self._cache = cache
        self._source_factory = source_factory

    def estimate(
        self, mosaic_dir: Path, *, offline: bool = False, force: bool = False
    ) -> PriceEstimate:
        """Schätzt den Preis und schreibt Preisdateien und Wanted List neu.

        Args:
            mosaic_dir: Ausgabeordner eines Mosaiks (mit ``ergebnis.json``).
            offline: Nur gespeicherte Preise verwenden, auch veraltete.
            force: Alle Preise neu abfragen, auch wenn sie noch aktuell sind.

        Raises:
            ConfigError: Wenn das Mosaik keine Teileliste hat (zu alt).
            PriceSourceError: Wenn die Abfrage bei BrickLink scheitert.
        """
        items = read_order_items(mosaic_dir)
        guides = self._guides(items, offline=offline, force=force)
        note = NOT_CACHED if offline else NO_PRICE_DATA
        estimate = estimate_prices(items, guides, self.pricing.currency, missing_note=note)
        self._write(mosaic_dir, items, estimate)
        return estimate

    def find_mosaic(self, reference: str | None) -> Path:
        """Ordner zu einer ID oder einem Pfad; ohne Angabe das neueste Mosaik.

        Raises:
            ConfigError: Wenn es kein passendes Mosaik gibt.
        """
        return find_mosaic(self._output_dir, reference)

    def _guides(
        self, items: list[OrderItem], *, offline: bool, force: bool
    ) -> dict[PriceKey, PriceGuide]:
        """Preise aus dem Cache, fehlende bzw. veraltete von BrickLink (außer offline)."""
        # dict.fromkeys entfernt Doppelte und behält die Reihenfolge
        keys = list(dict.fromkeys(key for item in items if (key := item.bricklink_key)))
        guides = {key: guide for key in keys if (guide := self._cache.get(key)) is not None}
        if offline:
            return guides
        todo = [k for k in keys if force or k not in guides or not self._cache.is_fresh(guides[k])]
        logger.info("%d Preise aus dem Cache, %d neu abzufragen.", len(keys) - len(todo), len(todo))
        if todo:
            fetched = self._source_factory().fetch(todo)
            self._cache.update(fetched)
            guides |= fetched
        return guides

    def _write(self, mosaic_dir: Path, items: list[OrderItem], estimate: PriceEstimate) -> None:
        payload = price_payload(estimate, self.pricing)
        write_price_files(mosaic_dir, payload, estimate)
        write_bricklink_xml(
            items,
            mosaic_dir / BRICKLINK_FILE_NAME,
            condition=self.pricing.condition,
            max_prices=self._max_prices(estimate),
        )
        update_result(
            mosaic_dir,
            {"preise": price_summary(estimate)},
            (JSON_FILE_NAME, CSV_FILE_NAME, BRICKLINK_FILE_NAME),
        )

    def _max_prices(self, estimate: PriceEstimate) -> Mapping[PriceKey, Decimal] | None:
        """Höchstpreise für die Wanted List: Ø Verkaufspreis × Faktor (falls eingestellt)."""
        factor = self.pricing.max_price_factor
        if factor is None:
            return None
        return {
            key: (line.sold * Decimal(str(factor))).quantize(UNIT_PLACES)
            for line in estimate.lines
            if line.sold is not None and (key := line.item.bricklink_key) is not None
        }


def read_order_items(mosaic_dir: Path) -> list[OrderItem]:
    """Teileliste aus ``ergebnis.json``.

    Raises:
        ConfigError: Wenn die Datei fehlt oder (bei alten Mosaiken) keine Teileliste hat.
    """
    path = mosaic_dir / RESULT_FILE
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("teileliste")
        if not isinstance(rows, list) or not rows:
            raise ValueError("keine Teileliste")
        return [order_item_from_json(row) for row in rows]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ConfigError(
            f"„{path}“ enthält keine lesbare Teileliste ({error}). Mosaik bitte neu erzeugen."
        ) from None
