"""Tests für ``VolumeMosaicRepository`` (Dateien auf dem Volume, Metadaten in PostgreSQL)."""

import json
import shutil
from pathlib import Path

import pytest
from lego_mosaik.domain.errors import NotFoundError
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from lego_mosaik_cloud.storage.mosaics import TMP_DIR, VolumeMosaicRepository

MOSAIC_ID = "2026-10-13_120000_katze_a1b2c3"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "volume"


@pytest.fixture
def repository(db: Engine, root: Path) -> VolumeMosaicRepository:
    return VolumeMosaicRepository(db, root)


def _write_mosaic(root: Path) -> Path:
    directory = root / MOSAIC_ID
    directory.mkdir(parents=True)
    result = {"titel": "katze", "dateien": ["vorschau.png", "ergebnis.json"]}
    (directory / "ergebnis.json").write_text(json.dumps(result), encoding="utf-8")
    (directory / "vorschau.png").write_bytes(b"\x89PNG")
    (directory / "eingaben.json").write_text("{}", encoding="utf-8")
    return directory


def test_store_moves_files_and_adds_row(repository: VolumeMosaicRepository, root: Path) -> None:
    with repository.new_output_dir() as output_dir:
        repository.store(_write_mosaic(output_dir))
    names = {path.name for path in (root / MOSAIC_ID).iterdir()}
    assert names == {"ergebnis.json", "vorschau.png", "eingaben.json"}
    assert not any((root / TMP_DIR).iterdir())
    assert repository.exists(MOSAIC_ID)
    assert json.loads(repository.read_bytes(MOSAIC_ID, "ergebnis.json"))["titel"] == "katze"
    stored = repository.open_file(MOSAIC_ID, "vorschau.png")
    assert stored.path == root / MOSAIC_ID / "vorschau.png" and stored.size == 4


def test_store_copies_foreign_directories(
    repository: VolumeMosaicRepository, root: Path, tmp_path: Path
) -> None:
    """``uebernehmen``: Mosaike aus ``ausgabe/`` bleiben, wo sie sind."""
    source = _write_mosaic(tmp_path / "ausgabe")
    repository.store(source)
    assert (source / "vorschau.png").is_file()
    assert repository.read_bytes(MOSAIC_ID, "vorschau.png") == b"\x89PNG"


def test_existing_mosaic_is_not_overwritten(repository: VolumeMosaicRepository, root: Path) -> None:
    repository.store(_write_mosaic(root.parent / "erstes"))
    with repository.new_output_dir() as output_dir, pytest.raises(FileExistsError):
        repository.store(_write_mosaic(output_dir))  # gleiche ID ein zweites Mal
    assert repository.read_bytes(MOSAIC_ID, "vorschau.png") == b"\x89PNG"


def test_failed_insert_removes_directory(repository: VolumeMosaicRepository, root: Path) -> None:
    repository.store(_write_mosaic(root.parent / "erstes"))
    shutil.rmtree(root / MOSAIC_ID)  # Zeile bleibt, Ordner fehlt: das Einfügen scheitert
    with repository.new_output_dir() as output_dir, pytest.raises(IntegrityError):
        repository.store(_write_mosaic(output_dir))
    assert not (root / MOSAIC_ID).exists()


@pytest.mark.parametrize("name", ["fehlt.png", "../ergebnis.json", "a/b.png"])
def test_unknown_files_are_not_found(
    repository: VolumeMosaicRepository, tmp_path: Path, name: str
) -> None:
    repository.store(_write_mosaic(tmp_path))
    with pytest.raises(NotFoundError):
        repository.open_file(MOSAIC_ID, name)


def test_unknown_mosaic_is_not_found(repository: VolumeMosaicRepository) -> None:
    assert not repository.exists("fehlt") and not repository.exists("../x")
    with pytest.raises(NotFoundError):
        repository.read_bytes("fehlt", "ergebnis.json")
    with pytest.raises(NotFoundError), repository.workspace("fehlt"):
        pass


def test_workspace_writes_back_changes(repository: VolumeMosaicRepository, tmp_path: Path) -> None:
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
