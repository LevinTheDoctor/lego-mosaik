"""Presets mit Bewertung und Favoriten in ``daten/presets.json``."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from lego_mosaik.domain.errors import ConfigError, NotFoundError
from lego_mosaik.domain.library import (
    Preset,
    PresetOrder,
    SettingValue,
    check_description,
    check_name,
    check_rating,
    slug,
    sort_presets,
)
from lego_mosaik.storage.json_file import JsonFile


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PresetStore:
    """Anlegen, lesen, bewerten, als Favorit markieren und löschen von Presets.

    Die Einstellungen prüft der Aufrufer (``storage.library.Library``), weil nur er die
    Formularfelder und die Pipeline kennt.

    Args:
        path: JSON-Datei der Presets.
        now: Uhr für ``erstellt``/``geaendert`` (in Tests austauschbar).
    """

    def __init__(self, path: Path, now: Callable[[], datetime] = _utc_now) -> None:
        self._file = JsonFile(path, "presets")
        self._now = now

    def list(
        self, order: PresetOrder = PresetOrder.RATING, *, favorites_only: bool = False
    ) -> tuple[Preset, ...]:
        """Alle Presets: Favoriten zuerst, dann nach ``order``."""
        presets = (_from_json(item) for item in self._file.read())
        return sort_presets(presets, order, favorites_only=favorites_only)

    def get(self, reference: str) -> Preset:
        """Preset zu Kennung oder Name.

        Raises:
            NotFoundError: Wenn es das Preset nicht gibt.
        """
        wanted = slug(reference)
        for preset in self.list(PresetOrder.NAME):
            if wanted in (preset.id, slug(preset.name)):
                return preset
        raise NotFoundError(f"Das Preset „{reference}“ gibt es nicht.")

    def create(
        self,
        name: str,
        description: str,
        settings: Mapping[str, SettingValue],
        *,
        favorite: bool = False,
        rating: int = 0,
    ) -> Preset:
        """Legt ein Preset an.

        Raises:
            ConfigError: Bei ungültigen Angaben oder wenn der Name schon vergeben ist.
        """
        now = self._now()
        preset = Preset(
            slug(name),
            check_name(name, "des Presets"),
            check_description(description),
            dict(settings),
            created=now,
            changed=now,
            favorite=favorite,
            rating=check_rating(rating),
        )
        with self._file.transaction() as items:
            _require_free(items, preset, None)
            items.append(_to_json(preset))
        return preset

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
        """Ersetzt ein Preset; Kennung, Nutzungen und Anlagedatum bleiben.

        Raises:
            NotFoundError: Wenn es das Preset nicht gibt.
            ConfigError: Bei ungültigen Angaben oder wenn der neue Name vergeben ist.
        """
        old = self.get(reference)
        preset = replace(
            old,
            name=check_name(name, "des Presets"),
            description=check_description(description),
            settings=dict(settings),
            favorite=favorite,
            rating=check_rating(rating),
            changed=self._now(),
        )
        return self._replace(preset)

    def mark(
        self, reference: str, *, favorite: bool | None = None, rating: int | None = None
    ) -> Preset:
        """Setzt nur Favorit und/oder Bewertung (``None`` = unverändert)."""
        old = self.get(reference)
        preset = replace(
            old,
            favorite=old.favorite if favorite is None else favorite,
            rating=old.rating if rating is None else check_rating(rating),
            changed=self._now(),
        )
        return self._replace(preset)

    def record_use(self, reference: str) -> Preset:
        """Zählt eine Benutzung (für die Sortierung nach Nutzung)."""
        old = self.get(reference)
        return self._replace(replace(old, uses=old.uses + 1))

    def delete(self, reference: str) -> None:
        """Löscht ein Preset.

        Raises:
            NotFoundError: Wenn es das Preset nicht gibt.
        """
        preset_id = self.get(reference).id
        with self._file.transaction() as items:
            items[:] = [item for item in items if item.get("id") != preset_id]

    def _replace(self, preset: Preset) -> Preset:
        with self._file.transaction() as items:
            _require_free(items, preset, preset.id)
            items[:] = [_to_json(preset) if item.get("id") == preset.id else item for item in items]
        return preset


def _require_free(
    items: Sequence[Mapping[str, object]], preset: Preset, own_id: str | None
) -> None:
    for item in items:
        if item.get("id") == own_id:
            continue
        if preset.id == item.get("id") or slug(preset.name) == slug(str(item.get("name", ""))):
            raise ConfigError(f"Ein Preset „{preset.name}“ gibt es schon.")


def _to_json(preset: Preset) -> dict[str, object]:
    return {
        "id": preset.id,
        "name": preset.name,
        "beschreibung": preset.description,
        "einstellungen": dict(preset.settings),
        "favorit": preset.favorite,
        "bewertung": preset.rating,
        "nutzungen": preset.uses,
        "erstellt": preset.created.isoformat(),
        "geaendert": preset.changed.isoformat(),
    }


def _from_json(item: Mapping[str, object]) -> Preset:
    try:
        settings = item["einstellungen"]
        if not isinstance(settings, dict):
            raise TypeError("einstellungen ist kein Objekt")
        return Preset(
            str(item["id"]),
            str(item["name"]),
            str(item.get("beschreibung", "")),
            settings,
            created=datetime.fromisoformat(str(item["erstellt"])),
            changed=datetime.fromisoformat(str(item["geaendert"])),
            favorite=bool(item.get("favorit", False)),
            rating=_int(item.get("bewertung", 0)),
            uses=_int(item.get("nutzungen", 0)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"Preset {item.get('name')!r} ist beschädigt ({error}).") from None


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Ganzzahl erwartet, nicht {value!r}")
    return value
