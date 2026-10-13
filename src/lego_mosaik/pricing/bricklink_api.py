"""Preise aus dem BrickLink Price Guide (Store API v1).

Pro Teil und Farbe gibt es zwei Abfragen::

    GET /items/PART/{nr}/price?color_id=11&guide_type=sold&new_or_used=N&currency_code=EUR …
    GET /items/PART/{nr}/price?color_id=11&guide_type=stock&new_or_used=N&currency_code=EUR …

``sold`` sind die Verkäufe der letzten 6 Monate, ``stock`` die aktuellen Angebote.
Jede Anfrage wird mit OAuth 1.0a signiert. BrickLink meldet Fehler im Feld
``meta.code`` der JSON-Antwort, nicht immer im HTTP-Status.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

lazy import httpx

from lego_mosaik.domain.errors import PriceSourceError
from lego_mosaik.pricing.credentials import Credentials
from lego_mosaik.pricing.oauth1 import authorization_header
from lego_mosaik.pricing.protocol import PriceGuide, PriceKey

logger = logging.getLogger(__name__)

ITEM_TYPE = "PART"
GUIDE_SOLD = "sold"  # Verkäufe der letzten 6 Monate
GUIDE_STOCK = "stock"  # aktuelle Angebote
CODE_OK = 200
CODE_NOT_FOUND = 404  # Teil oder Farbe kennt BrickLink nicht
AUTH_CODES = frozenset({401, 403})
# Mengengewichteter Durchschnitt zuerst: Bei Kleinteilen zählt der Preis großer Posten
AVERAGE_FIELDS = ("qty_avg_price", "avg_price")

type JsonObject = Mapping[str, object]


class BrickLinkPriceSource:
    """Fragt den Price Guide für viele Teile ab, bis zu ``max_parallel`` gleichzeitig.

    Args:
        credentials: OAuth-Zugangsdaten.
        base_url: Basis der Store API, z. B. ``https://api.bricklink.com/api/store/v1``.
        currency: Währung (ISO 4217).
        region: Region der Verkäufer oder ``None`` (weltweit).
        country_code: Land der Verkäufer oder ``None``.
        condition: ``N`` (neu) oder ``U`` (gebraucht).
        vat: ``Y``, ``N`` oder ``O`` (MwSt. laut BrickLink).
        timeout_seconds: Timeout pro Anfrage.
        max_parallel: Gleichzeitige Anfragen.
        user_agent: Eigener User-Agent.
        transport: Optionaler httpx-Transport (Tests: ``httpx.MockTransport``).
        now: Uhr für den Zeitstempel der Preise (Tests: feste Zeit).
    """

    def __init__(
        self,
        *,
        credentials: Credentials,
        base_url: str,
        currency: str,
        region: str | None,
        country_code: str | None,
        condition: str,
        vat: str,
        timeout_seconds: float,
        max_parallel: int,
        user_agent: str,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        self._params = {
            "new_or_used": condition,
            "currency_code": currency,
            "vat": vat,
            **({"region": region} if region else {}),
            **({"country_code": country_code} if country_code else {}),
        }
        self._timeout_seconds = timeout_seconds
        self._max_parallel = max_parallel
        self._user_agent = user_agent
        self._transport = transport
        self._now = now

    def fetch(self, keys: Sequence[PriceKey]) -> dict[PriceKey, PriceGuide]:
        """Fragt alle Schlüssel ab.

        Raises:
            PriceSourceError: Bei Netzwerk- oder Anmeldefehlern (bricht alles ab).
        """
        if not keys:
            return {}
        logger.info("Frage %d Preise bei BrickLink ab …", len(keys))
        with (
            httpx.Client(
                transport=self._transport,
                timeout=self._timeout_seconds,
                headers={"User-Agent": self._user_agent},
            ) as client,
            ThreadPoolExecutor(self._max_parallel) as pool,
        ):
            # map behält die Reihenfolge; ein Fehler in einem Thread kommt hier wieder an
            guides = list(pool.map(lambda key: self._guide(client, key), keys))
        return dict(zip(keys, guides, strict=True))

    def _guide(self, client: httpx.Client, key: PriceKey) -> PriceGuide:
        sold = self._query(client, key, GUIDE_SOLD)
        # Kennt BrickLink das Teil nicht, lohnt die zweite Abfrage nicht
        stock = None if sold is None else self._query(client, key, GUIDE_STOCK)
        return PriceGuide(sold=_average(sold), stock=_average(stock), fetched=self._now())

    def _query(self, client: httpx.Client, key: PriceKey, guide_type: str) -> JsonObject | None:
        """Eine Abfrage; ``None``, wenn BrickLink Teil oder Farbe nicht kennt."""
        item, color = key
        url = f"{self._base_url}/items/{ITEM_TYPE}/{quote(item, safe='')}/price"
        params = {"color_id": str(color), "guide_type": guide_type, **self._params}
        header = authorization_header("GET", url, params, self._credentials)
        try:
            response = client.get(url, params=params, headers={"Authorization": header})
        except httpx.HTTPError as error:
            raise PriceSourceError(f"BrickLink ist nicht erreichbar: {error}") from None
        code, message, body = _meta(response)
        if code == CODE_NOT_FOUND:
            logger.warning("BrickLink kennt Teil %s in Farbe %d nicht.", item, color)
            return None
        if code in AUTH_CODES:
            raise PriceSourceError(
                f"BrickLink lehnt die Anmeldung ab ({message or code}). Zugangsdaten prüfen und "
                "ob die IP-Adresse dieses Rechners für den Token freigegeben ist."
            )
        data = body.get("data")
        if code != CODE_OK or not isinstance(data, Mapping):
            raise PriceSourceError(
                f"BrickLink-Fehler {code} bei Teil {item} in Farbe {color}: {message or '?'}"
            )
        return data


def _meta(response: httpx.Response) -> tuple[int, str, JsonObject]:
    """(Code, Meldung, JSON) aus ``meta`` der Antwort, sonst aus dem HTTP-Status."""
    try:
        body = response.json()
    except ValueError:
        return response.status_code, response.reason_phrase, {}
    if not isinstance(body, Mapping):
        return response.status_code, response.reason_phrase, {}
    meta = body.get("meta")
    if isinstance(meta, Mapping) and isinstance(meta.get("code"), int):
        message = meta.get("description") or meta.get("message") or ""
        return int(meta["code"]), str(message), body
    return response.status_code, response.reason_phrase, body


def _average(data: JsonObject | None) -> Decimal | None:
    """Durchschnittspreis pro Stück; ``None`` ohne Verkäufe bzw. Angebote."""
    if data is None or not _decimal(data.get("total_quantity")):
        return None
    for field in AVERAGE_FIELDS:
        value = _decimal(data.get(field))
        if value:  # 0 oder None: nächstes Feld versuchen
            return value
    return None


def _decimal(value: object) -> Decimal | None:
    """BrickLink liefert Preise als Text (``"0.0523"``); Decimal rechnet ohne Rundungsfehler."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
