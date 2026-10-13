# HTTP-API für Frontends

Kurzanleitung für ein Web-Frontend, das Mosaike über die lokale API erzeugt. Die vollständige Beschreibung aller Felder und Antworten steht in [`openapi.yaml`](../openapi.yaml) (OpenAPI 3.1). Zum Ausprobieren gibt es `http://127.0.0.1:8000/docs`.

```bash
uv run lego-mosaik server          # http://127.0.0.1:8000
```

## Auf einen Blick

| Methode | Pfad | Wofür im Frontend |
| --- | --- | --- |
| `GET` | `/optionen` | Formular aufbauen: Standardwerte, Auswahllisten, Grenzen, Formate |
| `GET` | `/farben?teile=plate,tile` | Farbpalette anzeigen, z. B. für die Wahl der Rahmenfarbe |
| `POST` | `/mosaike` | Bild hochladen und Mosaik erzeugen |
| `GET` | `/mosaike/{id}` | Ergebnis später erneut laden (Inhalt von `ergebnis.json`) |
| `GET` | `/mosaike/{id}/dateien/{name}` | Vorschau, Anleitung, Teilelisten |
| `GET` | `/gesundheit` | Läuft der Server? Ist der Teilekatalog aktuell? |

## CORS

Seiten auf `localhost`, `127.0.0.1` oder `[::1]` dürfen die API mit **jedem Port** aufrufen, z. B. ein Vite-Dev-Server auf `http://localhost:5173`. Andere Adressen gibt man beim Start frei:

```bash
uv run lego-mosaik server --cors http://192.168.1.20:5173
```

Dauerhaft geht das über `ServerConfig.cors_origins` bzw. `cors_origin_regex` in `main.py`. Mit `cors_origin_regex=None` ist CORS ganz aus. Der Header `Content-Disposition` ist freigegeben, damit das Frontend beim Download den Dateinamen lesen kann.

## Typen erzeugen (TypeScript)

```bash
npx openapi-typescript openapi.yaml -o src/api.d.ts
```

Jede Route hat eine lesbare `operationId` (`create_mosaic`, `get_mosaic`, `get_options`, …). Damit passen auch Generatoren wie `openapi-fetch` oder `orval`. Die Datei `openapi.yaml` wird aus der App erzeugt (`uv run python scripts/openapi_exportieren.py`). Ein Test schlägt fehl, wenn sie nicht mehr zur API passt.

## Ablauf

### 1. Formular aus `/optionen` bauen

```json
{
  "felder": {
    "unterbau": { "standard": "art", "werte": ["art", "grundplatte", "platten", "ohne"],
                  "mehrfach": false, "minimum": null, "maximum": null },
    "teile":    { "standard": "plate,tile", "werte": ["plate", "tile", "brick"],
                  "mehrfach": true, "minimum": null, "maximum": null },
    "staerke":  { "standard": 1.0, "werte": [], "mehrfach": false, "minimum": 0, "maximum": 1 }
  },
  "formate": [ { "name": "quadrat", "platten_breite": 3, "platten_hoehe": 3,
                 "beschreibung": "wie die meisten LEGO-Art-Sets" } ]
}
```

- `werte` gefüllt, `mehrfach: false` → Auswahlliste.
- `mehrfach: true` → Checkboxen; gesendet wird kommagetrennt (`"plate,tile"`).
- `werte` leer → freies Feld; `minimum`/`maximum` als Grenzen für Zahlen.
- `standard: null` heißt: ergibt sich (z. B. Unterbaufarbe je nach Unterbau, Breite aus dem Seitenverhältnis).

Die Auswahllisten für `stil`, `dithering`, `zerlegung` und `ausgaben` kommen aus den registrierten Verfahren. Neue Verfahren erscheinen dort ohne Änderung im Frontend.

Felder, die man **nicht** mitschickt, nehmen den Standard. Ein Frontend schickt am besten nur, was der Nutzer geändert hat.

### 2. Mosaik erzeugen

```ts
const form = new FormData();
form.append("bild", file);                 // Pflicht: JPG, PNG, … (höchstens 20 MB)
form.append("format", "quadrat");          // optional, siehe /optionen
form.append("stil", "formen");
form.append("titel", "Unser Hund");

const response = await fetch("http://127.0.0.1:8000/mosaike", { method: "POST", body: form });
const body = await response.json();
if (!response.ok) throw new Error(body.fehler);   // deutsche Meldung, direkt anzeigbar
```

Die Antwort kommt erst, wenn das Mosaik **fertig** ist. Das dauert je nach Größe Sekunden bis wenige Minuten, mit Bauanleitung meist am längsten. Es gibt keine Fortschrittsanzeige. Das Frontend sollte also einen Ladezustand zeigen und den Button sperren, damit niemand doppelt absendet. Der Server bleibt währenddessen für andere Anfragen erreichbar.

Regeln, die der Server prüft (Fehler `422` mit Erklärung):

- Entweder `format` **oder** `platten`/`breite`/`hoehe`, nicht beides.
- Die Stile `punkte` und `noppen` brauchen einen Unterbau (nicht `ohne`).
- Formteil-Stile (`formen`, `punkte`, `noppen`) gehen nicht mit `teile=brick`.

### 3. Ergebnis anzeigen

Antwort von `POST /mosaike` (`201`):

```json
{
  "id": "2026-10-13_101500_Unser_Hund",
  "statistik": {
    "breite": 48, "hoehe": 48, "bild_breite": 46, "bild_hoehe": 46,
    "teile": 1083, "farben": 16, "altfarben": 0, "anteil_1x1": 0.263,
    "seiten": 64, "formteile": 303, "relief_teile": 155, "unterlagen": 256,
    "…": "weitere Kennzahlen, siehe StatsModel in openapi.yaml"
  },
  "dateien": {
    "vorschau.png": "http://127.0.0.1:8000/mosaike/2026-10-13_101500_Unser_Hund/dateien/vorschau.png",
    "anleitung.pdf": "http://127.0.0.1:8000/mosaike/…/dateien/anleitung.pdf"
  }
}
```

Die URLs in `dateien` sind fertig und lassen sich direkt verwenden: `<img src>` für Bilder, `<a href download>` für alles andere. Die `id` merkt man sich. Mit `GET /mosaike/{id}` lädt man später dasselbe Ergebnis samt Palette und Konfiguration.

Für eine Übersicht eignen sich `breite × hoehe` (Größe in Noppen, mit Rahmen), `teile`, `farben` und `seiten`. Eine Noppe ist 8 mm, 48 Noppen sind also 38,4 cm.

## Dateien

| Datei | Inhalt | Im Frontend |
| --- | --- | --- |
| `vorschau.png` | Draufsicht des fertigen Mosaiks | Großes Vorschaubild |
| `anleitung.pdf` | Bauanleitung (Titelblatt, Teile, Schritte) | Download oder `<iframe>` |
| `anleitung_01.png` … | Dieselbe Anleitung seitenweise | Blätterbare Galerie |
| `teileliste.csv` | Teileliste (`;`-getrennt, UTF-8 mit BOM, für Excel) | Download, Tabelle |
| `teileliste_bricklink.xml` | BrickLink-Wanted-List | Download „Bei BrickLink bestellen“ |
| `teileliste_rebrickable.csv` | Rebrickable-Import (`Part,Color,Quantity`) | Download |
| `teileliste_lego.csv` | Element-IDs für LEGO Pick a Brick | Download |
| `mosaik.ldr` | 3D-Modell (LDraw, z. B. für BrickLink Studio) | Download |
| `ergebnis.json` | Konfiguration, Statistik, Palette, Dateiliste | Nicht nötig, steht schon in der Antwort |

Welche Dateien entstehen, steuert das Feld `ausgaben`. `ergebnis.json` entsteht immer. Heruntergeladen werden kann nur, was in `dateien` steht.

## Fehler

Alle Fehler kommen als `{"fehler": "…"}` mit einem deutschen Satz, der sich direkt anzeigen lässt.

| Status | Bedeutung | Beispiel |
| --- | --- | --- |
| `400` | Datei ist kein lesbares Bild | PDF statt JPG hochgeladen |
| `404` | Mosaik oder Datei gibt es nicht | Alte ID, Ausgabeordner gelöscht |
| `413` | Datei zu groß (Standard: 20 MB) | Foto in voller Auflösung |
| `422` | Ungültige oder fehlende Angaben | `format` und `platten` zusammen, `relief_hoehe=5` |
| `500` | Verarbeitungsfehler | Teilekatalog fehlt und ist nicht ladbar |

## Grenzen

- **Keine Anmeldung:** Der Server ist für den eigenen Rechner gedacht und bindet nur an `127.0.0.1`. Nicht öffentlich erreichbar machen.
- **Keine Liste:** Es gibt keine Liste aller Mosaike und kein Löschen. Die IDs merkt sich das Frontend, die Dateien liegen in `ausgabe/`.
- **Synchron:** Pro Anfrage ein Mosaik, die Antwort kommt am Ende (siehe oben).
