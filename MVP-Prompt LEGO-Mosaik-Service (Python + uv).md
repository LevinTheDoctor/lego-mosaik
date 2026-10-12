# MVP-Prompt: LEGO-Mosaik-Service (Python + uv)

Oct 12, 2026 · @Levin Rüßmann

## So nutzt du diesen Prompt

Kopiere alles ab „Rolle und Ziel“ in einen Coding-Agenten (z. B. Claude Code im Terminal) oder lege es als `PROMPT.md` ins leere Projektverzeichnis und verweise darauf. Werte in der Konfiguration (Größe, Teiltypen, Ausgaben) kannst du vorher anpassen. Annahmen, die du prüfen solltest, sind im Text markiert.

## Rolle und Ziel

Du bist ein erfahrener Python-Entwickler und Software-Architekt. Baue mit mir den MVP eines **lokal laufenden LEGO-Mosaik-Service** ohne Frontend.

Der Service macht aus einem Bild ein baubares LEGO-Mosaik:

1. Bild laden und auf eine Zielgröße in Noppen skalieren (z. B. 48 × 48).
2. Farben per Dithering auf eine Palette echter LEGO-Farben reduzieren.
3. Die Palette ergibt sich aus Teilen, die es in diesen Farben tatsächlich gibt (Daten aus Rebrickable-CSV-Dumps).
4. Gleichfarbige Pixel zu möglichst wenigen, real existierenden Teilen zusammenfassen (Plates, Tiles, Bricks in 1×N und 2×N).
5. Ausgaben erzeugen: Vorschau-PNG, LDraw-Datei mit Bauschritten für Bricklink Studio, Teileliste und eine einfache Bauanleitung.

Bedienung über eine **deutsche CLI** und optional über einen **lokalen FastAPI-Endpunkt**. Beide nutzen dieselbe Pipeline. Konfiguriert wird zentral in `main.py`.

## Kontext, Annahmen und Nicht-Ziele

**Kontext**

- Ich bin dualer Student (Informatik, Anwendungsentwicklung). Der Code soll lehrreich und sauber sein.
- Dieser MVP ist ein Prototyp. Später wird er ggf. nach Spring Boot oder Rust portiert und die CSV-Quelle durch PostgreSQL ersetzt. Deshalb: klare Schnittstellen und reproduzierbare Testausgaben (Golden Files).
- Betriebssystem: Linux. Editor: Neovim (falls du Editor-Befehle nennst, dann `nvim`, nicht `nano`).

**Annahmen (bitte prüfen und im Code als Konfiguration ablegen, nicht hart codieren)**

- Mosaik-Modus im MVP: **Draufsicht**, eine Lage Teile flach nebeneinander, 1 Pixel = 1 Noppe.
- Rebrickable-Farb-IDs entsprechen für die meisten Standardfarben den LDraw-Farbcodes. Für Abweichungen gibt es eine Override-Datei `daten/farb_zuordnung.csv`.
- LDraw-Teilenummern für Standardteile (z. B. `3024` Plate 1×1, `3070b` Tile 1×1, `3005` Brick 1×1) werden in einer Geometrie-Tabelle gepflegt, nicht in der Logik.

**Nicht-Ziele im MVP**

- Keine Datenbank (nur CSV), kein Frontend, keine Benutzerverwaltung.
- Kein `.io`-Export für Studio (Studio importiert `.ldr`). Kein BrickLink-Preisabgleich.
- Kein stehendes Mosaik (Seitenansicht mit gestapelten Bricks). Das Design soll diesen Modus aber später als weitere Strategie erlauben.
- Kein öffentliches Hosting: der Server bindet standardmäßig nur an `127.0.0.1`.

## Tech-Stack und Setup mit uv

Paket- und Projektverwaltung ausschließlich mit **uv** (kein pip, kein venv von Hand, kein Poetry). Python 3.15.

| Zweck | Bibliothek |
| --- | --- |
| Bilder laden, skalieren, PNG schreiben | Pillow |
| Rechnen auf Pixel-Arrays | NumPy |
| Farbraum RGB ↔ Lab, Farbabstand | scikit-image |
| CLI auf Deutsch | Typer (bringt Rich mit) |
| HTTP-API | FastAPI, uvicorn, python-multipart |
| CSV-Dumps herunterladen | httpx |
| Request-/Response-Modelle | Pydantic |
| Tests, Linting, Typprüfung | pytest, ruff, mypy (nur dev) |

**Das Projekt ist bereits angelegt** (uv, Python 3.15). Führe kein `uv init` und kein `uv python pin` aus. Prüfe `pyproject.toml` und `uv.lock` und ergänze fehlende Abhängigkeiten aus der Tabelle nur mit `uv add` bzw. `uv add --dev`. Bestehende Einstellungen änderst du nur nach Rückfrage. Hat eine Abhängigkeit noch keine Wheels für 3.15, meldest du das, statt die Python-Version zu senken.

Einstiegspunkt (ergänzen, falls noch nicht vorhanden) in `pyproject.toml`:

```toml
[project.scripts]
lego-mosaik = "lego_mosaik.main:run"
```

Aufrufe, die funktionieren müssen:

```bash
uv run lego-mosaik --hilfe
uv run lego-mosaik erstellen bild.jpg
uv run lego-mosaik server
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

`uv.lock` wird mit eingecheckt. Optional: `uv tool install --editable .`, damit `lego-mosaik` global im Terminal verfügbar ist.

## Projektstruktur

Halte dich an diese Struktur. Jeder Ordner ist ein austauschbarer Baustein mit eigener Schnittstelle (`protocol.py`).

```text
lego-mosaik/
├── pyproject.toml
├── uv.lock
├── README.md                    # deutsch: Setup, Nutzung, Architektur
├── daten/
│   ├── rebrickable/             # heruntergeladene CSV-Dumps (in .gitignore)
│   ├── teil_geometrie.csv       # Teil → LDraw-Datei, Maße in Noppen, Höhe in LDU
│   └── farb_zuordnung.csv       # optionale Overrides Rebrickable-ID → LDraw-Code
├── ausgabe/                     # erzeugte Mosaike, ein Unterordner pro Lauf (in .gitignore)
├── src/lego_mosaik/
│   ├── main.py                  # Composition Root: Konfiguration + Verdrahtung
│   ├── config.py                # Dataclasses der Konfiguration
│   ├── registry.py              # Name aus Konfiguration → Implementierung
│   ├── pipeline.py              # orchestriert alle Schritte
│   ├── domain/models.py         # LegoColor, PartType, Placement, Mosaic, PartsList
│   ├── sources/                 # protocol.py, rebrickable_csv.py, updater.py
│   ├── imaging/                 # loader.py, fitting.py, color_space.py
│   ├── dithering/               # protocol.py, nearest.py, error_diffusion.py, ordered.py
│   ├── tiling/                  # protocol.py, single_stud.py, greedy.py
│   ├── outputs/                 # protocol.py, preview_png.py, ldraw.py,
│   │                            # parts_list_csv.py, instructions_png.py, result_json.py
│   └── interfaces/              # cli.py (Typer), api.py (FastAPI)
└── tests/
    ├── fixtures/                # Mini-Bilder (z. B. 8×8) und Mini-CSV-Dumps
    ├── golden/                  # erwartete Ausgaben für Regressionstests
    └── test_*.py
```

**Abhängigkeitsregel:** `domain` hängt von nichts ab. Fachmodule (`imaging`, `dithering`, `tiling`, `outputs`, `sources`) kennen nur `domain` und ihr eigenes Protocol. `pipeline.py` kennt nur Protocols. Nur `main.py` kennt konkrete Klassen und verdrahtet sie. `interfaces/` rufen ausschließlich die fertige Pipeline auf.

## Konfiguration zentral in main.py

`main.py` ist die **Composition Root**: Hier stehen alle Standardwerte als ein unveränderliches Konfigurationsobjekt, und hier werden die konkreten Klassen zusammengesteckt. Wer etwas umstellen will, ändert nur diese Datei. CLI-Optionen und API-Felder überschreiben einzelne Werte nur für einen Lauf (per `dataclasses.replace`).

Ziel-Form (Namen dürfen verbessert werden, die Idee bleibt):

```python
# main.py – einzige Stelle, an der konfiguriert und verdrahtet wird
CONFIG = AppConfig(
    mosaic=MosaicConfig(
        width_studs=48,                    # Breite in Noppen
        height_studs=48,                   # Höhe in Noppen
        fit_mode=FitMode.CROP,             # CROP = mittig zuschneiden, PAD = einpassen mit Rand
        pad_color_id=15,                   # Randfarbe bei PAD (Rebrickable-ID, 15 = White)
    ),
    palette=PaletteConfig(
        part_kinds=(PartKind.PLATE, PartKind.TILE),  # erlaubt: PLATE, TILE, BRICK
        exclude_transparent=True,
        exclude_color_ids=(),              # z. B. Metallic- oder Glitter-Farben
        max_colors=None,                   # optional auf N häufigste Farben begrenzen
    ),
    dithering=DitheringConfig(
        method="floyd_steinberg",          # Schlüssel aus der Registry
        strength=1.0,                      # 0.0 = kein Fehlertransport, 1.0 = voll
        color_distance=ColorDistance.CIEDE2000,  # oder CIE76
        serpentine=True,                   # Zeilen abwechselnd links/rechts abarbeiten
    ),
    tiling=TilingConfig(strategy="greedy", max_part_length=8),
    outputs=("preview_png", "ldraw", "parts_list_csv", "instructions_png", "result_json"),
    ldraw=LdrawConfig(rows_per_step=4, include_baseplate=False),
    source=SourceConfig(data_dir=Path("daten/rebrickable"), max_age_days=7, auto_update=True),
    output_dir=Path("ausgabe"),
    server=ServerConfig(host="127.0.0.1", port=8000),
)

def build_pipeline(config: AppConfig) -> MosaicPipeline:
    """Steckt die konkreten Bausteine anhand der Konfiguration zusammen."""
    ...

def run() -> None:
    """Entry Point aus pyproject.toml: startet die deutsche CLI."""
    ...
```

Regeln für die Konfiguration:

- Alle Config-Klassen sind `@dataclass(frozen=True, slots=True)` mit Typannotationen.
- Feste Auswahlwerte als `StrEnum`. Die CLI zeigt dazu deutsche Bezeichnungen (z. B. `zuschneiden` → `FitMode.CROP`).
- Validierung beim Start mit verständlichen deutschen Fehlermeldungen (z. B. „Breite muss zwischen 8 und 256 Noppen liegen“).
- **Registries** in `registry.py`: je ein Dict `Name → Fabrikfunktion` für Dithering, Zerlegung und Ausgaben. Ein neues Verfahren = eine neue Klasse + eine Zeile in der Registry, sonst keine Änderung (Open/Closed-Prinzip).
- Unbekannte Namen führen zu einer Fehlermeldung, die alle gültigen Namen auflistet.

## Module und Schnittstellen

Jeder Baustein wird über ein `typing.Protocol` angesprochen (strukturelle Typisierung, keine Vererbung nötig). Die Pipeline kennt nur diese Protocols.

**Domain-Modelle** (`domain/models.py`, alle `frozen`):

- `LegoColor`: `id`, `name`, `rgb`, `is_transparent`, `ldraw_code`
- `PartType`: `part_num`, `name`, `kind` (PLATE/TILE/BRICK), `width_studs`, `length_studs`, `ldraw_file`, `height_ldu`
- `PartCatalog`: Farben, Teiltypen und die Menge verfügbarer Kombinationen `(part_num, color_id)`
- `Placement`: Teil, Farbe, Startzeile, Startspalte, `rotated`
- `Mosaic`: Breite, Höhe, Farbindex-Raster (`np.ndarray[int]`), Palette, Platzierungen
- `PartsList`: aggregierte Stückzahlen pro `(part_num, color_id)`, berechnet aus den Platzierungen

**Palette bauen** (`domain/palette.py`, reine Funktion): Eine Farbe kommt nur in die Palette, wenn es in ihr **mindestens ein 1×1-Teil** der erlaubten Teilarten gibt. Sonst könnte ein einzelnes Pixel nicht belegt werden.

**Protocols**:

```python
class PartCatalogSource(Protocol):
    def load_catalog(self) -> PartCatalog: ...

class Ditherer(Protocol):
    def dither(self, image_lab: np.ndarray, palette_lab: np.ndarray) -> np.ndarray:
        """Gibt ein H×W-Raster mit Palettenindizes zurück."""

class TilingStrategy(Protocol):
    def tile(self, color_grid: np.ndarray, palette: Sequence[LegoColor],
             catalog: PartCatalog) -> list[Placement]: ...

class OutputWriter(Protocol):
    name: str
    def write(self, mosaic: Mosaic, target_dir: Path) -> Path: ...
```

**Pipeline** (`pipeline.py`): `MosaicPipeline.run(image, overrides) -> MosaicResult`. Ablauf: Katalog laden → Palette bauen → Bild laden und einpassen → nach Lab umrechnen → dithern → zerlegen → alle konfigurierten Writer aufrufen. `MosaicResult` enthält das `Mosaic`, die erzeugten Dateien (`dict[str, Path]`) und Statistiken (Anzahl Teile, genutzte Farben, Dauer pro Schritt). Jeder Schritt loggt auf Deutsch über `logging`.

## Datenquelle Rebrickable-CSV und Aktualisierung

Quelle sind die öffentlichen Rebrickable-Downloads (gzip-komprimierte CSV). Benötigt werden `colors.csv.gz`, `parts.csv.gz` und `elements.csv.gz`. URL-Schema prüfen und als Konfigurationswert ablegen, vermutlich `https://cdn.rebrickable.com/media/downloads/<datei>`.

**Einlesen** (`sources/rebrickable_csv.py`, Klasse `RebrickableCsvSource`):

- Spalten über den Header-Namen lesen, nicht über die Position. Zusätzliche Spalten tolerieren.
- `colors.csv`: `id`, `name`, `rgb` (Hex ohne `#`), `is_trans`. Sonderfarben `-1` (Unknown) und `9999` (Any Color) immer ausschließen.
- `elements.csv`: `element_id`, `part_num`, `color_id`. Daraus entsteht die Menge verfügbarer Kombinationen `(part_num, color_id)`.
- Nur Teile berücksichtigen, die in `daten/teil_geometrie.csv` stehen (Whitelist). Keine Kategorien aus Teilenamen raten.
- LDraw-Farbcode: standardmäßig gleich der Rebrickable-ID, außer `daten/farb_zuordnung.csv` (`rebrickable_id,ldraw_code`) sagt etwas anderes.
- Den geparsten Katalog pro Prozess im Speicher cachen.

**Startinhalt für `daten/teil_geometrie.csv`** (gegen Rebrickable und die LDraw-Bibliothek prüfen):

```csv
part_num,kind,width_studs,length_studs,ldraw_file,height_ldu
3024,plate,1,1,3024.dat,8
3023,plate,1,2,3023.dat,8
3623,plate,1,3,3623.dat,8
3710,plate,1,4,3710.dat,8
3666,plate,1,6,3666.dat,8
3460,plate,1,8,3460.dat,8
3022,plate,2,2,3022.dat,8
3021,plate,2,3,3021.dat,8
3020,plate,2,4,3020.dat,8
3795,plate,2,6,3795.dat,8
3034,plate,2,8,3034.dat,8
3070b,tile,1,1,3070b.dat,8
3069b,tile,1,2,3069b.dat,8
63864,tile,1,3,63864.dat,8
2431,tile,1,4,2431.dat,8
6636,tile,1,6,6636.dat,8
4162,tile,1,8,4162.dat,8
3068b,tile,2,2,3068b.dat,8
3005,brick,1,1,3005.dat,24
3004,brick,1,2,3004.dat,24
3622,brick,1,3,3622.dat,24
3010,brick,1,4,3010.dat,24
3009,brick,1,6,3009.dat,24
3008,brick,1,8,3008.dat,24
3003,brick,2,2,3003.dat,24
3002,brick,2,3,3002.dat,24
3001,brick,2,4,3001.dat,24
```

**Aktualisierung** (`sources/updater.py`):

- Herunterladen, wenn eine Datei fehlt oder älter als `max_age_days` ist, oder wenn erzwungen wird.
- Atomar schreiben: erst in eine temporäre Datei, dann umbenennen. Ein abgebrochener Download darf keine kaputte CSV hinterlassen.
- httpx mit Timeout und eigenem User-Agent. Netzwerkfehler ergeben eine deutsche Meldung. Vorhandene alte Dateien werden dann weiter genutzt.
- Regelmäßig: Im Server-Modus prüft ein Hintergrund-Task (FastAPI-`lifespan` mit `asyncio`) alle 24 h. Ohne Server dokumentierst du in der README einen systemd-User-Timer, der `lego-mosaik teile-aktualisieren` aufruft.

## Algorithmen

Alle Algorithmen sind **deterministisch**: gleiche Eingabe ergibt gleiche Ausgabe (kein Zufall). Nur so funktionieren Golden-File-Tests und später der Vergleich mit einer Portierung.

**1. Bildaufbereitung** (`imaging/`)

- Laden mit Pillow, EXIF-Drehung korrigieren (`ImageOps.exif_transpose`), Transparenz auf die Randfarbe legen, nach RGB konvertieren.
- `CROP`: mittig auf das Zielseitenverhältnis zuschneiden, dann skalieren. `PAD`: einpassen und mit `pad_color_id` auffüllen.
- Skalieren mit `Image.Resampling.BOX` (mittelt Flächen, gut für starkes Verkleinern), konfigurierbar.
- Optionale Vorverarbeitung über `ImageEnhance`: Kontrast und Sättigung, Standard jeweils 1.0 (unverändert).

**2. Farbraum und Farbabstand** (`imaging/color_space.py`)

- RGB (0–255) → Lab über `skimage.color.rgb2lab(rgb / 255.0)`. Lab bildet die menschliche Farbwahrnehmung besser ab als RGB.
- Farbabstand umschaltbar: `CIE76` (euklidisch in Lab, schnell) oder `CIEDE2000` (`skimage.color.deltaE_ciede2000`, genauer).
- Die Suche nach der nächsten Palettenfarbe vektorisiert mit NumPy-Broadcasting (`(H, W, 1, 3)` gegen `(1, 1, P, 3)`), wo möglich.

**3. Dithering** (`dithering/`)

- `nearest`: kein Dithering, nur nächste Farbe. Dient als Referenz.
- `error_diffusion.py`: **eine** Klasse `ErrorDiffusionDitherer`, die einen Kernel als Daten bekommt (Liste aus `dx, dy, Gewicht` plus Divisor). Daraus registrierte Varianten: `floyd_steinberg`, `atkinson`, `sierra_lite`. Kein kopierter Code pro Verfahren (DRY).
- Fehlertransport im Lab-Raum, multipliziert mit `strength`. Werte nach dem Aufaddieren begrenzen (L auf 0–100), damit sich Fehler nicht aufschaukeln.
- `serpentine=True`: ungerade Zeilen von rechts nach links abarbeiten, `dx` dabei spiegeln. Das verringert Streifenmuster.
- `ordered.py`: Bayer-Matrix 4×4 oder 8×8 (konfigurierbar). Den Schwellwert-Offset vor der Farbsuche auf RGB addieren, Stärke `spread` konfigurierbar.

**4. Zerlegung in Teile** (`tiling/`)

Konvention: `row` = Bildzeile (oben = 0), `col` = Bildspalte (links = 0). `rotated=False` bedeutet: `length_studs` liegt entlang der Spalten (x-Richtung).

- `single_stud`: jedes Pixel wird ein 1×1-Teil. Einfache Referenz.
- `greedy` (Standard):
  1. Pro Farbe die verfügbaren Teilgrößen bestimmen (aus `PartCatalog`), begrenzt durch `max_part_length`. Jede nicht quadratische Größe gibt es in beiden Ausrichtungen.
  2. Kandidaten sortieren: größte Fläche zuerst, dann längste Seite, dann Teilart-Priorität (Reihenfolge in `part_kinds`).
  3. Raster zeilenweise durchlaufen. Für jede freie Zelle den ersten Kandidaten nehmen, der passt: im Raster, alle Zellen gleiche Farbe, alle frei, Teil in dieser Farbe verfügbar.
  4. 1×1 passt immer, weil die Palette nur Farben mit 1×1-Teil enthält.
- Validierung: In der Draufsicht dürfen nur Teilarten gleicher Höhe gemischt werden (PLATE + TILE ist ok, BRICK nur allein). Sonst deutsche Fehlermeldung.
- Statistik ausgeben: Anzahl Pixel, Anzahl Teile, Einsparung in Prozent.

## Ausgaben

Jeder Writer ist eine eigene Klasse in `outputs/` und schreibt in einen Ordner pro Lauf, z. B. `ausgabe/2026-10-12_143005_katze/`. Welche Writer laufen, steht in `CONFIG.outputs`.

| Name | Datei(en) | Inhalt |
| --- | --- | --- |
| `preview_png` | `vorschau.png` | Jede Noppe als Quadrat (Größe konfigurierbar, z. B. 16 px) mit angedeutetem Noppenkreis; optional Teilekanten als Linien |
| `ldraw` | `mosaik.ldr` | LDraw-Modell mit Bauschritten für Bricklink Studio |
| `parts_list_csv` | `teileliste.csv`, `teileliste_rebrickable.csv` | Eigene Liste und eine Liste im Rebrickable-Importformat |
| `instructions_png` | `anleitung_01.png` …, `anleitung.pdf` | Einfache Bauanleitung in Abschnitten |
| `result_json` | `ergebnis.json` | Konfiguration, Statistik, Palette, Dateipfade (für die API) |

**LDraw-Datei (`outputs/ldraw.py`)**

- Kopf: `0 <Titel>`, `0 Name: mosaik.ldr`, `0 Author: lego-mosaik`, `0 !LDRAW_ORG Unofficial_Model`.
- Pro Teil eine Zeile Typ 1: `1 <farbe> <x> <y> <z> <a b c d e f g h i> <datei>`.
- Maße: 1 Noppe = 20 LDU. Mittelpunkt des Teils: `x = (col + breite_in_spalten / 2) * 20`, `z = (row + hoehe_in_zeilen / 2) * 20`, `y = 0` für alle (eine Lage; in LDraw zeigt −y nach oben).
- Rotationsmatrix: ohne Drehung `1 0 0 0 1 0 0 0 1`, um 90° um die Y-Achse `0 0 1 0 1 0 -1 0 0`.
- Annahme: Rechteckige Standardteile liegen in LDraw mit der langen Seite entlang X. Prüfe das mit einer Testdatei (je ein 1×4- und 2×4-Teil, gedreht und ungedreht). Falls nötig, ergänze in `teil_geometrie.csv` eine Spalte `ldraw_long_axis` (x oder z).
- Bauschritte: nach je `rows_per_step` Zeilen ein `0 STEP`. Ein Teil gehört zum Schritt seiner Startzeile. Innerhalb eines Schritts nach Farbe, dann Position sortieren.
- Grundplatte nur, wenn `include_baseplate=True` (Standard aus).
- Die README erklärt: Datei in Studio öffnen bzw. importieren, im Instruction-Modus werden die `STEP`s als Bauschritte übernommen.

**Teilelisten**

- `teileliste.csv`: Spalten `Teil-Nr.`, `Teilname`, `Farb-ID`, `Farbname`, `Anzahl`, `Element-ID` (erste passende aus `elements.csv`, sonst leer). Sortiert nach Farbe, dann Teil.
- `teileliste_rebrickable.csv`: Spalten `Part,Color,Quantity` für den Import auf Rebrickable.
- Kodierung `utf-8-sig`, damit Excel Umlaute korrekt anzeigt.

**Bauanleitung**

- Seite 1: Gesamtvorschau mit eingezeichneten Abschnittsgrenzen.
- Folgeseiten: je ein Abschnitt (Standard 16 × 16 Noppen). Teilekanten einzeichnen, Farben mit kurzen Kennungen (A, B, C …) beschriften, darunter die für den Abschnitt benötigten Teile mit Stückzahl.
- Alle Seiten zusätzlich als mehrseitiges PDF mit Pillow (`save_all=True`). Keine zusätzliche Bibliothek.

## CLI auf Deutsch

`interfaces/cli.py` mit Typer. Die CLI enthält **keine Fachlogik**: Sie liest Optionen, baut daraus Overrides, ruft die Pipeline auf und gibt das Ergebnis aus. Erzeugt wird sie über `create_cli(config, pipeline_factory)` aus `main.py`.

| Befehl | Zweck | Wichtige Optionen |
| --- | --- | --- |
| `erstellen BILD` | Mosaik erzeugen | `--breite`, `--hoehe`, `--modus zuschneiden\|einpassen`, `--teile plate,tile`, `--dithering`, `--staerke`, `--zerlegung`, `--ausgaben`, `--ziel` |
| `teile-aktualisieren` | Rebrickable-CSVs laden | `--erzwingen` |
| `farben` | Verfügbare Palette als Tabelle mit Farbfeld anzeigen | `--teile` |
| `verfahren` | Registrierte Dithering-, Zerlegungs- und Ausgabe-Namen auflisten | – |
| `server` | FastAPI lokal starten | `--host` (Standard `127.0.0.1`), `--port` (Standard `8000`) |

Anforderungen:

- Hilfe über `-h`, `--hilfe` und `--help`: `typer.Typer(context_settings={"help_option_names": ["-h", "--hilfe", "--help"]}, no_args_is_help=True)`.
- Alle Hilfetexte, Meldungen und Fehlermeldungen auf Deutsch mit ä, ö, ü, ß. Typer-interne Texte wie „Usage“ dürfen englisch bleiben.
- Fortschritt pro Pipeline-Schritt mit Rich anzeigen. Am Ende eine Zusammenfassung: Größe, Farben, Teile, Einsparung, Pfade der erzeugten Dateien.
- Fehler ohne Traceback mit verständlicher Meldung, Traceback nur mit `--debug`. Exit-Codes: `0` Erfolg, `1` Verarbeitungsfehler, `2` ungültige Eingabe.
- Optionen, die nicht angegeben werden, nehmen den Wert aus `CONFIG`.

Beispiel für die gewünschte Bedienung:

```bash
uv run lego-mosaik erstellen katze.jpg --breite 64 --hoehe 48 --teile plate,tile --dithering atkinson
uv run lego-mosaik farben --teile tile
uv run lego-mosaik teile-aktualisieren --erzwingen
```

## FastAPI-Service lokal

`interfaces/api.py` mit App-Fabrik `create_app(config, pipeline_factory)`. Start über `lego-mosaik server` (uvicorn, Standard `127.0.0.1:8000`). Keine Authentifizierung, weil nur lokal gebunden. Das steht als Hinweis in der README.

**DRY zwischen CLI und API:** Beide übersetzen ihre Eingaben in **ein** gemeinsames Objekt `MosaicOverrides` (in `config.py`). Eine Funktion `apply_overrides(config, overrides)` erzeugt daraus die Laufkonfiguration. Validierung und Standardwerte gibt es nur an dieser einen Stelle.

| Methode | Pfad | Zweck |
| --- | --- | --- |
| `GET` | `/gesundheit` | Status und Stand des Teilekatalogs |
| `GET` | `/farben?teile=plate,tile` | Palette für die gewählten Teilarten |
| `GET` | `/verfahren` | Registrierte Dithering-, Zerlegungs- und Ausgabe-Namen |
| `POST` | `/mosaike` | Multipart: Datei `bild` plus optionale Felder wie bei `erstellen`. Antwort `201` mit `id`, Statistik und Download-URLs |
| `GET` | `/mosaike/{id}` | Inhalt von `ergebnis.json` |
| `GET` | `/mosaike/{id}/dateien/{dateiname}` | Einzelne Datei herunterladen |
| `POST` | `/teile/aktualisieren?erzwingen=false` | Katalog aktualisieren |

Anforderungen:

- Rechenintensive Endpunkte als normales `def` (nicht `async def`), damit FastAPI sie im Threadpool ausführt und der Server ansprechbar bleibt.
- Request- und Response-Modelle mit Pydantic. Feldbeschreibungen auf Deutsch, damit `/docs` (OpenAPI) verständlich ist.
- Upload-Grenzen: maximale Dateigröße (Standard 20 MB), Bildformat über Pillow prüfen, Mosaikgröße begrenzen.
- Dateidownload nur für Namen, die im Ergebnis stehen. Kein Pfad aus der URL direkt ins Dateisystem (Schutz vor Path Traversal).
- Fehler als JSON `{"fehler": "…"}` auf Deutsch mit passenden Statuscodes (`400`, `404`, `413`, `422`).
- Hintergrund-Aktualisierung des Katalogs alle 24 h über `lifespan` (siehe Datenquelle).

## Code-Stil und Kommentare

**Grundsätze**

- Clean Code, DRY, sprechende Namen. Funktionen kurz (Richtwert unter 30 Zeilen) und mit genau einer Aufgabe.
- Bezeichner im Code auf Englisch. Kommentare, Docstrings (Google-Stil), Logs, CLI-Texte und Fehlermeldungen auf Deutsch, immer mit ä, ö, ü und ß (nie ae, oe, ue, ss als Ersatz).
- Vollständige Typannotationen. `mypy --strict` muss durchlaufen. `ruff` mit Regeln `E, F, I, B, UP, SIM, N, RUF`.
- Keine Magic Numbers: benannte Konstanten (z. B. `LDU_PER_STUD = 20`, `PLATE_HEIGHT_LDU = 8`).
- `pathlib.Path` statt String-Pfade. `logging` statt `print` (Ausnahme: Rich-Ausgabe der CLI).
- Eigene Fehlerhierarchie: `LegoMosaicError` als Basis, darunter z. B. `ConfigError`, `CatalogError`, `InvalidImageError`. Jeweils mit deutscher Meldung.

**Lazy Imports (PEP 810, ab Python 3.15)**

- Schwere Abhängigkeiten (NumPy, scikit-image, Pillow, httpx, FastAPI, uvicorn) auf Modulebene mit `lazy import` bzw. `lazy from … import …` laden. Ziel: `lego-mosaik --hilfe`, `verfahren` und `teile-aktualisieren` starten, ohne scikit-image oder FastAPI zu laden.
- Keine Imports mehr in Funktionen verstecken (altes Muster gegen langsamen Start). Stattdessen `lazy` oben in der Datei, damit alle Abhängigkeiten sichtbar bleiben.
- Imports, die nur für Typannotationen gebraucht werden, ebenfalls `lazy` statt `if TYPE_CHECKING:`.
- Grenzen: `lazy` ist nur auf Modulebene erlaubt. In Funktionen, Klassenkörpern und `try`/`except`/`finally` ist es ein `SyntaxError`. Kein `lazy from x import *`, kein `lazy from __future__ import …`.
- Folgen: Import-Seiteneffekte und Importfehler passieren erst beim ersten Zugriff. Deshalb registriert sich nichts per Import-Seiteneffekt (Registries sind explizite Dicts), und die Tests durchlaufen jeden Pfad mindestens einmal.
- Werkzeuge: ruff ab 0.15.6 (sortiert `lazy`-Imports), `target-version = "py315"`, mypy mit `python_version = "3.15"`. Kann ein Werkzeug die Syntax nicht verarbeiten, sag das offen, statt still auf normale Imports umzustellen.
- Test: In einem Subprozess `lego_mosaik.main` importieren und prüfen, dass `skimage` und `fastapi` nicht in `sys.modules` stehen. Startzeit messen mit `uv run python -X importtime -c "import lego_mosaik.main"`.

```python
# lazy import (PEP 810): bindet nur einen Platzhalter an den Namen "np",
# NumPy wird erst beim ersten Zugriff (z. B. np.zeros(...)) wirklich geladen
lazy import numpy as np
# lazy from: gleiches Prinzip für einzelne Namen aus einem Modul
lazy from skimage.color import deltaE_ciede2000, rgb2lab
# normaler Import: leichtgewichtig und sofort gebraucht
from pathlib import Path
```

**Kommentar-Regeln (wichtig, ich lerne damit)**

Erkläre jeden verwendeten Operator, jedes Pattern und jeden Aufbau kurz und präzise direkt am Code. Benenne Patterns beim Namen (Strategy, Factory, Registry, Composition Root, Protocol). Beispiel für die gewünschte Tiefe:

```python
# Broadcasting: (H, W, 1, 3) minus (1, 1, P, 3) ergibt (H, W, P, 3),
# also die Differenz jedes Pixels zu jeder Palettenfarbe in einem Schritt
differences = image_lab[:, :, np.newaxis, :] - palette_lab[np.newaxis, np.newaxis, :, :]
# norm(..., axis=-1): euklidische Länge über die letzte Achse (L, a, b) -> (H, W, P)
distances = np.linalg.norm(differences, axis=-1)
# argmin(axis=-1): Index der kleinsten Distanz pro Pixel = nächste Palettenfarbe
nearest_indices = distances.argmin(axis=-1)
```

Zusätzlich nach jedem größeren Schritt im Chat: ein kurzer Text, was du gebaut hast, welche Patterns drinstecken und warum.

## Tests und Akzeptanzkriterien

**Tests mit pytest** (keine Netzwerkzugriffe in Tests):

- Fixtures: Mini-CSV-Dumps mit etwa 6 Farben und wenigen Teilen sowie 8 × 8-Testbilder, die in `conftest.py` erzeugt werden.
- Farbraum: bekannte Werte (Weiß ergibt L ≈ 100, Schwarz L ≈ 0).
- Palette: Eine Farbe ohne 1×1-Teil wird ausgeschlossen.
- Dithering: Ein einfarbiges Bild ergibt genau eine Farbe. Zwei Läufe liefern identische Ergebnisse. Serpentinen-Modus wird getestet.
- Zerlegung: Die Summe der Teilflächen ist gleich Breite × Höhe, es gibt keine Überlappungen, und jedes Teil existiert im Katalog in seiner Farbe. Ein 4 × 4-Feld in einer Farbe mit verfügbarem 2×4 ergibt genau 2 Teile.
- LDraw: Koordinaten und Rotationsmatrix für bekannte Platzierungen, Anzahl `STEP`-Zeilen.
- Teileliste: Summe der Stückzahlen ist gleich der Anzahl Platzierungen.
- Updater: mit `httpx.MockTransport`. Ein abgebrochener Download lässt die alte Datei unverändert.
- CLI mit `typer.testing.CliRunner`, API mit `fastapi.testclient.TestClient`.
- Golden Files: Für 2–3 Testbilder werden das Farbindex-Raster (JSON) und die `.ldr` gespeichert und verglichen. Neu erzeugen über eine eigene pytest-Option `--golden-aktualisieren`.

**Akzeptanzkriterien**

- [ ] Frischer Klon: `uv sync` und `uv run lego-mosaik teile-aktualisieren` funktionieren ohne weitere Schritte.
- [ ] `uv run lego-mosaik erstellen <bild>` erzeugt alle fünf Ausgaben. 48 × 48 dauert unter 5 s, 128 × 128 unter 30 s.
- [ ] `mosaik.ldr` öffnet sich in Bricklink Studio: Teile richtig ausgerichtet, keine Überlappungen, Bauschritte sichtbar.
- [ ] Teilarten, Verfahren und Ausgaben lassen sich allein in `main.py` umstellen.
- [ ] Ein neues Dithering-Verfahren braucht nur eine neue Klasse und eine Registry-Zeile.
- [ ] `POST /mosaike` liefert bei gleichen Optionen dieselben Dateien wie die CLI.
- [ ] `pytest`, `ruff check`, `ruff format --check` und `mypy --strict` laufen fehlerfrei.
- [ ] README auf Deutsch: Setup, Befehle, Konfiguration, Architekturübersicht, Import in Studio, systemd-Timer.

## Vorgehen

Arbeite in kleinen, überprüfbaren Schritten. Nach jedem Schritt: Tests grün, kurze Erklärung im Chat, ein Commit im Stil von Conventional Commits mit deutscher Beschreibung (z. B. `feat: Floyd-Steinberg-Dithering im Lab-Raum`).

1. **Plan zuerst:** Fasse den Plan in eigenen Worten zusammen, nenne offene Fragen und Annahmen gebündelt. Warte auf meine Bestätigung, bevor du Code schreibst.
2. Vorhandenes uv-Setup prüfen und ergänzen: `pyproject.toml`, ruff- und mypy-Konfiguration für Python 3.15, `.gitignore`, leere Modulstruktur.
3. `domain/`, `config.py`, `registry.py`, Fehlerhierarchie.
4. Datenquelle und Updater inklusive Tests.
5. Bildaufbereitung, Farbraum und alle Dithering-Verfahren inklusive Tests.
6. Zerlegung (`single_stud`, `greedy`) inklusive Tests.
7. Ausgaben `preview_png`, `ldraw`, `parts_list_csv`, `result_json`. Dazu eine Orientierungs-Testdatei, die ich in Studio prüfe, bevor es weitergeht.
8. `pipeline.py`, `main.py` und CLI.
9. FastAPI-Service mit `lifespan`-Aktualisierung.
10. Bauanleitung als PNG und PDF.
11. Golden Files, README, Beispiel für systemd-User-Timer.

Wichtig: Erfinde keine APIs, Dateiformate oder Teilenummern. Wenn du dir unsicher bist (Rebrickable-URLs, LDraw-Ausrichtung, Studio-Import), sag das offen und baue einen Test oder eine Prüfdatei, mit der wir es verifizieren.
