"""Lokale HTTP-API (FastAPI). Nutzt dieselbe Pipeline wie die CLI.

Hinweis zu Imports: Dieses Modul wird selbst nur ``lazy`` geladen (erst beim Befehl
``server``). FastAPI und Pydantic stehen hier als normale Imports, weil die
Request-/Response-Modelle (``api_models.py``) beim Laden von ``BaseModel`` erben müssen.

Für Frontends: ``docs/api-frontend.md`` beschreibt Ablauf, Felder und Dateien.

Sicherheit: Der Server bindet standardmäßig an 127.0.0.1 und hat keine
Authentifizierung. Dateien werden nur ausgeliefert, wenn ihr Name in
``ergebnis.json`` steht (Schutz vor Path Traversal).
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import FastAPI, File, Form, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from lego_mosaik.config import AppConfig, OverrideInput, ServerConfig, parse_overrides, run_title
from lego_mosaik.domain.errors import ConfigError, InvalidImageError, LegoMosaicError
from lego_mosaik.domain.models import LegoColor
from lego_mosaik.interfaces.api_models import (
    CatalogFileModel,
    ColorModel,
    ErrorModel,
    FieldOption,
    HealthModel,
    MosaicCreatedModel,
    OptionsModel,
    ResultModel,
    UpdateFileModel,
    form_options,
    options_model,
)
from lego_mosaik.pipeline import FINAL_WRITER_NAME, MosaicPipeline, safe_name

logger = logging.getLogger(__name__)

BYTES_PER_MB = 1024 * 1024
SECONDS_PER_HOUR = 60 * 60
RESULT_FILE = "ergebnis.json"
# IDs sind Ordnernamen der Form 2026-10-12_143005_katze (nur sichere Zeichen)
VALID_ID = re.compile(r"^[A-Za-z0-9_-]+$")

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]

DESCRIPTION = """Erzeugt aus Bildern baubare LEGO-Mosaike (lokal, ohne Authentifizierung).

Ablauf für ein Frontend:

1. `GET /optionen` liefert Standardwerte und erlaubte Werte aller Formularfelder.
2. `POST /mosaike` mit dem Bild (multipart) und optionalen Feldern. Die Antwort kommt,
   wenn das Mosaik fertig ist (Sekunden bis wenige Minuten).
3. Vorschau, Anleitung und Teilelisten über die URLs in `dateien` laden.

Fehler kommen immer als `{"fehler": "…"}` mit deutscher Meldung.
"""
# Gruppen in /docs und in generierten Clients
TAG_MOSAICS, TAG_CATALOG = "Mosaike", "Katalog"


# --- Fehlerantworten (OpenAPI) ---------------------------------------------------------


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
        description=DESCRIPTION,
        version=version("lego-mosaik"),
        lifespan=lifespan,
        # operationId = Funktionsname (create_mosaic statt create_mosaic_mosaike_post):
        # ergibt lesbare Methodennamen in generierten Clients
        generate_unique_id_function=_operation_id,
    )
    _add_cors(app, config.server)
    _register_error_handlers(app)
    options = form_options(config, pipeline.method_names)
    _register_catalog_routes(app, pipeline, options)
    _register_mosaic_routes(app, pipeline, config, options)

    @app.get("/openapi.yaml", include_in_schema=False)
    def openapi_document() -> Response:
        """Die OpenAPI-Beschreibung als YAML (dieselbe wie ``/openapi.json``)."""
        return Response(openapi_yaml(app), media_type="application/yaml")

    return app


def _operation_id(route: APIRoute) -> str:
    return route.name


def _add_cors(app: FastAPI, server: ServerConfig) -> None:
    """Erlaubt Frontends auf anderen Origins (z. B. Vite auf localhost:5173) den Zugriff.

    ``Content-Disposition`` wird freigegeben, damit das Frontend beim Download den
    Dateinamen lesen kann.
    """
    if server.cors_origin_regex is None and not server.cors_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(server.cors_origins),
        allow_origin_regex=server.cors_origin_regex,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )


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


def _register_catalog_routes(
    app: FastAPI, pipeline: MosaicPipeline, options: dict[str, FieldOption]
) -> None:
    """``/gesundheit``, ``/optionen``, ``/farben``, ``/verfahren``, ``/teile/aktualisieren``."""

    @app.get("/gesundheit", summary="Status und Stand des Teilekatalogs", tags=[TAG_CATALOG])
    def get_health() -> HealthModel:
        files = [
            CatalogFileModel(
                datei=info.file_name,
                stand=info.modified.isoformat() if info.modified else None,
                veraltet=info.outdated,
            )
            for info in pipeline.catalog_status()
        ]
        return HealthModel(status="ok", katalog=files)

    @app.get(
        "/optionen",
        summary="Standardwerte und erlaubte Werte der Formularfelder",
        tags=[TAG_MOSAICS],
    )
    def get_options() -> OptionsModel:
        return options_model(options)

    @app.get(
        "/farben",
        summary="Palette für Teilarten und Farbkategorien",
        responses=_errors(422),
        tags=[TAG_CATALOG],
    )
    def list_colors(
        teile: Annotated[str | None, Query(description="z. B. plate,tile")] = None,
        farbkategorien: Annotated[str | None, Query(description="z. B. solid,pearl")] = None,
        exotisch: Annotated[bool, Query(description="Alle Kategorien außer Gummi")] = False,
    ) -> list[ColorModel]:
        raw = OverrideInput(
            part_kinds=teile, color_categories=farbkategorien, exotic_colors=exotisch
        )
        return [_color_model(color) for color in pipeline.palette(parse_overrides(raw))]

    @app.get("/verfahren", summary="Registrierte Verfahren und Ausgaben", tags=[TAG_CATALOG])
    def list_methods() -> dict[str, list[str]]:
        return {kind: list(names) for kind, names in pipeline.method_names.items()}

    @app.post(
        "/teile/aktualisieren", summary="Rebrickable-Katalog aktualisieren", tags=[TAG_CATALOG]
    )
    def update_catalog(
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


def _register_mosaic_routes(
    app: FastAPI, pipeline: MosaicPipeline, config: AppConfig, options: dict[str, FieldOption]
) -> None:
    """``POST /mosaike``, ``GET /mosaike/{id}``, ``GET /mosaike/{id}/dateien/{name}``."""
    max_bytes = config.server.max_upload_mb * BYTES_PER_MB

    def field(name: str, json_type: str, description: str, alias: str | None = None) -> Any:
        """Formularfeld mit Standardwert und erlaubten Werten aus ``options``."""
        option = options[name]
        return Form(
            alias=alias,
            description=option.describe(description),
            json_schema_extra=option.schema(json_type),
        )

    # Normales def (kein async def): FastAPI führt es im Threadpool aus, der Server
    # bleibt während der rechenintensiven Mosaikerzeugung ansprechbar
    @app.post(
        "/mosaike",
        status_code=status.HTTP_201_CREATED,
        summary="Mosaik erzeugen",
        responses=_errors(400, 413, 422, 500),
        tags=[TAG_MOSAICS],
    )
    def create_mosaic(
        request: Request,
        bild: Annotated[UploadFile, File(description="Bilddatei (JPG, PNG, …)")],
        unterbau: Annotated[str | None, field("unterbau", "string", "Unterbau")] = None,
        plattengroesse: Annotated[
            int | None, field("plattengroesse", "integer", "Plattengröße in Noppen")
        ] = None,
        format_name: Annotated[
            str | None,
            field("format", "string", "Format wie bei LEGO Art (statt platten)", alias="format"),
        ] = None,
        platten: Annotated[
            str | None, field("platten", "string", "Platten als BxH, z. B. 3x3")
        ] = None,
        breite: Annotated[int | None, field("breite", "integer", "Eigene Breite in Noppen")] = None,
        hoehe: Annotated[int | None, field("hoehe", "integer", "Eigene Höhe in Noppen")] = None,
        unterbaufarbe: Annotated[
            int | None,
            field("unterbaufarbe", "integer", "Rebrickable-ID (Standard je nach Unterbau)"),
        ] = None,
        rahmen: Annotated[int | None, field("rahmen", "integer", "Rahmenbreite in Noppen")] = None,
        rahmenfarbe: Annotated[
            int | None, field("rahmenfarbe", "integer", "Rebrickable-ID des Rahmens")
        ] = None,
        eckenfarbe: Annotated[
            int | None, field("eckenfarbe", "integer", "Rebrickable-ID der Ecken")
        ] = None,
        rahmen_teile: Annotated[
            str | None, field("rahmen_teile", "string", "Teilarten des Rahmens")
        ] = None,
        rahmen_erhoehung: Annotated[
            int | None, field("rahmen_erhoehung", "integer", "Plates unter dem Rahmen")
        ] = None,
        modus: Annotated[
            str | None, field("modus", "string", "Bild zuschneiden oder einpassen")
        ] = None,
        kontrast: Annotated[
            float | None, field("kontrast", "number", "Kontrast, 1.0 = unverändert")
        ] = None,
        saettigung: Annotated[
            float | None, field("saettigung", "number", "Sättigung, 1.0 = unverändert")
        ] = None,
        teile: Annotated[str | None, field("teile", "string", "Teilarten des Bilds")] = None,
        farbkategorien: Annotated[
            str | None, field("farbkategorien", "string", "Farbkategorien")
        ] = None,
        exotische_farben: Annotated[
            bool, Form(description="Alle Farbkategorien außer Gummi (Standard: nein)")
        ] = False,
        max_farben: Annotated[
            int | None, field("max_farben", "integer", "Höchstens so viele Farben, 0 = alle")
        ] = None,
        stil: Annotated[str | None, field("stil", "string", "Stil der Bildteile")] = None,
        relief: Annotated[str | None, field("relief", "string", "Was erhöht wird")] = None,
        relief_hoehe: Annotated[
            int | None, field("relief_hoehe", "integer", "Höchste Relief-Stufe in Plates")
        ] = None,
        dithering: Annotated[
            str | None, field("dithering", "string", "Dithering-Verfahren")
        ] = None,
        staerke: Annotated[float | None, field("staerke", "number", "Dithering-Stärke")] = None,
        farbabstand: Annotated[
            str | None, field("farbabstand", "string", "Formel für den Farbabstand")
        ] = None,
        aufraeumen: Annotated[
            bool | None,
            field("aufraeumen", "boolean", "Einzelne Pixel durch die Nachbarfarbe ersetzen"),
        ] = None,
        zerlegung: Annotated[
            str | None, field("zerlegung", "string", "Zerlegungsverfahren")
        ] = None,
        ausgaben: Annotated[
            str | None,
            field("ausgaben", "string", "Erzeugte Dateien (ergebnis.json kommt immer dazu)"),
        ] = None,
        titel: Annotated[
            str | None, field("titel", "string", "Name auf dem Titelblatt (Standard: Dateiname)")
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
            despeckle=aufraeumen,
            tiling_strategy=zerlegung,
            outputs=ausgaben,
            title=titel,
        )
        overrides = parse_overrides(raw)
        # ergebnis.json braucht die API immer: Dateiliste und Statistik kommen daraus
        if overrides.outputs and FINAL_WRITER_NAME not in overrides.outputs:
            overrides = replace(overrides, outputs=(*overrides.outputs, FINAL_WRITER_NAME))
        title = run_title(raw, safe_name(Path(bild.filename or "mosaik").stem))
        result = pipeline.run(data, overrides, title=title)
        content = _result(result.target_dir)
        base_url = str(request.base_url).rstrip("/")
        urls = {name: f"{base_url}/mosaike/{result.id}/dateien/{name}" for name in content.dateien}
        return MosaicCreatedModel(id=result.id, statistik=content.statistik, dateien=urls)

    @app.get(
        "/mosaike/{mosaic_id}",
        summary="Inhalt von ergebnis.json",
        responses=_errors(404),
        tags=[TAG_MOSAICS],
    )
    def get_mosaic(mosaic_id: str) -> ResultModel:
        return _result(_mosaic_dir(config.output_dir, mosaic_id))

    @app.get(
        "/mosaike/{mosaic_id}/dateien/{file_name}",
        summary="Datei herunterladen",
        response_class=FileResponse,
        tags=[TAG_MOSAICS],
        responses={
            200: {"description": "Die Datei (PNG, PDF, LDraw, CSV, XML oder JSON)"},
            **_errors(404),
        },
    )
    def download_file(mosaic_id: str, file_name: str) -> FileResponse:
        directory = _mosaic_dir(config.output_dir, mosaic_id)
        # Nur Namen aus ergebnis.json; der Pfad wird nie direkt aus der URL gebaut
        if file_name not in _result(directory).dateien:
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


def _result(directory: Path) -> ResultModel:
    """``ergebnis.json`` eines Mosaiks; seine Dateiliste ist die einzige Quelle für Downloads."""
    return ResultModel.model_validate_json((directory / RESULT_FILE).read_bytes())
