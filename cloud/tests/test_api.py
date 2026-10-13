"""Die API des Kerns mit Cloud-Speichern, plus Actuator-Endpunkte."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import BUCKET
from fastapi.testclient import TestClient

from lego_mosaik_cloud.app import CloudServices, build_cloud_app


@pytest.fixture
def client(imported: CloudServices) -> Iterator[TestClient]:
    with TestClient(build_cloud_app(imported)) as test_client:
        yield test_client


def _create(client: TestClient, image_file: Path) -> dict[str, Any]:
    with image_file.open("rb") as handle:
        response = client.post(
            "/mosaike", files={"bild": ("katze.png", handle, "image/png")}, data={"titel": "Katze"}
        )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_create_download_and_edit(client: TestClient, image_file: Path, s3: Any) -> None:
    created = _create(client, image_file)
    mosaic_id = created["id"]
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]}
    assert f"mosaike/{mosaic_id}/ergebnis.json" in keys
    assert any(key.startswith(f"mosaike/{mosaic_id}/original.") for key in keys)

    preview = client.get(f"/mosaike/{mosaic_id}/dateien/vorschau.png")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")
    assert client.get(f"/mosaike/{mosaic_id}").json()["titel"] == "Katze"
    assert client.get(f"/mosaike/{mosaic_id}/dateien/..%2Fx").status_code == 404

    edited = client.post(
        f"/mosaike/{mosaic_id}/bearbeiten",
        json={"noppen": [{"zeile": 2, "spalte": 2, "farbe_id": 4}]},
    )
    assert edited.status_code == 201, edited.text
    assert edited.json()["basis"] == mosaic_id
    raster = client.get(f"/mosaike/{edited.json()['id']}/raster").json()
    assert raster["zeilen"][1][1] == 4


def test_offline_prices_are_written_back(client: TestClient, image_file: Path) -> None:
    mosaic_id = _create(client, image_file)["id"]
    assert client.get(f"/mosaike/{mosaic_id}/preise").status_code == 404
    estimate = client.post(f"/mosaike/{mosaic_id}/preise", params={"offline": True})
    assert estimate.status_code == 200, estimate.text
    assert client.get(f"/mosaike/{mosaic_id}/preise").status_code == 200
    assert "preise.json" in client.get(f"/mosaike/{mosaic_id}").json()["dateien"]
    assert client.get(f"/mosaike/{mosaic_id}/bricklink").status_code == 200


def test_palettes_and_presets_in_postgres(client: TestClient, image_file: Path) -> None:
    palette = client.post("/paletten", json={"name": "Rot-Weiß", "farb_ids": [4, 15]})
    assert palette.status_code == 201, palette.text
    preset = client.post(
        "/presets", json={"name": "Klein", "einstellungen": {"dithering": "nearest"}}
    )
    assert preset.status_code == 201, preset.text
    with image_file.open("rb") as handle:
        response = client.post(
            "/mosaike",
            files={"bild": ("b.png", handle, "image/png")},
            data={"preset": "klein", "palette": "rot-weiss"},
        )
    assert response.status_code == 201, response.text
    assert client.get("/presets/klein").json()["nutzungen"] == 1


def test_unknown_mosaic_is_404(client: TestClient) -> None:
    assert client.get("/mosaike/fehlt").status_code == 404
    assert client.get("/mosaike/fehlt/raster").status_code == 404


def test_actuator_health_and_probes(client: TestClient) -> None:
    health = client.get("/actuator/health")
    assert health.status_code == 200, health.text
    components = health.json()["components"]
    assert {name: c["status"] for name, c in components.items()} == {
        "db": "UP",
        "s3": "UP",
        "katalog": "UP",
    }
    assert client.get("/actuator/health/liveness").json() == {"status": "UP"}
    readiness = client.get("/actuator/health/readiness").json()
    assert set(readiness["components"]) == {"db", "s3"}
    assert client.get("/actuator/health/db").json()["status"] == "UP"
    assert client.get("/actuator/health/fehlt").status_code == 404


def test_readiness_is_down_without_bucket(client: TestClient, s3: Any) -> None:
    s3.delete_bucket(Bucket=BUCKET)
    response = client.get("/actuator/health/readiness")
    assert response.status_code == 503
    assert response.json()["components"]["s3"]["status"] == "DOWN"


def test_actuator_info_metrics_and_prometheus(client: TestClient, image_file: Path) -> None:
    _create(client, image_file)
    info = client.get("/actuator/info").json()
    assert info["app"]["name"] == "lego-mosaik" and "git_sha" in info["build"]
    text = client.get("/actuator/prometheus").text
    assert 'lego_mosaik_mosaike_erzeugt_total{art="neu"} 1.0' in text
    assert 'uri="/mosaike"' in text and 'outcome="SUCCESS"' in text
    assert "lego_mosaik_pipeline_schritt_seconds_bucket" in text
    names = client.get("/actuator/metrics").json()["names"]
    assert "http_server_requests_seconds" in names
    detail = client.get("/actuator/metrics/http_server_requests_seconds").json()
    assert {m["statistic"] for m in detail["measurements"]} == {"COUNT", "TOTAL_TIME"}
    assert any(tag["tag"] == "uri" for tag in detail["availableTags"])
    assert client.get("/actuator/metrics/fehlt").status_code == 404
    assert "/actuator/health" in client.get("/actuator").json()["_links"]["health"]["href"]


def test_actuator_loggers_change_level(client: TestClient) -> None:
    assert (
        client.post("/actuator/loggers/lego_mosaik", json={"configuredLevel": "DEBUG"}).status_code
        == 204
    )
    assert client.get("/actuator/loggers/lego_mosaik").json()["configuredLevel"] == "DEBUG"
    assert (
        client.post("/actuator/loggers/lego_mosaik", json={"configuredLevel": None}).status_code
        == 204
    )
    assert client.get("/actuator/loggers/lego_mosaik").json()["configuredLevel"] is None
    assert client.post("/actuator/loggers/x", json={"configuredLevel": "LAUT"}).status_code == 422
    assert "ROOT" in client.get("/actuator/loggers").json()["loggers"]


def test_actuator_is_not_in_openapi(client: TestClient) -> None:
    assert not any(
        path.startswith("/actuator") for path in client.get("/openapi.json").json()["paths"]
    )
