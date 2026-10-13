"""Ausgaben der Preisschätzung: ``preise.json`` (für API und Frontends) und eine CSV für Excel."""

import csv
import json
import logging
from decimal import Decimal
from pathlib import Path

from lego_mosaik.config import PricingConfig
from lego_mosaik.pricing.estimate import PriceEstimate, PriceLine

logger = logging.getLogger(__name__)

JSON_FILE_NAME = "preise.json"
CSV_FILE_NAME = "preise_bricklink.csv"
CSV_ENCODING = "utf-8-sig"  # BOM, damit Excel UTF-8 erkennt
CSV_DELIMITER = ";"  # deutsches Excel
CSV_HEADER = (
    "BrickLink-Teil",
    "Teilname",
    "Farbe",
    "BrickLink-Farbe",
    "Anzahl",
    "Ø verkauft/Stück",
    "Ø angeboten/Stück",
    "Summe verkauft",
    "Summe angeboten",
    "Hinweis",
)
UNIT_PLACES = Decimal("0.0001")  # BrickLink rechnet Stückpreise auf 4 Stellen
TOTAL_PLACES = Decimal("0.01")


def write_price_files(
    target_dir: Path, payload: dict[str, object], estimate: PriceEstimate
) -> None:
    """Schreibt ``preise.json`` (Inhalt von ``price_payload``) und ``preise_bricklink.csv``."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    (target_dir / JSON_FILE_NAME).write_text(text, encoding="utf-8")
    with (target_dir / CSV_FILE_NAME).open("w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.writer(handle, delimiter=CSV_DELIMITER)
        writer.writerow(CSV_HEADER)
        writer.writerows(_csv_row(line) for line in estimate.lines)
        writer.writerow(
            (
                "Summe",
                "",
                "",
                "",
                "",
                "",
                "",
                _german(estimate.total_sold, TOTAL_PLACES),
                _german(estimate.total_stock, TOTAL_PLACES),
                "" if estimate.complete else "unvollständig",
            )
        )
    logger.info("Preise geschrieben: %s", target_dir / JSON_FILE_NAME)


def price_payload(estimate: PriceEstimate, pricing: PricingConfig) -> dict[str, object]:
    """Inhalt von ``preise.json`` und der API-Antwort (JSON-fähig, deutsche Schlüssel)."""
    return {
        **price_summary(estimate),
        "zustand": pricing.condition,
        "region": pricing.region,
        "land": pricing.country_code,
        "mwst": pricing.vat,
        "upload_url": pricing.upload_url,
        "positionen": [_line_json(line) for line in estimate.lines],
    }


def price_summary(estimate: PriceEstimate) -> dict[str, object]:
    """Kurzfassung für ``ergebnis.json`` (Feld ``preise``)."""
    oldest = estimate.oldest
    return {
        "waehrung": estimate.currency,
        "summe_verkauft": _number(estimate.total_sold, TOTAL_PLACES),
        "summe_angeboten": _number(estimate.total_stock, TOTAL_PLACES),
        "vollstaendig": estimate.complete,
        "fehlend": len(estimate.missing),
        "stand": None if oldest is None else oldest.isoformat(),
    }


def _line_json(line: PriceLine) -> dict[str, object]:
    item = line.item
    return {
        "teil": item.part_num,
        "bricklink_teil": item.bricklink_item,
        "name": item.name,
        "farbe": item.color_name,
        "bricklink_farbe": item.bricklink_color,
        "anzahl": item.quantity,
        "stueck_verkauft": _number(line.sold, UNIT_PLACES),
        "stueck_angeboten": _number(line.stock, UNIT_PLACES),
        "summe_verkauft": _number(line.total_sold, TOTAL_PLACES),
        "summe_angeboten": _number(line.total_stock, TOTAL_PLACES),
        "hinweis": line.note,
    }


def _csv_row(line: PriceLine) -> tuple[object, ...]:
    item = line.item
    return (
        item.bricklink_item,
        item.name,
        item.color_name,
        "" if item.bricklink_color is None else item.bricklink_color,
        item.quantity,
        _german(line.sold, UNIT_PLACES),
        _german(line.stock, UNIT_PLACES),
        _german(line.total_sold, TOTAL_PLACES),
        _german(line.total_stock, TOTAL_PLACES),
        line.note,
    )


def _number(value: Decimal | None, places: Decimal) -> float | None:
    """Gerundet als JSON-Zahl (Frontends rechnen mit Zahlen, nicht mit Text)."""
    return None if value is None else float(value.quantize(places))


def _german(value: Decimal | None, places: Decimal) -> str:
    """Dezimalkomma für deutsches Excel, leer ohne Wert."""
    return "" if value is None else str(value.quantize(places)).replace(".", ",")
