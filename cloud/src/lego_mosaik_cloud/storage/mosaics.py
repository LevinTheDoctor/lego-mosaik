"""Fertige Mosaike in S3 (Garage) mit Metadaten in PostgreSQL.

Jede Datei eines Mosaiks liegt als Objekt ``<präfix>/<id>/<datei>`` im Bucket. Die Tabelle
``mosaike`` hält ``ergebnis.json`` und die Liste aller Objekte; eine Zeile entsteht erst,
wenn alle Dateien hochgeladen sind. Damit gibt es nie ein halbes Mosaik.
"""

import hashlib
import json
import logging
import mimetypes
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError
from lego_mosaik.domain.errors import NotFoundError
from lego_mosaik.outputs.result_json import FILE_NAME as RESULT_FILE
from lego_mosaik.storage.mosaics import VALID_ID, check_file_name
from lego_mosaik.storage.protocol import StoredFile
from sqlalchemy import Engine, insert, select, update

from lego_mosaik_cloud.db.schema import mosaics

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
else:
    S3Client = Any

__all__ = ["S3Client", "S3MosaicRepository"]

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class S3MosaicRepository:
    """Erfüllt ``lego_mosaik.storage.protocol.MosaicRepository``.

    Args:
        engine: Datenbankverbindung (Tabelle ``mosaike``).
        s3: boto3-S3-Client (für Garage mit Path-Style-Adressen).
        bucket: Bucket der Mosaike.
        prefix: Schlüssel-Präfix, z. B. ``mosaike``.
    """

    def __init__(self, engine: Engine, s3: S3Client, bucket: str, prefix: str) -> None:
        self._engine = engine
        self._s3 = s3
        self._bucket = bucket
        self._prefix = prefix.strip("/")

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
            return _json_bytes(result)  # aus der Datenbank, spart den Weg zu S3
        data: bytes = self._get(self._key(mosaic_id, self._known(files, file_name))).read()
        return data

    def open_file(self, mosaic_id: str, file_name: str) -> StoredFile:
        files, _ = self._row(mosaic_id)
        key = self._key(mosaic_id, self._known(files, file_name))
        response = self._get_object(key)
        body = response["Body"]
        return StoredFile(
            file_name, chunks=body.iter_chunks(CHUNK_BYTES), size=response["ContentLength"]
        )

    # --- Schreiben ------------------------------------------------------------------

    @contextmanager
    def new_output_dir(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="mosaik-") as directory:
            yield Path(directory)

    def store(self, target_dir: Path) -> None:
        """Lädt alle Dateien hoch und legt danach die Zeile in ``mosaike`` an."""
        mosaic_id = target_dir.name
        paths = sorted(path for path in target_dir.iterdir() if path.is_file())
        for path in paths:
            self._upload(mosaic_id, path)
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
        with self._engine.begin() as connection:
            connection.execute(insert(mosaics).values(row))
        logger.info("Mosaik %s gespeichert (%d Dateien).", mosaic_id, len(paths))

    @contextmanager
    def workspace(self, mosaic_id: str, *, write: bool = False) -> Iterator[Path]:
        """Lädt die Dateien in einen Temp-Ordner ``<tmp>/<id>``.

        Der Ordnername muss die ID sein: ``MosaicPipeline.edit`` nimmt ihn als ``basis``.
        Mit ``write=True`` werden danach neue und geänderte Dateien hochgeladen.
        """
        files, _ = self._row(mosaic_id)
        with tempfile.TemporaryDirectory(prefix="mosaik-") as root:
            directory = Path(root) / mosaic_id
            directory.mkdir()
            for name in files:
                with self._get(self._key(mosaic_id, name)) as body:
                    (directory / name).write_bytes(body.read())
            before = _digests(directory)
            yield directory
            if write:
                self._write_back(mosaic_id, directory, before)

    def _write_back(self, mosaic_id: str, directory: Path, before: dict[str, str]) -> None:
        after = _digests(directory)
        changed = [name for name, digest in after.items() if before.get(name) != digest]
        for name in changed:
            self._upload(mosaic_id, directory / name)
        if not changed:
            return
        result = json.loads((directory / RESULT_FILE).read_text(encoding="utf-8"))
        size = sum((directory / name).stat().st_size for name in after)
        values = {"ergebnis": result, "dateien": sorted(after), "bytes": size}
        with self._engine.begin() as connection:
            connection.execute(update(mosaics).where(mosaics.c.id == mosaic_id).values(values))
        logger.info("Mosaik %s aktualisiert: %s.", mosaic_id, ", ".join(changed))

    # --- Hilfen ---------------------------------------------------------------------

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

    def _key(self, mosaic_id: str, file_name: str) -> str:
        return f"{self._prefix}/{mosaic_id}/{file_name}"

    def _upload(self, mosaic_id: str, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._s3.upload_file(
            str(path),
            self._bucket,
            self._key(mosaic_id, path.name),
            ExtraArgs={"ContentType": content_type},
        )

    def _get_object(self, key: str) -> Any:
        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if str(error.response.get("Error", {}).get("Code")) in MISSING_CODES:
                raise NotFoundError(
                    f"Datei „{key.rsplit('/', 1)[-1]}“ fehlt im Speicher."
                ) from None
            raise

    def _get(self, key: str) -> Any:
        return self._get_object(key)["Body"]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _digests(directory: Path) -> dict[str, str]:
    """SHA-256 jeder Datei, um geänderte Dateien zu erkennen."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.iterdir()
        if path.is_file()
    }
