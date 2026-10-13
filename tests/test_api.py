"""Tests für die HTTP-API mit ``fastapi.testclient.TestClient`` (offline)."""

import dataclasses
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import FakePriceSource, add_bricklink_colors, make_pipeline, make_price_service
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from lego_mosaik.config import AppConfig, MosaicOverrides, ServerConfig
from lego_mosaik.domain.errors import MissingCredentialsError
from lego_mosaik.interfaces.api import create_app, openapi_yaml
from lego_mosaik.interfaces.api_models import StatsModel
from lego_mosaik.main import CONFIG, build_pipeline, build_price_service
from lego_mosaik.pricing.protocol import PriceGuide, PriceKey
from lego_mosaik.pricing.service import PriceService


@pytest.fixture
def client(pipeline_config: AppConfig) -> Iterator[TestClient]:
    config = dataclasses.replace(pipeline_config, server=ServerConfig(max_upload_mb=1))
    # "with" startet den lifespan (Hintergrund-Aktualisierung, hier offline)
    with TestClient(create_app(config, make_pipeline, make_price_service)) as test_client:
        yield test_client


def _upload(client: TestClient, image_file: Path, **fields: str) -> dict[str, object]:
    with image_file.open("rb") as handle:
        response = client.post("/mosaike", files={"bild": ("bild.png", handle)}, data=fields)
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_health(client: TestClient) -> None:
    body = client.get("/gesundheit").json()
    assert body["status"] == "ok"
    assert {entry["datei"] for entry in body["katalog"]} >= {"colors.csv.gz"}


def test_colors_and_methods(client: TestClient) -> None:
    names = [color["name"] for color in client.get("/farben?teile=plate").json()]
    assert "Black" in names and "Trans-Red" not in names
    exotic = [c["name"] for c in client.get("/farben?teile=plate&exotisch=true").json()]
    assert "Trans-Red" in exotic
    assert "floyd_steinberg" in client.get("/verfahren").json()["Dithering"]


def test_create_and_download(client: TestClient, image_file: Path) -> None:
    body = _upload(client, image_file, rahmen="2")
    mosaic_id = str(body["id"])
    dateien = body["dateien"]
    assert isinstance(dateien, dict) and "mosaik.ldr" in dateien
    result = client.get(f"/mosaike/{mosaic_id}").json()
    assert result["konfiguration"]["frame"]["width"] == 2
    download = client.get(f"/mosaike/{mosaic_id}/dateien/mosaik.ldr")
    assert download.status_code == 200
    assert download.text.startswith("0 bild")


def test_api_matches_pipeline_output(
    client: TestClient, image_file: Path, pipeline_config: AppConfig
) -> None:
    body = _upload(client, image_file, platten="1x1", teile="plate,tile")
    api_dir = pipeline_config.output_dir / str(body["id"])
    overrides = MosaicOverrides(plates_wide=1, plates_high=1)
    cli_result = make_pipeline(pipeline_config).run(image_file, overrides, title="bild")
    for name in ("mosaik.ldr", "teileliste.csv", "teileliste_bricklink.xml"):
        assert (api_dir / name).read_bytes() == (cli_result.target_dir / name).read_bytes()


def test_path_traversal_is_blocked(client: TestClient, image_file: Path) -> None:
    mosaic_id = str(_upload(client, image_file)["id"])
    assert client.get(f"/mosaike/{mosaic_id}/dateien/..%2F..%2Fpyproject.toml").status_code == 404
    assert client.get("/mosaike/..%2F..").status_code == 404
    response = client.get(f"/mosaike/{mosaic_id}/dateien/geheim.txt")
    assert response.status_code == 404
    assert "fehler" in response.json()


def test_invalid_image_is_400(client: TestClient) -> None:
    response = client.post("/mosaike", files={"bild": ("x.png", b"kein bild")})
    assert response.status_code == 400
    assert "kein lesbares Bild" in response.json()["fehler"]


def test_too_large_upload_is_413(client: TestClient) -> None:
    response = client.post("/mosaike", files={"bild": ("x.png", b"0" * (1024 * 1024 + 1))})
    assert response.status_code == 413
    assert "größer als 1 MB" in response.json()["fehler"]


def test_invalid_parameter_is_422(client: TestClient, image_file: Path) -> None:
    with image_file.open("rb") as handle:
        response = client.post("/mosaike", files={"bild": handle}, data={"platten": "drei"})
    assert response.status_code == 422
    assert "BxH" in response.json()["fehler"]


def test_missing_upload_is_422(client: TestClient) -> None:
    response = client.post("/mosaike")
    assert response.status_code == 422
    assert "bild" in response.json()["fehler"]


def test_unknown_mosaic_is_404(client: TestClient) -> None:
    assert client.get("/mosaike/gibt-es-nicht").status_code == 404


def test_create_with_format_style_and_relief(client: TestClient, image_file: Path) -> None:
    fields = {"format": "klein", "stil": "punkte", "relief": "hell", "relief_hoehe": "2"}
    body = _upload(client, image_file, **fields)
    stats = body["statistik"]
    assert isinstance(stats, dict)
    assert (stats["breite"], stats["hoehe"]) == (32, 32)
    assert stats["formteile"] == 30 * 30  # punkte: jede Bildnoppe rund
    assert stats["relief_teile"]


def test_openapi_yaml_is_served_and_up_to_date(client: TestClient) -> None:
    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    document = yaml.safe_load(response.text)
    assert document["openapi"].startswith("3.")
    form = document["components"]["schemas"]["Body_create_mosaic"]["properties"]
    assert {"bild", "format", "stil", "relief", "titel"} <= set(form)
    # Die eingecheckte openapi.yaml muss zur App passen (neu: scripts/openapi_exportieren.py)
    committed = (Path(__file__).parent.parent / "openapi.yaml").read_text(encoding="utf-8")
    assert committed == openapi_yaml(create_app(CONFIG, build_pipeline, build_price_service))


def test_title_field_names_the_mosaic(client: TestClient, image_file: Path) -> None:
    body = _upload(client, image_file, titel="Über den Wolken", ausgaben="result_json")
    assert str(body["id"]).endswith("_Ueber_den_Wolken")


def test_options_list_defaults_and_choices(client: TestClient) -> None:
    body = client.get("/optionen").json()
    fields = body["felder"]
    assert fields["unterbau"]["werte"] == ["art", "grundplatte", "platten", "ohne"]
    assert fields["teile"]["mehrfach"] is True
    assert fields["relief_hoehe"]["minimum"] == 1
    assert "floyd_steinberg" in fields["dithering"]["werte"]  # aus der Registry
    assert {"quadrat", "weltkarte"} <= {f["name"] for f in body["formate"]}


def test_form_schema_lists_allowed_values(client: TestClient) -> None:
    document = yaml.safe_load(client.get("/openapi.yaml").text)
    form = document["components"]["schemas"]["Body_create_mosaic"]["properties"]
    assert form["relief"]["anyOf"][0]["enum"] == ["ohne", "motiv", "hell", "dunkel"]
    assert "Kommagetrennt aus: plate, tile, brick" in form["teile"]["description"]


def test_statistics_are_fully_described(client: TestClient, image_file: Path) -> None:
    # Jede Kennzahl, die der Server liefert, steht im Modell (und damit in openapi.yaml)
    body = _upload(client, image_file)
    result = client.get(f"/mosaike/{body['id']}").json()
    stats = body["statistik"]
    assert isinstance(stats, dict)
    assert set(stats) == set(result["statistik"]) == set(StatsModel.model_fields)
    assert result["palette"][0].keys() == {"id", "name", "hex", "ldraw_code", "noppen"}


def test_result_json_is_always_written(client: TestClient, image_file: Path) -> None:
    body = _upload(client, image_file, ausgaben="preview_png")
    dateien = body["dateien"]
    assert isinstance(dateien, dict)
    # ergebnis.json und raster.json immer; Original und Eingaben für „bearbeiten“
    assert set(dateien) == {
        "vorschau.png",
        "ergebnis.json",
        "raster.json",
        "original.png",
        "eingaben.json",
    }


def test_cors_allows_local_frontends_only(client: TestClient) -> None:
    local = client.get("/gesundheit", headers={"Origin": "http://localhost:5173"})
    assert local.headers["access-control-allow-origin"] == "http://localhost:5173"
    foreign = client.get("/gesundheit", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in foreign.headers


# --- BrickLink: Preise und Wanted List ---------------------------------------------------


def _mosaic_with_codes(client: TestClient, image_file: Path, output_dir: Path) -> str:
    """Erzeugt ein Mosaik und gibt seiner Teileliste BrickLink-Farbcodes."""
    mosaic_id = str(_upload(client, image_file, ausgaben="parts_list_csv")["id"])
    add_bricklink_colors(output_dir / mosaic_id)
    return mosaic_id


def test_prices_estimate_and_download(
    client: TestClient, image_file: Path, pipeline_config: AppConfig
) -> None:
    mosaic_id = _mosaic_with_codes(client, image_file, pipeline_config.output_dir)
    assert client.get(f"/mosaike/{mosaic_id}/preise").status_code == 404
    response = client.post(f"/mosaike/{mosaic_id}/preise")
    assert response.status_code == 200, response.text
    body = response.json()
    parts = sum(line["anzahl"] for line in body["positionen"])
    assert body["waehrung"] == "EUR" and body["vollstaendig"] is True
    assert body["summe_verkauft"] == pytest.approx(0.1 * parts)
    assert body["summe_angeboten"] == pytest.approx(0.12 * parts)
    assert body["upload_url"].startswith("https://www.bricklink.com/")
    assert client.get(f"/mosaike/{mosaic_id}/preise").json() == body
    result = client.get(f"/mosaike/{mosaic_id}").json()
    assert result["preise"]["summe_verkauft"] == body["summe_verkauft"]
    download = client.get(f"/mosaike/{mosaic_id}/dateien/preise_bricklink.csv")
    assert download.status_code == 200 and "Summe" in download.text


def test_bricklink_export(client: TestClient, image_file: Path, pipeline_config: AppConfig) -> None:
    mosaic_id = _mosaic_with_codes(client, image_file, pipeline_config.output_dir)
    body = client.get(f"/mosaike/{mosaic_id}/bricklink").json()
    assert body["xml"].startswith("<?xml") and "<CONDITION>N</CONDITION>" in body["xml"]
    assert body["positionen"] > 0 and body["ausgelassen"] == []
    assert body["teile"] == client.get(f"/mosaike/{mosaic_id}").json()["anzahl_teile"]
    assert body["upload_url"] == "https://www.bricklink.com/v2/wanted/upload.page"


def test_bricklink_export_names_colors_without_code(client: TestClient, image_file: Path) -> None:
    mosaic_id = str(_upload(client, image_file, ausgaben="parts_list_csv")["id"])
    body = client.get(f"/mosaike/{mosaic_id}/bricklink").json()
    assert body["positionen"] == 0 and "White" in body["ausgelassen"]


class _NoCredentials(FakePriceSource):
    def fetch(self, keys: Sequence[PriceKey]) -> dict[PriceKey, PriceGuide]:
        raise MissingCredentialsError("BrickLink-Zugangsdaten fehlen: BRICKLINK_CONSUMER_KEY.")


def test_missing_credentials_is_503(pipeline_config: AppConfig, image_file: Path) -> None:
    def prices(config: AppConfig) -> PriceService:
        return make_price_service(config, _NoCredentials())

    with TestClient(create_app(pipeline_config, make_pipeline, prices)) as test_client:
        mosaic_id = _mosaic_with_codes(test_client, image_file, pipeline_config.output_dir)
        response = test_client.post(f"/mosaike/{mosaic_id}/preise")
        assert response.status_code == 503
        assert "BRICKLINK_CONSUMER_KEY" in response.json()["fehler"]
        offline = test_client.post(f"/mosaike/{mosaic_id}/preise?offline=true")
        assert offline.status_code == 200 and offline.json()["vollstaendig"] is False


def test_prices_for_unknown_mosaic_is_404(client: TestClient) -> None:
    assert client.post("/mosaike/gibt-es-nicht/preise").status_code == 404
    assert client.get("/mosaike/gibt-es-nicht/bricklink").status_code == 404


# --- Bild bearbeiten, Raster, Paletten, Presets -----------------------------------------


@pytest.fixture
def disk_file(tmp_path: Path) -> Path:
    """Weißer Hintergrund mit roter Scheibe (64 × 64)."""
    path = tmp_path / "scheibe.png"
    image = Image.new("RGB", (64, 64), (255, 255, 255))
    ImageDraw.Draw(image).ellipse((16, 16, 48, 48), fill=(201, 26, 9))
    image.save(path)
    return path


def _post_image(client: TestClient, path: str, image: Path, **fields: str) -> Any:
    with image.open("rb") as handle:
        return client.post(path, files={"bild": ("bild.png", handle)}, data=fields)


def test_detect_background(client: TestClient, disk_file: Path) -> None:
    response = _post_image(client, "/bild/hintergrund", disk_file, hintergrund_toleranz="20")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["farbe_hex"] == "#FFFFFF"
    assert body["lego_farbe"]["name"] == "White"
    assert 0.5 < body["anteil"] < 0.9
    assert body["maske_png"].startswith("data:image/png;base64,")


def test_edited_image_preview(client: TestClient, disk_file: Path) -> None:
    response = _post_image(
        client, "/bild/vorschau", disk_file, farbfilter="sepia", drehen="90", groesse="64"
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    bad = _post_image(client, "/bild/vorschau", disk_file, farbfilter="bunt")
    assert bad.status_code == 422 and "Farbfilter" in bad.json()["fehler"]


def test_background_removal_and_raster(client: TestClient, disk_file: Path) -> None:
    body = _upload(client, disk_file, hintergrund="entfernen", hintergrundfarbe="1")
    raster = client.get(f"/mosaike/{body['id']}/raster").json()
    assert (raster["breite"], raster["hoehe"], raster["rahmen"]) == (16, 16, 1)
    assert raster["zeilen"][1][1] == 1  # Hintergrund jetzt Blue
    assert raster["zeilen"][8][8] == 4  # Motiv Red
    assert {color["id"] for color in raster["farben"]} >= {0, 1, 4}


def test_edit_mosaic(client: TestClient, image_file: Path) -> None:
    mosaic_id = str(_upload(client, image_file)["id"])
    response = client.post(
        f"/mosaike/{mosaic_id}/bearbeiten",
        json={"noppen": [{"zeile": 2, "spalte": 2, "farbe_id": 4}], "fuellen": []},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != mosaic_id and body["basis"] == mosaic_id
    raster = client.get(f"/mosaike/{body['id']}/raster").json()
    assert raster["zeilen"][1][1] == 4
    assert client.get(f"/mosaike/{body['id']}").json()["basis"] == mosaic_id
    frame = client.post(
        f"/mosaike/{mosaic_id}/bearbeiten",
        json={"noppen": [{"zeile": 1, "spalte": 1, "farbe_id": 4}]},
    )
    assert frame.status_code == 422 and "Bildfläche" in frame.json()["fehler"]
    empty = client.post(f"/mosaike/{mosaic_id}/bearbeiten", json={})
    assert empty.status_code == 422
    assert client.post("/mosaike/fehlt/bearbeiten", json={}).status_code == 404


def test_palettes_crud_and_use(client: TestClient, image_file: Path) -> None:
    created = client.post(
        "/paletten", json={"name": "Schwarz Weiß", "farb_ids": [0, 15], "beschreibung": "Kontrast"}
    )
    assert created.status_code == 201, created.text
    palette = created.json()
    assert palette["id"] == "schwarz-weiss"
    assert [color["name"] for color in palette["farben"]] == ["Black", "White"]
    assert client.post("/paletten", json={"name": "x", "farb_ids": [999]}).status_code == 422
    assert (
        client.post("/paletten", json={"name": "schwarz weiß", "farb_ids": [0]}).status_code == 422
    )

    body = _upload(client, image_file, palette="schwarz-weiss")
    colors = {color["id"] for color in client.get(f"/mosaike/{body['id']}").json()["palette"]}
    assert colors <= {0, 15}
    assert "schwarz-weiss" in client.get("/optionen").json()["felder"]["palette"]["werte"]

    updated = client.put(
        "/paletten/schwarz-weiss", json={"name": "Schwarz Weiß", "farb_ids": [0, 15, 4]}
    )
    assert updated.json()["farb_ids"] == [0, 15, 4]
    assert client.delete("/paletten/schwarz-weiss").status_code == 204
    assert client.get("/paletten/schwarz-weiss").status_code == 404
    assert _post_image(client, "/mosaike", image_file, palette="fehlt").status_code == 404


def test_presets_crud_ranking_and_use(client: TestClient, image_file: Path) -> None:
    first = client.post(
        "/presets",
        json={"name": "Breiter Rahmen", "einstellungen": {"rahmen": 2, "kontrast": 1.1}},
    )
    assert first.status_code == 201, first.text
    client.post("/presets", json={"name": "Top", "einstellungen": {"rahmen": 0}, "bewertung": 5})
    invalid = client.post("/presets", json={"name": "Kaputt", "einstellungen": {"rahmen": 20}})
    assert invalid.status_code == 422
    wrong = client.post("/presets", json={"name": "Titel", "einstellungen": {"titel": "x"}})
    assert wrong.status_code == 422

    names = [p["name"] for p in client.get("/presets").json()]
    assert names == ["Top", "Breiter Rahmen"]  # beste Bewertung zuerst
    marked = client.patch("/presets/breiter-rahmen", json={"favorit": True})
    assert marked.json()["favorit"] is True and marked.json()["bewertung"] == 0
    assert next(p["name"] for p in client.get("/presets").json()) == "Breiter Rahmen"
    favorites = client.get("/presets?favoriten=true").json()
    assert [p["id"] for p in favorites] == ["breiter-rahmen"]
    assert client.patch("/presets/top", json={"bewertung": 9}).status_code == 422

    body = _upload(client, image_file, preset="breiter-rahmen", kontrast="1.0")
    result = client.get(f"/mosaike/{body['id']}").json()
    assert result["konfiguration"]["frame"]["width"] == 2  # aus dem Preset
    assert result["konfiguration"]["mosaic"]["contrast"] == 1.0  # angegeben geht vor
    assert client.get("/presets/breiter-rahmen").json()["nutzungen"] == 1
    assert [p["name"] for p in client.get("/presets?sortierung=nutzung").json()][:1] == [
        "Breiter Rahmen"
    ]

    replaced = client.put(
        "/presets/top", json={"name": "Top", "einstellungen": {"stil": "quadrat"}, "bewertung": 3}
    )
    assert replaced.json()["einstellungen"] == {"stil": "quadrat"}
    assert client.delete("/presets/top").status_code == 204
    assert client.get("/presets/top").status_code == 404


def test_preset_from_mosaic(client: TestClient, image_file: Path) -> None:
    mosaic_id = str(_upload(client, image_file, rahmen="2", titel="Katze", farbfilter="warm")["id"])
    response = client.post(
        f"/mosaike/{mosaic_id}/preset", json={"name": "Wie Katze", "bewertung": 4}
    )
    assert response.status_code == 201, response.text
    preset = response.json()
    assert preset["einstellungen"]["rahmen"] == 2
    assert preset["einstellungen"]["farbfilter"] == "warm"
    assert "titel" not in preset["einstellungen"]
    assert preset["bewertung"] == 4


def test_options_list_new_fields(client: TestClient) -> None:
    fields = client.get("/optionen").json()["felder"]
    assert "sepia" in fields["farbfilter"]["werte"]
    assert "entfernen" in fields["hintergrund"]["werte"]
    assert fields["drehen"]["werte"] == [0, 90, 180, 270]
    assert fields["glaettung"]["werte"] == ["aus", "leicht", "mittel", "stark"]
    assert fields["glaettung"]["standard"] == "leicht"
    assert {"palette", "preset", "farb_ids", "ausschnitt"} <= set(fields)


def test_cors_allows_editing_methods(client: TestClient) -> None:
    response = client.options(
        "/presets/x",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"},
    )
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]
