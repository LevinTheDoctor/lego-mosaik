"""Tests für ``LocalMosaicRepository`` (Mosaike als Ordner unter ``ausgabe/``)."""

from pathlib import Path

import pytest

from lego_mosaik.domain.errors import NotFoundError
from lego_mosaik.storage.mosaics import LocalMosaicRepository


@pytest.fixture
def repository(tmp_path: Path) -> LocalMosaicRepository:
    directory = tmp_path / "2026-10-13_120000_katze"
    directory.mkdir()
    (directory / "ergebnis.json").write_text('{"dateien": ["vorschau.png"]}', encoding="utf-8")
    (directory / "vorschau.png").write_bytes(b"png")
    return LocalMosaicRepository(tmp_path)


def test_reads_and_opens_files(repository: LocalMosaicRepository) -> None:
    mosaic_id = "2026-10-13_120000_katze"
    assert repository.exists(mosaic_id)
    assert repository.read_bytes(mosaic_id, "vorschau.png") == b"png"
    stored = repository.open_file(mosaic_id, "vorschau.png")
    assert stored.path is not None and stored.size == 3


@pytest.mark.parametrize("mosaic_id", ["fehlt", "../etc", "a/b", ""])
def test_unknown_or_unsafe_ids_are_not_found(
    repository: LocalMosaicRepository, mosaic_id: str
) -> None:
    assert not repository.exists(mosaic_id)
    with pytest.raises(NotFoundError):
        repository.read_bytes(mosaic_id, "ergebnis.json")


@pytest.mark.parametrize("file_name", ["../ergebnis.json", ".versteckt", "fehlt.png", "a/b.png"])
def test_unknown_or_unsafe_files_are_not_found(
    repository: LocalMosaicRepository, file_name: str
) -> None:
    with pytest.raises(NotFoundError):
        repository.open_file("2026-10-13_120000_katze", file_name)


def test_output_dir_and_workspace_are_local_folders(
    repository: LocalMosaicRepository, tmp_path: Path
) -> None:
    with repository.new_output_dir() as output_dir:
        assert output_dir == tmp_path
    with repository.workspace("2026-10-13_120000_katze", write=True) as directory:
        assert (directory / "vorschau.png").is_file()
    with pytest.raises(NotFoundError), repository.workspace("fehlt"):
        pass
