"""Einstellungen der Cloud-Variante aus Umgebungsvariablen (12-Factor).

Jedes Feld kommt aus ``LEGO_MOSAIK_<FELD>``, z. B. ``LEGO_MOSAIK_DATABASE_URL``. Im
Helm-Chart setzen ConfigMap und Secret diese Variablen; lokal geht auch eine ``.env``.
Die fachliche Konfiguration (Größe, Verfahren, Ausgaben, …) bleibt ``lego_mosaik.main.CONFIG``.
"""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudSettings(BaseSettings):
    """Verbindungen, Server und Observability.

    Attributes:
        database_url: SQLAlchemy-URL, z. B. ``postgresql+psycopg://user:pw@host:5432/db``.
        s3_endpoint: S3-Endpunkt von Garage, z. B. ``http://garage:3900``.
        s3_prefix: Schlüssel-Präfix der Mosaike im Bucket.
        data_dir: Ordner mit ``teil_geometrie.csv`` und ``farb_zuordnung.csv`` (für den Import).
        studio_color_file: Optional Studios ``StudioColorDefinition.txt`` (für den Import).
        catalog_refresh_seconds: So oft prüft jeder Pod, ob ein neuer Katalog importiert wurde.
        cors_origins: Kommagetrennte Origins, die die API im Browser aufrufen dürfen.
    """

    model_config = SettingsConfigDict(env_prefix="LEGO_MOSAIK_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lego:lego@localhost:5432/lego_mosaik"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    s3_endpoint: str = "http://localhost:3900"
    s3_region: str = "garage"
    s3_bucket: str = "lego-mosaik"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_prefix: str = "mosaike"

    host: str = "0.0.0.0"  # im Container immer von außen erreichbar
    port: int = 8080
    max_upload_mb: int = 20
    cors_origins: str = ""
    cors_origin_regex: str | None = None

    data_dir: Path = Path("daten")
    studio_color_file: Path | None = None
    catalog_refresh_seconds: int = 60

    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    # Tracing ist an, sobald OTEL_EXPORTER_OTLP_ENDPOINT gesetzt ist (Standard-Variable von OTel)
    service_name: str = "lego-mosaik"
    git_sha: str = "unbekannt"
    build_time: str | None = None

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        """``cors_origins`` als Tupel ohne leere Einträge."""
        return tuple(origin.strip() for origin in self.cors_origins.split(",") if origin.strip())
