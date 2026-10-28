"""Fertige Mosaike auf einem Volume (z. B. iSCSI-PVC) mit Metadaten in PostgreSQL.

Jedes Mosaik ist ein Ordner ``<root>/<id>``. Die Tabelle ``mosaike`` hält ``ergebnis.json``
und die Liste aller Dateien; eine Zeile entsteht erst, wenn der Ordner vollständig an seinem
Platz liegt. Neue Mosaike entstehen in ``<root>/.tmp`` und werden per ``rename`` übernommen
(gleiches Dateisystem, also atomar). Damit gibt es nie ein halbes Mosaik.

Das Volume ist ``ReadWriteOnce``: Alle Pods, die es nutzen, laufen auf einem Knoten.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from lego_mosaik.domain.errors import NotFoundError
from lego_mosaik.outputs.result_json import FILE_NAME as RESULT_FILE
from lego_mosaik.storage.mosaics import VALID_ID, check_file_name
from lego_mosaik.storage.protocol import StoredFile
from sqlalchemy import Engine, insert, select, update

from lego_mosaik_cloud.db.schema import mosaics

__all__ = ["VolumeMosaicRepository"]

logger = logging.getLogger(__name__)

TMP_DIR = ".tmp"  # beginnt mit Punkt: nie eine gültige Mosaik-ID


class VolumeMosaicRepository:
    """Erfüllt ``lego_mosaik.storage.protocol.MosaicRepository``.

    Args:
        engine: Datenbankverbindung (Tabelle ``mosaike``).
        root: Ordner auf dem Volume, z. B. ``/var/lib/lego-mosaik``.
    """

    def __init__(self, engine: Engine, root: Path) -> None:
        self._engine = engine
        self.root = root

    # --- Lesen ---------------------------------------------------------------------

    def exists(self, mosaic_id: str) -> bool:
        if not VALID_ID.match(mosaic_id):
            return False
        with self._engine.connect() as connection:
            query = select(mosaics.c.id).where(mosaics.c.id == mosaic_id)
            return connection.execute(query).first() is not None

    def read_bytes(self, mosaic_id: str, file_name: str) -> bytes:
        files, result = self._row(mosaic_id)
        if file_name == RESULT_FILE:
            return _json_bytes(result)  # aus der Datenbank, spart den Weg zur Platte
        return self._path(mosaic_id, self._known(files, file_name)).read_bytes()

    def open_file(self, mosaic_id: str, file_name: str) -> StoredFile:
        files, _ = self._row(mosaic_id)
        path = self._path(mosaic_id, self._known(files, file_name))
        return StoredFile(file_name, path=path, size=path.stat().st_size)

    # --- Schreiben ------------------------------------------------------------------

    @contextmanager
    def new_output_dir(self) -> Iterator[Path]:
        with self._temp_dir() as directory:
            yield directory

    def store(self, target_dir: Path) -> None:
        """Verschiebt den Ordner nach ``<root>/<id>`` und legt danach die Zeile an."""
        mosaic_id = target_dir.name
        paths = sorted(path for path in target_dir.iterdir() if path.is_file())
        result = json.loads((target_dir / RESULT_FILE).read_text(encoding="utf-8"))
        row = {
            "id": mosaic_id,
            "titel": str(result.get("titel", mosaic_id)),
            "basis": result.get("basis"),
            "erstellt": datetime.now(UTC),
            "ergebnis": result,
            "dateien": [path.name for path in paths],
            "bytes": sum(path.stat().st_size for path in paths),
        }
        directory = self.root / mosaic_id
        if directory.exists():
            raise FileExistsError(f"Mosaik-Ordner {directory} gibt es schon.")
        # Die API liest nach store() noch aus target_dir: nicht verschieben, sondern verlinken
        # (gleiches Dateisystem) bzw. kopieren (z. B. „uebernehmen“ aus ausgabe/)
        on_volume = target_dir.resolve().is_relative_to((self.root / TMP_DIR).resolve())
        copy = os.link if on_volume else shutil.copy2
        with self._temp_dir() as staging:
            shutil.copytree(target_dir, staging / mosaic_id, copy_function=copy)
            (staging / mosaic_id).rename(directory)
        try:
            with self._engine.begin() as connection:
                connection.execute(insert(mosaics).values(row))
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        logger.info("Mosaik %s gespeichert (%d Dateien).", mosaic_id, len(paths))

    @contextmanager
    def workspace(self, mosaic_id: str, *, write: bool = False) -> Iterator[Path]:
        """Kopiert die Dateien in einen Temp-Ordner ``<root>/.tmp/…/<id>``.

        Der Ordnername muss die ID sein: ``MosaicPipeline.edit`` nimmt ihn als ``basis``.
        Mit ``write=True`` werden danach neue und geänderte Dateien übernommen.
        """
        files, _ = self._row(mosaic_id)
        with self._temp_dir() as root:
            directory = root / mosaic_id
            directory.mkdir()
            for name in files:
                shutil.copyfile(self._path(mosaic_id, name), directory / name)
            before = _digests(directory)
            yield directory
            if write:
                self._write_back(mosaic_id, directory, before)

    def _write_back(self, mosaic_id: str, directory: Path, before: dict[str, str]) -> None:
        after = _digests(directory)
        changed = [name for name, digest in after.items() if before.get(name) != digest]
        if not changed:
            return
        target = self.root / mosaic_id
        for name in changed:
            os.replace(directory / name, target / name)  # je Datei atomar
        result = json.loads((target / RESULT_FILE).read_text(encoding="utf-8"))
        size = sum((target / name).stat().st_size for name in after)
        values = {"ergebnis": result, "dateien": sorted(after), "bytes": size}
        with self._engine.begin() as connection:
            connection.execute(update(mosaics).where(mosaics.c.id == mosaic_id).values(values))
        logger.info("Mosaik %s aktualisiert: %s.", mosaic_id, ", ".join(changed))

    # --- Hilfen ---------------------------------------------------------------------

    @contextmanager
    def _temp_dir(self) -> Iterator[Path]:
        """Temp-Ordner auf dem Volume, damit ``rename`` und ``os.replace`` atomar sind."""
        parent = self.root / TMP_DIR
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="mosaik-", dir=parent) as directory:
            yield Path(directory)

    def _row(self, mosaic_id: str) -> tuple[list[str], dict[str, object]]:
        """Dateiliste und ``ergebnis.json`` (``NotFoundError`` bei unbekannter ID)."""
        if VALID_ID.match(mosaic_id):
            query = select(mosaics.c.dateien, mosaics.c.ergebnis).where(mosaics.c.id == mosaic_id)
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
            if row is not None:
                return list(row.dateien), dict(row.ergebnis)
        raise NotFoundError(f"Mosaik „{mosaic_id}“ gibt es nicht.")

    @staticmethod
    def _known(files: list[str], file_name: str) -> str:
        if check_file_name(file_name) not in files:
            raise NotFoundError(f"Datei „{file_name}“ gibt es nicht.")
        return file_name

    def _path(self, mosaic_id: str, file_name: str) -> Path:
        path = self.root / mosaic_id / file_name
        if not path.is_file():
            raise NotFoundError(f"Datei „{file_name}“ fehlt im Speicher.")
        return path


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _digests(directory: Path) -> dict[str, str]:
    """SHA-256 jeder Datei, um geänderte Dateien zu erkennen."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.iterdir()
        if path.is_file()
    }
