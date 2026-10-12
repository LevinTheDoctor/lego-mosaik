"""Schnittstellen für Datenquellen des Teilekatalogs und deren Aktualisierung."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from lego_mosaik.domain.errors import DownloadError
from lego_mosaik.domain.models import PartCatalog


class PartCatalogSource(Protocol):
    """Liefert den Teilekatalog.

    ``typing.Protocol`` beschreibt eine Schnittstelle strukturell ("Duck Typing mit
    Typprüfung"): Jede Klasse mit einer passenden ``load_catalog``-Methode erfüllt das
    Protocol, ohne davon erben zu müssen. Später kann so eine PostgreSQL-Quelle die
    CSV-Quelle ersetzen, ohne dass die Pipeline sich ändert.
    """

    def load_catalog(self) -> PartCatalog:
        """Lädt den Katalog (Farben, Teiltypen, verfügbare Kombinationen)."""
        ...

    def invalidate(self) -> None:
        """Verwirft einen gecachten Katalog, z. B. nach einer Aktualisierung."""
        ...


class FileStatus(StrEnum):
    """Ergebnis der Aktualisierung einer Datei."""

    DOWNLOADED = "heruntergeladen"
    UP_TO_DATE = "aktuell"
    FAILED = "fehlgeschlagen"


@dataclass(frozen=True, slots=True)
class FileUpdate:
    """Status einer einzelnen Datei nach der Aktualisierung.

    Attributes:
        file_name: Dateiname, z. B. ``colors.csv.gz``.
        status: Was passiert ist.
        message: Deutsche Erklärung (bei Fehlern der Grund).
        usable: True, wenn danach eine (ggf. alte) Datei vorhanden ist.
    """

    file_name: str
    status: FileStatus
    message: str
    usable: bool


@dataclass(frozen=True, slots=True)
class UpdateReport:
    """Gesamtergebnis einer Aktualisierung."""

    files: tuple[FileUpdate, ...]

    @property
    def downloaded_any(self) -> bool:
        """True, wenn mindestens eine Datei neu geladen wurde."""
        return any(update.status is FileStatus.DOWNLOADED for update in self.files)

    @property
    def failed(self) -> tuple[FileUpdate, ...]:
        """Alle fehlgeschlagenen Dateien."""
        return tuple(update for update in self.files if update.status is FileStatus.FAILED)

    def raise_if_unusable(self) -> None:
        """Bricht ab, wenn eine Datei weder neu geladen noch alt vorhanden ist.

        Raises:
            DownloadError: Mit den Gründen aller unbrauchbaren Dateien.
        """
        unusable = [update for update in self.files if not update.usable]
        if unusable:
            reasons = "; ".join(f"{update.file_name}: {update.message}" for update in unusable)
            raise DownloadError(f"Teilekatalog nicht verfügbar. {reasons}")


@dataclass(frozen=True, slots=True)
class FileInfo:
    """Stand einer lokalen Katalogdatei.

    Attributes:
        file_name: Dateiname.
        modified: Letzte Änderung (UTC) oder ``None``, wenn die Datei fehlt.
        outdated: True, wenn die Datei fehlt oder älter als erlaubt ist.
    """

    file_name: str
    modified: datetime | None
    outdated: bool


class CatalogUpdaterProtocol(Protocol):
    """Aktualisiert die lokalen Katalogdateien."""

    def update(self, *, force: bool = False) -> UpdateReport:
        """Lädt fehlende oder veraltete Dateien (bzw. alle mit ``force``) herunter."""
        ...

    def status(self) -> tuple[FileInfo, ...]:
        """Stand der lokalen Dateien."""
        ...
