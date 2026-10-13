"""Prometheus-Metriken (wie Micrometer bei Spring Boot).

Namen folgen den Spring-Konventionen, damit vorhandene Dashboards passen:
``http_server_requests_seconds{method, uri, status, outcome}`` mit dem Routen-Template
als ``uri`` (``/mosaike/{mosaic_id}`` statt jeder einzelnen ID).
"""

import time
from collections.abc import Callable

from lego_mosaik.pipeline import MosaicResult
from opentelemetry import trace
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Mosaike dauern Sekunden bis Minuten: Buckets bis 10 Minuten
DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)
UNKNOWN_URI = "UNKNOWN"


class Metrics:
    """Alle Metriken der Anwendung in einer Registry.

    Args:
        registry: Prometheus-Registry (Tests: eigene, sonst die globale mit Prozessmetriken).
    """

    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.registry = registry
        self.http_requests = Histogram(
            "http_server_requests_seconds",
            "Dauer der HTTP-Anfragen",
            ("method", "uri", "status", "outcome"),
            buckets=DURATION_BUCKETS,
            registry=registry,
        )
        self.mosaics = Counter(
            "lego_mosaik_mosaike_erzeugt",
            "Erzeugte Mosaike (neu oder durch Bearbeiten)",
            ("art",),
            registry=registry,
        )
        self.steps = Histogram(
            "lego_mosaik_pipeline_schritt_seconds",
            "Dauer der Pipeline-Schritte",
            ("schritt",),
            buckets=DURATION_BUCKETS,
            registry=registry,
        )
        self.parts = Histogram(
            "lego_mosaik_mosaik_teile",
            "Anzahl Teile pro Mosaik",
            buckets=(100, 500, 1000, 2500, 5000, 10000, 25000, 50000),
            registry=registry,
        )
        self.catalog_imported = Gauge(
            "lego_mosaik_katalog_stand_timestamp_seconds",
            "Zeitpunkt des geladenen Teilekatalogs (Unix-Zeit)",
            registry=registry,
        )

    def observe_result(self, result: MosaicResult) -> None:
        """Observer für die Pipeline: zählt Mosaike und misst die Schritte."""
        kind = "bearbeitet" if result.mosaic.metadata.get("basis") else "neu"
        self.mosaics.labels(art=kind).inc()
        durations = result.stats.get("dauer_sekunden", {})
        span = trace.get_current_span()
        if isinstance(durations, dict):
            for step, seconds in durations.items():
                if isinstance(seconds, int | float):
                    self.steps.labels(schritt=str(step)).observe(seconds)
                    span.add_event("pipeline.schritt", {"schritt": str(step), "sekunden": seconds})
        parts = result.stats.get("teile")
        if isinstance(parts, int):
            self.parts.observe(parts)
        span.set_attribute("mosaik.id", result.id)


def outcome(status: int) -> str:
    """Spring-``outcome`` zum Statuscode."""
    if status < 200:
        return "INFORMATIONAL"
    if status < 300:
        return "SUCCESS"
    if status < 400:
        return "REDIRECTION"
    return "CLIENT_ERROR" if status < 500 else "SERVER_ERROR"


class HttpMetricsMiddleware:
    """ASGI-Middleware: misst jede HTTP-Anfrage.

    Das Routen-Template steht nach dem Routing in ``scope["route"]`` (setzt FastAPI);
    da ``scope`` geteilt wird, ist es hier nach dem Aufruf lesbar.
    """

    def __init__(
        self, app: ASGIApp, metrics: Metrics, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self.app = app
        self.metrics = metrics
        self.clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = self.clock()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            uri = getattr(route, "path", None) or UNKNOWN_URI
            self.metrics.http_requests.labels(
                method=scope["method"], uri=uri, status=str(status), outcome=outcome(status)
            ).observe(self.clock() - start)
