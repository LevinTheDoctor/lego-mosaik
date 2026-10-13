"""Tests für ``S3MosaicRepository`` (Dateien in S3, Metadaten in PostgreSQL)."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import BUCKET
from lego_mosaik.domain.errors import NotFoundError
from sqlalchemy import Engine

from lego_mosaik_cloud.storage.mosaics import S3MosaicRepository

MOSAIC_ID = "2026-10-13_120000_katze_a1b2c3"


@pytest.fixture
def repository(db: Engine, s3: Any) -> S3MosaicRepository:
    return S3MosaicRepository(db, s3, BUCKET, "mosaike")


def _write_mosaic(root: Path) -> Path:
    directory = root / MOSAIC_ID
    directory.mkdir()
    result = {"titel": "katze", "dateien": ["vorschau.png", "ergebnis.json"]}
    (directory / "ergebnis.json").write_text(json.dumps(result), encoding="utf-8")
    (directory / "vorschau.png").write_bytes(b"\x89PNG")
    (directory / "eingaben.json").write_text("{}", encoding="utf-8")
    return directory


def test_store_uploads_files_and_row(repository: S3MosaicRepository, s3: Any) -> None:
    with repository.new_output_dir() as output_dir:
        repository.store(_write_mosaic(output_dir))
    keys = {obj["Key"] for obj in s3.list_objects_v2(Bucket=BUCKET)["Contents"]}
    assert keys == {
        f"mosaike/{MOSAIC_ID}/{n}" for n in ("ergebnis.json", "vorschau.png", "eingaben.json")
    }
    head = s3.head_object(Bucket=BUCKET, Key=f"mosaike/{MOSAIC_ID}/vorschau.png")
    assert head["ContentType"] == "image/png"
    assert repository.exists(MOSAIC_ID)
    assert json.loads(repository.read_bytes(MOSAIC_ID, "ergebnis.json"))["titel"] == "katze"
    stored = repository.open_file(MOSAIC_ID, "vorschau.png")
    assert b"".join(stored.chunks) == b"\x89PNG" and stored.size == 4


@pytest.mark.parametrize("name", ["fehlt.png", "../ergebnis.json", "a/b.png"])
def test_unknown_files_are_not_found(
    repository: S3MosaicRepository, tmp_path: Path, name: str
) -> None:
    repository.store(_write_mosaic(tmp_path))
    with pytest.raises(NotFoundError):
        repository.open_file(MOSAIC_ID, name)


def test_unknown_mosaic_is_not_found(repository: S3MosaicRepository) -> None:
    assert not repository.exists("fehlt") and not repository.exists("../x")
    with pytest.raises(NotFoundError):
        repository.read_bytes("fehlt", "ergebnis.json")
    with pytest.raises(NotFoundError), repository.workspace("fehlt"):
        pass


def test_workspace_writes_back_changes(repository: S3MosaicRepository, tmp_path: Path) -> None:
    repository.store(_write_mosaic(tmp_path))
    with repository.workspace(MOSAIC_ID) as directory:
        assert directory.name == MOSAIC_ID  # edit() nimmt den Ordnernamen als basis
        (directory / "nur_lokal.txt").write_text("x")
    with pytest.raises(NotFoundError):
        repository.read_bytes(MOSAIC_ID, "nur_lokal.txt")  # ohne write=True nicht übernommen
    with repository.workspace(MOSAIC_ID, write=True) as directory:
        (directory / "preise.json").write_text('{"summe": 1}')
        result = json.loads((directory / "ergebnis.json").read_text())
        result["preise"] = {"summe": 1}
        (directory / "ergebnis.json").write_text(json.dumps(result))
    assert repository.read_bytes(MOSAIC_ID, "preise.json") == b'{"summe": 1}'
    assert json.loads(repository.read_bytes(MOSAIC_ID, "ergebnis.json"))["preise"] == {"summe": 1}
