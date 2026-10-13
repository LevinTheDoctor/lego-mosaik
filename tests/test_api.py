"""Tests für die HTTP-API mit ``fastapi.testclient.TestClient`` (offline)."""

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from conftest import make_pipeline
from fastapi.testclient import TestClient

from lego_mosaik.config import AppConfig, MosaicOverrides, ServerConfig
from lego_mosaik.interfaces.api import create_app, openapi_yaml
from lego_mosaik.interfaces.api_models import StatsModel
from lego_mosaik.main import CONFIG, build_pipeline


@pytest.fixture
def client(pipeline_config: AppConfig) -> Iterator[TestClient]:
    config = dataclasses.replace(pipeline_config, server=ServerConfig(max_upload_mb=1))
    # "with" startet den lifespan (Hintergrund-Aktualisierung, hier offline)
    with TestClient(create_app(config, make_pipeline)) as test_client:
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
    assert committed == openapi_yaml(create_app(CONFIG, build_pipeline))


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
    assert set(dateien) == {"vorschau.png", "ergebnis.json"}


def test_cors_allows_local_frontends_only(client: TestClient) -> None:
    local = client.get("/gesundheit", headers={"Origin": "http://localhost:5173"})
    assert local.headers["access-control-allow-origin"] == "http://localhost:5173"
    foreign = client.get("/gesundheit", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in foreign.headers
