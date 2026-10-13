"""Eigene Farbpaletten in ``daten/paletten.json``."""

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from lego_mosaik.domain.errors import ConfigError, NotFoundError
from lego_mosaik.domain.library import ColorPalette, check_description, check_name, slug
from lego_mosaik.storage.json_file import JsonFile


class PaletteStore:
    """Anlegen, lesen, ändern und löschen eigener Paletten.

    Die Kennung (``id``) entsteht beim Anlegen aus dem Namen und bleibt danach gleich,
    auch wenn die Palette umbenannt wird.

    Args:
        path: JSON-Datei der Paletten.
    """

    def __init__(self, path: Path) -> None:
        self._file = JsonFile(path, "paletten")

    def list(self) -> tuple[ColorPalette, ...]:
        """Alle Paletten, nach Name sortiert."""
        palettes = (_from_json(item) for item in self._file.read())
        return tuple(sorted(palettes, key=lambda palette: palette.name.casefold()))

    def get(self, reference: str) -> ColorPalette:
        """Palette zu Kennung oder Name.

        Raises:
            NotFoundError: Wenn es sie nicht gibt.
        """
        wanted = slug(reference)
        for palette in self.list():
            if wanted in (palette.id, slug(palette.name)):
                return palette
        raise NotFoundError(f"Die Palette „{reference}“ gibt es nicht.")

    def create(self, name: str, description: str, color_ids: Sequence[int]) -> ColorPalette:
        """Legt eine Palette an.

        Raises:
            ConfigError: Bei ungültigen Angaben oder wenn der Name schon vergeben ist.
        """
        palette = _checked(slug(name), name, description, color_ids)
        with self._file.transaction() as items:
            _require_free(items, palette, None)
            items.append(_to_json(palette))
        return palette

    def update(
        self, reference: str, name: str, description: str, color_ids: Sequence[int]
    ) -> ColorPalette:
        """Ersetzt Name, Beschreibung und Farben einer Palette.

        Raises:
            NotFoundError: Wenn es sie nicht gibt.
            ConfigError: Bei ungültigen Angaben oder wenn der neue Name vergeben ist.
        """
        palette_id = self.get(reference).id
        palette = _checked(palette_id, name, description, color_ids)
        with self._file.transaction() as items:
            _require_free(items, palette, palette_id)
            items[:] = [
                _to_json(palette) if item.get("id") == palette_id else item for item in items
            ]
        return palette

    def delete(self, reference: str) -> None:
        """Löscht eine Palette.

        Raises:
            NotFoundError: Wenn es sie nicht gibt.
        """
        palette_id = self.get(reference).id
        with self._file.transaction() as items:
            items[:] = [item for item in items if item.get("id") != palette_id]


def _checked(
    palette_id: str, name: str, description: str, color_ids: Iterable[int]
) -> ColorPalette:
    ids = tuple(dict.fromkeys(color_ids))  # Doppelte entfernen, Reihenfolge behalten
    if not ids:
        raise ConfigError("Eine Palette braucht mindestens eine Farbe.")
    return ColorPalette(
        palette_id, check_name(name, "der Palette"), check_description(description), ids
    )


def _require_free(
    items: Sequence[Mapping[str, object]], palette: ColorPalette, own_id: str | None
) -> None:
    """Name und Kennung dürfen nur bei der Palette selbst vorkommen."""
    for item in items:
        if item.get("id") == own_id:
            continue
        if palette.id == item.get("id") or slug(palette.name) == slug(str(item.get("name", ""))):
            raise ConfigError(f"Eine Palette „{palette.name}“ gibt es schon.")


def _to_json(palette: ColorPalette) -> dict[str, object]:
    return {
        "id": palette.id,
        "name": palette.name,
        "beschreibung": palette.description,
        "farb_ids": list(palette.color_ids),
    }


def _from_json(item: Mapping[str, object]) -> ColorPalette:
    ids = item.get("farb_ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise ConfigError(f"Palette {item.get('name')!r} ist beschädigt (farb_ids).")
    return ColorPalette(
        str(item["id"]), str(item["name"]), str(item.get("beschreibung", "")), tuple(ids)
    )
