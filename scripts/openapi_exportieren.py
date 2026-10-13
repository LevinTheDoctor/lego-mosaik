"""Schreibt die OpenAPI-Beschreibung der HTTP-API nach ``openapi.yaml`` (OpenAPI 3).

Aufruf::

    uv run python scripts/openapi_exportieren.py

Die Datei entsteht aus der laufenden FastAPI-App, ist also immer genau so wie
``/openapi.json`` bzw. ``/openapi.yaml`` des Servers. ``tests/test_api.py`` prüft,
dass die eingecheckte Datei aktuell ist.
"""

from pathlib import Path

from lego_mosaik.interfaces.api import create_app, openapi_yaml
from lego_mosaik.main import CONFIG, build_pipeline, build_price_service

TARGET = Path(__file__).parent.parent / "openapi.yaml"


def main() -> None:
    """Erzeugt die App (ohne Server zu starten) und schreibt ihre Beschreibung."""
    TARGET.write_text(
        openapi_yaml(create_app(CONFIG, build_pipeline, build_price_service)), encoding="utf-8"
    )
    print(f"OpenAPI-Beschreibung geschrieben: {TARGET}")


if __name__ == "__main__":
    main()
