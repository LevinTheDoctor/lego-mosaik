"""Verteiltes Tracing mit OpenTelemetry (OTLP).

Aktiv, sobald ``OTEL_EXPORTER_OTLP_ENDPOINT`` gesetzt ist (z. B. auf einen OTel Collector,
Grafana Tempo oder Jaeger). Weitere Standard-Variablen wie ``OTEL_TRACES_SAMPLER`` wirken
direkt. Instrumentiert werden HTTP-Server, Datenbank, S3 und ausgehende HTTP-Aufrufe.
"""

import logging
import os
from importlib.metadata import version

from fastapi import FastAPI
from sqlalchemy import Engine

logger = logging.getLogger(__name__)

ENDPOINT_VARIABLES = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
EXCLUDED_URLS = "actuator/.*"  # Probes und Scrapes erzeugen keine Traces


def tracing_enabled() -> bool:
    """True, wenn ein OTLP-Endpunkt konfiguriert ist."""
    return any(os.environ.get(name) for name in ENDPOINT_VARIABLES)


def configure_tracing(app: FastAPI, engine: Engine, service_name: str) -> bool:
    """Richtet Tracer-Provider und Instrumentierung ein.

    Returns:
        True, wenn Tracing aktiv ist.
    """
    if not tracing_enabled():
        logger.info("Tracing aus (OTEL_EXPORTER_OTLP_ENDPOINT nicht gesetzt).")
        return False
    # Importe erst hier: ohne Tracing bleiben Start und Speicherbedarf kleiner
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {"service.name": service_name, "service.version": version("lego-mosaik-cloud")}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)
    SQLAlchemyInstrumentor().instrument(engine=engine)
    BotocoreInstrumentor().instrument()  # type: ignore[no-untyped-call]
    HTTPXClientInstrumentor().instrument()
    logger.info("Tracing an (OTLP).")
    return True
