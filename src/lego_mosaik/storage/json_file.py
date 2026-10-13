"""Kleine JSON-Datei als Speicher: threadsicher und atomar geschrieben."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lego_mosaik.domain.errors import ConfigError

TEMP_SUFFIX = ".tmp"


class JsonFile:
    """Liste von Einträgen unter einem Schlüssel einer JSON-Datei.

    Die API bedient Anfragen parallel (Threadpool). ``transaction`` hält deshalb eine
    Sperre vom Lesen bis zum Schreiben, damit sich zwei Änderungen nicht überschreiben.
    Geschrieben wird über eine temporäre Datei, ein Abbruch hinterlässt nie eine halbe Datei.

    Args:
        path: Pfad der Datei (fehlt sie, gilt die Liste als leer).
        key: Schlüssel der Liste, z. B. „paletten“.
    """

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self._key = key
        self._lock = threading.Lock()

    def read(self) -> list[dict[str, object]]:
        """Alle Einträge (ohne Sperre; jede Änderung ersetzt die Datei atomar)."""
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data[self._key]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ConfigError(f"{self.path} lässt sich nicht lesen ({error}).") from None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ConfigError(f"{self.path} ist beschädigt: „{self._key}“ ist keine Liste.")
        return items

    @contextmanager
    def transaction(self) -> Iterator[list[dict[str, object]]]:
        """Liest die Einträge, lässt sie im ``with``-Block ändern und schreibt sie zurück.

        Wirft der Block eine Exception, bleibt die Datei unverändert.
        """
        with self._lock:
            items = self.read()
            yield items
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + TEMP_SUFFIX)
            text = json.dumps({self._key: items}, ensure_ascii=False, indent=2) + "\n"
            temp.write_text(text, encoding="utf-8")
            temp.replace(self.path)
