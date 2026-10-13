"""Tests für eigene Paletten, Presets (Bewertung, Favoriten) und die Bibliothek."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lego_mosaik.config import MosaicOverrides
from lego_mosaik.domain.errors import ConfigError, NotFoundError
from lego_mosaik.domain.library import PresetOrder, slug
from lego_mosaik.storage.library import Library
from lego_mosaik.storage.palettes import PaletteStore
from lego_mosaik.storage.presets import PresetStore


class _Clock:
    """Uhr, die bei jedem Aufruf eine Minute weiterläuft."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library(
        PaletteStore(tmp_path / "paletten.json"), PresetStore(tmp_path / "presets.json", _Clock())
    )


def test_slug() -> None:
    assert slug("  Pastell Töne!  ") == "pastell-toene"


def test_palettes_create_get_update_delete(library: Library) -> None:
    palettes = library.palettes
    created = palettes.create("Graustufen", "Schwarz bis Weiß", [0, 15, 0, 71])
    assert created.id == "graustufen" and created.color_ids == (0, 15, 71)
    assert palettes.get("GRAUSTUFEN") == created  # Name oder ID, Groß/klein egal
    with pytest.raises(ConfigError, match="gibt es schon"):
        palettes.create("graustufen", "", [0])
    updated = palettes.update("graustufen", "Grau", "", [71, 72])
    assert updated.id == "graustufen" and updated.name == "Grau"
    assert [p.name for p in palettes.list()] == ["Grau"]
    palettes.delete("grau")
    with pytest.raises(NotFoundError):
        palettes.get("grau")


def test_palette_needs_colors_and_a_name(library: Library) -> None:
    with pytest.raises(ConfigError, match="mindestens eine Farbe"):
        library.palettes.create("Leer", "", [])
    with pytest.raises(ConfigError, match="Buchstaben"):
        library.palettes.create("!!!", "", [0])


def test_presets_sort_favorites_first_then_by_order(library: Library) -> None:
    presets = library.presets
    presets.create("Alpha", "", {"stil": "punkte"}, rating=2)
    presets.create("Beta", "", {"stil": "formen"}, rating=5)
    presets.create("Gamma", "", {"stil": "quadrat"}, favorite=True)
    presets.record_use("alpha")
    presets.record_use("alpha")
    names = lambda order: [p.name for p in presets.list(order)]  # noqa: E731
    assert names(PresetOrder.RATING) == ["Gamma", "Beta", "Alpha"]
    assert names(PresetOrder.USES) == ["Gamma", "Alpha", "Beta"]
    assert names(PresetOrder.NAME) == ["Gamma", "Alpha", "Beta"]
    assert names(PresetOrder.NEWEST)[0] == "Gamma"
    assert [p.name for p in presets.list(favorites_only=True)] == ["Gamma"]
    assert presets.get("alpha").uses == 2


def test_preset_mark_and_update_keep_uses(library: Library) -> None:
    presets = library.presets
    created = presets.create("Comic", "", {"staerke": 0.0, "aufraeumen": True})
    presets.record_use("comic")
    marked = presets.mark("comic", favorite=True, rating=4)
    assert marked.favorite and marked.rating == 4 and marked.uses == 1
    assert marked.changed > created.changed
    updated = presets.update(
        "comic", "Comic 2", "neu", {"stil": "punkte"}, favorite=False, rating=1
    )
    assert updated.id == "comic" and updated.uses == 1 and updated.created == created.created
    with pytest.raises(ConfigError, match="Bewertung"):
        presets.mark("comic", rating=6)


def test_resolve_merges_preset_values_and_palette(library: Library) -> None:
    library.palettes.create("Kontrast", "", [0, 15])
    library.palettes.create("Bunt", "", [4, 1])
    library.presets.create("Basis", "", {"stil": "punkte", "kontrast": 1.2, "palette": "kontrast"})
    resolved = library.resolve({"kontrast": 1.5, "titel": None}, preset="basis")
    assert resolved.inputs.style == "punkte"  # aus dem Preset
    assert resolved.inputs.contrast == 1.5  # angegeben geht vor
    assert resolved.inputs.color_ids == "0,15"  # Palette des Presets
    assert resolved.preset is not None and resolved.preset.id == "basis"
    other = library.resolve({}, preset="basis", palette="bunt")
    assert other.inputs.color_ids == "4,1"
    direct = library.resolve({"farb_ids": "71"}, preset="basis")
    assert direct.inputs.color_ids == "71"
    with pytest.raises(ConfigError, match="entweder"):
        library.resolve({"farb_ids": "71"}, palette="bunt")
    with pytest.raises(NotFoundError):
        library.resolve({}, preset="fehlt")


def test_preset_settings_are_checked(library: Library) -> None:
    checked: list[MosaicOverrides] = []
    settings = library.preset_settings({"stil": "punkte", "drehen": 90}, checked.append)
    assert settings == {"stil": "punkte", "drehen": 90}
    assert checked and checked[0].rotate == 90
    with pytest.raises(ConfigError, match="gehören nicht"):
        library.preset_settings({"titel": "Katze"}, checked.append)
    with pytest.raises(ConfigError, match="Unbekannte Felder"):
        library.preset_settings({"farbe": "rot"}, checked.append)
    with pytest.raises(ConfigError, match="Ungültiger Wert"):
        library.preset_settings({"breite": "viel"}, checked.append)


def test_damaged_file_gives_a_german_error(tmp_path: Path) -> None:
    path = tmp_path / "paletten.json"
    path.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ConfigError, match="lässt sich nicht lesen"):
        PaletteStore(path).list()
