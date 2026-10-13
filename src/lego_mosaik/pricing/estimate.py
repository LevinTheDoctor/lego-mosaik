"""Preisschätzung einer Teileliste aus Stückpreisen (reine Rechnung, ohne Netz)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from lego_mosaik.domain.models import OrderItem
from lego_mosaik.pricing.protocol import PriceGuide, PriceKey

NO_COLOR_CODE = "kein BrickLink-Farbcode"
NO_PRICE_DATA = "keine Preisdaten bei BrickLink"
NO_SALES = "keine Verkäufe in 6 Monaten"
NOT_CACHED = "nicht im Cache (offline)"


@dataclass(frozen=True, slots=True)
class PriceLine:
    """Eine Position mit Stückpreisen.

    Attributes:
        item: Teil, Farbe und Menge.
        sold: Ø Verkaufspreis pro Stück oder ``None``.
        stock: Ø Angebotspreis pro Stück oder ``None``.
        fetched: Stand des Preises oder ``None``.
        note: Warum der Verkaufspreis fehlt (leer, wenn er bekannt ist).
    """

    item: OrderItem
    sold: Decimal | None
    stock: Decimal | None
    fetched: datetime | None
    note: str

    @property
    def total_sold(self) -> Decimal | None:
        return None if self.sold is None else self.sold * self.item.quantity

    @property
    def total_stock(self) -> Decimal | None:
        return None if self.stock is None else self.stock * self.item.quantity


@dataclass(frozen=True, slots=True)
class PriceEstimate:
    """Geschätzter Preis einer Teileliste.

    Die Summen enthalten nur Positionen mit bekanntem Preis. ``complete`` sagt, ob
    die Verkaufssumme alle Positionen abdeckt.
    """

    lines: tuple[PriceLine, ...]
    currency: str

    @property
    def total_sold(self) -> Decimal:
        return sum((line.total_sold or Decimal(0) for line in self.lines), Decimal(0))

    @property
    def total_stock(self) -> Decimal:
        return sum((line.total_stock or Decimal(0) for line in self.lines), Decimal(0))

    @property
    def missing(self) -> tuple[PriceLine, ...]:
        """Positionen ohne Verkaufspreis."""
        return tuple(line for line in self.lines if line.sold is None)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def oldest(self) -> datetime | None:
        """Ältester Stand aller Preise (``None`` ohne Preise)."""
        return min((line.fetched for line in self.lines if line.fetched), default=None)


def estimate_prices(
    items: Sequence[OrderItem],
    guides: Mapping[PriceKey, PriceGuide],
    currency: str,
    *,
    missing_note: str = NO_PRICE_DATA,
) -> PriceEstimate:
    """Ordnet jeder Position ihren Stückpreis zu.

    Args:
        items: Bestellpositionen.
        guides: Preise je (BrickLink-Teil, BrickLink-Farbe).
        currency: Währung der Preise.
        missing_note: Hinweis für Positionen ohne Eintrag in ``guides``.
    """
    lines = []
    for item in items:
        key = item.bricklink_key
        guide = None if key is None else guides.get(key)
        if guide is None:
            note = NO_COLOR_CODE if key is None else missing_note
            lines.append(PriceLine(item, None, None, None, note))
            continue
        note = ""
        if guide.sold is None:
            note = NO_PRICE_DATA if guide.stock is None else NO_SALES
        lines.append(PriceLine(item, guide.sold, guide.stock, guide.fetched, note))
    return PriceEstimate(tuple(lines), currency)
