"""Actuator-Endpunkte wie bei Spring Boot unter ``/actuator``.

| Endpunkt | Zweck |
|---|---|
| ``/actuator`` | Links auf alle Endpunkte |
| ``/actuator/health`` | Gesamtstatus mit Komponenten (``db``, ``s3``, ``katalog``, …) |
| ``/actuator/health/liveness`` | Prozess lebt (livenessProbe) |
| ``/actuator/health/readiness`` | Abhängigkeiten erreichbar (readinessProbe) |
| ``/actuator/info`` | Version, Build, Python, Katalogstand |
| ``/actuator/metrics[/{name}]`` | Metriken als JSON |
| ``/actuator/prometheus`` | Metriken im Prometheus-Format |
| ``/actuator/loggers[/{name}]`` | Log-Level lesen und zur Laufzeit ändern (POST) |

Antworten folgen dem Format von Spring Boot, damit vorhandene Werkzeuge (z. B. Spring Boot
Admin, Dashboards) sie lesen können. Die Endpunkte stehen nicht in der OpenAPI-Beschreibung.
"""

import logging
import platform
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import version
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from lego_mosaik_cloud.observability.metrics import Metrics

ACTUATOR = "/actuator"
LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")


class Status(StrEnum):
    """Status einer Komponente (wie ``org.springframework.boot.actuate.health.Status``)."""

    UP = "UP"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


@dataclass(frozen=True, slots=True)
class Health:
    """Ergebnis einer Prüfung."""

    status: Status
    details: Mapping[str, object] = field(default_factory=dict)


type HealthIndicator = Callable[[], Health]


@dataclass(frozen=True, slots=True)
class HealthGroup:
    """Komponenten, die zusammen den Status einer Gruppe ergeben.

    Attributes:
        indicators: Name → Prüfung.
        readiness: Namen, die für ``readiness`` zählen (z. B. nicht der Katalog, damit
            Pods schon vor dem ersten Import bereit sind und den Import-Job annehmen).
    """

    indicators: Mapping[str, HealthIndicator]
    readiness: frozenset[str]


def check(indicator: HealthIndicator) -> Health:
    """Führt eine Prüfung aus; Exceptions ergeben ``DOWN`` mit Fehlermeldung."""
    start = time.perf_counter()
    try:
        health = indicator()
    except Exception as error:  # jede Störung ist ein DOWN, kein 500
        health = Health(Status.DOWN, {"fehler": f"{type(error).__name__}: {error}"})
    millis = round((time.perf_counter() - start) * 1000, 1)
    return Health(health.status, {**health.details, "dauer_ms": millis})


def aggregate(results: Mapping[str, Health]) -> Status:
    """Schlechtester Status gewinnt; ``UNKNOWN`` zählt als ``UP``."""
    statuses = {health.status for health in results.values()}
    for worst in (Status.DOWN, Status.OUT_OF_SERVICE):
        if worst in statuses:
            return worst
    return Status.UP


def _health_response(results: Mapping[str, Health], *, details: bool = True) -> JSONResponse:
    overall = aggregate(results)
    body: dict[str, Any] = {"status": overall.value}
    if details:
        body["components"] = {
            name: {"status": health.status.value, "details": dict(health.details)}
            for name, health in results.items()
        }
    code = status.HTTP_200_OK if overall is Status.UP else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(body, status_code=code)


def actuator_router(
    health: HealthGroup,
    metrics: Metrics,
    info: Callable[[], Mapping[str, object]],
) -> APIRouter:
    """Baut den Router mit allen Actuator-Endpunkten."""
    router = APIRouter(prefix=ACTUATOR, include_in_schema=False)

    @router.get("")
    def links(request: Request) -> dict[str, object]:
        base = str(request.base_url).rstrip("/") + ACTUATOR
        names = (
            "health",
            "health/liveness",
            "health/readiness",
            "info",
            "metrics",
            "prometheus",
            "loggers",
        )
        return {
            "_links": {
                "self": {"href": base, "templated": False},
                **{name: {"href": f"{base}/{name}", "templated": False} for name in names},
            }
        }

    @router.get("/health")
    def get_health() -> JSONResponse:
        return _health_response({name: check(ind) for name, ind in health.indicators.items()})

    @router.get("/health/liveness")
    def liveness() -> JSONResponse:
        # Nur der Prozess selbst: hängende Abhängigkeiten sollen keinen Neustart auslösen
        return _health_response({"ping": Health(Status.UP)}, details=False)

    @router.get("/health/readiness")
    def readiness() -> JSONResponse:
        results = {
            name: check(indicator)
            for name, indicator in health.indicators.items()
            if name in health.readiness
        }
        return _health_response(results)

    @router.get("/health/{component}")
    def component_health(component: str) -> JSONResponse:
        indicator = health.indicators.get(component)
        if indicator is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Komponente „{component}“ gibt es nicht."
            )
        result = check(indicator)
        code = 200 if result.status in (Status.UP, Status.UNKNOWN) else 503
        body = {"status": result.status.value, "details": dict(result.details)}
        return JSONResponse(body, status_code=code)

    @router.get("/info")
    def get_info() -> dict[str, object]:
        return {
            "app": {"name": "lego-mosaik", "version": version("lego-mosaik-cloud")},
            "kern": {"version": version("lego-mosaik")},
            "python": {"version": platform.python_version()},
            **info(),
        }

    @router.get("/prometheus")
    def prometheus() -> Response:
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @router.get("/metrics")
    def metric_names() -> dict[str, list[str]]:
        return {"names": sorted(family.name for family in metrics.registry.collect())}

    @router.get("/metrics/{name}")
    def metric(name: str) -> dict[str, object]:
        return _metric_details(metrics, name)

    @router.get("/loggers")
    def loggers() -> dict[str, object]:
        names = ["ROOT", *sorted(logging.root.manager.loggerDict)]
        return {"levels": list(LOG_LEVELS), "loggers": {n: _logger_levels(n) for n in names}}

    @router.get("/loggers/{name}")
    def get_logger(name: str) -> dict[str, str | None]:
        return _logger_levels(name)

    @router.post("/loggers/{name}", status_code=status.HTTP_204_NO_CONTENT)
    def set_logger(
        name: str,
        configured_level: Annotated[str | None, Body(alias="configuredLevel", embed=True)] = None,
    ) -> None:
        level = None if configured_level is None else configured_level.upper()
        if level is not None and level not in LOG_LEVELS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Unbekanntes Log-Level „{configured_level}“. Gültig: {', '.join(LOG_LEVELS)}.",
            )
        target = logging.getLogger(None if name == "ROOT" else name)
        target.setLevel(level or logging.NOTSET)

    return router


def _logger_levels(name: str) -> dict[str, str | None]:
    target = logging.getLogger(None if name == "ROOT" else name)
    configured = logging.getLevelName(target.level) if target.level else None
    return {
        "configuredLevel": configured,
        "effectiveLevel": logging.getLevelName(target.getEffectiveLevel()),
    }


def _metric_details(metrics: Metrics, name: str) -> dict[str, object]:
    """Eine Metrik im Format von ``/actuator/metrics/{name}`` (Summen über alle Tags)."""
    for family in metrics.registry.collect():
        if family.name != name:
            continue
        measurements: dict[str, float] = {}
        tags: dict[str, set[str]] = {}
        for sample in family.samples:
            statistic = _statistic(family.name, sample.name)
            if statistic is None:
                continue
            measurements[statistic] = measurements.get(statistic, 0.0) + sample.value
            for key, value in sample.labels.items():
                if key != "le":
                    tags.setdefault(key, set()).add(value)
        return {
            "name": family.name,
            "description": family.documentation,
            "baseUnit": family.unit or None,
            "measurements": [{"statistic": k, "value": v} for k, v in measurements.items()],
            "availableTags": [{"tag": k, "values": sorted(v)} for k, v in sorted(tags.items())],
        }
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"Metrik „{name}“ gibt es nicht.")


def _statistic(family: str, sample: str) -> str | None:
    """Spring-Statistik zu einem Prometheus-Sample (Buckets und Zeitstempel fallen weg)."""
    suffixes = {"_count": "COUNT", "_sum": "TOTAL_TIME", "_total": "COUNT"}
    if sample == family:
        return "VALUE"
    for suffix, statistic in suffixes.items():
        if sample == family + suffix:
            return statistic
    return None
