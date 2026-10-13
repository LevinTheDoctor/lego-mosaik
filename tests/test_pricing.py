"""Tests für die BrickLink-Preisschätzung (OAuth, API-Client, Cache, Rechnung, Ablauf).

Kein Test geht ins Netz: Der API-Client bekommt ``httpx.MockTransport``, der Ablauf
eine Fake-Quelle (``conftest.FakePriceSource``).
"""

import csv
import dataclasses
import json
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs
from xml.etree import ElementTree

import httpx
import pytest
from conftest import (
    PRICE_TIME,
    FakePriceSource,
    add_bricklink_colors,
    make_pipeline,
    make_price_service,
)

from lego_mosaik.config import AppConfig, PricingConfig, validate_config
from lego_mosaik.domain.errors import ConfigError, MissingCredentialsError, PriceSourceError
from lego_mosaik.domain.models import OrderItem
from lego_mosaik.pricing.bricklink_api import BrickLinkPriceSource
from lego_mosaik.pricing.cache import PriceCache
from lego_mosaik.pricing.credentials import ENV_NAMES, Credentials, load_credentials
from lego_mosaik.pricing.estimate import (
    NO_COLOR_CODE,
    NO_PRICE_DATA,
    NO_SALES,
    NOT_CACHED,
    estimate_prices,
)
from lego_mosaik.pricing.oauth1 import authorization_header, signature
from lego_mosaik.pricing.protocol import PriceGuide

CREDENTIALS = Credentials(
    "dpf43f3p2l4k3l03", "kd94hf93k423kf44", "nnch734d00sl2jdk", "pfkkdhi9sl3r4s00"
)
BASE_URL = "https://api.bricklink.test/api/store/v1"

type Handler = Callable[[httpx.Request], httpx.Response]


# --- OAuth und Zugangsdaten ---------------------------------------------------------


def test_signature_matches_oauth_spec_example() -> None:
    # Beispiel aus der OAuth-1.0-Spezifikation (Anhang A.5)
    params = {
        "file": "vacation.jpg",
        "size": "original",
        "oauth_consumer_key": "dpf43f3p2l4k3l03",
        "oauth_token": "nnch734d00sl2jdk",
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": "1191242096",
        "oauth_nonce": "kllo9940pd9333jh",
        "oauth_version": "1.0",
    }
    url = "http://photos.example.net/photos"
    assert signature("GET", url, params, CREDENTIALS) == "tR3+Ty81lMeYAr/Fid0kMTYa/WM="


def test_authorization_header_contains_encoded_signature() -> None:
    header = authorization_header(
        "GET",
        "http://photos.example.net/photos",
        {"file": "vacation.jpg", "size": "original"},
        CREDENTIALS,
        nonce="kllo9940pd9333jh",
        timestamp=1191242096,
    )
    assert header.startswith("OAuth ")
    assert 'oauth_signature="tR3%2BTy81lMeYAr%2FFid0kMTYa%2FWM%3D"' in header
    assert 'oauth_token="nnch734d00sl2jdk"' in header


def test_missing_credentials_name_the_variables() -> None:
    with pytest.raises(MissingCredentialsError, match="BRICKLINK_TOKEN_SECRET"):
        load_credentials({name: "x" for name in ENV_NAMES[:3]})


def test_credentials_never_show_secrets() -> None:
    credentials = load_credentials(dict.fromkeys(ENV_NAMES, "geheim"))
    assert "geheim" not in repr(credentials)


# --- BrickLink-API-Client -----------------------------------------------------------


def _price_data(avg: str, qty_avg: str, quantity: int) -> dict[str, object]:
    return {
        "avg_price": avg,
        "qty_avg_price": qty_avg,
        "total_quantity": quantity,
        "currency_code": "EUR",
    }


def _source(handler: Handler) -> BrickLinkPriceSource:
    return BrickLinkPriceSource(
        credentials=CREDENTIALS,
        base_url=BASE_URL,
        currency="EUR",
        region="europe",
        country_code=None,
        condition="N",
        vat="Y",
        timeout_seconds=5,
        max_parallel=2,
        user_agent="test",
        transport=httpx.MockTransport(handler),
        now=lambda: PRICE_TIME,
    )


def test_client_reads_sold_and_stock_prices() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        sold = request.url.params["guide_type"] == "sold"
        data = _price_data("0.0700", "0.0523", 900) if sold else _price_data("0.09", "0.08", 50)
        return httpx.Response(200, json={"meta": {"code": 200}, "data": data})

    guides = _source(handler).fetch([("3024", 11)])
    assert guides == {("3024", 11): PriceGuide(Decimal("0.0523"), Decimal("0.08"), PRICE_TIME)}
    assert {r.url.params["guide_type"] for r in requests} == {"sold", "stock"}
    query = parse_qs(requests[0].url.query.decode())
    assert query["color_id"] == ["11"] and query["currency_code"] == ["EUR"]
    assert query["region"] == ["europe"] and query["new_or_used"] == ["N"]
    assert requests[0].url.path == "/api/store/v1/items/PART/3024/price"
    assert requests[0].headers["Authorization"].startswith("OAuth ")


def test_client_without_sales_or_unknown_part_has_no_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/9999/" in request.url.path:
            return httpx.Response(404, json={"meta": {"code": 404, "message": "NOT_FOUND"}})
        return httpx.Response(200, json={"meta": {"code": 200}, "data": _price_data("0", "0", 0)})

    guides = _source(handler).fetch([("9999", 11), ("3024", 11)])
    assert guides[("9999", 11)] == PriceGuide(None, None, PRICE_TIME)
    assert guides[("3024", 11)] == PriceGuide(None, None, PRICE_TIME)


def test_client_reports_rejected_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        meta = {"code": 401, "message": "BAD_OAUTH_REQUEST", "description": "Invalid IP"}
        return httpx.Response(200, json={"meta": meta})

    with pytest.raises(PriceSourceError, match="IP-Adresse"):
        _source(handler).fetch([("3024", 11)])


def test_client_reports_network_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(PriceSourceError, match="nicht erreichbar"):
        _source(handler).fetch([("3024", 11)])


# --- Cache ------------------------------------------------------------------------------


def test_cache_round_trip_and_age(tmp_path: Path) -> None:
    path = tmp_path / "cache" / "preise.json"
    guide = PriceGuide(Decimal("0.0523"), None, PRICE_TIME)
    PriceCache(path, "N|EUR", 7).update({("3024", 11): guide})
    assert PriceCache(path, "N|EUR", 7).get(("3024", 11)) == guide
    assert PriceCache(path, "N|USD", 7).get(("3024", 11)) is None  # andere Währung
    later = PriceCache(path, "N|EUR", 7, now=lambda: PRICE_TIME + timedelta(days=8))
    assert not later.is_fresh(guide)
    assert PriceCache(path, "N|EUR", 7, now=lambda: PRICE_TIME).is_fresh(guide)


def test_broken_cache_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "preise.json"
    path.write_text("{kaputt", encoding="utf-8")
    assert PriceCache(path, "x", 7).get(("3024", 11)) is None


# --- Rechnung ---------------------------------------------------------------------------


def _item(part: str, color: int | None, quantity: int) -> OrderItem:
    return OrderItem(part, part, f"Teil {part}", 0, "Farbe", color, quantity)


def test_estimate_sums_and_notes() -> None:
    items = [_item("a", 1, 10), _item("b", 1, 2), _item("c", None, 5), _item("d", 1, 1)]
    guides = {
        ("a", 1): PriceGuide(Decimal("0.05"), Decimal("0.07"), PRICE_TIME),
        ("b", 1): PriceGuide(None, Decimal("1.00"), PRICE_TIME),
    }
    estimate = estimate_prices(items, guides, "EUR")
    assert estimate.total_sold == Decimal("0.50")
    assert estimate.total_stock == Decimal("2.70")
    assert [line.note for line in estimate.lines] == ["", NO_SALES, NO_COLOR_CODE, NO_PRICE_DATA]
    assert not estimate.complete and len(estimate.missing) == 3
    assert estimate.oldest == PRICE_TIME


# --- Ablauf für ein Mosaik ---------------------------------------------------------


@pytest.fixture
def mosaic_dir(pipeline_config: AppConfig, image_file: Path) -> Path:
    """Fertiges Mosaik, dessen Teileliste BrickLink-Farbcodes hat."""
    directory = make_pipeline(pipeline_config).run(image_file, title="bild").target_dir
    add_bricklink_colors(directory)
    return directory


def _result(directory: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((directory / "ergebnis.json").read_text("utf-8"))
    return data


def test_result_json_contains_parts_list(mosaic_dir: Path) -> None:
    rows = _result(mosaic_dir)["teileliste"]
    assert isinstance(rows, list) and rows
    assert set(rows[0]) == {
        "teil",
        "bricklink_teil",
        "name",
        "farbe_id",
        "farbe",
        "bricklink_farbe",
        "anzahl",
    }
    assert sum(row["anzahl"] for row in rows) == _result(mosaic_dir)["anzahl_teile"]


def test_service_writes_price_files_and_updates_result(
    pipeline_config: AppConfig, mosaic_dir: Path
) -> None:
    estimate = make_price_service(pipeline_config).estimate(mosaic_dir)
    parts = sum(line.item.quantity for line in estimate.lines)
    assert estimate.complete
    assert estimate.total_sold == Decimal("0.1") * parts
    result = _result(mosaic_dir)
    assert {"preise.json", "preise_bricklink.csv", "teileliste_bricklink.xml"} <= set(
        result["dateien"]  # type: ignore[arg-type]
    )
    summary = result["preise"]
    assert isinstance(summary, dict)
    assert summary["waehrung"] == "EUR" and summary["vollstaendig"] is True
    assert summary["summe_verkauft"] == pytest.approx(0.1 * parts)
    with (mosaic_dir / "preise_bricklink.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    assert rows[-1][0] == "Summe" and rows[1][5] == "0,1000"  # Dezimalkomma für Excel


def test_service_uses_cache_and_offline_mode(pipeline_config: AppConfig, mosaic_dir: Path) -> None:
    source = FakePriceSource()
    service = make_price_service(pipeline_config, source)
    service.estimate(mosaic_dir)
    service.estimate(mosaic_dir)  # alles aktuell im Cache
    assert len(source.calls) == 1
    service.estimate(mosaic_dir, force=True)
    assert len(source.calls) == 2
    offline = make_price_service(pipeline_config, FakePriceSource())
    assert offline.estimate(mosaic_dir, offline=True).complete  # aus dem Cache


def test_offline_without_cache_marks_everything_missing(
    pipeline_config: AppConfig, mosaic_dir: Path
) -> None:
    source = FakePriceSource()
    estimate = make_price_service(pipeline_config, source).estimate(mosaic_dir, offline=True)
    assert not source.calls
    assert {line.note for line in estimate.lines} == {NOT_CACHED}
    assert estimate.total_sold == 0


def test_wanted_list_gets_condition_and_max_price(
    pipeline_config: AppConfig, mosaic_dir: Path
) -> None:
    pricing = dataclasses.replace(pipeline_config.pricing, max_price_factor=1.5, condition="U")
    config = dataclasses.replace(pipeline_config, pricing=pricing)
    make_price_service(config).estimate(mosaic_dir)
    root = ElementTree.parse(mosaic_dir / "teileliste_bricklink.xml").getroot()
    items = list(root.iter("ITEM"))
    assert items
    assert {item.findtext("CONDITION") for item in items} == {"U"}
    assert {item.findtext("MAXPRICE") for item in items} == {"0.1500"}


def test_old_mosaic_without_parts_list_is_reported(
    pipeline_config: AppConfig, mosaic_dir: Path
) -> None:
    result = _result(mosaic_dir)
    del result["teileliste"]
    (mosaic_dir / "ergebnis.json").write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ConfigError, match="neu erzeugen"):
        make_price_service(pipeline_config).estimate(mosaic_dir)


def test_find_mosaic(pipeline_config: AppConfig, mosaic_dir: Path) -> None:
    service = make_price_service(pipeline_config)
    assert service.find_mosaic(None) == mosaic_dir
    assert service.find_mosaic(mosaic_dir.name) == mosaic_dir
    assert service.find_mosaic(str(mosaic_dir)) == mosaic_dir
    with pytest.raises(ConfigError, match="gibt es nicht"):
        service.find_mosaic("gibt_es_nicht")


# --- Konfiguration ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "euro"),
        ("region", "mars"),
        ("country_code", "deu"),
        ("condition", "X"),
        ("vat", "J"),
        ("max_price_factor", 0.0),
    ],
)
def test_invalid_pricing_config(app_config: AppConfig, field: str, value: object) -> None:
    pricing = dataclasses.replace(app_config.pricing, **{field: value})
    with pytest.raises(ConfigError):
        validate_config(dataclasses.replace(app_config, pricing=pricing))


def test_default_pricing_config_is_valid(app_config: AppConfig) -> None:
    validate_config(dataclasses.replace(app_config, pricing=PricingConfig()))
