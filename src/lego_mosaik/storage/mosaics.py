"""Fertige Mosaike als Ordner unter ``ausgabe/`` (lokale Variante des ``MosaicRepository``)."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lego_mosaik.domain.errors import NotFoundError
from lego_mosaik.outputs.result_json import FILE_NAME as RESULT_FILE
from lego_mosaik.storage.protocol import StoredFile

# IDs sind Ordnernamen der Form 2026-10-12_143005_katze (nur sichere Zeichen)
VALID_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# Dateinamen eines Mosaiks: keine Pfade, nur Name mit Endung
VALID_FILE = re.compile(r"^[A-Za-z0-9_.-]+$")


def check_mosaic_id(mosaic_id: str) -> str:
    """Prüft eine Mosaik-ID; unsichere IDs gelten als unbekannt.

    Raises:
        NotFoundError: Wenn die ID unzulässige Zeichen enthält.
    """
    if not VALID_ID.match(mosaic_id):
        raise NotFoundError(f"Mosaik „{mosaic_id}“ gibt es nicht.")
    return mosaic_id


def check_file_name(file_name: str) -> str:
    """Prüft einen Dateinamen; Pfadangaben (``..``, ``/``) gelten als unbekannt.

    Raises:
        NotFoundError: Wenn der Name unzulässig ist.
    """
    if not VALID_FILE.match(file_name) or file_name.startswith("."):
        raise NotFoundError(f"Datei „{file_name}“ gibt es nicht.")
    return file_name


class LocalMosaicRepository:
    """Jedes Mosaik ist ein Ordner ``<output_dir>/<id>`` mit ``ergebnis.json``.

    Erfüllt das Protocol ``MosaicRepository``. Die Pipeline schreibt direkt hierher,
    ``store`` hat deshalb nichts zu tun.

    Args:
        output_dir: Ordner mit allen Mosaiken.
    """

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def exists(self, mosaic_id: str) -> bool:
        return (
            bool(VALID_ID.match(mosaic_id))
            and (self.output_dir / mosaic_id / RESULT_FILE).is_file()
        )

    def read_bytes(self, mosaic_id: str, file_name: str) -> bytes:
        return self._path(mosaic_id, file_name).read_bytes()

    def open_file(self, mosaic_id: str, file_name: str) -> StoredFile:
        path = self._path(mosaic_id, file_name)
        return StoredFile(file_name, path=path, size=path.stat().st_size)

    @contextmanager
    def new_output_dir(self) -> Iterator[Path]:
        yield self.output_dir

    def store(self, target_dir: Path) -> None:
        """Nichts zu tun: Die Pipeline hat schon in ``output_dir`` geschrieben."""

    @contextmanager
    def workspace(self, mosaic_id: str, *, write: bool = False) -> Iterator[Path]:
        yield self._directory(mosaic_id)

    def _directory(self, mosaic_id: str) -> Path:
        """Ordner eines Mosaiks; unbekannte oder unsichere IDs ergeben ``NotFoundError``."""
        if not self.exists(mosaic_id):
            raise NotFoundError(f"Mosaik „{mosaic_id}“ gibt es nicht.")
        return self.output_dir / mosaic_id

    def _path(self, mosaic_id: str, file_name: str) -> Path:
        path = self._directory(mosaic_id) / check_file_name(file_name)
        if not path.is_file():
            raise NotFoundError(f"Datei „{file_name}“ gibt es nicht.")
        return path
