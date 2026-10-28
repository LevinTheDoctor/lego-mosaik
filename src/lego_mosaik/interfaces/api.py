"""Lokale HTTP-API (FastAPI). Nutzt dieselbe Pipeline wie die CLI.

Hinweis zu Imports: Dieses Modul wird selbst nur ``lazy`` geladen (erst beim Befehl
``server``). FastAPI und Pydantic stehen hier als normale Imports, weil die
Request-/Response-Modelle (``api_models.py``) beim Laden von ``BaseModel`` erben müssen.

Für Frontends: ``docs/api-frontend.md`` beschreibt Ablauf, Felder und Dateien.

Sicherheit: Der Server bindet standardmäßig an 127.0.0.1 und hat keine
Authentifizierung. Dateien werden nur ausgeliefert, wenn ihr Name in
``ergebnis.json`` steht (Schutz vor Path Traversal).

Speicher: Paletten, Presets und Mosaike kommen über ``ApiBackend``. Ohne Angabe sind es
die lokalen Dateien; die Cloud-Variante (``lego_mosaik_cloud``) setzt PostgreSQL und ein Volume ein.
"""

import asyncio
import base64
import logging
import mimetypes
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any
from xml.etree import ElementTree

import yaml
from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException

from lego_mosaik.config import (
    AppConfig,
    MosaicOverrides,
    OverrideInput,
    ServerConfig,
    form_values,
    override_input,
    parse_overrides,
    run_title,
)
from lego_mosaik.domain.editing import AreaEdit, FillEdit, GridEdit, StudEdit
from lego_mosaik.domain.errors import (
    ConfigError,
    InvalidImageError,
    LegoMosaicError,
    MissingCredentialsError,
    NotFoundError,
    PriceSourceError,
)
from lego_mosaik.domain.library import ColorPalette, Preset, PresetOrder
from lego_mosaik.domain.models import LegoColor
from lego_mosaik.interfaces.api_models import (
    BackgroundModel,
    BrickLinkExportModel,
    CatalogFileModel,
    ColorModel,
    EditRequestModel,
    ErrorModel,
    FieldOption,
    HealthModel,
    MosaicCreatedModel,
    OptionsModel,
    PaletteInputModel,
    PaletteModel,
    PresetFromMosaicModel,
    PresetInputModel,
    PresetMarkModel,
    PresetModel,
    PriceEstimateModel,
    RasterColorModel,
    RasterModel,
    ResultModel,
    UpdateFileModel,
    form_options,
    options_model,
)
from lego_mosaik.outputs.official_parts_lists import BRICKLINK_FILE_NAME, bricklink_xml
from lego_mosaik.outputs.price_list import JSON_FILE_NAME as PRICES_FILE
from lego_mosaik.outputs.raster_json import FILE_NAME as RASTER_FILE
from lego_mosaik.pipeline import FINAL_WRITER_NAME, MosaicPipeline, MosaicResult, safe_name
from lego_mosaik.pricing.service import PriceService, read_order_items
from lego_mosaik.storage.library import Library
from lego_mosaik.storage.mosaics import LocalMosaicRepository
from lego_mosaik.storage.protocol import MosaicRepository

logger = logging.getLogger(__name__)

BYTES_PER_MB = 1024 * 1024
SECONDS_PER_HOUR = 60 * 60
RESULT_FILE = "ergebnis.json"
# Ausgaben, die die API immer braucht: Dateiliste und Statistik bzw. das Raster für den Editor
REQUIRED_OUTPUTS = ("raster_json", FINAL_WRITER_NAME)
PREVIEW_PX, MIN_PREVIEW_PX, MAX_PREVIEW_PX = 800, 64, 2048
MASK_PREVIEW_PX = 512

type PipelineFactory = Callable[[AppConfig], MosaicPipeline]
type PriceFactory = Callable[[AppConfig], PriceService]

DESCRIPTION = """Erzeugt aus Bildern baubare LEGO-Mosaike (lokal, ohne Authentifizierung).

Ablauf für ein Frontend:

1. `GET /optionen` liefert Standardwerte und erlaubte Werte aller Formularfelder.
2. Optional das Bild vorbereiten: `POST /bild/hintergrund` erkennt den Hintergrund,
   `POST /bild/vorschau` zeigt das bearbeitete Bild (Filter, Ausschnitt, Hintergrund).
3. `POST /mosaike` mit dem Bild (multipart) und optionalen Feldern, auch `preset` und
   `palette`. Die Antwort kommt, wenn das Mosaik fertig ist (Sekunden bis wenige Minuten).
4. Vorschau, Anleitung und Teilelisten über die URLs in `dateien` laden.
5. Optional bearbeiten: `GET /mosaike/{id}/raster` liefert jede Noppe,
   `POST /mosaike/{id}/bearbeiten` färbt Noppen und Flächen um (neues Mosaik).
6. Optional: `POST /mosaike/{id}/preise` schätzt den Preis über BrickLink,
   `GET /mosaike/{id}/bricklink` liefert die Wanted List zum Hochladen auf BrickLink.

Eigene Farbpaletten (`/paletten`) und Presets mit Bewertung und Favoriten (`/presets`)
werden lokal in `daten/` gespeichert.

Fehler kommen immer als `{"fehler": "…"}` mit deutscher Meldung.
"""
# Gruppen in /docs und in generierten Clients
TAG_MOSAICS, TAG_CATALOG, TAG_BRICKLINK = "Mosaike", "Katalog", "BrickLink"
TAG_IMAGE, TAG_PALETTES, TAG_PRESETS = "Bild", "Paletten", "Presets"


@dataclass(frozen=True, slots=True)
class ApiBackend:
    """Speicher und Hintergrundaufgaben der API.

    Attributes:
        library: Eigene Paletten und Presets.
        mosaics: Fertige Mosaike.
        catalog_auto_update: Katalog im Server-Prozess regelmäßig aktualisieren. Die
            Cloud-Variante macht das per CronJob und schaltet es hier ab.
    """

    library: Library
    mosaics: MosaicRepository
    catalog_auto_update: bool = True

    @classmethod
    def local(cls, config: AppConfig) -> ApiBackend:
        """Dateien aus der Konfiguration: ``daten/*.json`` und ``ausgabe/``."""
        return cls(Library.open(config.storage), LocalMosaicRepository(config.output_dir))


# --- Fehlerantworten (OpenAPI) ---------------------------------------------------------


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Fehlerantworten für die OpenAPI-Beschreibung einer Route (alle als ``ErrorModel``)."""
    texts = {
        400: "Kein lesbares Bild",
        404: "Nicht gefunden",
        413: "Datei zu groß",
        422: "Ungültige Angaben",
        500: "Verarbeitungsfehler",
        502: "BrickLink antwortet nicht oder mit einem Fehler",
        503: "BrickLink-Zugangsdaten fehlen",
    }
    return {code: {"model": ErrorModel, "description": texts[code]} for code in codes}


# --- App-Fabrik ------------------------------------------------------------------------


def create_app(
    config: AppConfig,
    pipeline_factory: PipelineFactory,
    price_factory: PriceFactory | None = None,
    *,
    backend: ApiBackend | None = None,
) -> FastAPI:
    """Baut die FastAPI-App (Factory) mit Hintergrund-Aktualisierung des Katalogs.

    Ohne ``price_factory`` fehlt nur die Preisschätzung (``/mosaike/{id}/preise``).
    Ohne ``backend`` speichert die App lokal (``ApiBackend.local``).
    """
    pipeline = pipeline_factory(config)
    backend = backend or ApiBackend.local(config)
    library, mosaics = backend.library, backend.mosaics

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Läuft beim Start (vor yield) und beim Beenden (nach yield) des Servers."""
        if not backend.catalog_auto_update:
            yield
            return
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
    _register_catalog_routes(app, pipeline, library, options)
    _register_image_routes(app, pipeline, config, options)
    _register_mosaic_routes(app, pipeline, library, mosaics, config, options)
    _register_library_routes(app, pipeline, library, mosaics)
    _register_bricklink_routes(
        app, config, mosaics, None if price_factory is None else price_factory(config)
    )

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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
        # Ungültige Parameter → 422, ungültiges Bild → 400, BrickLink → 503 bzw. 502,
        # sonst Verarbeitungsfehler → 500
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if isinstance(error, NotFoundError):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(error, ConfigError):
            code = status.HTTP_422_UNPROCESSABLE_CONTENT
        elif isinstance(error, InvalidImageError):
            code = status.HTTP_400_BAD_REQUEST
        elif isinstance(error, MissingCredentialsError):
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif isinstance(error, PriceSourceError):
            code = status.HTTP_502_BAD_GATEWAY
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
    app: FastAPI, pipeline: MosaicPipeline, library: Library, options: dict[str, FieldOption]
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
        palettes = tuple(palette.id for palette in library.palettes.list())
        presets = tuple(preset.id for preset in library.presets.list())
        return options_model(options, palettes, presets)

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


type FormField = Callable[..., Any]


def _form_field(options: dict[str, FieldOption]) -> FormField:
    """Baut Formularfelder mit Standardwert und erlaubten Werten aus ``options``."""

    def field(name: str, json_type: str, description: str, alias: str | None = None) -> Any:
        option = options[name]
        return Form(
            alias=alias,
            description=option.describe(description),
            json_schema_extra=option.schema(json_type),
        )

    return field


def _image_form(options: dict[str, FieldOption]) -> Callable[..., dict[str, object]]:
    """Formularfelder der Bildbearbeitung (für ``/mosaike`` und ``/bild/...``) als Abhängigkeit.

    FastAPI übernimmt die Felder einer Abhängigkeit in denselben Multipart-Body; so stehen
    sie nur einmal hier und gelten für alle drei Routen gleich.
    """
    field = _form_field(options)

    def image_fields(
        kontrast: Annotated[
            float | None, field("kontrast", "number", "Kontrast, 1.0 = unverändert")
        ] = None,
        saettigung: Annotated[
            float | None, field("saettigung", "number", "Sättigung, 1.0 = unverändert")
        ] = None,
        helligkeit: Annotated[
            float | None, field("helligkeit", "number", "Helligkeit, 1.0 = unverändert")
        ] = None,
        farbfilter: Annotated[
            str | None, field("farbfilter", "string", "Farbfilter vor dem Dithering")
        ] = None,
        filter_staerke: Annotated[
            float | None, field("filter_staerke", "number", "Stärke des Filters, 0 bis 1")
        ] = None,
        drehen: Annotated[
            int | None, field("drehen", "integer", "Drehen im Uhrzeigersinn in Grad")
        ] = None,
        spiegeln: Annotated[
            bool | None, field("spiegeln", "boolean", "Waagerecht spiegeln")
        ] = None,
        ausschnitt: Annotated[
            str | None,
            field(
                "ausschnitt", "string", "Ausschnitt x,y,Breite,Höhe in Prozent, z. B. 10,0,80,100"
            ),
        ] = None,
        hintergrund: Annotated[
            str | None, field("hintergrund", "string", "Was mit dem Hintergrund passiert")
        ] = None,
        hintergrundfarbe: Annotated[
            int | None,
            field("hintergrundfarbe", "integer", "Neue Farbe bei „entfernen“ (Rebrickable-ID)"),
        ] = None,
        hintergrund_toleranz: Annotated[
            float | None,
            field(
                "hintergrund_toleranz",
                "number",
                "So ähnlich (ΔE) muss ein Pixel der Randfarbe sein",
            ),
        ] = None,
        hintergrund_staerke: Annotated[
            float | None,
            field("hintergrund_staerke", "number", "Stärke für aufhellen, abdunkeln, …, 0 bis 1"),
        ] = None,
    ) -> dict[str, object]:
        return {
            "kontrast": kontrast,
            "saettigung": saettigung,
            "helligkeit": helligkeit,
            "farbfilter": farbfilter,
            "filter_staerke": filter_staerke,
            "drehen": drehen,
            "spiegeln": spiegeln,
            "ausschnitt": ausschnitt,
            "hintergrund": hintergrund,
            "hintergrundfarbe": hintergrundfarbe,
            "hintergrund_toleranz": hintergrund_toleranz,
            "hintergrund_staerke": hintergrund_staerke,
        }

    return image_fields


def _register_image_routes(
    app: FastAPI, pipeline: MosaicPipeline, config: AppConfig, options: dict[str, FieldOption]
) -> None:
    """``POST /bild/hintergrund`` und ``POST /bild/vorschau``: Bild vor dem Mosaik bearbeiten."""
    max_bytes = config.server.max_upload_mb * BYTES_PER_MB
    image_fields = _image_form(options)

    @app.post(
        "/bild/hintergrund",
        summary="Hintergrund erkennen",
        description="Erkennt den Hintergrund (häufigste Randfarbe, zusammenhängend vom Rand aus) "
        "des gedrehten und zugeschnittenen Bilds. Mit `hintergrund_toleranz` steuern, wie "
        "ähnlich ein Pixel sein muss. Danach z. B. `hintergrund=entfernen` und "
        "`hintergrundfarbe=lego_farbe.id` an `POST /mosaike` schicken.",
        responses=_errors(400, 413, 422),
        tags=[TAG_IMAGE],
    )
    def detect_background(
        bild: Annotated[UploadFile, File(description="Bilddatei (JPG, PNG, …)")],
        bearbeitung: Annotated[dict[str, object], Depends(image_fields)],
    ) -> BackgroundModel:
        data = _read_limited(bild, max_bytes)
        result = pipeline.detect_background(
            data, parse_overrides(override_input(bearbeitung)), max_px=MASK_PREVIEW_PX
        )
        red, green, blue = result.report.color
        mask = base64.b64encode(result.report.mask_png).decode("ascii")
        return BackgroundModel(
            farbe_hex=f"#{red:02X}{green:02X}{blue:02X}",
            anteil=round(result.report.share, 4),
            lego_farbe=_color_model(result.lego_color),
            maske_png=f"data:image/png;base64,{mask}",
        )

    @app.post(
        "/bild/vorschau",
        summary="Bearbeitetes Bild als PNG",
        description="Das Bild nach Drehen, Ausschnitt, Farbfilter, Helligkeit/Kontrast/Sättigung "
        "und Hintergrund, so wie es ins Dithering geht (noch nicht auf Noppen verkleinert).",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}, **_errors(400, 413, 422)},
        tags=[TAG_IMAGE],
    )
    def preview_image(
        bild: Annotated[UploadFile, File(description="Bilddatei (JPG, PNG, …)")],
        bearbeitung: Annotated[dict[str, object], Depends(image_fields)],
        groesse: Annotated[
            int,
            Form(
                ge=MIN_PREVIEW_PX,
                le=MAX_PREVIEW_PX,
                description=f"Längere Seite in Pixeln (Standard: {PREVIEW_PX})",
            ),
        ] = PREVIEW_PX,
    ) -> Response:
        data = _read_limited(bild, max_bytes)
        png = pipeline.edited_image(
            data, parse_overrides(override_input(bearbeitung)), max_px=groesse
        )
        return Response(png, media_type="image/png")


def _register_mosaic_routes(
    app: FastAPI,
    pipeline: MosaicPipeline,
    library: Library,
    mosaics: MosaicRepository,
    config: AppConfig,
    options: dict[str, FieldOption],
) -> None:
    """``/mosaike``, ``/mosaike/{id}``, Dateien, Raster und Bearbeiten."""
    max_bytes = config.server.max_upload_mb * BYTES_PER_MB
    field = _form_field(options)
    image_fields = _image_form(options)

    # Normales def (kein async def): FastAPI führt es im Threadpool aus, der Server
    # bleibt während der rechenintensiven Mosaikerzeugung ansprechbar
    @app.post(
        "/mosaike",
        status_code=status.HTTP_201_CREATED,
        summary="Mosaik erzeugen",
        responses=_errors(400, 404, 413, 422, 500),
        tags=[TAG_MOSAICS],
    )
    def create_mosaic(
        request: Request,
        bild: Annotated[UploadFile, File(description="Bilddatei (JPG, PNG, …)")],
        bearbeitung: Annotated[dict[str, object], Depends(image_fields)],
        preset: Annotated[
            str | None,
            Form(description="Gespeichertes Preset als Grundlage; angegebene Felder gehen vor"),
        ] = None,
        palette: Annotated[
            str | None, Form(description="Eigene Palette (Name oder ID, siehe /paletten)")
        ] = None,
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
        farb_ids: Annotated[
            str | None,
            field("farb_ids", "string", "Nur diese Farben (Rebrickable-IDs, z. B. 0,15,71)"),
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
        glaettung: Annotated[
            str | None,
            field(
                "glaettung",
                "string",
                "Glättung: aus, leicht, mittel (Mehrheitsfarbe je Noppe) oder stark",
            ),
        ] = None,
        zerlegung: Annotated[
            str | None, field("zerlegung", "string", "Zerlegungsverfahren")
        ] = None,
        ausgaben: Annotated[
            str | None,
            field(
                "ausgaben",
                "string",
                "Erzeugte Dateien (ergebnis.json und raster.json kommen immer dazu)",
            ),
        ] = None,
        titel: Annotated[
            str | None, field("titel", "string", "Name auf dem Titelblatt (Standard: Dateiname)")
        ] = None,
    ) -> MosaicCreatedModel:
        data = _read_limited(bild, max_bytes)
        values = bearbeitung | {
            "unterbau": unterbau,
            "plattengroesse": plattengroesse,
            "format": format_name,
            "platten": platten,
            "breite": breite,
            "hoehe": hoehe,
            "unterbaufarbe": unterbaufarbe,
            "rahmen": rahmen,
            "rahmenfarbe": rahmenfarbe,
            "eckenfarbe": eckenfarbe,
            "rahmen_teile": rahmen_teile,
            "rahmen_erhoehung": rahmen_erhoehung,
            "modus": modus,
            "teile": teile,
            "farbkategorien": farbkategorien,
            "exotische_farben": exotische_farben or None,  # False = nicht angegeben
            "max_farben": max_farben,
            "farb_ids": farb_ids,
            "stil": stil,
            "relief": relief,
            "relief_hoehe": relief_hoehe,
            "dithering": dithering,
            "staerke": staerke,
            "farbabstand": farbabstand,
            "aufraeumen": aufraeumen,
            "glaettung": glaettung,
            "zerlegung": zerlegung,
            "ausgaben": ausgaben,
            "titel": titel,
        }
        resolved = library.resolve(values, preset=preset, palette=palette)
        overrides = _with_required_outputs(parse_overrides(resolved.inputs), config)
        title = run_title(resolved.inputs, safe_name(Path(bild.filename or "mosaik").stem))
        with mosaics.new_output_dir() as output_dir:
            overrides = replace(overrides, output_dir=output_dir)
            result = pipeline.run(data, overrides, title=title, inputs=resolved.inputs)
            mosaics.store(result.target_dir)
            created = _created(request, result)
        if resolved.preset is not None:
            library.presets.record_use(resolved.preset.id)
        return created

    @app.get(
        "/mosaike/{mosaic_id}",
        summary="Inhalt von ergebnis.json",
        responses=_errors(404),
        tags=[TAG_MOSAICS],
    )
    def get_mosaic(mosaic_id: str) -> ResultModel:
        return ResultModel.model_validate_json(mosaics.read_bytes(mosaic_id, RESULT_FILE))

    @app.get(
        "/mosaike/{mosaic_id}/dateien/{file_name}",
        summary="Datei herunterladen",
        response_class=FileResponse,
        tags=[TAG_MOSAICS],
        responses={
            200: {"description": "Die Datei (PNG, PDF, LDraw, CSV, XML, JSON oder Originalbild)"},
            **_errors(404),
        },
    )
    def download_file(mosaic_id: str, file_name: str) -> Response:
        result = ResultModel.model_validate_json(mosaics.read_bytes(mosaic_id, RESULT_FILE))
        # Nur Namen aus ergebnis.json; der Pfad wird nie direkt aus der URL gebaut
        if file_name not in result.dateien:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Datei „{file_name}“ gibt es nicht.")
        stored = mosaics.open_file(mosaic_id, file_name)
        if stored.path is not None:
            return FileResponse(stored.path, filename=file_name)
        # Aus S3 gestreamt: dieselben Header wie FileResponse
        media_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        headers = {"Content-Disposition": f'attachment; filename="{file_name}"'}
        if stored.size is not None:
            headers["Content-Length"] = str(stored.size)
        return StreamingResponse(iter(stored.chunks), media_type=media_type, headers=headers)

    @app.get(
        "/mosaike/{mosaic_id}/raster",
        summary="Farbraster für einen Editor",
        description="Farbe jeder Noppe (Inhalt von `raster.json`). Zeilen und Spalten zählen "
        "ab 1 inklusive Rahmen, so wie `POST /mosaike/{id}/bearbeiten` sie erwartet.",
        responses=_errors(404),
        tags=[TAG_MOSAICS],
    )
    def get_raster(mosaic_id: str) -> RasterModel:
        data = _optional_file(
            mosaics, mosaic_id, RASTER_FILE, "Für dieses Mosaik gibt es kein Raster (raster_json)."
        )
        return RasterModel.model_validate_json(data)

    @app.post(
        "/mosaike/{mosaic_id}/bearbeiten",
        status_code=status.HTTP_201_CREATED,
        summary="Noppen und Flächen umfärben",
        description="Rechnet das Mosaik mit den Änderungen neu: Teile, Teilelisten und Anleitung "
        "passen danach zum Bild. Es entsteht ein neues Mosaik mit neuer ID (`basis` zeigt auf "
        "dieses); das alte bleibt. Änderungen sammeln sich, das neue Mosaik lässt sich also "
        "weiter bearbeiten. Erlaubt ist jede Farbe, die es für Teilarten und Stil als "
        "1×1-Teil gibt; der Rahmen bleibt fest.",
        responses=_errors(404, 422, 500),
        tags=[TAG_MOSAICS],
    )
    def edit_mosaic(
        request: Request, mosaic_id: str, aenderungen: EditRequestModel
    ) -> MosaicCreatedModel:
        if not mosaics.exists(mosaic_id):
            raise NotFoundError(f"Mosaik „{mosaic_id}“ gibt es nicht.")
        edits = _edits(aenderungen)
        if not edits:
            raise ConfigError("Bitte mindestens eine Änderung angeben.")
        with mosaics.workspace(mosaic_id) as directory, mosaics.new_output_dir() as output_dir:
            result = pipeline.edit(
                directory, edits, required_outputs=REQUIRED_OUTPUTS, output_dir=output_dir
            )
            mosaics.store(result.target_dir)
            return _created(request, result)


def _register_library_routes(
    app: FastAPI, pipeline: MosaicPipeline, library: Library, mosaics: MosaicRepository
) -> None:
    """``/paletten`` und ``/presets`` sowie ``POST /mosaike/{id}/preset``."""

    def palette_model(palette: ColorPalette) -> PaletteModel:
        known = pipeline.load_catalog().colors
        colors = [known[cid] for cid in palette.color_ids if cid in known]
        return PaletteModel(
            id=palette.id,
            name=palette.name,
            beschreibung=palette.description,
            farb_ids=list(palette.color_ids),
            farben=[RasterColorModel(id=c.id, name=c.name, hex=c.hex) for c in colors],
        )

    def check_preset(values: dict[str, object]) -> dict[str, str | int | float | bool]:
        return library.preset_settings(values, pipeline.check)

    @app.get("/paletten", summary="Eigene Farbpaletten", tags=[TAG_PALETTES])
    def list_palettes() -> list[PaletteModel]:
        return [palette_model(palette) for palette in library.palettes.list()]

    @app.post(
        "/paletten",
        status_code=status.HTTP_201_CREATED,
        summary="Palette anlegen",
        responses=_errors(422),
        tags=[TAG_PALETTES],
    )
    def create_palette(palette: PaletteInputModel) -> PaletteModel:
        pipeline.colors(palette.farb_ids)  # unbekannte IDs → 422
        created = library.palettes.create(palette.name, palette.beschreibung, palette.farb_ids)
        return palette_model(created)

    @app.get(
        "/paletten/{palette_id}",
        summary="Eine Palette",
        responses=_errors(404),
        tags=[TAG_PALETTES],
    )
    def get_palette(palette_id: str) -> PaletteModel:
        return palette_model(library.palettes.get(palette_id))

    @app.put(
        "/paletten/{palette_id}",
        summary="Palette ersetzen",
        responses=_errors(404, 422),
        tags=[TAG_PALETTES],
    )
    def update_palette(palette_id: str, palette: PaletteInputModel) -> PaletteModel:
        pipeline.colors(palette.farb_ids)
        updated = library.palettes.update(
            palette_id, palette.name, palette.beschreibung, palette.farb_ids
        )
        return palette_model(updated)

    @app.delete(
        "/paletten/{palette_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Palette löschen",
        responses=_errors(404),
        tags=[TAG_PALETTES],
    )
    def delete_palette(palette_id: str) -> None:
        library.palettes.delete(palette_id)

    @app.get("/presets", summary="Presets (Favoriten zuerst)", tags=[TAG_PRESETS])
    def list_presets(
        sortierung: Annotated[
            PresetOrder, Query(description="Nach Favoriten: bewertung, nutzung, name oder neu")
        ] = PresetOrder.RATING,
        favoriten: Annotated[bool, Query(description="Nur Favoriten")] = False,
    ) -> list[PresetModel]:
        presets = library.presets.list(sortierung, favorites_only=favoriten)
        return [_preset_model(preset) for preset in presets]

    @app.post(
        "/presets",
        status_code=status.HTTP_201_CREATED,
        summary="Preset anlegen",
        description="Die Einstellungen werden wie bei `POST /mosaike` geprüft.",
        responses=_errors(404, 422),
        tags=[TAG_PRESETS],
    )
    def create_preset(preset: PresetInputModel) -> PresetModel:
        created = library.presets.create(
            preset.name,
            preset.beschreibung,
            check_preset(dict(preset.einstellungen)),
            favorite=preset.favorit,
            rating=preset.bewertung,
        )
        return _preset_model(created)

    @app.get(
        "/presets/{preset_id}", summary="Ein Preset", responses=_errors(404), tags=[TAG_PRESETS]
    )
    def get_preset(preset_id: str) -> PresetModel:
        return _preset_model(library.presets.get(preset_id))

    @app.put(
        "/presets/{preset_id}",
        summary="Preset ersetzen",
        responses=_errors(404, 422),
        tags=[TAG_PRESETS],
    )
    def update_preset(preset_id: str, preset: PresetInputModel) -> PresetModel:
        updated = library.presets.update(
            preset_id,
            preset.name,
            preset.beschreibung,
            check_preset(dict(preset.einstellungen)),
            favorite=preset.favorit,
            rating=preset.bewertung,
        )
        return _preset_model(updated)

    @app.patch(
        "/presets/{preset_id}",
        summary="Favorit oder Bewertung setzen",
        responses=_errors(404, 422),
        tags=[TAG_PRESETS],
    )
    def mark_preset(preset_id: str, markierung: PresetMarkModel) -> PresetModel:
        marked = library.presets.mark(
            preset_id, favorite=markierung.favorit, rating=markierung.bewertung
        )
        return _preset_model(marked)

    @app.delete(
        "/presets/{preset_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Preset löschen",
        responses=_errors(404),
        tags=[TAG_PRESETS],
    )
    def delete_preset(preset_id: str) -> None:
        library.presets.delete(preset_id)

    @app.post(
        "/mosaike/{mosaic_id}/preset",
        status_code=status.HTTP_201_CREATED,
        summary="Einstellungen eines Mosaiks als Preset speichern",
        responses=_errors(404, 422),
        tags=[TAG_PRESETS],
    )
    def preset_from_mosaic(mosaic_id: str, preset: PresetFromMosaicModel) -> PresetModel:
        with mosaics.workspace(mosaic_id) as directory:
            values = form_values(pipeline.inputs_of(directory))
        values.pop("titel", None)
        created = library.presets.create(
            preset.name,
            preset.beschreibung,
            check_preset(values),
            favorite=preset.favorit,
            rating=preset.bewertung,
        )
        return _preset_model(created)


def _preset_model(preset: Preset) -> PresetModel:
    return PresetModel(
        id=preset.id,
        name=preset.name,
        beschreibung=preset.description,
        einstellungen=dict(preset.settings),
        favorit=preset.favorite,
        bewertung=preset.rating,
        nutzungen=preset.uses,
        erstellt=preset.created.isoformat(),
        geaendert=preset.changed.isoformat(),
    )


def _with_required_outputs(overrides: MosaicOverrides, config: AppConfig) -> MosaicOverrides:
    """``ergebnis.json`` und ``raster.json`` braucht die API immer (Dateiliste, Editor)."""
    outputs = overrides.outputs or config.outputs
    missing = tuple(name for name in REQUIRED_OUTPUTS if name not in outputs)
    return replace(overrides, outputs=(*outputs, *missing)) if missing else overrides


def _edits(request: EditRequestModel) -> list[GridEdit]:
    """Änderungen in der Reihenfolge noppen, flaechen, fuellen."""
    edits: list[GridEdit] = [StudEdit(e.zeile, e.spalte, e.farbe_id) for e in request.noppen]
    edits += [AreaEdit(e.zeile, e.spalte, e.breite, e.hoehe, e.farbe_id) for e in request.flaechen]
    edits += [FillEdit(e.zeile, e.spalte, e.farbe_id) for e in request.fuellen]
    return edits


def _created(request: Request, result: MosaicResult) -> MosaicCreatedModel:
    """Antwort mit Statistik und absoluten Download-URLs aus ``ergebnis.json``."""
    content = ResultModel.model_validate_json((result.target_dir / RESULT_FILE).read_bytes())
    base_url = str(request.base_url).rstrip("/")
    urls = {name: f"{base_url}/mosaike/{result.id}/dateien/{name}" for name in content.dateien}
    return MosaicCreatedModel(
        id=result.id, statistik=content.statistik, dateien=urls, basis=content.basis
    )


def _register_bricklink_routes(
    app: FastAPI, config: AppConfig, mosaics: MosaicRepository, prices: PriceService | None
) -> None:
    """``/mosaike/{id}/preise`` (nur mit Preisdienst) und ``/mosaike/{id}/bricklink``."""

    if prices is not None:

        @app.post(
            "/mosaike/{mosaic_id}/preise",
            summary="Preis bei BrickLink schätzen",
            description="Fragt fehlende oder veraltete Preise beim BrickLink Price Guide ab "
            "(Verkäufe der letzten 6 Monate und aktuelle Angebote) und speichert "
            "`preise.json`, `preise_bricklink.csv` und die Wanted List im Mosaik.",
            responses=_errors(404, 422, 502, 503),
            tags=[TAG_BRICKLINK],
        )
        def estimate_prices(
            mosaic_id: str,
            offline: Annotated[
                bool, Query(description="Nur gespeicherte Preise, keine Abfrage")
            ] = False,
            erzwingen: Annotated[
                bool, Query(description="Alle Preise neu abfragen, auch aktuelle")
            ] = False,
        ) -> PriceEstimateModel:
            with mosaics.workspace(mosaic_id, write=True) as directory:
                prices.estimate(directory, offline=offline, force=erzwingen)
                return _prices(directory)

    @app.get(
        "/mosaike/{mosaic_id}/preise",
        summary="Gespeicherte Preisschätzung",
        responses=_errors(404),
        tags=[TAG_BRICKLINK],
    )
    def get_prices(mosaic_id: str) -> PriceEstimateModel:
        data = _optional_file(
            mosaics, mosaic_id, PRICES_FILE, "Für dieses Mosaik gibt es noch keine Preise."
        )
        return PriceEstimateModel.model_validate_json(data)

    @app.get(
        "/mosaike/{mosaic_id}/bricklink",
        summary="Wanted List für BrickLink",
        description="Die BrickLink-XML zum Einfügen auf der Upload-Seite (`upload_url`). "
        "Die BrickLink-API selbst kann keine Wanted Lists anlegen.",
        responses=_errors(404, 422),
        tags=[TAG_BRICKLINK],
    )
    def get_bricklink_export(mosaic_id: str) -> BrickLinkExportModel:
        with mosaics.workspace(mosaic_id) as directory:
            items = read_order_items(directory)
            path = directory / BRICKLINK_FILE_NAME
            # Die Datei enthält nach einer Preisschätzung auch Höchstpreise
            if path.is_file():
                xml = path.read_text(encoding="utf-8")
            else:
                xml, _ = bricklink_xml(items, condition=config.pricing.condition)
        quantities = [
            int(item.findtext("MINQTY") or 0) for item in ElementTree.fromstring(xml).iter("ITEM")
        ]
        skipped = sorted({item.color_name for item in items if item.bricklink_color is None})
        return BrickLinkExportModel(
            xml=xml,
            upload_url=config.pricing.upload_url,
            positionen=len(quantities),
            teile=sum(quantities),
            ausgelassen=skipped,
        )


def _prices(directory: Path) -> PriceEstimateModel:
    return PriceEstimateModel.model_validate_json((directory / PRICES_FILE).read_bytes())


def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Liest den Upload, höchstens ``max_bytes``; größere Dateien ergeben 413."""
    data = upload.file.read(max_bytes + 1)  # ein Byte mehr, um Überschreitung zu erkennen
    if len(data) > max_bytes:
        limit_mb = max_bytes // BYTES_PER_MB
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"Die Datei ist größer als {limit_mb} MB."
        )
    return data


def _optional_file(
    mosaics: MosaicRepository, mosaic_id: str, file_name: str, missing: str
) -> bytes:
    """Datei, die nicht jedes Mosaik hat: unbekanntes Mosaik und fehlende Datei → 404."""
    if not mosaics.exists(mosaic_id):
        raise NotFoundError(f"Mosaik „{mosaic_id}“ gibt es nicht.")
    try:
        return mosaics.read_bytes(mosaic_id, file_name)
    except NotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, missing) from None
