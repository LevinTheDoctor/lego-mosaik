"""Eigene Fehlerhierarchie des Services.

Alle fachlichen Fehler erben von ``LegoMosaicError``. So können CLI und API mit
einem einzigen ``except LegoMosaicError`` alle erwartbaren Fehler abfangen und
eine deutsche Meldung ohne Traceback zeigen. Unerwartete Fehler (Programmierfehler)
bleiben normale Exceptions und fallen dadurch auf.
"""


class LegoMosaicError(Exception):
    """Basisklasse aller fachlichen Fehler. Die Meldung ist immer deutsch."""


class ConfigError(LegoMosaicError):
    """Ungültige Konfiguration oder ungültige Eingabe (z. B. Breite außerhalb der Grenzen)."""


class NotFoundError(LegoMosaicError):
    """Ein gespeichertes Objekt (Palette, Preset, Mosaik) gibt es nicht."""


class CatalogError(LegoMosaicError):
    """Der Teilekatalog fehlt, ist unvollständig oder lässt sich nicht lesen."""


class DownloadError(LegoMosaicError):
    """Die Rebrickable-Dateien konnten nicht heruntergeladen werden."""


class InvalidImageError(LegoMosaicError):
    """Die Bilddatei fehlt oder ist kein lesbares Bild."""


class TilingError(LegoMosaicError):
    """Die Zerlegung in Teile ist mit den gewählten Teilarten nicht möglich."""


class PriceSourceError(LegoMosaicError):
    """Die BrickLink-Preise konnten nicht abgefragt werden (Netz, Anmeldung, Antwort)."""


class MissingCredentialsError(PriceSourceError):
    """Die BrickLink-Zugangsdaten (Umgebungsvariablen) fehlen."""
