"""Schnittstelle und Datenklassen der Preisabfrage."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

type PriceKey = tuple[str, int]  # (BrickLink-Teilenummer, BrickLink-Farbcode)


@dataclass(frozen=True, slots=True)
class PriceGuide:
    """Durchschnittspreise eines Teils in einer Farbe, jeweils pro Stück.

    Attributes:
        sold: Ø Verkaufspreis der letzten 6 Monate (nach Menge gewichtet);
            ``None``, wenn nichts verkauft wurde oder BrickLink das Teil nicht kennt.
        stock: Ø Preis der aktuellen Angebote; ``None``, wenn nichts angeboten wird.
        fetched: Zeitpunkt der Abfrage (UTC).
    """

    sold: Decimal | None
    stock: Decimal | None
    fetched: datetime


class PriceSource(Protocol):
    """Liefert Preise für BrickLink-Teile (z. B. über die BrickLink-API)."""

    def fetch(self, keys: Sequence[PriceKey]) -> dict[PriceKey, PriceGuide]:
        """Fragt die Preise ab; das Ergebnis enthält jeden Schlüssel genau einmal.

        Raises:
            PriceSourceError: Bei Netzwerk- oder Anmeldefehlern.
        """
        ...
