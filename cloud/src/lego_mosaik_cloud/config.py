"""Fachliche Konfiguration der Cloud-Variante: ``CONFIG`` aus dem Kern mit Cloud-Pfaden."""

import tempfile
from dataclasses import replace
from pathlib import Path

from lego_mosaik.config import AppConfig
from lego_mosaik.main import CONFIG

from lego_mosaik_cloud.settings import CloudSettings

USER_AGENT = "lego-mosaik-cloud/0.1"


def cloud_app_config(settings: CloudSettings, base: AppConfig = CONFIG) -> AppConfig:
    """Übernimmt die lokale Konfiguration und ersetzt nur, was in der Cloud anders ist.

    Mosaike entstehen in Temp-Ordnern auf dem Volume (``mosaic_dir``); die Rebrickable-Dumps
    lädt nur der Katalog-Import (ebenfalls in einen Temp-Ordner).
    """
    work_dir = Path(tempfile.gettempdir()) / "lego-mosaik"
    return replace(
        base,
        output_dir=work_dir / "ausgabe",
        source=replace(
            base.source,
            data_dir=work_dir / "rebrickable",
            geometry_file=settings.data_dir / "teil_geometrie.csv",
            color_mapping_file=settings.data_dir / "farb_zuordnung.csv",
            studio_color_file=settings.studio_color_file,
            user_agent=USER_AGENT,
        ),
        server=replace(
            base.server,
            host=settings.host,
            port=settings.port,
            max_upload_mb=settings.max_upload_mb,
            cors_origins=settings.cors_origin_list,
            cors_origin_regex=settings.cors_origin_regex,
        ),
        # Eingaben und Originalbild braucht „bearbeiten“; in der Cloud also immer behalten
        storage=replace(base.storage, keep_source=True),
    )
