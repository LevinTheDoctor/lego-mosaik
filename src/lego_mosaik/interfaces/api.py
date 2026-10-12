"""Lokale HTTP-API (FastAPI). Nutzt dieselbe Pipeline wie die CLI.

Hinweis zu Imports: Dieses Modul wird selbst nur ``lazy`` geladen (erst beim Befehl
``server``). FastAPI und Pydantic stehen hier als normale Imports, weil die
Request-/Response-Modelle beim Laden des Moduls von ``BaseModel`` erben müssen.

Sicherheit: Der Server bindet standardmäßig an 127.0.0.1 und hat keine
Authentifizierung. Dateien werden nur ausgeliefert, wenn ihr Name in
``ergebnis.json`` steht (Schutz vor Path Traversal).
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import FastAPI, File, Form, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from lego_mosaik.config import AppConfig, OverrideInput, parse_overrides, run_title
from lego_mosaik.domain.errors import ConfigError, InvalidImageError, LegoMosaicError
from lego_mosaik.domain.models import LegoColor
from lego_mosaik.pipeline import MosaicPipeline, safe_name

logger = logging.getLogger(__name__)

BYTES_PER_MB = 1024 * 1024
SECONDS_PER_HOUR = 60 * 60
RESULT_FILE = "ergebnis.json"
# IDs sind Ordnernamen der Form 2026-10-12_143005_katze (nur sichere Zeichen)
VALID_ID = re.compile(r"^[A-Za-z0-9_-]+$")

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]


# --- Pydantic-Modelle (Feldbeschreibungen erscheinen in /docs) -----------------------


class ErrorModel(BaseModel):
    fehler: str = Field(description="Deutsche Fehlermeldung")


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Fehlerantworten für die OpenAPI-Beschreibung einer Route (alle als ``ErrorModel``)."""
    texts = {
        400: "Kein lesbares Bild",
        404: "Nicht gefunden",
        413: "Datei zu groß",
        422: "Ungültige Angaben",
        500: "Verarbeitungsfehler",
    }
    return {code: {"model": ErrorModel, "description": texts[code]} for code in codes}


class CatalogFileModel(BaseModel):
    datei: str = Field(description="Dateiname der Rebrickable-CSV")
    stand: str | None = Field(description="Letzte Änderung (ISO 8601, UTC) oder null")
    veraltet: bool = Field(description="True, wenn die Datei fehlt oder zu alt ist")


class HealthModel(BaseModel):
    status: str = Field(description="„ok“, wenn der Server läuft")
    katalog: list[CatalogFileModel] = Field(description="Stand der Katalogdateien")


class ColorModel(BaseModel):
    id: int = Field(description="Rebrickable-Farb-ID")
    name: str = Field(description="Farbname (Rebrickable)")
    hex: str = Field(description="Farbwert als #RRGGBB")
    kategorie: str = Field(description="Oberfläche: solid, metallic, pearl, …")
    ldraw_code: int | None = Field(description="LDraw-Farbcode")
    bricklink_code: int | None = Field(description="BrickLink-Farbcode, falls bekannt")
    lego_name: str | None = Field(description="Offizieller LEGO-Farbname, falls bekannt")


class MosaicCreatedModel(BaseModel):
    id: str = Field(description="ID des Mosaiks (Name des Ausgabeordners)")
    statistik: dict[str, object] = Field(description="Größe, Farben, Teile, Dauer")
    dateien: dict[str, str] = Field(description="Dateiname → Download-URL")


class UpdateFileModel(BaseModel):
    datei: str = Field(description="Dateiname")
    status: str = Field(description="heruntergeladen, aktuell oder fehlgeschlagen")
    hinweis: str = Field(description="Erklärung, bei Fehlern der Grund")


# --- App-Fabrik ------------------------------------------------------------------------


def create_app(config: AppConfig, pipeline_factory: PipelineFactory) -> FastAPI:
    """Baut die FastAPI-App (Factory) mit Hintergrund-Aktualisierung des Katalogs."""
    pipeline = pipeline_factory(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Läuft beim Start (vor yield) und beim Beenden (nach yield) des Servers."""
        task = asyncio.create_task(_update_periodically(pipeline, config))
        yield
        task.cancel()  # Hintergrund-Task beim Herunterfahren beenden

    app = FastAPI(
        title="LEGO-Mosaik-Service",
        description="Erzeugt aus Bildern baubare LEGO-Mosaike (lokal, ohne Authentifizierung).",
        version=version("lego-mosaik"),
        lifespan=lifespan,
    )
    _register_error_handlers(app)
    _register_catalog_routes(app, pipeline)
    _register_mosaic_routes(app, pipeline, config)

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_document() -> Response:
        """Die OpenAPI-Beschreibung als YAML (dieselbe wie ``/openapi.json``)."""
        return Response(openapi_yaml(app), media_type="application/yaml")

    return app


def openapi_yaml(app: FastAPI) -> str:
    """OpenAPI-3-Beschreibung der App als YAML, Schlüssel in der Reihenfolge von FastAPI."""
    return yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True, width=100)


async def _update_periodically(pipeline: MosaicPipeline, config: AppConfig) -> None:
    """Prüft den Katalog sofort und dann alle ``update_interval_hours`` Stunden."""
    interval = config.server.update_interval_hours * SECONDS_PER_HOUR
    while True:
        try:
            # to_thread: der blockierende Download läuft in einem Thread, der Server bleibt frei
            report = await asyncio.to_thread(pipeline.update_catalog)
            for failure in report.failed:
                logger.warning(
                    "Aktualisierung von %s fehlgeschlagen: %s", failure.file_name, failure.message
                )
        except LegoMosaicError as error:
            logger.warning("Katalog-Aktualisierung fehlgeschlagen: %s", error)
        await asyncio.sleep(interval)


def _register_error_handlers(app: FastAPI) -> None:
    """Alle Fehler als ``{"fehler": "…"}`` mit passendem Statuscode."""

    @app.exception_handler(LegoMosaicError)
    async def domain_error(_: Request, error: LegoMosaicError) -> JSONResponse:
        # Ungültige Parameter → 422, ungültiges Bild → 400, sonst Verarbeitungsfehler → 500
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if isinstance(error, ConfigError):
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
        elif isinstance(error, InvalidImageError):
            code = status.HTTP_400_BAD_REQUEST
        return JSONResponse({"fehler": str(error)}, status_code=code)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse({"fehler": str(error.detail)}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        fields = ", ".join(".".join(map(str, item["loc"])) for item in error.errors())
        return JSONResponse(
            {"fehler": f"Ungültige oder fehlende Angaben: {fields}."},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


def _register_catalog_routes(app: FastAPI, pipeline: MosaicPipeline) -> None:
    """``/gesundheit``, ``/farben``, ``/verfahren``, ``/teile/aktualisieren``."""

    @app.get("/gesundheit", summary="Status und Stand des Teilekatalogs")
    def health() -> HealthModel:
        files = [
            CatalogFileModel(
                datei=info.file_name,
                stand=info.modified.isoformat() if info.modified else None,
                veraltet=info.outdated,
            )
            for info in pipeline.catalog_status()
        ]
        return HealthModel(status="ok", katalog=files)

    @app.get("/farben", summary="Palette für Teilarten und Farbkategorien", responses=_errors(422))
    def colors(
        teile: Annotated[str | None, Query(description="z. B. plate,tile")] = None,
        farbkategorien: Annotated[str | None, Query(description="z. B. solid,pearl")] = None,
        exotisch: Annotated[bool, Query(description="Alle Kategorien außer Gummi")] = False,
    ) -> list[ColorModel]:
        raw = OverrideInput(
            part_kinds=teile, color_categories=farbkategorien, exotic_colors=exotisch
        )
        return [_color_model(color) for color in pipeline.palette(parse_overrides(raw))]

    @app.get("/verfahren", summary="Registrierte Verfahren und Ausgaben")
    def methods() -> dict[str, list[str]]:
        return {kind: list(names) for kind, names in pipeline.method_names.items()}

    @app.post("/teile/aktualisieren", summary="Rebrickable-Katalog aktualisieren")
    def update(
        erzwingen: Annotated[bool, Query(description="Auch aktuelle Dateien neu laden")] = False,
    ) -> list[UpdateFileModel]:
        report = pipeline.update_catalog(force=erzwingen)
        return [
            UpdateFileModel(datei=u.file_name, status=u.status.value, hinweis=u.message)
            for u in report.files
        ]


def _color_model(color: LegoColor) -> ColorModel:
    return ColorModel(
        id=color.id,
        name=color.name,
        hex=color.hex,
        kategorie=color.category.value,
        ldraw_code=color.ldraw_code,
        bricklink_code=color.bricklink_code,
        lego_name=color.lego_name,
    )


def _register_mosaic_routes(app: FastAPI, pipeline: MosaicPipeline, config: AppConfig) -> None:
    """``POST /mosaike``, ``GET /mosaike/{id}``, ``GET /mosaike/{id}/dateien/{name}``."""
    max_bytes = config.server.max_upload_mb * BYTES_PER_MB

    # Normales def (kein async def): FastAPI führt es im Threadpool aus, der Server
    # bleibt während der rechenintensiven Mosaikerzeugung ansprechbar
    @app.post(
        "/mosaike",
        status_code=status.HTTP_201_CREATED,
        summary="Mosaik erzeugen",
        responses=_errors(400, 413, 422, 500),
    )
    def create_mosaic(
        request: Request,
        bild: Annotated[UploadFile, File(description="Bilddatei (JPG, PNG, …)")],
        unterbau: Annotated[
            str | None, Form(description="art, platten, grundplatte oder ohne")
        ] = None,
        plattengroesse: Annotated[int | None, Form(description="16, 32 oder 48")] = None,
        format_name: Annotated[
            str | None, Form(alias="format", description="z. B. quadrat, quer, weltkarte")
        ] = None,
        platten: Annotated[str | None, Form(description="BxH, z. B. 3x3")] = None,
        breite: Annotated[int | None, Form(description="Eigene Breite in Noppen")] = None,
        hoehe: Annotated[int | None, Form(description="Eigene Höhe in Noppen")] = None,
        unterbaufarbe: Annotated[int | None, Form(description="Rebrickable-ID")] = None,
        rahmen: Annotated[int | None, Form(description="Rahmenbreite in Noppen")] = None,
        rahmenfarbe: Annotated[int | None, Form(description="Rebrickable-ID")] = None,
        eckenfarbe: Annotated[int | None, Form(description="Rebrickable-ID der Ecken")] = None,
        rahmen_teile: Annotated[str | None, Form(description="z. B. tile")] = None,
        rahmen_erhoehung: Annotated[int | None, Form(description="Plates unter dem Rahmen")] = None,
        modus: Annotated[str | None, Form(description="zuschneiden oder einpassen")] = None,
        kontrast: Annotated[float | None, Form(description="1.0 = unverändert")] = None,
        saettigung: Annotated[float | None, Form(description="1.0 = unverändert")] = None,
        teile: Annotated[str | None, Form(description="z. B. plate,tile")] = None,
        farbkategorien: Annotated[str | None, Form(description="z. B. solid,pearl")] = None,
        exotische_farben: Annotated[bool, Form(description="Alle außer Gummi")] = False,
        max_farben: Annotated[int | None, Form(description="0 = unbegrenzt")] = None,
        stil: Annotated[str | None, Form(description="quadrat, formen, punkte, noppen")] = None,
        relief: Annotated[str | None, Form(description="ohne, motiv, hell, dunkel")] = None,
        relief_hoehe: Annotated[int | None, Form(description="1 bis 3 Plates")] = None,
        dithering: Annotated[str | None, Form(description="Dithering-Verfahren")] = None,
        staerke: Annotated[float | None, Form(description="0.0 bis 1.0")] = None,
        farbabstand: Annotated[str | None, Form(description="cie76 oder ciede2000")] = None,
        zerlegung: Annotated[str | None, Form(description="Zerlegungsverfahren")] = None,
        ausgaben: Annotated[str | None, Form(description="z. B. preview_png,ldraw")] = None,
        titel: Annotated[
            str | None, Form(description="Name auf dem Titelblatt (Standard: Dateiname)")
        ] = None,
    ) -> MosaicCreatedModel:
        data = _read_limited(bild, max_bytes)
        raw = OverrideInput(
            substrate=unterbau,
            plate_studs=plattengroesse,
            art_format=format_name,
            plates=platten,
            width_studs=breite,
            height_studs=hoehe,
            substrate_color_id=unterbaufarbe,
            frame_width=rahmen,
            frame_color_id=rahmenfarbe,
            frame_corner_color_id=eckenfarbe,
            frame_part_kinds=rahmen_teile,
            frame_raise_plates=rahmen_erhoehung,
            fit_mode=modus,
            contrast=kontrast,
            saturation=saettigung,
            part_kinds=teile,
            color_categories=farbkategorien,
            exotic_colors=exotische_farben,
            max_colors=max_farben,
            style=stil,
            relief=relief,
            relief_levels=relief_hoehe,
            dithering_method=dithering,
            strength=staerke,
            color_distance=farbabstand,
            tiling_strategy=zerlegung,
            outputs=ausgaben,
            title=titel,
        )
        overrides = parse_overrides(raw)
        title = run_title(raw, safe_name(Path(bild.filename or "mosaik").stem))
        result = pipeline.run(data, overrides, title=title)
        files = _result_files(result.target_dir)
        base_url = str(request.base_url).rstrip("/")
        urls = {name: f"{base_url}/mosaike/{result.id}/dateien/{name}" for name in files}
        return MosaicCreatedModel(id=result.id, statistik=result.stats, dateien=urls)

    @app.get("/mosaike/{mosaic_id}", summary="Inhalt von ergebnis.json", responses=_errors(404))
    def get_mosaic(mosaic_id: str) -> dict[str, object]:
        result_path = _mosaic_dir(config.output_dir, mosaic_id) / RESULT_FILE
        content: dict[str, object] = json.loads(result_path.read_text(encoding="utf-8"))
        return content

    @app.get(
        "/mosaike/{mosaic_id}/dateien/{file_name}",
        summary="Datei herunterladen",
        response_class=FileResponse,
        responses={
            200: {"description": "Die Datei (PNG, PDF, LDraw, CSV, XML oder JSON)"},
            **_errors(404),
        },
    )
    def download(mosaic_id: str, file_name: str) -> FileResponse:
        directory = _mosaic_dir(config.output_dir, mosaic_id)
        # Nur Namen aus ergebnis.json; der Pfad wird nie direkt aus der URL gebaut
        if file_name not in _result_files(directory):
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Datei „{file_name}“ gibt es nicht.")
        return FileResponse(directory / file_name, filename=file_name)


def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Liest den Upload, höchstens ``max_bytes``; größere Dateien ergeben 413."""
    data = upload.file.read(max_bytes + 1)  # ein Byte mehr, um Überschreitung zu erkennen
    if len(data) > max_bytes:
        limit_mb = max_bytes // BYTES_PER_MB
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Die Datei ist größer als {limit_mb} MB."
        )
    return data


def _mosaic_dir(output_dir: Path, mosaic_id: str) -> Path:
    """Ordner eines Mosaiks; unbekannte oder unsichere IDs ergeben 404."""
    directory = output_dir / mosaic_id
    if not VALID_ID.match(mosaic_id) or not (directory / RESULT_FILE).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Mosaik „{mosaic_id}“ gibt es nicht.")
    return directory


def _result_files(directory: Path) -> list[str]:
    """Dateinamen laut ``ergebnis.json`` (die einzige Quelle für erlaubte Downloads)."""
    content = json.loads((directory / RESULT_FILE).read_text(encoding="utf-8"))
    files = content.get("dateien", [])
    return [str(name) for name in files]
