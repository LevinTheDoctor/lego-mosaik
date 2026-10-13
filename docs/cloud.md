# Cloud-Variante (`lego-mosaik-cloud`)

Die Cloud-Variante ist dieselbe Anwendung wie lokal – gleiche Pipeline, gleiche HTTP-API
(siehe `api-frontend.md`) – nur mit anderen Speichern und mit Observability:

| Lokal (`lego-mosaik`) | Cloud (`lego-mosaik-cloud`) |
|---|---|
| Rebrickable-CSV-Dumps in `daten/rebrickable/` | Teilekatalog in PostgreSQL (Tabellen `farben`, `teiltypen`, `verfuegbar`) |
| `daten/paletten.json`, `daten/presets.json` | PostgreSQL (`paletten`, `presets`) |
| `daten/bricklink/preise.json` | PostgreSQL (`preis_cache`) |
| Mosaik-Ordner in `ausgabe/<id>/` | Dateien in Garage (S3) unter `mosaike/<id>/`, Metadaten in `mosaike` |
| Katalog-Update im Server-Prozess | Job/CronJob `katalog-import` |
| – | `/actuator/*` (Health, Info, Metrics, Prometheus, Loggers), JSON-Logs, OpenTelemetry |

## Aufbau im Repository

```
src/lego_mosaik/          Kern (lokale App, CLI, API) – ohne Cloud-Abhängigkeiten
cloud/                    uv-Workspace-Paket lego-mosaik-cloud
  src/lego_mosaik_cloud/
    app.py                Composition Root: Kern-API + PostgreSQL + S3 + Actuator
    catalog/              Katalog-Import (nutzt den CSV-Parser des Kerns) und PostgreSQL-Quelle
    storage/              Paletten/Presets (ItemTable), Preis-Cache, Mosaike in S3
    observability/        Actuator, Prometheus-Metriken, JSON-Logs, Tracing
    db/                   Schema und Alembic-Migrationen
    garage_init.py        Garage einrichten (Layout, Key, Bucket)
    cli.py                server | migrieren | katalog-import | uebernehmen | garage-init
Dockerfile, compose.yaml  Container und lokale Cloud-Umgebung
deploy/helm/lego-mosaik/  Helm-Chart mit Subcharts postgresql und garage
```

Der Kern kennt nur Schnittstellen (`storage/protocol.py`: `MosaicRepository`, `ItemTable`,
`PaletteRepository`, `PresetRepository`; `pricing/protocol.py`: `PriceCacheProtocol`;
`sources/protocol.py`: `PartCatalogSource`, `CatalogUpdaterProtocol`). Lokal stecken
Dateien dahinter, in der Cloud PostgreSQL und S3. So lassen sich beide Varianten parallel
weiterentwickeln: Neue Funktionen entstehen im Kern und laufen in beiden.

## Entwickeln

```bash
uv sync --all-packages                  # Kern + Cloud-Paket in einer .venv
uv run pytest                           # Tests des Kerns
cd cloud && uv run pytest               # Cloud-Tests (PostgreSQL per testcontainers → Docker nötig)
cd cloud && uv run mypy && uv run ruff check
```

Ohne Docker überspringen die Datenbank-Tests sich selbst. Eine vorhandene Datenbank geht auch:
`LEGO_MOSAIK_TEST_DATABASE_URL=postgresql+psycopg://…`.

Hinweis: Ein einfaches `uv sync` im Hauptordner entfernt die Cloud-Abhängigkeiten wieder aus
der `.venv`; deshalb `--all-packages`.

### Lokal mit Docker Compose

```bash
docker compose up --build -d                    # PostgreSQL, Garage, garage-init, App
docker compose run --rm app katalog-import      # einmalig: Teilekatalog laden (einige Minuten)
curl localhost:8080/actuator/health
open http://localhost:8080/docs
```

Vorhandene lokale Daten übernehmen (Paletten, Presets und Mosaike aus `ausgabe/`):

```bash
docker compose run --rm -v "$PWD/daten:/daten:ro" -v "$PWD/ausgabe:/ausgabe:ro" \
  app uebernehmen --daten /daten --ausgabe /ausgabe
```

## Einstellungen (Umgebungsvariablen)

Alle mit Präfix `LEGO_MOSAIK_` (siehe `cloud/src/lego_mosaik_cloud/settings.py`):

| Variable | Standard | Bedeutung |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://lego:lego@localhost:5432/lego_mosaik` | SQLAlchemy-URL |
| `S3_ENDPOINT` | `http://localhost:3900` | Garage-S3-Endpunkt |
| `S3_BUCKET` / `S3_PREFIX` / `S3_REGION` | `lego-mosaik` / `mosaike` / `garage` | Ablage der Mosaike |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | – | S3-Zugang (Garage: `GK` + 24 Hex / 64 Hex) |
| `PORT` | `8080` | HTTP-Port |
| `MAX_UPLOAD_MB` | `20` | Größte Bilddatei |
| `CORS_ORIGINS` / `CORS_ORIGIN_REGEX` | – | Frontends im Browser |
| `CATALOG_REFRESH_SECONDS` | `60` | So oft prüft ein Pod, ob ein neuer Katalog importiert wurde |
| `STUDIO_COLOR_FILE` | – | Optional `StudioColorDefinition.txt` (BrickLink-Farbcodes) |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` | Logs (`json` oder `text`) |
| `GIT_SHA` / `BUILD_TIME` | aus dem Image | erscheinen in `/actuator/info` |

Tracing: `OTEL_EXPORTER_OTLP_ENDPOINT` (z. B. `http://otel-collector:4318`) schaltet
OpenTelemetry ein; alle `OTEL_*`-Standardvariablen wirken. BrickLink-Zugangsdaten für
Preise wie lokal über `BRICKLINK_*` (im Chart per `config.extraEnv`/`extraEnvFrom`).

## Observability (wie Spring Boot Actuator)

| Endpunkt | Inhalt |
|---|---|
| `GET /actuator` | Links auf alle Endpunkte |
| `GET /actuator/health` | Gesamtstatus, Komponenten `db`, `s3`, `katalog` mit Details und Dauer |
| `GET /actuator/health/liveness` | Prozess lebt (livenessProbe) |
| `GET /actuator/health/readiness` | `db` und `s3` erreichbar (readinessProbe); 503 sonst |
| `GET /actuator/health/{komponente}` | Eine Komponente |
| `GET /actuator/info` | Version, Git-SHA, Build-Zeit, Python, geladener Katalog |
| `GET /actuator/metrics[/{name}]` | Metriken als JSON (Spring-Format) |
| `GET /actuator/prometheus` | Prometheus-Format |
| `GET/POST /actuator/loggers[/{name}]` | Log-Level lesen/setzen: `{"configuredLevel": "DEBUG"}` |

Der Katalog zählt nicht zur Readiness: Pods werden auch vor dem ersten Import bereit
(`katalog: UNKNOWN`), sonst würde `helm install --wait` nie fertig.

Metriken:

- `http_server_requests_seconds{method,uri,status,outcome}` – wie Micrometer, `uri` ist das Routen-Template
- `lego_mosaik_mosaike_erzeugt_total{art="neu"|"bearbeitet"}`
- `lego_mosaik_pipeline_schritt_seconds{schritt}` – Dauer jedes Pipeline-Schritts
- `lego_mosaik_mosaik_teile` – Teile pro Mosaik
- `lego_mosaik_katalog_stand_timestamp_seconds`
- Prozess- und GC-Metriken von `prometheus_client`

## Container

```bash
docker build -t lego-mosaik:dev \
  --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
  --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) .
# Für gemischte Cluster (z. B. Raspberry Pi als Worker):
docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/<user>/lego-mosaik:0.1.0 --push .
```

Das Image läuft als UID 10001, schreibt nur nach `/tmp` und verträgt `readOnlyRootFilesystem`.

## Kubernetes (Helm)

```bash
helm install lego deploy/helm/lego-mosaik -n lego-mosaik --create-namespace \
  --set image.repository=ghcr.io/<user>/lego-mosaik --set image.tag=0.1.0
kubectl -n lego-mosaik get pods,jobs
helm test lego -n lego-mosaik
kubectl -n lego-mosaik port-forward svc/lego-lego-mosaik 8080:80
```

Was das Chart anlegt:

- **App** (Deployment, 2 Replicas): initContainer `migrieren` (Alembic mit Advisory-Lock,
  parallel startende Pods warten aufeinander), Startup-/Liveness-/Readiness-Probes auf
  `/actuator/health/*`, Prometheus-Annotationen, PodDisruptionBudget, optional HPA,
  Ingress und ServiceMonitor.
- **Job `garage-init`**: Layout zuweisen, S3-Key importieren, Bucket anlegen (idempotent).
  Key-ID und Secret erzeugt das Chart einmalig (Secret `<release>-lego-mosaik-s3`).
- **Job und CronJob `katalog-import`**: lädt die Rebrickable-Dumps und schreibt den Katalog.
- **Subchart `postgresql`**: StatefulSet `postgres:18-alpine` mit PVC; Passwort wird einmalig
  erzeugt und bei Upgrades beibehalten. Abschalten mit `postgresql.enabled=false` und
  `externalDatabase.url` bzw. `externalDatabase.existingSecret`.
- **Subchart `garage`**: StatefulSet `dxflrs/garage:v2.4.1` mit PVCs für Metadaten und Daten;
  Knoten finden sich über die Kubernetes-API. Mehrere Knoten: `garage.replicaCount=3`,
  `garage.replicationFactor=3`. Abschalten mit `garage.enabled=false` und `s3.endpoint`,
  `s3.existingSecret`.

Secrets mit `helm.sh/resource-policy: keep` (Datenbank-Passwort, Garage, S3-Key) bleiben
bei `helm uninstall` erhalten, damit sie zu den Volumes passen. Für eine komplette
Neuinstallation Secrets und PVCs von Hand löschen.

Wichtig: Die API hat keine Anmeldung. Ingress ist deshalb standardmäßig aus; vor dem
Öffnen eine Authentifizierung vorschalten (z. B. oauth2-proxy).

## Bekannte Grenzen

- Mosaike entstehen synchron in der Anfrage (Sekunden bis Minuten); der Ingress braucht
  passende Timeouts (im Chart gesetzt). Eine Job-Queue wäre der nächste Schritt.
- `bearbeiten`, Preise und BrickLink-Export laden die Dateien eines Mosaiks in einen
  Temp-Ordner (`/tmp`, im Chart `tmpVolume.sizeLimit`).
