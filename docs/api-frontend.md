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
| `POST` | `/bild/hintergrund` | Hintergrund erkennen: Farbe, Anteil, Maske, passende LEGO-Farbe |
| `POST` | `/bild/vorschau` | Bearbeitetes Bild (Filter, Drehen, Ausschnitt, Hintergrund) als PNG |
| `POST` | `/mosaike` | Bild hochladen und Mosaik erzeugen (auch mit `preset` und `palette`) |
| `GET` | `/mosaike/{id}` | Ergebnis später erneut laden (Inhalt von `ergebnis.json`) |
| `GET` | `/mosaike/{id}/dateien/{name}` | Vorschau, Anleitung, Teilelisten |
| `GET` | `/mosaike/{id}/raster` | Farbe jeder Noppe für einen Editor |
| `POST` | `/mosaike/{id}/bearbeiten` | Noppen und Flächen umfärben → neues Mosaik |
| `POST` | `/mosaike/{id}/preset` | Einstellungen eines Mosaiks als Preset speichern |
| `GET` `POST` | `/paletten` | Eigene Farbpaletten auflisten, anlegen |
| `GET` `PUT` `DELETE` | `/paletten/{id}` | Palette lesen, ersetzen, löschen |
| `GET` `POST` | `/presets` | Presets auflisten (Favoriten zuerst, `sortierung`), anlegen |
| `GET` `PUT` `PATCH` `DELETE` | `/presets/{id}` | Preset lesen, ersetzen, Favorit/Bewertung setzen, löschen |
| `POST` | `/mosaike/{id}/preise` | Preis bei BrickLink schätzen (Button „Preis schätzen“) |
| `GET` | `/mosaike/{id}/preise` | Gespeicherte Preisschätzung erneut laden |
| `GET` | `/mosaike/{id}/bricklink` | Wanted List zum Kopieren plus Link zur BrickLink-Upload-Seite |
| `GET` | `/gesundheit` | Läuft der Server? Ist der Teilekatalog aktuell? |

## CORS

Seiten auf `localhost`, `127.0.0.1` oder `[::1]` dürfen die API mit **jedem Port** aufrufen, z. B. ein Vite-Dev-Server auf `http://localhost:5173`. Andere Adressen gibt man beim Start frei:

```bash
uv run lego-mosaik server --cors http://192.168.1.20:5173
```

Dauerhaft geht das über `ServerConfig.cors_origins` bzw. `cors_origin_regex` in `main.py`. Mit `cors_origin_regex=None` ist CORS ganz aus. Erlaubt sind `GET`, `POST`, `PUT`, `PATCH` und `DELETE`. Der Header `Content-Disposition` ist freigegeben, damit das Frontend beim Download den Dateinamen lesen kann.

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

`glaettung` (`aus`, `leicht`, `mittel`, `stark`; Standard `leicht`) eignet sich als Schieberegler mit vier Stufen. Für Logos und Comics `mittel` oder `stark` vorschlagen: Dann gibt es klare Flächen ohne Mischfarben an den Kanten, und kontrastreiche Details wie Augen bleiben erhalten.

Die Auswahllisten für `stil`, `dithering`, `zerlegung` und `ausgaben` kommen aus den registrierten Verfahren. Neue Verfahren erscheinen dort ohne Änderung im Frontend. `palette` und `preset` listen die gespeicherten Paletten und Presets (IDs) und ändern sich, sobald jemand eine anlegt oder löscht. `/optionen` daher nach dem Anlegen neu laden.

Felder, die man **nicht** mitschickt, nehmen den Standard. Ein Frontend schickt am besten nur, was der Nutzer geändert hat.

### 2. Bild vorbereiten (optional)

Vor dem Mosaik lässt sich das Bild bearbeiten. Dieselben Felder gehen an `POST /bild/vorschau`, `POST /bild/hintergrund` und `POST /mosaike`, die Vorschau zeigt also genau, was ins Mosaik geht.

| Feld | Werte | Wirkung |
| --- | --- | --- |
| `drehen` | `0`, `90`, `180`, `270` | im Uhrzeigersinn drehen |
| `spiegeln` | `true`/`false` | waagerecht spiegeln |
| `ausschnitt` | `x,y,breite,hoehe` in Prozent | z. B. `10,0,80,100` (bezieht sich auf das gedrehte Bild) |
| `farbfilter` | `ohne`, `graustufen`, `sepia`, `warm`, `kalt`, `invertieren`, `poster`, `vintage` | Farbfilter vor dem Dithering |
| `filter_staerke` | `0` bis `1` | mischt Filter und Original |
| `helligkeit`, `kontrast`, `saettigung` | Faktor, `1.0` = unverändert | |
| `hintergrund` | `behalten`, `entfernen`, `aufhellen`, `abdunkeln`, `entsaettigen`, `weichzeichnen` | was mit dem erkannten Hintergrund passiert |
| `hintergrundfarbe` | Rebrickable-ID | neue Farbe bei `entfernen` (Standard: Weiß) |
| `hintergrund_toleranz` | `0` bis `100` (ΔE) | größer = mehr Pixel zählen zum Hintergrund |
| `hintergrund_staerke` | `0` bis `1` | für aufhellen, abdunkeln, entsättigen, weichzeichnen |

**Hintergrund erkennen** (z. B. beim Hochladen, um „Hintergrund entfernen“ anzubieten):

```ts
const form = new FormData();
form.append("bild", file);
form.append("hintergrund_toleranz", "15");
const bg = await (await fetch(`${API}/bild/hintergrund`, { method: "POST", body: form })).json();
// bg.farbe_hex: "#EBF0F4", bg.anteil: 0.8, bg.lego_farbe: { id: 15, name: "White", hex: … }
// bg.maske_png: "data:image/png;base64,…" → als halbtransparentes Overlay über das Bild legen
```

Der Hintergrund ist die häufigste Farbe am Bildrand, soweit sie zusammenhängend vom Rand aus reicht. Gleichfarbige Flächen im Motiv bleiben deshalb erhalten. Ein Schieberegler für `hintergrund_toleranz` mit sofort neu geladener Maske funktioniert gut (die Erkennung braucht unter einer Sekunde).

**Vorschau des bearbeiteten Bilds:** `POST /bild/vorschau` mit denselben Feldern plus `groesse` (längere Seite in Pixeln, Standard 800) liefert ein PNG. Im Frontend z. B. `URL.createObjectURL(await response.blob())`.

Bei `hintergrund=entfernen` bekommt jede Hintergrund-Noppe genau die `hintergrundfarbe`, ohne Dithering-Rauschen. Das spart viele Teile, weil große Flächen zu großen Plates werden. Mit `relief=motiv` liegt dann genau das erkannte Motiv erhöht.

### 3. Mosaik erzeugen

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
- Entweder `palette` **oder** `farb_ids`, nicht beides.

`preset=<id>` nimmt die gespeicherten Einstellungen als Grundlage; jedes mitgeschickte Feld geht vor. `palette=<id>` (oder `farb_ids=0,15,71`) dithert nur mit diesen Farben. Unbekannte Presets oder Paletten ergeben `404`.

### 4. Ergebnis anzeigen

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

### 5. Mosaik bearbeiten: Noppen umfärben (optional)

Für einen Pixel-Editor liefert `GET /mosaike/{id}/raster` jede Noppe:

```json
{
  "breite": 48, "hoehe": 48, "rahmen": 1,
  "farben": [ { "id": 0, "name": "Black", "hex": "#05131D" }, { "id": 15, "name": "White", "hex": "#FFFFFF" } ],
  "zeilen": [ [0, 0, 0, …], [0, 15, 15, …], … ]
}
```

`zeilen[z][s]` ist die Farb-ID der Noppe in Zeile `z + 1`, Spalte `s + 1`. Die äußeren `rahmen` Ringe sind der Rahmen und lassen sich nicht ändern. Als Farbwähler eignet sich `GET /farben` mit denselben Teilarten wie das Mosaik.

Die Änderungen des Nutzers sammelt das Frontend und schickt sie gemeinsam:

```ts
await fetch(`${API}/mosaike/${id}/bearbeiten`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    noppen:   [ { zeile: 5, spalte: 7, farbe_id: 15 } ],                       // einzelne Noppe
    flaechen: [ { zeile: 2, spalte: 2, breite: 4, hoehe: 3, farbe_id: 0 } ],   // Rechteck
    fuellen:  [ { zeile: 20, spalte: 20, farbe_id: 1 } ],                      // Farbeimer
  }),
});
```

Zeilen und Spalten zählen **ab 1 inklusive Rahmen**, wie in Vorschau und Anleitung. Der Server rechnet das Mosaik mit den Änderungen neu (Teile, Teilelisten, Anleitung) und antwortet `201` wie `POST /mosaike`, mit **neuer `id`** und `basis` = alte ID. Das alte Mosaik bleibt, so entsteht ein Verlauf („Rückgängig“ = zur `basis` zurück). Änderungen sammeln sich: Ein bearbeitetes Mosaik lässt sich weiter bearbeiten. Erlaubt ist jede Farbe, die es für Teilarten und Stil als 1×1-Teil gibt, auch außerhalb einer eigenen Palette.

### 6. Paletten und Presets

**Eigene Palette** (z. B. „Graustufen“, „Pastell“) anlegen und benutzen:

```ts
await fetch(`${API}/paletten`, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ name: "Graustufen", beschreibung: "", farb_ids: [0, 72, 71, 15] }) });
form.append("palette", "graustufen");      // beim Erzeugen
```

Die Antwort enthält `id` (aus dem Namen, z. B. `graustufen`), die Farb-IDs und `farben` mit Name und Hex-Wert zum Anzeigen. `PUT /paletten/{id}` ersetzt Name, Beschreibung und Farben, `DELETE` löscht.

**Presets** speichern Formularfelder unter einem Namen, mit Bewertung (0–5 Sterne) und Favorit:

```json
POST /presets
{ "name": "Comic klar", "beschreibung": "Flächen ohne Rauschen",
  "einstellungen": { "glaettung": "mittel", "stil": "formen", "palette": "graustufen" },
  "favorit": true, "bewertung": 4 }
```

- `einstellungen` nutzen die Feldnamen von `POST /mosaike` (ohne `bild` und `titel`), dazu `palette`. Der Server prüft sie wie beim Erzeugen (`422` bei ungültigen Werten).
- `GET /presets?sortierung=bewertung|nutzung|name|neu&favoriten=true`: Favoriten stehen immer oben; `nutzungen` zählt, wie oft ein Preset für ein Mosaik benutzt wurde.
- `PATCH /presets/{id}` mit `{ "favorit": true }` oder `{ "bewertung": 5 }` für Stern- und Herz-Buttons.
- `POST /mosaike/{id}/preset` mit `{ "name": "Wie Hund" }` speichert die Einstellungen eines fertigen Mosaiks.

Gespeichert wird lokal in `daten/paletten.json` und `daten/presets.json`.

### 7. Preis anzeigen und bei BrickLink bestellen (optional)

`POST /mosaike/{id}/preise` fragt die Preise beim BrickLink Price Guide ab und antwortet, wenn alle da sind (bei einem großen Mosaik einige Sekunden; danach kommen die Preise 7 Tage lang aus dem Cache). Der Server braucht dafür BrickLink-Zugangsdaten (siehe README); ohne sie kommt `503`. Mit `?offline=true` nimmt er nur gespeicherte Preise.

```json
{
  "waehrung": "EUR",
  "summe_verkauft": 41.37,
  "summe_angeboten": 52.10,
  "vollstaendig": false,
  "fehlend": 1,
  "stand": "2026-10-13T08:12:44+00:00",
  "zustand": "N", "region": "europe", "land": null, "mwst": "Y",
  "upload_url": "https://www.bricklink.com/v2/wanted/upload.page",
  "positionen": [
    {
      "teil": "3024", "bricklink_teil": "3024", "name": "Plate 1 x 1",
      "farbe": "Black", "bricklink_farbe": 11, "anzahl": 212,
      "stueck_verkauft": 0.0213, "stueck_angeboten": 0.0301,
      "summe_verkauft": 4.52, "summe_angeboten": 6.38, "hinweis": ""
    }
  ]
}
```

Anzeigen: `summe_verkauft` als „ca. 41,37 €“ (Ø Verkäufe der letzten 6 Monate), `summe_angeboten` als Vergleich. Ist `vollstaendig` false, fehlen Preise für `fehlend` Positionen; der Grund steht in `hinweis`. Dieselben Summen stehen danach auch in `GET /mosaike/{id}` unter `preise`; `GET /mosaike/{id}/preise` liefert die ganze Antwort erneut.

**Bei BrickLink bestellen:** Die BrickLink-API kann keine Wanted Lists anlegen. `GET /mosaike/{id}/bricklink` liefert deshalb die XML als Text (`xml`, z. B. für einen Button „In Zwischenablage kopieren“) und `upload_url`. Dort fügt man die XML ein oder lädt `teileliste_bricklink.xml` hoch. `ausgelassen` nennt Farben ohne BrickLink-Farbcode, die nicht in der Liste stehen.

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
| `raster.json` | Farbe jeder Noppe | Dasselbe wie `GET /mosaike/{id}/raster` |
| `original.jpg`/`.png`/… | Das hochgeladene Bild | Vergleich vorher/nachher |
| `eingaben.json` | Eingaben und bisherige Änderungen (für `bearbeiten`) | Nicht nötig |
| `ergebnis.json` | Konfiguration, Statistik, Palette, Teileliste, Dateiliste | Nicht nötig, steht schon in der Antwort |
| `preise.json` | Preisschätzung (erst nach `POST …/preise`) | Wie die Antwort von `POST …/preise` |
| `preise_bricklink.csv` | Preise für Excel (`;`, Dezimalkomma) | Download |

Welche Dateien entstehen, steuert das Feld `ausgaben`. `ergebnis.json` und `raster.json` entstehen über die API immer, Originalbild und `eingaben.json` ebenfalls (abschaltbar mit `storage.keep_source`). Heruntergeladen werden kann nur, was in `dateien` steht.

## Fehler

Alle Fehler kommen als `{"fehler": "…"}` mit einem deutschen Satz, der sich direkt anzeigen lässt.

| Status | Bedeutung | Beispiel |
| --- | --- | --- |
| `400` | Datei ist kein lesbares Bild | PDF statt JPG hochgeladen |
| `404` | Mosaik, Datei, Palette oder Preset gibt es nicht | Alte ID, Ausgabeordner gelöscht, Preset gelöscht |
| `413` | Datei zu groß (Standard: 20 MB) | Foto in voller Auflösung |
| `422` | Ungültige oder fehlende Angaben | `format` und `platten` zusammen, `relief_hoehe=5`, Noppe im Rahmen umfärben, Preset-Name schon vergeben |
| `500` | Verarbeitungsfehler | Teilekatalog fehlt und ist nicht ladbar |
| `502` | BrickLink antwortet nicht oder mit Fehler | Keine Internetverbindung, IP nicht für den Token freigegeben |
| `503` | BrickLink-Zugangsdaten fehlen | Umgebungsvariablen nicht gesetzt; `?offline=true` geht trotzdem |

## Grenzen

- **Keine Anmeldung:** Der Server ist für den eigenen Rechner gedacht und bindet nur an `127.0.0.1`. Nicht öffentlich erreichbar machen.
- **Keine Liste:** Es gibt keine Liste aller Mosaike und kein Löschen. Die IDs merkt sich das Frontend, die Dateien liegen in `ausgabe/`.
- **Synchron:** Pro Anfrage ein Mosaik, die Antwort kommt am Ende (siehe oben). Das gilt auch für `bearbeiten`: Es rechnet das ganze Mosaik neu.
- **Hintergrund ohne KI:** Die Erkennung arbeitet mit Randfarbe und Toleranz. Sie passt für Produktfotos, Logos und Porträts vor ruhigem Hintergrund, nicht für unruhige Szenen.
