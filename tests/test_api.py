"""Tests für die HTTP-API mit ``fastapi.testclient.TestClient`` (offline)."""

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import make_pipeline
from fastapi.testclient import TestClient

from lego_mosaik.config import AppConfig, MosaicOverrides, ServerConfig
from lego_mosaik.interfaces.api import create_app


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
