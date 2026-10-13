"""Strukturierte Logs: eine JSON-Zeile pro Eintrag, mit Trace-ID für die Korrelation.

Log-Sammler (Loki, Elasticsearch, Cloud Logging) lesen die Felder direkt; ``trace_id``
und ``span_id`` verbinden Logzeilen mit Traces (z. B. in Grafana Tempo).
"""

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from opentelemetry import trace

# Felder, die jeder LogRecord hat; alles andere kam über ``extra=`` und wird mitgeschrieben
_STANDARD_FIELDS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formatiert einen LogRecord als JSON-Objekt."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "zeit": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "nachricht": record.getMessage(),
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            entry["trace_id"] = format(context.trace_id, "032x")
            entry["span_id"] = format(context.span_id, "016x")
        if record.exc_info:
            entry["fehler"] = self.formatException(record.exc_info)
        entry |= {key: value for key, value in vars(record).items() if key not in _STANDARD_FIELDS}
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str, log_format: Literal["json", "text"]) -> None:
    """Ein Handler auf dem Root-Logger; uvicorn loggt über denselben Weg."""
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
