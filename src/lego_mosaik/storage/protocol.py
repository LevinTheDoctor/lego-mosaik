"""Schnittstellen der Speicher: Paletten, Presets und fertige Mosaike.

Lokal liegen Paletten und Presets in JSON-Dateien und jedes Mosaik in einem Ordner unter
``ausgabe/``. Die Cloud-Variante (Paket ``lego_mosaik_cloud``) erfüllt dieselben Protocols
mit PostgreSQL und S3; API und Bibliothek kennen nur diese Schnittstellen.
"""

from collections.abc import Iterable, Mapping, MutableSequence, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from lego_mosaik.domain.library import ColorPalette, Preset, PresetOrder, SettingValue


class ItemTable(Protocol):
    """Liste von JSON-Einträgen, auf der Paletten- und Preset-Speicher aufbauen.

    Lokal eine JSON-Datei (``storage/json_file.py``), in der Cloud eine PostgreSQL-Tabelle.
    """

    def read(self) -> list[dict[str, object]]:
        """Alle Einträge."""
        ...

    def transaction(self) -> AbstractContextManager[MutableSequence[dict[str, object]]]:
        """Einträge lesen, im ``with``-Block ändern und danach gesammelt speichern.

        Wirft der Block eine Exception, bleibt alles unverändert.
        """
        ...


class PaletteRepository(Protocol):
    """Eigene Farbpaletten (siehe ``storage/palettes.py``)."""

    def list(self) -> tuple[ColorPalette, ...]:
        """Alle Paletten, nach Name sortiert."""
        ...

    def get(self, reference: str) -> ColorPalette:
        """Palette zu Kennung oder Name (``NotFoundError``, wenn es sie nicht gibt)."""
        ...

    def create(self, name: str, description: str, color_ids: Sequence[int]) -> ColorPalette:
        """Legt eine Palette an."""
        ...

    def update(
        self, reference: str, name: str, description: str, color_ids: Sequence[int]
    ) -> ColorPalette:
        """Ersetzt Name, Beschreibung und Farben."""
        ...

    def delete(self, reference: str) -> None:
        """Löscht eine Palette."""
        ...


class PresetRepository(Protocol):
    """Presets mit Bewertung und Favoriten (siehe ``storage/presets.py``)."""

    def list(
        self, order: PresetOrder = PresetOrder.RATING, *, favorites_only: bool = False
    ) -> tuple[Preset, ...]:
        """Alle Presets: Favoriten zuerst, dann nach ``order``."""
        ...

    def get(self, reference: str) -> Preset:
        """Preset zu Kennung oder Name (``NotFoundError``, wenn es das Preset nicht gibt)."""
        ...

    def create(
        self,
        name: str,
        description: str,
        settings: Mapping[str, SettingValue],
        *,
        favorite: bool = False,
        rating: int = 0,
    ) -> Preset:
        """Legt ein Preset an."""
        ...

    def update(
        self,
        reference: str,
        name: str,
        description: str,
        settings: Mapping[str, SettingValue],
        *,
        favorite: bool,
        rating: int,
    ) -> Preset:
        """Ersetzt ein Preset; Kennung, Nutzungen und Anlagedatum bleiben."""
        ...

    def mark(
        self, reference: str, *, favorite: bool | None = None, rating: int | None = None
    ) -> Preset:
        """Setzt nur Favorit und/oder Bewertung."""
        ...

    def record_use(self, reference: str) -> Preset:
        """Zählt eine Benutzung."""
        ...

    def delete(self, reference: str) -> None:
        """Löscht ein Preset."""
        ...


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Eine Datei eines Mosaiks zum Ausliefern.

    Attributes:
        name: Dateiname, z. B. ``vorschau.png``.
        path: Lokaler Pfad, falls die Datei auf der Platte liegt (dann ``chunks`` leer).
        chunks: Inhalt in Stücken, falls die Datei gestreamt wird (z. B. aus S3).
        size: Größe in Bytes, falls bekannt.
    """

    name: str
    path: Path | None = None
    chunks: Iterable[bytes] = ()
    size: int | None = None


class MosaicRepository(Protocol):
    """Fertige Mosaike: Dateien und ``ergebnis.json`` je Mosaik-ID.

    Die Pipeline schreibt immer in einen lokalen Ordner. ``new_output_dir`` liefert ihn,
    ``store`` übernimmt das fertige Mosaik dauerhaft (lokal: liegt schon richtig).
    Für Schritte, die Dateien eines Mosaiks lesen oder ergänzen (bearbeiten, Preise),
    stellt ``workspace`` einen lokalen Ordner bereit.
    """

    def exists(self, mosaic_id: str) -> bool:
        """True, wenn es das Mosaik gibt."""
        ...

    def read_bytes(self, mosaic_id: str, file_name: str) -> bytes:
        """Inhalt einer Datei (``NotFoundError``, wenn Mosaik oder Datei fehlen)."""
        ...

    def open_file(self, mosaic_id: str, file_name: str) -> StoredFile:
        """Datei zum Ausliefern (``NotFoundError``, wenn Mosaik oder Datei fehlen)."""
        ...

    def new_output_dir(self) -> AbstractContextManager[Path]:
        """Ordner, in dem die Pipeline neue Mosaik-Ordner anlegt."""
        ...

    def store(self, target_dir: Path) -> None:
        """Übernimmt ein fertiges Mosaik (Ordnername = ID) dauerhaft."""
        ...

    def workspace(self, mosaic_id: str, *, write: bool = False) -> AbstractContextManager[Path]:
        """Lokaler Ordner mit den Dateien des Mosaiks.

        Mit ``write=True`` werden geänderte und neue Dateien danach übernommen.
        """
        ...
