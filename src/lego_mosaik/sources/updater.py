"""Aktualisierung der Rebrickable-CSV-Dumps.

Heruntergeladen wird nur, wenn eine Datei fehlt, älter als ``max_age_days`` ist
oder die Aktualisierung erzwungen wird. Geschrieben wird atomar: erst in eine
temporäre Datei, dann umbenennen. Ein abgebrochener Download hinterlässt so nie
eine kaputte CSV, und die alte Datei bleibt nutzbar.
"""

import gzip
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

lazy import httpx

from lego_mosaik.sources.protocol import FileInfo, FileStatus, FileUpdate, UpdateReport

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 24 * 60 * 60
TEMP_SUFFIX = ".tmp"
# Größe der Blöcke beim Prüfen der gzip-Datei (1 MiB)
READ_CHUNK_BYTES = 1024 * 1024


class CatalogUpdater:
    """Lädt die Rebrickable-Dateien herunter, wenn nötig.

    Args:
        data_dir: Zielordner der Dateien.
        base_url: URL-Präfix, an das der Dateiname angehängt wird.
        file_names: Herunterzuladende Dateien, z. B. ``("colors.csv.gz", …)``.
        optional_file_names: Weitere Dateien, ohne die der Katalog trotzdem funktioniert;
            schlägt ihr Download fehl, bleibt der Katalog nutzbar.
        max_age_days: Ab diesem Alter gilt eine Datei als veraltet.
        timeout_seconds: Timeout pro HTTP-Anfrage.
        user_agent: Eigener User-Agent, damit Rebrickable den Client erkennt.
        transport: Optionaler httpx-Transport. Tests übergeben ``httpx.MockTransport``,
            damit kein echter Netzwerkzugriff stattfindet (Dependency Injection).
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        base_url: str,
        file_names: Sequence[str],
        max_age_days: int,
        timeout_seconds: float,
        user_agent: str,
        optional_file_names: Sequence[str] = (),
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._base_url = base_url
        self._file_names = (*file_names, *optional_file_names)
        self._optional = frozenset(optional_file_names)
        self._max_age_days = max_age_days
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._transport = transport

    def status(self) -> tuple[FileInfo, ...]:
        """Stand der lokalen Dateien (Änderungszeit oder ``None``, wenn sie fehlen)."""
        return tuple(
            FileInfo(name, _modified(self._path(name)), self.needs_update(self._path(name)))
            for name in self._file_names
        )

    def needs_update(self, path: Path) -> bool:
        """True, wenn die Datei fehlt oder älter als ``max_age_days`` ist."""
        if not path.exists():
            return True
        age_seconds = time.time() - path.stat().st_mtime  # st_mtime = letzte Änderung
        return age_seconds > self._max_age_days * SECONDS_PER_DAY

    def update(self, *, force: bool = False) -> UpdateReport:
        """Prüft alle Dateien und lädt veraltete oder fehlende herunter.

        Args:
            force: Alle Dateien unabhängig vom Alter neu laden.
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        pending = [
            name for name in self._file_names if force or self.needs_update(self._path(name))
        ]
        results = {name: self._up_to_date(name) for name in self._file_names}
        if pending:
            # "with" schließt den Client am Ende sicher, auch bei Ausnahmen (Context Manager)
            with self._create_client() as client:
                for name in pending:
                    results[name] = self._download(client, name)
        return UpdateReport(files=tuple(results[name] for name in self._file_names))

    def _path(self, file_name: str) -> Path:
        return self._data_dir / file_name

    def _up_to_date(self, file_name: str) -> FileUpdate:
        return FileUpdate(file_name, FileStatus.UP_TO_DATE, "Datei ist aktuell.", usable=True)

    def _create_client(self) -> httpx.Client:
        """Erzeugt den HTTP-Client mit Timeout und eigenem User-Agent."""
        return httpx.Client(
            timeout=self._timeout_seconds,
            headers={"User-Agent": self._user_agent},
            follow_redirects=True,
            transport=self._transport,
        )

    def _download(self, client: httpx.Client, file_name: str) -> FileUpdate:
        """Lädt eine Datei atomar herunter. Fehler werden zu einem ``FAILED``-Status."""
        target = self._path(file_name)
        temp = target.with_name(target.name + TEMP_SUFFIX)
        url = self._base_url + file_name
        logger.info("Lade %s herunter …", url)
        try:
            _stream_to_file(client, url, temp)
            _verify_gzip(temp)
            # replace() ist auf demselben Dateisystem atomar: Andere sehen entweder
            # die alte oder die neue Datei, nie eine halb geschriebene
            temp.replace(target)
        except (httpx.HTTPError, OSError, EOFError) as error:
            temp.unlink(missing_ok=True)
            message = _describe_error(error)
            logger.warning("Download von %s fehlgeschlagen: %s", file_name, message)
            usable = target.exists() or file_name in self._optional
            return FileUpdate(file_name, FileStatus.FAILED, message, usable=usable)
        return FileUpdate(file_name, FileStatus.DOWNLOADED, "Neu heruntergeladen.", usable=True)


def _modified(path: Path) -> datetime | None:
    """Änderungszeit einer Datei in UTC, ``None`` wenn sie fehlt."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _stream_to_file(client: httpx.Client, url: str, path: Path) -> None:
    """Schreibt die Antwort stückweise in eine Datei, ohne sie ganz im Speicher zu halten."""
    with client.stream("GET", url) as response:
        response.raise_for_status()  # wirft httpx.HTTPStatusError bei 4xx/5xx
        with path.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)


def _verify_gzip(path: Path) -> None:
    """Liest die gzip-Datei einmal komplett, um abgeschnittene Downloads zu erkennen.

    Raises:
        EOFError: Wenn die Datei vorzeitig endet.
        gzip.BadGzipFile: Wenn sie kein gzip ist (Unterklasse von ``OSError``).
    """
    with gzip.open(path, "rb") as compressed:
        # Die Schleife läuft, solange read() noch Daten liefert; b"" am Dateiende
        # ist "falsy" und beendet sie. Die Daten selbst werden verworfen.
        while compressed.read(READ_CHUNK_BYTES):
            pass


def _describe_error(error: Exception) -> str:
    """Übersetzt technische Fehler in eine verständliche deutsche Meldung."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"Server antwortete mit Status {error.response.status_code}."
    if isinstance(error, httpx.TimeoutException):
        return "Zeitüberschreitung beim Herunterladen."
    if isinstance(error, httpx.HTTPError):
        return f"Netzwerkfehler ({type(error).__name__}). Bitte Internetverbindung prüfen."
    if isinstance(error, (EOFError, gzip.BadGzipFile)):
        return "Die heruntergeladene Datei ist unvollständig oder beschädigt."
    return f"Datei konnte nicht geschrieben werden ({error})."
