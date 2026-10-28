# LEGO-Mosaik-Service

Lokaler Service, der aus einem Bild ein **baubares LEGO-Mosaik aus echten Teilen** erzeugt:

1. Vorher lässt sich das Bild bearbeiten: drehen, spiegeln, zuschneiden, Farbfilter (Graustufen, Sepia, warm, kalt, …), Helligkeit, Kontrast, Sättigung. Der Hintergrund wird erkannt und kann entfernt (einfarbig), aufgehellt, abgedunkelt, entsättigt oder weichgezeichnet werden.
2. Das Bild wird auf eine Größe gebracht: als Format (z. B. `quadrat` = 3 × 3 LEGO-Art-Paneele à 16 × 16 Noppen, `weltkarte` = 8 × 5) oder frei in Noppen. Freie Größen rasten auf ganze Platten ein, damit der Unterbau immer genau passt. Für große Bilder gibt es einen Unterbau aus großen Platten (16 × 16, 32 × 32, 48 × 48), bis 384 Noppen pro Seite.
3. Die Farben werden per Dithering auf echte LEGO-Farben reduziert, auf Wunsch nur auf die einer eigenen Palette.
4. Die Palette besteht nur aus Farben, die es wirklich als 1×1-Teil gibt (Rebrickable-Daten).
5. Gleichfarbige Flächen werden zu möglichst wenigen, real existierenden Plates und Tiles zusammengefasst.
6. An Konturen kommen Formteile dazu (Standard), inspiriert von LEGO Art und Brick Sketches: Viertel- und Halbkreise, runde Tiles, Dreieck-Tiles und Wedge Plates (2 × 2, 3 × 2, 4 × 2, 2 × 4). Alternativ besteht das ganze Bild aus runden „Punkten“ bzw. „Noppen“. Ein Relief hebt Motiv oder helle bzw. dunkle Bereiche auf Plates an.
7. Der Unterbau hält alles zusammen: LEGO-Art-Paneele mit Technic-Pins oder große Platten mit einer Lage Plates auf Lücke, darauf ein (optional erhöhter) Rahmen.
8. Ausgaben entstehen:
   - Vorschau
   - LDraw-Datei für Bricklink Studio
   - Teilelisten (BrickLink, Rebrickable, LEGO)
   - Bauanleitung wie ein LEGO-Heft (Querformat, Titelblatt mit eigenem Namen, Teileliste mit Element-IDs) als PDF
9. Danach lassen sich einzelne Noppen oder Flächen umfärben; Teile, Listen und Anleitung werden neu berechnet.
10. Einstellungen lassen sich als Preset speichern, mit Bewertung (0–5 Sterne) und Favoriten.
11. Auf Abruf schätzt der Service den Preis über die BrickLink-API (Ø Verkäufe der letzten 6 Monate und aktuelle Angebote) und schreibt die Wanted List für BrickLink neu.

Bedienung über eine deutsche Kommandozeile (auch interaktiv mit Menü) oder eine lokale HTTP-API. Alle nutzen dieselbe Pipeline.

## Frontend und Repos

| Teil | Repo | Inhalt |
| --- | --- | --- |
| Backend | [LevinTheDoctor/lego-mosaik](https://github.com/LevinTheDoctor/lego-mosaik) (dieses Repo) | Pipeline, CLI, HTTP-API; Cloud-Variante mit Helm-Chart (PostgreSQL, Mosaik-Volume z. B. auf iSCSI) |
| Frontend | [LevinTheDoctor/pic2brick](https://github.com/LevinTheDoctor/pic2brick) | Web-Oberfläche „Pic2Brick“: Foto bearbeiten, Mosaik in 3D, Noppen-Editor, Preise |

Pic2Brick nutzt ausschließlich die [HTTP-API](#http-api). Lokal laufen beide nebeneinander (`uv run lego-mosaik server` hier, `npm run dev` im Frontend). Für den Betrieb auf k3s liegen Frontend-Chart, Werte für beide Charts und die Anleitung im Frontend-Repo, siehe [Deployment auf k3s](#deployment-auf-k3s).

## Setup

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Python 3.15. uv lädt Python bei Bedarf selbst.

```bash
uv sync                                  # Abhängigkeiten installieren
uv run lego-mosaik teile-aktualisieren   # Rebrickable-Daten laden (ca. 2 MB)
uv run lego-mosaik erstellen katze.jpg   # erstes Mosaik
```

Optional, damit `lego-mosaik` global im Terminal verfügbar ist:

```bash
uv tool install --editable .
```

Die Pfade `daten/` und `ausgabe/` gelten relativ zum aktuellen Ordner. Der Befehl muss deshalb im Projektordner aufgerufen werden.

**Bricklink Studio (empfohlen, optional):** Ist Studio installiert (macOS: `/Applications/Studio 2.0`), liest der Service dessen Farbtabelle. Damit stimmen die LDraw-Farbcodes sicher, es gibt BrickLink-Farbcodes für die Wanted List, offizielle LEGO-Farbnamen und Farbkategorien (siehe [Daten und Annahmen](#daten-und-annahmen)).

## Befehle

| Befehl | Zweck |
| --- | --- |
| `lego-mosaik erstellen BILD` | Mosaik erzeugen |
| `lego-mosaik interaktiv [BILD]` | Menügeführt: Bild laden, Optionen einstellen, Vorschau im Terminal, Mosaik ausgeben |
| `lego-mosaik teile-aktualisieren [--erzwingen]` | Rebrickable-CSV-Dateien laden |
| `lego-mosaik farben` | Palette als Tabelle mit Farbfeld anzeigen |
| `lego-mosaik formate` | Formate mit Größe in Noppen |
| `lego-mosaik verfahren` | Registrierte Dithering-, Zerlegungs-, Stil- und Ausgabe-Namen |
| `lego-mosaik hintergrund BILD [--toleranz 12] [--maske maske.png]` | Hintergrund erkennen, passende LEGO-Farbe vorschlagen, Maske speichern |
| `lego-mosaik bearbeiten [ID] --noppe 5,7=15 --flaeche 2,2,4,3=0 --fuellen 20,20=1` | Noppen, Rechtecke oder Flächen umfärben (ohne ID: das neueste), siehe [Mosaik bearbeiten](#mosaik-bearbeiten) |
| `lego-mosaik paletten [anlegen NAME --farben 0,15 \| loeschen NAME]` | Eigene Farbpaletten anzeigen, anlegen, löschen |
| `lego-mosaik presets [--sortierung bewertung\|nutzung\|name\|neu] [--favoriten]` | Presets anzeigen; Unterbefehle `speichern NAME [ID]`, `favorit NAME [--aus]`, `bewerten NAME STERNE`, `loeschen NAME` |
| `lego-mosaik preise [ID] [--offline] [--erzwingen]` | Preis eines Mosaiks bei BrickLink schätzen (ohne ID: das neueste), siehe [BrickLink-Preise](#bricklink-preise) |
| `lego-mosaik server [--host] [--port] [--cors URL]` | HTTP-API starten (Standard `127.0.0.1:8000`) |

Hilfe gibt es mit `-h`, `--hilfe` oder `--help`, auch für jeden Befehl (`lego-mosaik erstellen --hilfe`). `--debug` vor dem Befehl zeigt ausführliche Logs und Tracebacks.

Optionen von `erstellen` (in der Hilfe nach Gruppen sortiert):

| Option | Bedeutung |
| --- | --- |
| **Größe und Unterbau** | |
| `--unterbau art\|platten\|grundplatte\|ohne` | LEGO-Art-Paneele (mit Pins), große Platten (mit Plates verbunden), lose Grundplatten oder nichts |
| `--plattengroesse 16\|32\|48` | Größe einer Platte in Noppen (Art-Paneel: 16) |
| `--format quadrat` | Format aus `lego-mosaik formate` (statt `--platten`) |
| `--platten 3x3` | Anzahl Platten (Breite × Höhe) |
| `--breite 50` / `--hoehe 70` | Eigene Größe in Noppen statt Platten, rastet auf ganze Platten ein; nur eine Seite → die andere aus dem Seitenverhältnis |
| `--unterbaufarbe 0` | Farbe der Paneele bzw. Grundplatten (Rebrickable-ID) |
| `--modus zuschneiden\|einpassen` | Bild mittig zuschneiden oder ganz einpassen |
| **Rahmen** | |
| `--rahmen 1` / `--rahmenfarbe 0` | Rahmenbreite in Noppen (0 = ohne) und Farbe |
| `--eckenfarbe 15` | Eigene Farbe für die vier Ecken |
| `--rahmen-teile tile` | Teilarten des Rahmens (glatt mit `tile`) |
| `--rahmen-erhoehung 1` | Rahmen um so viele Plates höher als das Bild (0 = bündig) |
| **Bild bearbeiten** | |
| `--kontrast 1.2` / `--saettigung 1.1` / `--helligkeit 0.9` | Bild vorher anpassen (1.0 = unverändert) |
| `--farbfilter sepia` / `--filter-staerke 0.6` | `ohne`, `graustufen`, `sepia`, `warm`, `kalt`, `invertieren`, `poster`, `vintage`; Stärke 0–1 mischt mit dem Original |
| `--drehen 90` / `--spiegeln` | Im Uhrzeigersinn drehen (90, 180, 270), waagerecht spiegeln |
| `--ausschnitt 10,0,80,100` | Ausschnitt x, y, Breite, Höhe in Prozent (des gedrehten Bilds) |
| **Hintergrund** | |
| `--hintergrund entfernen` | `behalten`, `entfernen` (einfarbig), `aufhellen`, `abdunkeln`, `entsaettigen`, `weichzeichnen` |
| `--hintergrundfarbe 15` | Neue Farbe bei `entfernen` (Standard: Randfarbe White) |
| `--hintergrund-toleranz 12` | So ähnlich (ΔE) muss ein Pixel der Randfarbe sein; größer = mehr Hintergrund |
| `--hintergrund-staerke 0.5` | Stärke für aufhellen, abdunkeln, entsättigen, weichzeichnen (0–1) |
| **Farben, Paletten und Presets** | |
| `--teile plate,tile` | Erlaubte Teilarten des Bilds (`brick` nur allein) |
| `--farbkategorien solid,pearl` | Erlaubte Farbkategorien |
| `--exotische-farben` | Alle Kategorien außer Gummi (Metallic, Pearl, Chrome, Transparent, …) |
| `--max-farben 12` | Höchstens so viele Farben (0 = alle) |
| `--palette graustufen` / `--farb-ids 0,72,71,15` | Nur mit den Farben einer eigenen Palette bzw. diesen Farb-IDs dithern |
| `--preset comic` | Gespeichertes Preset als Grundlage; angegebene Optionen gehen vor |
| **Stil und Relief** | |
| `--stil formen\|quadrat\|punkte\|noppen` | Formteile und Wedges an Konturen (Standard), nur eckige Teile, alles runde Tiles oder alles runde Plates |
| `--relief ohne\|motiv\|hell\|dunkel` | Motiv erhöht (Hintergrund flach) bzw. helle oder dunkle Farben höher |
| `--relief-hoehe 2` | Höchste Relief-Stufe in Plates (1–3) |
| **Verfahren und Ausgaben** | |
| `--dithering atkinson` / `--staerke 0.8` | Verfahren und Stärke |
| `--glaettung aus\|leicht\|mittel\|stark` | Rauschen glätten (Standard `leicht`); `mittel` und `stark` geben klare Flächen für Logos und Comics, Augen und andere kontrastreiche Details bleiben |
| `--aufraeumen` | Einzelne Pixel durch die Nachbarfarbe ersetzen (Comics und Logos; zusammen mit `--staerke 0` am ruhigsten) |
| `--farbabstand cie76\|ciede2000` | Formel für den Farbabstand (schnell bzw. genau) |
| `--zerlegung greedy` | Zerlegungsverfahren |
| `--ausgaben preview_png,ldraw` | Nur bestimmte Ausgaben |
| `--ziel ordner/` | Ausgabeordner |
| `--titel "Mein Bild"` | Name auf dem Titelblatt und im Ordnernamen (Standard: Name der Bilddatei) |
| `--preise` | Danach den Preis bei BrickLink schätzen (braucht Zugangsdaten) |

Beispiele:

```bash
uv run lego-mosaik erstellen katze.jpg --platten 3x3 --rahmen-erhoehung 1 --eckenfarbe 15
uv run lego-mosaik erstellen katze.jpg --format quer --relief motiv --titel "Unsere Katze"
uv run lego-mosaik erstellen katze.jpg --unterbau platten --plattengroesse 32 --format weltkarte
uv run lego-mosaik erstellen katze.jpg --format quadrat --stil punkte   # Look wie LEGO Art
uv run lego-mosaik erstellen katze.jpg --breite 64            # Höhe aus dem Seitenverhältnis
uv run lego-mosaik erstellen katze.jpg --unterbau grundplatte --plattengroesse 48 --platten 1x1
uv run lego-mosaik erstellen katze.jpg --exotische-farben --dithering atkinson
uv run lego-mosaik erstellen comic.png --glaettung mittel   # klare Flächen, keine Mischfarben an Kanten
uv run lego-mosaik erstellen logo.png --glaettung stark --stil punkte
uv run lego-mosaik erstellen produkt.jpg --hintergrund entfernen --hintergrundfarbe 0 --relief motiv
uv run lego-mosaik erstellen portrait.jpg --farbfilter sepia --hintergrund weichzeichnen --drehen 90
uv run lego-mosaik paletten anlegen Graustufen --farben 0,72,71,15
uv run lego-mosaik erstellen katze.jpg --palette graustufen --preset comic
uv run lego-mosaik interaktiv katze.jpg
uv run lego-mosaik farben --teile tile --farbkategorien solid,pearl
uv run lego-mosaik teile-aktualisieren --erzwingen
```

### Interaktiver Modus

`lego-mosaik interaktiv` öffnet ein Menü im Terminal:

| Taste | Aktion |
| --- | --- |
| `1` | Bild laden (Pfad eingeben; Größe, Format und Seitenverhältnis werden angezeigt, dazu passende Formate) |
| `2`–`9` | Größe und Unterbau, Rahmen, Bild anpassen (Kontrast, Helligkeit, Drehen, Spiegeln, Ausschnitt), Farbfilter und Hintergrund, Farben und Teile, Stil und Relief, Verfahren, Name und Ausgaben |
| `10` | Alle wirksamen Einstellungen als Tabelle |
| `11` | Vorschau im Terminal: das Mosaik als farbige Blöcke, ohne Dateien zu schreiben |
| `12` | Mosaik erstellen und alle Dateien ausgeben |
| `13` | Farbpalette für die aktuellen Teilarten und Farbkategorien |
| `14` | Formate mit Größe in Noppen |
| `15` | Preset laden (Liste mit Sternen und Favoriten, Name des Mosaiks bleibt) |
| `16` | Aktuelle Einstellungen als Preset speichern |
| `17` | Preis des zuletzt erstellten Mosaiks bei BrickLink schätzen |
| `0` | Beenden (auch Strg+D) |

Unter dem Bild steht im Menü der Name des Mosaiks; ändern lässt er sich unter `9` (Name und Ausgaben). Bei jeder Frage steht der aktuelle Wert dabei. Enter behält ihn, `-` setzt den Standard aus `CONFIG` zurück. Format und eigene Größe schließen sich aus: Wer ein Format wählt, verwirft die eigene Größe und umgekehrt. Ungültige Angaben werden mit einer deutschen Meldung abgelehnt; die vorherigen Einstellungen bleiben dann gültig. Das Bild kann auch direkt mitgegeben werden: `lego-mosaik interaktiv katze.jpg`.

Exit-Codes: `0` Erfolg, `1` Verarbeitungsfehler (z. B. Download), `2` ungültige Eingabe.

## Konfiguration

Alle Standardwerte stehen **an einer Stelle**: `CONFIG` in `src/lego_mosaik/main.py` (öffnen mit `nvim src/lego_mosaik/main.py`). CLI-Optionen und API-Felder überschreiben einzelne Werte nur für einen Lauf.

| Bereich | Wichtige Werte (Standard) |
| --- | --- |
| `layout` | Unterbau LEGO-Art-Paneele (16 × 16), 3 × 3 Platten = 48 × 48 Noppen, freie Größe aus, 2 Pins pro Naht |
| `frame` | 1 Noppe Schwarz, Ecken wie Rahmen, nur Tiles, eine Plate erhöht |
| `mosaic` | Zuschneiden, Randfarbe White, Kontrast/Sättigung/Helligkeit 1.0, kein Farbfilter (`color_filter`, `filter_strength`), keine Drehung (`rotate`, `mirror`, `crop`), Hintergrund behalten (`background`, `background_color_id`, `background_tolerance` ΔE 12, `background_amount` 0,5) |
| `palette` | Teilarten Plate + Tile, Kategorie `solid`, `include_color_ids`, `exclude_color_ids`, eigene Palette `only_color_ids`, `max_colors`, seltene Farben zusammenlegen (`min_color_share` 0,2 %) |
| `shapes` | Stil `formen`, erlaubte Formteile (inklusive Wedges), 6 Teilpixel pro Noppe, Mindestgewinn 20 % und ΔE 3, Unterlagen erlaubt |
| `relief` | aus; Stufen 1, Farbe der Stützlagen wie der Unterbau |
| `dithering` | `floyd_steinberg`, Stärke 1.0, CIEDE2000, Serpentine, Aufräumen aus (`despeckle`), Glättung `leicht` (`smoothing`) |
| `tiling` | `greedy`, längste Teilseite 8, Zerlegung pro Abschnitt |
| `outputs` | `preview_png`, `ldraw`, `parts_list_csv`, `official_parts_lists`, `instructions_png`, `raster_json`, `result_json` |
| `instructions` | Abschnitte 16 × 16, A4 quer, Kamera der 3D-Bilder (60° Höhe, 0° Drehung), Bauschritte senkrecht von oben, Größe der Schrittansicht, Schriften (normal und fett) |
| `source` | Rebrickable-URL, Höchstalter 7 Tage, automatische Aktualisierung, nur aktuelle Teile (`recent_years` 3), Pfad zur Studio-Farbtabelle |
| `server` | `127.0.0.1:8000`, Upload bis 20 MB, Katalogprüfung alle 24 h, CORS für `localhost` mit jedem Port (`cors_origin_regex`, `cors_origins`) |
| `storage` | Paletten in `daten/paletten.json`, Presets in `daten/presets.json`, Originalbild und Eingaben im Mosaik aufheben (`keep_source`, nötig für „bearbeiten“) |
| `pricing` | BrickLink-Preise in EUR, Verkäufer aus Europa (`region`, `country_code`), neu (`condition`), mit MwSt. (`vat`), Cache 7 Tage in `daten/bricklink/preise.json`, 4 Anfragen gleichzeitig, kein Höchstpreis in der Wanted List (`max_price_factor`) |

**Exotische Farben:**
- `color_categories` legt fest, welche Oberflächen erlaubt sind: `solid`, `transparent`, `metallic`, `pearl`, `chrome`, `glitter`, `speckle`, `satin`, `milky`, `rubber`.
- Einzelne Farben lassen sich mit `include_color_ids` trotzdem erlauben oder mit `exclude_color_ids` sperren.

**Nur aktuelle Farben (`source.recent_years`):**
- `elements.csv` enthält auch Elemente, die es seit Jahren nicht mehr gibt, z. B. das alte Grau und Dunkelgrau (2004 durch Medium und Dark Stone Grey ersetzt), Earth Orange oder Light Reddish Violet. Sonst landen beide Grau-Generationen im selben Bild.
- Deshalb gilt eine (Teil, Farbe)-Kombination nur, wenn sie in einem Set der letzten 3 Jahre vorkommt. Das ergibt sich aus `sets.csv` (Jahr), `inventories.csv` und `inventory_parts.csv` von Rebrickable (zusammen ca. 15 MB, werden mit aktualisiert, Einlesen ca. 1,5 s).
- Teile, die in keinem dieser Sets vorkommen (Rebrickable führt z. B. Paneele und Pins dort unter anderer Nummer), behalten alle Farben, die nicht ausgelaufen sind. Fehlen die Inventare (offline), wird nur das letzte Jahr der Farbe (`y2` in `colors.csv`) geprüft.
- `recent_years=None` erlaubt wieder alle historischen Kombinationen.

**Farben zusammenlegen und Aufräumen:**
- `min_color_share`: Farben, die nach dem Dithering auf weniger als 0,2 % der Bildfläche liegen (bei 46 × 46 also auf höchstens 4 Noppen), fallen weg; das Bild wird ohne sie neu gedithert. So verschwinden Farben, die nur für ein einziges Teil gekauft werden müssten.
- `smoothing` (`--glaettung`): Gleichfarbige Flecken aus wenigen Noppen übernehmen die Farbe ihrer Umgebung, aber nur, wenn die Farben ähnlich sind. Ein dunkelbrauner Fleck im Schwarz oder Hellgrau im Weiß verschwindet, eine schwarze Pupille im weißen Auge (ΔE 93) bleibt.

  | Stufe | Farbe je Noppe | Flecken bis | ähnlich bis (ΔE2000) | 3×3-Mehrheit |
  | --- | --- | --- | --- | --- |
  | `aus` | Dithering | – | – | – |
  | `leicht` (Standard) | Dithering | 2 Noppen | 25 | – |
  | `mittel` | Mehrheitsfarbe | 3 Noppen | 30 | – |
  | `stark` | Mehrheitsfarbe | 5 Noppen | 55 (auch Glanzlichter) | ab 6 von 9 Zellen |

  Die **Mehrheitsfarbe** ersetzt ab `mittel` das Dithering: Jede Noppe wird in 4 × 4 Teilpixel zerlegt (beim Stil `formen` dessen Detailbild), und die häufigste Farbe gewinnt. Kanten ergeben dadurch keine Mischfarben mehr (Gelb + Schwarz ≠ Olive Green). Für Fotos mit Verläufen besser bei `leicht` bleiben.
- Neutrale Grautöne bleiben neutral: Fast farblose Pixel bekommen bei der Farbsuche einen Aufschlag für getönte Farben. Ein Grau zwischen White und Light Bluish Gray wird so nicht mehr zu Light Aqua.
- `despeckle` (`--aufraeumen`): Pixel ohne gleichfarbigen Nachbarn (auch diagonal) bekommen die häufigste Nachbarfarbe. Einnoppige Linien wie ein schwarzer Umriss bleiben erhalten. Für Comics mit klaren Flächen ist `--staerke 0 --aufraeumen` am ruhigsten; bei Fotos lieber aus lassen.

**Größe:**
- Entweder ein Format, Platten × Plattengröße oder frei in Noppen (`width_studs`, `height_studs`), jeweils 8 bis 384 Noppen pro Seite.
- Formate (`lego-mosaik formate`): `mini` 1 × 1, `klein` 2 × 2, `quadrat` 3 × 3 (wie die meisten LEGO-Art-Sets), `gross` 4 × 4, `riesig` 5 × 5, `quer` 4 × 3, `hoch` 3 × 4, `breit` 6 × 4, `panorama` 6 × 3, `weltkarte` 8 × 5 (wie LEGO Art 31203). Angegeben in Platten; mit Art-Paneelen ist `quer` also 64 × 48 Noppen, mit 32er-Platten 128 × 96.
- Freie Größen rasten mit Unterbau auf ganze Platten ein (kaufmännisch gerundet: 40 → 48, 23 → 16). So steht nie ein Paneel über und keins fehlt. Ohne Unterbau (`--unterbau ohne`) bleibt die Größe exakt.
- Fehlt bei freier Größe eine Seite, bekommt die Bildfläche innerhalb des Rahmens das Seitenverhältnis des Bilds (EXIF-Drehung berücksichtigt); danach rastet auch sie ein.
- Der interaktive Modus schlägt nach dem Laden die Formate vor, deren Seitenverhältnis am besten zum Bild passt.

**Stil (`shapes`):**
- `formen` (Standard): Das Bild wird zusätzlich feiner ausgewertet (6 × 6 Teilpixel pro Noppe). An Konturen setzt der Service Tile 1 × 1 Quarter Circle (25269), Tile 1 × 1 Half Circle (24246), Tile Round 1 × 1 (98138), Tile 2 × 2 Triangular (35787), Wedge Plate 2 × 2 Cut Corner (26601) und die Wedge Plates 2 × 2 links/rechts (24299/24307), 3 × 2 (43722/43723), 4 × 2 (41769/41770) und 2 × 4 (51739) in der passenden Drehung, wenn sie die Kontur deutlich genauer treffen als volle Noppen. Die langen Wedges zeichnen flache Diagonalen nach. In den Lücken ist der Untergrund zu sehen. Mit `underlay` (Standard) darf ein Formteil auf einer Plate in zweiter Farbe liegen, wie die Plate-Stapel bei Brick Sketches: Dann zeigt die Lücke z. B. die Nachbarfarbe, und das Formteil steht eine Plate höher.
- `punkte`: jede Noppe ein Tile Round 1 × 1, wie LEGO Art. Die Palette enthält dann nur Farben, in denen es dieses Teil gibt.
- `quadrat`: nur eckige Plates und Tiles (wie früher).
- `noppen`: jede Noppe eine Plate Round 1 × 1 („Studs“, BrickLink 4073).
- `punkte` und `noppen` brauchen einen Unterbau (er ist zwischen den Punkten zu sehen). `formen` geht auch ohne: Dann liegt jedes Formteil auf einer Unterlage. Mit Bricks gehen Formteile nicht.

**Relief (`relief`):**
- `motiv`: Der Hintergrund (häufigste Randfarbe, soweit vom Rand aus zusammenhängend) bleibt flach, alles andere liegt `levels` Plates höher.
- `hell` / `dunkel`: Helle bzw. dunkle Farben liegen höher, in `levels` + 1 gleich großen Stufen.
- Unter erhöhten Teilen liegen Stützen in der Farbe des Unterbaus: Wo drei Plates übereinander kämen, steht ein Brick (so hoch wie drei Plates). Das spart zwei Drittel der Stützteile und Bauschritte; der Rest wird mit Plates aufgefüllt. Ein Bildteil reicht nie über eine Stufe; 2 × 2-Formteile liegen nur auf gleich hohen Noppen.

**Unterbau (`substrate`):**
- `ART_PANEL` (Standard): LEGO-Art-Paneele „Brick 16 x 16 x 1 1/3 with Pin Holes“ (65803). Sie haben Technic-Löcher an den Seiten; an jeder Naht stecken `pins_per_seam` Technic-Pins (2780). So hält das ganze Bild zusammen.
- `PLATES` (`--unterbau platten`): große Platten für große Bilder. Bei `--plattengroesse 16` eine echte Plate 16 × 16 (91405), bei 32 und 48 die Grundplatten 3811 und 4186. Liegen mehrere nebeneinander, kommt darauf eine **Verbindungslage** aus Plates (bevorzugt 16 × 16, 8 × 16, 6 × 16, 2 × 16), „auf Lücke“ versetzt: Jede Naht liegt in jeder Zeile und Spalte mitten unter einer Plate (die Pipeline prüft das). Das Bild liegt dann eine Plate höher. Damit sind z. B. 128 × 96 aus 12 Grundplatten 32 × 32 oder die Weltkarte aus 48er-Platten (384 × 240) möglich.
- `BASEPLATE`: dünne Grundplatten 3867 (16 × 16), 3811 (32 × 32) und 4186 (48 × 48), lose nebeneinander. Mehrere davon halten nur über Rahmen und Teile an den Nähten zusammen; die Pipeline warnt deshalb bei mehr als einer Grundplatte.
- `NONE`: ohne Unterbau.
- Ohne `substrate_color_id` gilt: Paneele Black, große Platten und Grundplatten Light Bluish Gray (die 32er- und 48er-Grundplatten gibt es nicht in Schwarz). Ob es das Teil in der Farbe gibt, wird gegen die Rebrickable-Daten geprüft.

**Rahmen (`frame`):**
- Der Rahmen liegt auf dem äußeren Ring; das Bild füllt die Fläche innen.
- `corner_color_id`: eigene Farbe für die Ecken (`width` × `width` Noppen).
- `part_kinds`: eigene Teilarten, z. B. nur `TILE` für einen glatten Rahmen wie bei LEGO Art.
- `raise_plates`: hebt den Rahmen um so viele Plates über das Bild. Darunter liegen Plates in Rahmenfarbe, „auf Lücke“ verlegt (Mauerverband): Ihre Fugen liegen nie auf einer Naht des Unterbaus, sie verbinden die Platten also zusätzlich am Rand.

## Bild bearbeiten

Vor dem Dithering durchläuft das Bild diese Schritte (alle optional, Werte in `CONFIG.mosaic` bzw. als Option):

1. **Drehen und spiegeln** (`--drehen 90|180|270`, `--spiegeln`).
2. **Ausschnitt** (`--ausschnitt x,y,Breite,Höhe` in Prozent des gedrehten Bilds). Danach schneidet `--modus zuschneiden` wie gewohnt auf das Seitenverhältnis zu. Eine fehlende Seite (`--breite` ohne `--hoehe`) richtet sich nach dem gedrehten Ausschnitt.
3. **Hintergrund erkennen** (nur wenn `--hintergrund` nicht `behalten` ist).
4. **Farbfilter** (`--farbfilter`, `--filter-staerke`): `graustufen`, `sepia` (Braun bis Creme), `warm` (mehr Rot/Gelb), `kalt` (mehr Blau), `invertieren`, `poster` (8 Tonstufen pro Kanal), `vintage` (verblasst, leicht sepia). Mit Stärke unter 1 wird mit dem Original gemischt.
5. **Helligkeit, Kontrast, Sättigung.**
6. **Hintergrund anpassen.**

**Hintergrund:** Erkannt wird die häufigste Farbe am Bildrand. Zum Hintergrund gehört jedes Pixel, das ihr ähnlich ist (ΔE ≤ `--hintergrund-toleranz`, Standard 12) und zusammenhängend vom Rand aus erreichbar ist. Gleichfarbige Flächen mitten im Motiv (z. B. ein weißes Hemd vor weißer Wand, wenn der Kopf dazwischen liegt) bleiben deshalb Motiv. Kleine Lücken werden geschlossen. Das funktioniert gut bei Produktfotos, Logos, Porträts vor ruhigem Hintergrund; bei unruhigen Hintergründen hilft eine höhere Toleranz. Ein KI-Modell wird bewusst nicht verwendet (offline, keine 170-MB-Downloads).

| `--hintergrund` | Wirkung |
| --- | --- |
| `behalten` | nichts (Standard, keine Erkennung) |
| `entfernen` | Jede Hintergrund-Noppe bekommt genau `--hintergrundfarbe` (Standard: Randfarbe White), ohne Dithering-Rauschen. Große einfarbige Flächen werden zu großen Plates und sparen Teile. Die Farbe muss es als 1×1-Teil für Teilarten und Stil geben. |
| `aufhellen` / `abdunkeln` | Hintergrund Richtung Weiß bzw. Schwarz (`--hintergrund-staerke`) |
| `entsaettigen` | Hintergrund grau, das Motiv bleibt farbig |
| `weichzeichnen` | Hintergrund unscharf: weniger Unruhe und weniger 1×1-Teile |

Mit `--relief motiv` liegt bei gesetztem Hintergrund-Modus genau das erkannte Motiv erhöht (statt der häufigsten Randfarbe im fertigen Raster).

`lego-mosaik hintergrund BILD` zeigt vorher, was erkannt wird: Randfarbe, Anteil an der Fläche und die nächste LEGO-Farbe der Palette; `--maske maske.png` speichert die Maske (weiß = Hintergrund). Über die API gibt es dafür `POST /bild/hintergrund` und `POST /bild/vorschau` (bearbeitetes Bild als PNG).

## Eigene Paletten und Presets

**Eigene Paletten** legen fest, mit welchen Farben gedithert wird, z. B. eine Graustufen- oder Pastell-Palette oder genau die Farben, die man schon zu Hause hat:

```bash
uv run lego-mosaik farben                                   # IDs nachsehen
uv run lego-mosaik paletten anlegen Graustufen --farben 0,72,71,15 --beschreibung "Schwarz bis Weiß"
uv run lego-mosaik paletten                                 # Liste mit Farbfeldern
uv run lego-mosaik erstellen katze.jpg --palette graustufen
uv run lego-mosaik erstellen katze.jpg --farb-ids 0,15      # ohne Speichern
```

Eine Palette ersetzt die Farbkategorien: Erlaubt sind genau ihre Farben, sofern es sie als 1×1-Teil der Teilarten und des Stils gibt (sonst erscheint eine Warnung und die Farbe fehlt). `--max-farben` und `min_color_share` wirken danach wie gewohnt. Die Kennung (`graustufen`) entsteht aus dem Namen.

**Presets** speichern Einstellungen unter einem Namen, mit Bewertung (0–5 Sterne), Favorit und Nutzungszähler:

```bash
uv run lego-mosaik erstellen comic.png --staerke 0 --aufraeumen --stil formen
uv run lego-mosaik presets speichern "Comic klar"            # Einstellungen des neuesten Mosaiks
uv run lego-mosaik presets bewerten comic-klar 5
uv run lego-mosaik presets favorit comic-klar
uv run lego-mosaik presets --sortierung nutzung              # Favoriten stehen immer oben
uv run lego-mosaik erstellen hund.png --preset comic-klar --rahmen 2   # Angaben gehen vor
```

- Ein Preset enthält alle Optionen von `erstellen` außer Bild, Titel und Zielordner, auch `palette` (die Palette wird erst beim Benutzen aufgelöst).
- Beim Speichern wird geprüft, ob die Einstellungen gültig sind.
- Sortierung: `bewertung` (Standard, dann Nutzung), `nutzung`, `name`, `neu` (zuletzt geändert); Favoriten immer zuerst.
- Gespeichert wird in `daten/paletten.json` und `daten/presets.json` (`CONFIG.storage`). Die Dateien werden atomar geschrieben; der Server sperrt sie bei parallelen Anfragen.
- Im interaktiven Modus: Menüpunkte „Preset laden“ und „Einstellungen als Preset speichern“. Über die API: `/paletten`, `/presets`, `POST /mosaike/{id}/preset`.

## Mosaik bearbeiten

Nach dem Erzeugen lassen sich einzelne Noppen umfärben, etwa um ein Auge zu korrigieren oder störende Pixel zu entfernen:

```bash
uv run lego-mosaik bearbeiten --noppe 12,20=15 --noppe 12,21=15        # neuestes Mosaik
uv run lego-mosaik bearbeiten 2026-10-13_101500_katze --flaeche 2,2,10,4=0
uv run lego-mosaik bearbeiten --fuellen 3,3=1                           # Farbeimer
```

- **Koordinaten** `ZEILE,SPALTE` zählen ab 1 **inklusive Rahmen**, genau wie die Zeilen- und Spaltennummern in Vorschau und Bauanleitung. Der Rahmen selbst ist fest.
- `--noppe Z,S=FARBE` färbt eine Noppe, `--flaeche Z,S,BREITE,HOEHE=FARBE` ein Rechteck, `--fuellen Z,S=FARBE` die zusammenhängende gleichfarbige Fläche (Farbeimer). Jede Option geht mehrfach.
- Erlaubt ist jede Farbe, die es für Teilarten und Stil als 1×1-Teil gibt, auch außerhalb einer eigenen Palette.
- Das Mosaik wird **komplett neu berechnet**: Formteile, Relief, Zerlegung, Teilelisten und Anleitung passen danach zum Bild. An umgefärbten Noppen setzt der Stil `formen` keine Rundungen nach dem alten Bild.
- Es entsteht ein **neuer Ordner**, der alte bleibt (`basis` in `ergebnis.json` zeigt auf den Vorgänger). Die Änderungen sammeln sich in `eingaben.json`; ein bearbeitetes Mosaik lässt sich weiter bearbeiten.
- Dafür speichert jeder Lauf das Originalbild (`original.jpg` …) und die Eingaben (`eingaben.json`). Mit `storage.keep_source=False` entfällt das, dann geht „bearbeiten“ nicht.
- Für einen Editor im Browser liefert die API `GET /mosaike/{id}/raster` (Farbe jeder Noppe) und `POST /mosaike/{id}/bearbeiten`.

## Ausgaben

Jeder Lauf schreibt in einen eigenen Ordner, z. B. `ausgabe/2026-10-12_143005_katze/`:

| Name | Datei(en) | Inhalt |
| --- | --- | --- |
| `preview_png` | `vorschau.png` | Draufsicht mit Noppen und Teilekanten |
| `ldraw` | `mosaik.ldr` | Modell für Bricklink Studio, Bauschritte je Abschnitt |
| `parts_list_csv` | `teileliste.csv`, `teileliste_rebrickable.csv` | Eigene Liste (Semikolon, für Excel) und Rebrickable-Import |
| `official_parts_lists` | `teileliste_bricklink.xml`, `teileliste_lego.csv` | BrickLink Wanted List und Liste mit LEGO-Element-IDs |
| `instructions_png` | `anleitung_01.png` …, `anleitung.pdf` | Bauanleitung im LEGO-Stil mit 3D-Renderbildern (siehe unten) |
| `raster_json` | `raster.json` | Farb-ID jeder Noppe (Zeilen von oben), Rahmenbreite, Farben; für Editoren |
| `result_json` | `ergebnis.json` | Konfiguration, Statistik, Palette, Teileliste, Dateiliste |
| (`storage.keep_source`) | `original.jpg` …, `eingaben.json` | Originalbild, Eingaben und Änderungen (für [Mosaik bearbeiten](#mosaik-bearbeiten)) |

Nach `lego-mosaik preise` kommen `preise.json` und `preise_bricklink.csv` dazu, und `ergebnis.json` bekommt das Feld `preise` mit den Summen.

**Kennzahlen in `ergebnis.json` (`statistik`)** zum Vergleichen von Läufen: `teile`, `farben` (dieselbe Zahl wie Titelblatt und Teileliste), `altfarben` (Farben, die zuletzt vor dem Zeitraum `recent_years` in einem Set vorkamen; sollte 0 sein), `anteil_1x1`, `seiten` der Anleitung, außerdem Formteile, Relief-Teile, Unterlagen, Stützteile und die Dauer pro Schritt.

Alle Teilelisten sind nach Farbfamilien sortiert (Grautöne von Weiß bis Schwarz, dann Rot, Gelb, Grün, Blau, Violett, Pink), innerhalb einer Farbe nach Teilart und Größe.

## Bauanleitung

`anleitung.pdf` sieht aus wie ein LEGO-Heft und entsteht vollautomatisch ohne externe Programme. Gerendert wird mit einem eigenen kleinen 3D-Renderer auf Basis von Pillow (`outputs/iso_render.py`). Seiten im Querformat (A4 quer), gebaut wird erst die stabile Grundlage, dann das Bild.

1. **Titelblatt:** dunkler Hintergrund mit gelbem Band, der Name des Mosaiks groß (`--titel`, im interaktiven Modus unter „Name und Ausgaben“), das fertige Mosaik in 3D, Teilezahl, Größe in Noppen (mit Rahmen zusätzlich die Bildfläche, z. B. „48 × 48 Noppen (Bild 46 × 46)“) und Zentimetern, Farben (wie in der Teileliste)
2. **So wird gebaut:** Ablauf mit den echten Schrittnummern („Schritte 3–4: Rahmen bauen“), Karte der Abschnitte, Legende der Zeichen und alle Farbkürzel mit Farbfeld und Namen
3. **Grundlage:** Unterbau auslegen, Paneele mit Pins verbinden (Verbindungsplan) bzw. große Platten mit der Verbindungslage, dann Rahmen-Unterbau und Rahmen
4. **Pro Abschnitt** (16 × 16, also genau ein Paneel), jeder Schritt auf eigener Seite:
   - oben links die große Schrittnummer, darunter klein „Abschnitt 3/9 · Teil 2 von 4“ (bewusst ohne Zeilenangabe: lange Teile reichen über ihren Block hinaus)
   - daneben der hellblaue Teile-Kasten mit „3x“ und Farbkürzel unter jedem Teilbild, nach Farbfamilie und Größe sortiert
   - im ersten Schritt eines Abschnitts oben rechts die Fortschrittskarte: gebaut farbig, offen grau, aktueller Abschnitt rot umrandet
   - darunter die Ansicht **senkrecht von oben** mit Zeilen- und Spaltennummern am Rand; neue Teile orange umrandet und mit Farbkürzel beschriftet, Formteile mit Umriss und Drehung, der Unterbau hell (sonst wären schwarze Teile auf schwarzen Paneelen kaum zu sehen)
   - zuerst die Relief-Lagen („Relief, Lage 1“), dann die Unterlagen der Formteile als eigener Schritt („Unterlagen“: farbige Plates, deren Farbe neben runden und schrägen Teilen zu sehen ist), dann das Bild
5. **Fertig:** das ganze Mosaik in 3D und was es zusammenhält
6. **Teileliste** wie hinten in LEGO-Heften: nach Farbfamilien, jedes Teil mit Stückzahl, Farbkürzel, LEGO-Farbname und Element-ID (ohne bekannte Element-ID die BrickLink-Nummer)

Die Seitenzahl steht unten rechts. Schriften: die erste vorhandene aus `font_paths` bzw. `bold_font_paths` in `CONFIG.instructions`.

**Kamera:** Die Bauschritte sind senkrecht von oben gezeichnet: unverzerrt, jede Noppe ein Kreis, erhöhte Teile liegen genau über ihren Zellen. Titelblatt, Schlussseite und Teilbilder zeigen das Mosaik in 3D, gerade von vorne und steil von oben (wie bei LEGO Art); einstellbar über `view_elevation_deg` und `view_azimuth_deg` in `CONFIG.instructions`.

**Farben:** Bereits gebaute Teile behalten ihre echte Farbe. Ausgeblasste Farben würden wie andere LEGO-Farben aussehen.

**Schritte:** Die Bauschritte sind exakt dieselben wie in `mosaik.ldr`.

### Optional: Bricklink Studio

`mosaik.ldr` lässt sich in Studio über *File › Import › Import Model …* öffnen. Studio übernimmt die Bauschritte. Im Instruction-Modus (Button *Instruction* bzw. *Tool › Instruction Maker*) kann man daraus ebenfalls ein PDF exportieren (*Export* oben rechts). Die eigene Anleitung ist dafür aber nicht nötig.

Zur Ausrichtung: `scripts/orientierung.py` erzeugt kleine Testmodelle (`ausgabe/orientierung/`), darunter `formteile/` mit jedem Formteil in vier Drehungen und einer Vorschau zum Vergleich. `scripts/ldraw_achsen_pruefen.py` prüft die Teileausrichtung automatisch gegen die LDraw-Bibliothek von Studio.

## Teile bestellen

- **BrickLink:** `teileliste_bricklink.xml` unter *Want › Upload* hochladen (https://www.bricklink.com/v2/wanted/upload.page). Teilenummern und Farbcodes sind BrickLink-Werte, z. B. Tile 1 × 1 = `3070`, nicht Rebrickables `3070b`. Jede Position hat einen Zustand (`<CONDITION>N</CONDITION>` = neu); nach `lego-mosaik preise` mit `max_price_factor` auch einen Höchstpreis (`<MAXPRICE>`). Über die HTTP-API liefert `GET /mosaike/{id}/bricklink` dieselbe XML zum Einfügen.
- **Rebrickable:** `teileliste_rebrickable.csv` (`Part,Color,Quantity`) über den CSV-Import einer Teileliste.
- **LEGO Pick a Brick:** `teileliste_lego.csv` enthält Element-IDs und offizielle LEGO-Farbnamen zum Suchen. Ob ein Element dort verfügbar ist, wird nicht geprüft.

## BrickLink-Preise

`lego-mosaik preise` schätzt, was die Teile eines fertigen Mosaiks bei BrickLink kosten. Pro Teil und Farbe zeigt es zwei Durchschnittspreise aus dem BrickLink Price Guide: den der **Verkäufe der letzten 6 Monate** (nach Menge gewichtet) und den der **aktuellen Angebote**. Die Summe aus den Verkaufspreisen ist die Schätzung. Positionen ohne Preis (kein BrickLink-Farbcode, keine Verkäufe) stehen mit Hinweis in der Tabelle, die Summe gilt dann als unvollständig.

```bash
uv run lego-mosaik preise                       # neuestes Mosaik in ausgabe/
uv run lego-mosaik preise 2026-10-13_101500_katze
uv run lego-mosaik preise --offline             # nur gespeicherte Preise, ohne Zugangsdaten
uv run lego-mosaik erstellen katze.jpg --preise # gleich nach dem Erzeugen
```

**Zugang einrichten (einmalig):**

1. Auf bricklink.com unter *My BrickLink › Settings › API* einen API-Zugang registrieren (https://www.bricklink.com/v2/api/register_consumer.page). Dabei die IP-Adresse angeben, von der der Service abfragt. Der Token gilt nur für diese IP.
2. Die vier Werte als Umgebungsvariablen setzen, z. B. in `~/.zshrc`:

   ```bash
   export BRICKLINK_CONSUMER_KEY=…
   export BRICKLINK_CONSUMER_SECRET=…
   export BRICKLINK_TOKEN_VALUE=…
   export BRICKLINK_TOKEN_SECRET=…
   ```

Die Zugangsdaten stehen bewusst nicht in `CONFIG`, damit sie weder in `ergebnis.json` noch in Git landen.

**Abfragen und Cache:** Gefragt wird nur auf Abruf, `erstellen` bleibt offline. Jede Position braucht zwei Anfragen; BrickLink erlaubt 5.000 pro Tag. Deshalb landen alle Preise in `daten/bricklink/preise.json` und gelten 7 Tage (`max_age_days`). Ein zweiter Aufruf fragt nur fehlende oder veraltete Preise ab. `--erzwingen` fragt alles neu ab, `--offline` nimmt nur den Cache (auch veraltete Preise).

**Ergebnis im Mosaik-Ordner:**
- `preise.json`: alle Positionen mit Stückpreisen und Summen (dieselben Daten wie die API)
- `preise_bricklink.csv`: dasselbe für Excel (Semikolon, Dezimalkomma)
- `teileliste_bricklink.xml`: neu geschrieben, mit `max_price_factor` (z. B. `1.5`) inklusive Höchstpreis = Ø Verkaufspreis × Faktor
- `ergebnis.json`: Feld `preise` mit Summen, Währung und Stand

**Direkt zu BrickLink:** Die BrickLink-API kann keine Wanted Lists anlegen. Der Weg ist immer die XML: Datei auf der Upload-Seite hochladen oder ihren Inhalt dort einfügen. Die Tabelle nennt den Link, die HTTP-API liefert XML und Link über `GET /mosaike/{id}/bricklink`.

## HTTP-API

```bash
uv run lego-mosaik server        # danach http://127.0.0.1:8000/docs im Browser
```

**Für Frontends:** [`docs/api-frontend.md`](docs/api-frontend.md) beschreibt den Ablauf (Formular aus `/optionen`, Upload, Dateien anzeigen), CORS, Fehler und das Erzeugen von TypeScript-Typen.

| Methode | Pfad | Zweck |
| --- | --- | --- |
| `GET` | `/gesundheit` | Status und Stand des Teilekatalogs |
| `GET` | `/optionen` | Standardwerte, erlaubte Werte und Grenzen aller Formularfelder, Formate |
| `GET` | `/farben?teile=plate,tile&exotisch=false` | Palette |
| `GET` | `/verfahren` | Registrierte Verfahren und Ausgaben |
| `POST` | `/bild/hintergrund` | Multipart: `bild` plus `hintergrund_toleranz`, `drehen`, `ausschnitt`, … → Randfarbe, Anteil, nächste LEGO-Farbe, Maske als PNG-Data-URL |
| `POST` | `/bild/vorschau` | Multipart: `bild` plus Bearbeitungsfelder (`farbfilter`, `hintergrund`, `drehen`, `ausschnitt`, …) und `groesse` → bearbeitetes Bild als PNG |
| `POST` | `/mosaike` | Multipart: Datei `bild` plus optionale Felder wie bei `erstellen` (z. B. `unterbau`, `format`, `breite`, `stil`, `relief`, `farbfilter`, `hintergrund`, `hintergrundfarbe`, `drehen`, `ausschnitt`, `farb_ids`, `titel`), dazu `preset` und `palette` → `201` mit ID, Statistik, Download-URLs (wartet, bis das Mosaik fertig ist) |
| `GET` | `/mosaike/{id}` | Inhalt von `ergebnis.json` |
| `GET` | `/mosaike/{id}/dateien/{dateiname}` | Datei herunterladen (nur Namen aus `ergebnis.json`) |
| `GET` | `/mosaike/{id}/raster` | Farbe jeder Noppe (`raster.json`) |
| `POST` | `/mosaike/{id}/bearbeiten` | JSON `{"noppen": […], "flaechen": […], "fuellen": […]}` → `201`, neues Mosaik mit `basis` |
| `POST` | `/mosaike/{id}/preset` | JSON `{"name": …, "bewertung": 0–5, "favorit": …}` → Einstellungen des Mosaiks als Preset |
| `GET` / `POST` | `/paletten` | Eigene Paletten auflisten / anlegen (`{"name", "beschreibung", "farb_ids"}`) |
| `GET` / `PUT` / `DELETE` | `/paletten/{id}` | Palette lesen / ersetzen / löschen |
| `GET` / `POST` | `/presets?sortierung=bewertung&favoriten=false` | Presets auflisten (Favoriten zuerst) / anlegen (`{"name", "einstellungen", "favorit", "bewertung"}`) |
| `GET` / `PUT` / `PATCH` / `DELETE` | `/presets/{id}` | Preset lesen / ersetzen / nur `favorit` und `bewertung` setzen / löschen |
| `POST` | `/mosaike/{id}/preise?offline=false&erzwingen=false` | Preis bei BrickLink schätzen (wie `lego-mosaik preise`) |
| `GET` | `/mosaike/{id}/preise` | Gespeicherte Preisschätzung (`preise.json`) |
| `GET` | `/mosaike/{id}/bricklink` | Wanted List als XML-Text, Upload-Link, Positionen, ausgelassene Farben |
| `POST` | `/teile/aktualisieren?erzwingen=false` | Katalog aktualisieren |
| `GET` | `/openapi.yaml` bzw. `/openapi.json` | API-Beschreibung (OpenAPI 3) |

**OpenAPI:** Die vollständige Beschreibung aller Pfade, Felder und Fehlerantworten liegt als [`openapi.yaml`](openapi.yaml) im Projekt (OpenAPI 3.1). Sie wird aus der App erzeugt (`uv run python scripts/openapi_exportieren.py`); ein Test schlägt fehl, wenn sie nicht mehr zur API passt. Damit lassen sich z. B. Clients generieren oder die API in Postman/Bruno importieren. Auswahlfelder haben ihre erlaubten Werte als `enum`, Zahlen ihre Grenzen, und alle Antworten (auch `statistik` und `ergebnis.json`) sind vollständig beschrieben.

```bash
curl -F bild=@katze.jpg -F format=quadrat -F stil=formen -F relief=motiv http://127.0.0.1:8000/mosaike
```

Fehler kommen als `{"fehler": "…"}` mit `400` (kein Bild), `404` (auch unbekanntes Preset oder unbekannte Palette), `413` (zu groß), `422` (ungültige Angaben), `502` (BrickLink-Fehler) oder `503` (BrickLink-Zugangsdaten fehlen).

**CORS:** Webseiten auf `localhost`, `127.0.0.1` oder `[::1]` dürfen die API mit jedem Port aufrufen (z. B. Vite auf `:5173`), mit `GET`, `POST`, `PUT`, `PATCH` und `DELETE`. Weitere Adressen gibt `--cors http://192.168.1.20:5173` frei (mehrfach möglich).

> **Sicherheit:** Die API hat **keine Authentifizierung**. Sie bindet deshalb standardmäßig nur an `127.0.0.1`. Bitte nicht öffentlich erreichbar machen.

## Katalog regelmäßig aktualisieren

Im Server-Modus prüft ein Hintergrund-Task den Katalog beim Start und danach alle 24 Stunden. Ohne Server übernimmt das ein **systemd-User-Timer** (Linux):

`~/.config/systemd/user/lego-mosaik-update.service`

```ini
[Unit]
Description=LEGO-Mosaik: Rebrickable-Teilekatalog aktualisieren

[Service]
Type=oneshot
WorkingDirectory=%h/projekte/lego-mosaik
ExecStart=%h/.local/bin/uv run lego-mosaik teile-aktualisieren
```

`~/.config/systemd/user/lego-mosaik-update.timer`

```ini
[Unit]
Description=LEGO-Mosaik: Katalog täglich aktualisieren

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
nvim ~/.config/systemd/user/lego-mosaik-update.service   # Pfade anpassen
systemctl --user daemon-reload
systemctl --user enable --now lego-mosaik-update.timer
systemctl --user list-timers                              # prüfen
journalctl --user -u lego-mosaik-update.service           # Logs
```

`Persistent=true` holt einen verpassten Lauf nach, z. B. wenn der Rechner aus war. Unter macOS erfüllt ein `launchd`-Agent (`~/Library/LaunchAgents/`) denselben Zweck.

## Cloud-Variante

Neben der lokalen App gibt es eine Cloud-Variante im selben Repository (uv-Workspace-Paket `cloud/`, Befehl `lego-mosaik-cloud`). Sie nutzt dieselbe Pipeline und dieselbe HTTP-API, speichert aber Bilder und alle erzeugten Dateien auf einem Volume (z. B. iSCSI-LUN) und Katalog, Paletten, Presets, Preis-Cache und Mosaik-Metadaten in PostgreSQL. Dazu kommen Actuator-Endpunkte wie bei Spring Boot (`/actuator/health`, `/actuator/prometheus`, …), JSON-Logs und OpenTelemetry-Tracing.

```bash
uv sync --all-packages                          # Kern + Cloud-Paket
docker compose up --build -d                    # PostgreSQL und App lokal
docker compose run --rm app katalog-import      # Teilekatalog laden
helm install lego deploy/helm/lego-mosaik       # Kubernetes (mit PostgreSQL und Mosaik-Volume)
```

Details zu Aufbau, Einstellungen, Metriken und Helm-Chart: [`docs/cloud.md`](docs/cloud.md). Die lokale App bleibt davon unberührt.

### Deployment auf k3s

Zusammen mit dem Frontend läuft die Cloud-Variante auf k3s so:

- Release `lego-mosaik` dieses Charts (`deploy/helm/lego-mosaik`) und Release `pic2brick` des Frontend-Charts im selben Namespace.
- Das Backend bleibt `ClusterIP` ohne Ingress. Die API hat keine Anmeldung und ist deshalb nur über nginx im Frontend erreichbar (`/api/` → `http://lego-mosaik:80/`). nginx begrenzt die Anfragen pro Client-IP und sperrt `/actuator` und `/teile/aktualisieren` nach außen.
- Von außen erreichbar ist nur das Frontend: über die IP des Servers (Traefik) und über einen Cloudflare Tunnel.
- BrickLink-Zugangsdaten kommen aus dem Secret `lego-mosaik-bricklink` (`config.extraEnvFrom`); das Datenbank-Passwort erzeugt das Chart. Die Mosaik-Dateien liegen auf einer iSCSI-LUN der Synology (StorageClass `synology-iscsi-storage`).
- Images: GitHub Actions (`.github/workflows/ci.yml`) baut bei jedem Push `ghcr.io/levinthedoctor/lego-mosaik:sha-<commit>` für amd64 und arm64.

Werte (`deploy/k3s/lego-mosaik.values.yaml`), Deploy-Skript und die vollständige Liste aller Secrets und Einstellungen: [Pic2Brick → `deploy/KONFIGURATION.md`](https://github.com/LevinTheDoctor/pic2brick/blob/main/deploy/KONFIGURATION.md).

## Architektur

```text
src/lego_mosaik/
├── main.py            Composition Root: CONFIG + Verdrahtung (einzige Stelle mit konkreten Klassen)
├── config.py          Dataclasses der Konfiguration, FORM_FIELDS, OverrideInput → MosaicOverrides, Validierung
├── registry.py        Name → Fabrikfunktion für Dithering, Zerlegung, Ausgaben
├── pipeline.py        Orchestrierung; kennt nur Protocols und Domain
├── domain/            Modelle, Palette, Layout (Formate, Größe, Unterbau, Pins, Rahmen), Formteil-Umrisse, Relief, Bauschritte, Umfärben (editing), Paletten/Presets (library), Fehler
├── sources/           Rebrickable-CSV, Studio-Farbtabelle, Updater (+ protocol.py)
├── imaging/           Laden, Drehen/Ausschnitt/Farbfilter (filters), Hintergrund (background), Einpassen, Farbraum Lab (+ protocol.py)
├── dithering/         nearest, error_diffusion (Floyd-Steinberg, Atkinson, Sierra Lite), ordered
├── tiling/            single_stud, greedy (+ protocol.py)
├── shaping/           Stile: plain (quadrat), contour (formen), dots (punkte, noppen) (+ protocol.py)
├── outputs/           Vorschau, LDraw, Teilelisten, offizielle Listen, Preislisten, 3D-Renderer, Pläne, Anleitung, Raster, JSON
├── pricing/           BrickLink-Preise: OAuth 1.0a, API-Client, Cache, Rechnung, Ablauf (+ protocol.py)
├── storage/           Paletten und Presets als JSON (atomar, threadsicher), Library löst Preset/Palette auf
└── interfaces/        cli.py (Typer), interactive.py (Menü), display.py (Rich-Ausgaben), api.py (FastAPI)
```

**Ablauf:** Katalog laden → Palette (ggf. eigene), Farben und Unterbau prüfen → Größe bestimmen (Format, ggf. aus dem Seitenverhältnis des gedrehten Ausschnitts, auf ganze Platten eingerastet) → Bild bearbeiten (drehen, Ausschnitt, Hintergrund erkennen, Filter, anpassen) und einpassen (für `formen` zusätzlich feiner) → nach Lab umrechnen → dithern (bei `max_colors` zweimal, seltene Farben zusammenlegen, ggf. aufräumen) → entfernten Hintergrund und Änderungen aus `bearbeiten` einsetzen → Rahmen samt Ecken einsetzen → Relief-Höhen bestimmen → Formteile pro Abschnitt wählen → übrige Noppen pro Abschnitt und Höhe zerlegen, Rahmen am Stück, Stützlagen → Unterbau und Pins bzw. Verbindungslage platzieren → alle Writer (`result_json` zuletzt).

**Lagen im Mosaik:** `Mosaic` trennt Unterbau, Verbinder, Verbindungslage, Stützteile, Rahmen, Relief (Bricks und Plates), Unterlagen der Formteile und Bild. Ein Test prüft für mehrere Stile, dass jede Stelle pro Höhe nur von einem Teil belegt ist und jedes erhöhte Teil auf etwas steht. `domain/sections.py` macht daraus die Bauschritte (`assembly_steps`); LDraw-Datei und Anleitung nutzen genau diese Reihenfolge.

**DRY bei den Oberflächen:** CLI, interaktiver Modus und API füllen alle dasselbe `OverrideInput` (rohe Texte und Zahlen). `FORM_FIELDS` ordnet die deutschen Feldnamen (API, Presets) einmal den Feldern zu, `Library.resolve` setzt Preset und Palette davor. `parse_overrides` übersetzt an genau einer Stelle in `MosaicOverrides`, `apply_overrides` prüft das Ergebnis. Die Bildfelder der API stehen als FastAPI-Abhängigkeit nur einmal da und gelten für `/mosaike` und `/bild/…`.

**Abhängigkeitsregel:**
- `domain` hängt von nichts ab.
- Fachmodule kennen nur `domain` und ihr eigenes Protocol.
- `pipeline.py` kennt nur Protocols.
- Nur `main.py` kennt konkrete Klassen.
- `interfaces/` rufen ausschließlich die Pipeline auf.
- Wo ein Fachmodul etwas aus einem anderen braucht, gibt es ein Protocol. Beispiel: `ColorSpace` in `dithering/protocol.py` wird von `imaging/color_space.py` erfüllt (Dependency Inversion).

**Patterns:**
- Composition Root: `main.py`
- Strategy: Dithering, Zerlegung, Stil (Formwahl), Writer
- Null Object: Stil `quadrat` (`PlainShaper`) setzt keine Formteile, die Pipeline verzweigt nie
- Registry + Factory: `registry.py`
- Protocol (strukturelle Typisierung)
- Facade: `PillowImagePreparer`
- Builder: Anleitung aus Einheiten zusammensetzen, die nie über Seiten getrennt werden
- Facade + Lazy Initialization: `pipeline.preview()` rechnet ohne Dateien, die Pipeline im Menü entsteht erst beim ersten Zugriff
- Table-Driven Design: die Menüfragen stehen als Daten (`GROUPS` in `interactive.py`), eine Funktion fragt sie alle ab
- Painter's Algorithm: 3D-Verdeckung durch Zeichnen von hinten nach vorne
- Dependency Injection: Konstruktorparameter statt Konfigurationsobjekt
- Context Manager: Zeitmessung pro Schritt

**Erweitern:** Ein neues Dithering-Verfahren braucht eine neue Klasse mit `dither(image_lab, palette_lab)` und eine Zeile in `DITHERERS`. Sonst ändert sich nichts.

**Lazy Imports (PEP 810):** NumPy, scikit-image, Pillow, httpx, FastAPI und uvicorn werden mit `lazy import` geladen. `lego-mosaik --hilfe` startet in etwa 0,2 s, ohne sie zu laden (Test: `tests/test_lazy_imports.py`; messen mit `uv run python -X importtime -c "import lego_mosaik.main"`).

## Daten und Annahmen

- **Quelle:** Rebrickable-Downloads `colors.csv.gz`, `parts.csv.gz` und `elements.csv.gz`, dazu optional `sets.csv.gz`, `inventories.csv.gz` und `inventory_parts.csv.gz` für „nur aktuelle Teile“. Gelesen wird über die Spaltennamen. Die Sonderfarben `-1` und `9999` sind ausgeschlossen.
- **Whitelist:** Nur Teile aus `daten/teil_geometrie.csv` werden verwendet. Die Tabelle enthält Maße, LDraw-Datei, Höhe, Achse der langen Seite, BrickLink-Nummer, Art des Umrisses (`shape`) und für eckige Formteile den Umriss als Polygon in Noppen (`outline`, Lage wie in der LDraw-Datei).
- **LDraw-Farbcodes:** Rebrickable-IDs entsprechen **nicht immer** den LDraw-Codes. Beispiel: Rebrickable 326 „Olive Green“ ist LDraw 330, LDraw 326 ist „Yellowish Green“. Deshalb gilt:
  1. Override aus `daten/farb_zuordnung.csv`
  2. Namensabgleich mit Studios Farbtabelle
  3. ohne Studio: Rebrickable-ID (ungeprüft, mit Warnung)

  Ist die Studio-Tabelle vorhanden und findet sich kein Treffer, wird die Farbe nicht verwendet, weil sie in Studio falsch erscheinen würde. Das betrifft einige sehr neue Farben.
- **Palette:** Eine Farbe kommt nur hinein, wenn es ein 1×1-Teil der erlaubten Teilarten in ihr gibt.
- **Draufsicht:** Es dürfen nur Teilarten gleicher Höhe gemischt werden (Plate + Tile; Brick nur allein).
- **Formteile:** Die Umrisse und die Grundausrichtung sind aus Studios LDraw-Bibliothek gemessen (Dreiecke und Wedges in `outline`, Rundungen in `domain/shapes.py`); die Dreieck-Schenkel sind 1,85 Noppen lang (37 LDU). Noppen auf Wedge Plates stehen dort, wo der Umriss eine ganze Noppe enthält. 25269 liegt in Studio unter `UnOfficial`, wird aber normal geladen. Ob eine Form gewählt wird, entscheidet der quadrierte Lab-Abstand (CIE76) der Teilpixel, unabhängig von `--farbabstand`.
- **LEGO-Art-Paneel und Pins (Annahmen, bitte in Studio prüfen):** 65803 ist 32 LDU hoch, der Ursprung der LDraw-Datei liegt wie bei Plates an der Oberkante. Der Pin 2780 liegt in LDraw entlang z (`ldraw_long_axis`), seine Mitte sitzt `connector_depth_ldu` = 16 LDU unter der Paneel-Oberkante. Die Pins sind gleichmäßig auf jede Naht verteilt (bei 2 Pins: Noppe 4 und 12). Die genaue Lage der Seitenlöcher von 65803 habe ich nicht verlässlich gefunden. `scripts/orientierung.py` erzeugt dafür `art_paneele/mosaik.ldr`, und `scripts/ldraw_achsen_pruefen.py` prüft die Pin-Achse gegen Studios LDraw-Bibliothek.

## Entwicklung

```bash
uv run pytest                                  # alle Tests (offline)
uv run pytest --golden-aktualisieren           # Golden Files bewusst neu erzeugen
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run python scripts/ldraw_achsen_pruefen.py  # Teileausrichtung gegen LDraw prüfen
uv run python scripts/openapi_exportieren.py   # openapi.yaml nach API-Änderungen neu schreiben
cd cloud && uv run pytest && uv run mypy       # Cloud-Variante (braucht Docker für PostgreSQL)
```

Die Tests in `tests/test_studio_reference.py` gleichen BrickLink-Nummern, Teileachsen und die Umrisse der Formteile (in allen vier Drehungen) mit einer lokalen Studio-Installation ab. Ohne Studio werden sie übersprungen.

## Grenzen

- Draufsicht mit höchstens drei Relief-Stufen; ein stehendes Mosaik aus gestapelten Bricks wäre eine weitere Strategie.
- Formteile setzt der Stil `formen` nur dort, wo sie klar besser sind; Jumper-Versatz (halbe Noppen) und gewinkelte Teile wie bei Brick Sketches gibt es nicht.
- Lose Grundplatten (`--unterbau grundplatte` mit mehreren Platten) halten nur über Rahmen und Teile an den Nähten zusammen; stabil sind `art` und `platten`.
- Die Hintergrund-Erkennung arbeitet ohne KI (Randfarbe, Toleranz, Zusammenhang). Unruhige Hintergründe oder Motive, die den Rand in Hintergrundfarbe berühren, erkennt sie nur teilweise.
- „Bearbeiten“ rechnet das ganze Mosaik neu (bei großen Mosaiken mit Anleitung einige Sekunden) und legt jedes Mal einen neuen Ordner an; alte Stände löscht der Service nicht.
- Die Bauanleitung imitiert den Aufbau eines LEGO-Hefts, nutzt aber kein LEGO-Logo.
- Kein Aufhänger (LEGO Art hat ein eigenes Element dafür) und kein Signatur-Tile.
- Die Greedy-Zerlegung findet nicht immer die minimale Teilezahl, ist aber schnell und deterministisch.
- Kein `.io`-Export, keine Prüfung der Verfügbarkeit bei Pick a Brick. Preise gibt es nur von BrickLink, und sie sind Durchschnitte: Versandkosten, Mindestbestellwerte und die Zahl der nötigen Shops fließen nicht ein.
