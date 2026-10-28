# Container der Cloud-Variante (lego-mosaik-cloud). Die lokale App braucht ihn nicht.
#
#   docker build -t lego-mosaik:dev --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
#
# Mehrstufig: uv baut eine virtuelle Umgebung, das Laufzeit-Image enthält nur sie,
# die Katalog-Stammdaten (daten/*.csv) und Schriften für die Anleitung.

ARG PYTHON_VERSION=3.15

# --- Build ------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS build

# Compiler für Pakete ohne Wheels für Python 3.15 (z. B. scikit-image); nur in dieser Stufe
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /src
# Erst nur die Abhängigkeiten (gecachte Schicht, solange uv.lock gleich bleibt)
COPY pyproject.toml uv.lock README.md ./
COPY cloud/pyproject.toml cloud/README.md cloud/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package lego-mosaik-cloud

# Dann der Code von Kern und Cloud-Paket
COPY src src
COPY cloud/src cloud/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package lego-mosaik-cloud

# --- Laufzeit ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

# DejaVu: Schrift für Titelblatt und Anleitung (siehe InstructionsConfig.font_paths)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ARG GIT_SHA=unbekannt
ARG BUILD_TIME=""
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LEGO_MOSAIK_DATA_DIR=/app/daten \
    LEGO_MOSAIK_MOSAIC_DIR=/var/lib/lego-mosaik \
    LEGO_MOSAIK_GIT_SHA=${GIT_SHA} \
    LEGO_MOSAIK_BUILD_TIME=${BUILD_TIME}

RUN groupadd --system --gid 10001 mosaik \
    && useradd --system --uid 10001 --gid mosaik --home-dir /app --no-create-home mosaik \
    && install -d -o 10001 -g 10001 /var/lib/lego-mosaik

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY daten/teil_geometrie.csv daten/farb_zuordnung.csv /app/daten/

LABEL org.opencontainers.image.title="lego-mosaik" \
      org.opencontainers.image.description="LEGO-Mosaik-Service (Cloud-Variante)" \
      org.opencontainers.image.source="https://github.com/LevinTheDoctor/lego-mosaik" \
      org.opencontainers.image.revision="${GIT_SHA}"

USER 10001:10001
EXPOSE 8080
# Schreibt nur nach /tmp und ins Mosaik-Volume unter /var/lib/lego-mosaik:
# readOnlyRootFilesystem funktioniert mit einem emptyDir auf /tmp und einem PVC
ENTRYPOINT ["lego-mosaik-cloud"]
CMD ["server"]
