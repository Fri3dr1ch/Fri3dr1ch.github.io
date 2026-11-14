# Veranstaltungskalender Karlsruhe und Umgebung

Statischer Kalender aus 15 getrennt nachvollziehbaren Quellen. Der gemeinsame
Produktionsaufruf aktualisiert RSS/iCalendar, regionale Kalender und die
Änderungsprüfungen der manuell kontrollierten Grötzinger Quellen. Die
GitHub-Übernahme und Aktivierung sind in [DEPLOYMENT.md](DEPLOYMENT.md) beschrieben.

## Lokal ausführen

Python 3.12 und Node.js 24 werden in der Action verwendet. Abhängigkeiten sind
vollständig fixiert. `requirements-import.txt` dokumentiert die direkten
Python-Abhängigkeiten; für reproduzierbare Installationen gilt die Lockdatei.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts --no-audit --no-fund
python -u scripts/update_calendar.py
python -m unittest discover -s tests -v
python scripts/validate_calendar.py
npm test
```

Weitere Aufrufe:

```sh
python scripts/update_calendar.py --source karlsruhe
python scripts/update_calendar.py --source naturfreunde-soellingen
python scripts/update_calendar.py --source naturfreunde-groetzingen
python scripts/update_calendar.py --source pdfs
python scripts/update_calendar.py --offline
python scripts/update_calendar.py --check-review
python scripts/sync_github_issues.py --repo Fri3dr1ch/Fri3dr1ch.github.io --dry-run
```

`--source` akzeptiert alle IDs in `scripts/import_local_calendars.py` sowie
`karlsruhe`, `pdfs` und `regional`. Ohne Option laufen alle Quellen.
`--source regional` aktualisiert die zwölf regionalen Quellen und die beiden
PDF-Änderungsprüfungen, falls Karlsruhe bereits separat aktualisiert wurde.
Der bisherige Aufruf `import_local_calendars.py` delegiert an denselben Produktionsablauf.
`--offline` setzt gespeicherte Daten zusammen und validiert sie; es bestätigt
keinen neuen Abruf und verschiebt keine Sichtprüfung.

## Quellen und Grenzen

| Quelle | Automatischer Abruf und Umfang |
| --- | --- |
| Stadt Karlsruhe | Aktuelle RSS-Auswahl mit iCalendar-Details je Eintrag; RSS-Veranstaltungsdatum ist kein Abrufzeitpunkt. Die Feed-Auswahl ist zeitlich begrenzt. |
| Grötzingen Jahresprogramm | Kontrollierter Extrakt der Ausgabe 2026; Links und PDF-Hash werden erneut geprüft. |
| Grötzingen Kultur-Natur-Termine | Kontrollierter Extrakt für das zweite Halbjahr 2026; Links und PDF-Hash werden erneut geprüft. |
| Durlacher.de | Veröffentlichte mehrseitige Vorschau und Detailseiten. |
| Weingarten (Baden) | Alle Ergebnisseiten der veröffentlichten Terminliste. |
| Pfinztal Gemeinde | Öffentlicher EventON-Leseabruf für sechs Monate ab Monatsanfang. |
| Pfinztal Vereinskalender | Alle Ortsteile aus der veröffentlichten PDF-Tabelle; uneindeutige Zeilen bleiben separat. |
| Gemeinde Remchingen | Vollständige veröffentlichte Ergebnisliste einschließlich öffentlicher Ratstermine. |
| Kulturhalle Remchingen | Programmliste mit Detailseiten. |
| Evangelische Kirche Grötzingen | Alle ausdrücklich veröffentlichten Termine. |
| Walzbachtal · Jöhlingen und Wössingen | Öffentlicher Kalenderabruf ab Monatsanfang mit vollständiger Seitenfolge und Ergebniszahl; alle ausdrücklich veröffentlichten Vorkommen. Der Ortsteil wird nur aus einer ausdrücklichen Ortsangabe übernommen. |
| Pfinztal Haus der Begegnungen | Eigene EventON-Kategorie; bestätigte leere Antworten sind zulässig. |
| NaturFreunde Berghausen | Aktuelle und nächste HTML-Jahresprogramme, Detailseiten und Verschiebungen. |
| NaturFreunde Söllingen | Öffentlicher iCalendar-Export mit Wiederholungen, Ausnahmen und Einzeländerungen; zwölf Monate ab Monatsanfang. |
| NaturFreunde Grötzingen | Manuell geprüfte Meldungen und Bildaushänge; automatische Änderungsprüfung und monatliche Sichtprüfung. |

Aktuelle Zahlen und Abdeckungszeiträume stehen in `dist/events.json` und nach
einem Lauf in `.etl/summary.md`. Die Quellenzahlen enthalten Überschneidungen.
Der Kalender ist keine vollständige Erfassung aller Veranstaltungen der Region.
Originalquellen bleiben bei jedem Termin sichtbar.

## Verhalten bei Fehlern und Änderungen

Der Quellenclient verwendet ausschließlich freigegebene öffentliche HTTPS-Ziele,
Zeit- und Größenlimits, begrenzte Parallelität und höchstens drei Versuche bei
vorübergehenden Netzwerkfehlern. Wiederholt ausfallende Hosts werden für den
laufenden Prozess ausgesetzt. Es werden keine Zugangsdaten an Quellen gesendet.

Bei Remchingen werden die veröffentlichte Ergebniszahl, Seitennummer und
Seitengröße abgeglichen. Überschneidende oder unvollständige zwischengespeicherte
Seiten lösen genau einen erneuten Abruf der gesamten Liste mit einem gemeinsamen
frischen Cache-Schlüssel aus. Bleibt die Liste widersprüchlich, gilt weiterhin
der letzte bestätigte Stand; doppelte Zeilen werden nicht einfach entfernt.

Jede Quelle hat einen getrennten gespeicherten Stand. Ein Abruffehler erhält die
zuletzt bestätigten Termine und deren erfolgreichen Abrufzeitpunkt; die Quelle
wird als fehlerhaft markiert. Karlsruhe kann bei einzelnen fehlenden Details
bestätigte iCalendar-Daten behalten und meldet dann einen unvollständigen Abruf.
Neue RSS-Einträge ohne abrufbare Details sind ausdrücklich als unvollständig
gekennzeichnet. Unbekannte Uhrzeiten bleiben unbekannt.
Unveränderte Karlsruher Details werden höchstens sechs Stunden wiederverwendet;
ihr wirklicher Detailprüfzeitpunkt bleibt erhalten. Geänderte RSS-Metadaten und
unvollständige Einträge werden immer erneut geprüft. Danach unterstützen ETag
und Last-Modified einen bedingten erneuten Detailabruf.

Das Skript validiert den gemeinsamen Kalender und die Gleichheit von HTML und
JSON vor der Freigabe. Exit-Codes: `0` erfolgreich, `1` gültiger Kalender mit
Quellenfehlern bzw. überfälligem Abruf, `2` Lauf oder Validierung abgebrochen.
Fällige redaktionelle Prüfungen sind Hinweise und werden als Issues verwaltet.
Die Action kann gültige, sichtbar gekennzeichnete Ersatzdaten sichern und
veröffentlichen, auch wenn eine einzelne Quelle ausfällt; der Lauf bleibt rot.
Bei fehlgeschlagener Validierung oder Tests wird nichts veröffentlicht.

Die Webseite lädt beim Öffnen das aktuelle `events.json` nach und verwendet den
neueren Stand. Bei einem Netzfehler bleibt der eingebettete Offline-Stand
benutzbar. Warnungen nennen den tatsächlichen Quellenabrufzeitpunkt. Ein
laufender ETL-Prozess aktualisiert keine bereits ausgelieferten Dateien; dafür
muss der neue Stand veröffentlicht werden. Ein bereits offener Tab benötigt
weiterhin ein Neuladen.

## Redaktionelle Prüfungen

NaturFreunde Grötzingen: Neue oder geänderte Meldungen/Bilder lösen sofort einen
Prüfhinweis aus. Zusätzlich ist am ersten Tag des nächsten Monats eine Prüfung
fällig. Ein unveränderter Abruf verschiebt die Frist nicht. Der standardisierte
Prüfauftrag und die Übernahme stehen in
[docs/naturfreunde-groetzingen-review.md](docs/naturfreunde-groetzingen-review.md).
Ein LLM kann beim Auslesen helfen; die Action startet keinen LLM.

Grötzinger PDFs: Neue oder zusätzliche Ausgaben sowie geänderte PDF-Inhalte
lösen einen Prüfhinweis aus, auch wenn eine alte Ausgabe noch verlinkt ist.
Die halbjährliche Prüfung am 15. Januar und 15. Juli sowie ein abgelaufener
Programmzeitraum bleiben zusätzliche Auslöser. Nach Kontrolle werden Extrakte,
Belege, Hash und tatsächliches `reviewedOn` aktualisiert. Die beiden freigegebenen
Dateinamen stehen in `data/groetzingen/manifest.json`. Der Prüfzeitpunkt jeder
Ausgabe zählt einzeln; das Prüfen nur einer Ausgabe erledigt nicht die andere.

```sh
python scripts/extract_pdf_candidates.py neue-ausgabe.pdf pruefextrakt.json
```

Der Extraktionsaufruf liefert Kandidaten zur anschließenden Kontrolle. Ein
Download und das manuelle Schließen eines Issues ersetzen keine Sichtprüfung.

## Zusammenführung und laufende Angebote

Gleiche Titel, Datum, bekannte Uhrzeit und Veranstaltungsort erlauben eine
konservative Zusammenführung. Bedeutungsähnliche Termine und abweichende Angaben
werden nur anhand dokumentierter Einzelentscheidungen verknüpft. Die geprüften
Zuordnungen stehen in `data/reviewed-overlaps.json`; alle Belege und Zeitkonflikte
bleiben sichtbar. Zwei Vorkommen derselben Quelle werden nicht allein wegen
ähnlicher Metadaten verschmolzen.

Die automatische Prüfung vereinheitlicht Schreibweise, Satzzeichen, „&/und“ und
die Wortreihenfolge vollständig benannter Räume. Bekannte abweichende Uhrzeiten,
Zeiträume oder Veranstalter verhindern eine automatische Zusammenführung.
Bei allgemeinen Ortsnamen wie „Hasenheim“ muss auch der Ortskontext passen.
Mehrere passende Kandidaten bleiben getrennt. Eine unscharfe Titelähnlichkeit
allein entfernt niemals einen Termin.

Die am 14.11.2026 zusätzlich geprüften Paare betreffen „Warum ein Papst die
Rockmusik ersann“, „Wölfle, Wurst & Wein“, „Vorlesezeit mit Basteln in Durlach“,
„Mit schwarzen Segeln segelt mein Schiff“ und „Nacht der Gitarren“. Für die
zusätzlichen Verknüpfungen sind Titel, Datum, Beginn und Ort abgesichert; ändern
sich die geprüften Angaben, werden die Meldungen wieder getrennt übernommen.
Die zwei Lokalschauen am 15.11. in Grötzingen und Wöschbach sowie verschiedene
Vorstellungszeiten von „Elternabend“ und „ABBA Waterloo im Bällebad“ bleiben
ausdrücklich getrennt. Allgemeine Ortsnamen erhalten einen sichtbaren Ortskontext,
laufende Angebote mit bekannter Uhrzeit zeigen ihren Quellenbeginn auf der Karte.

Söllingens Hausöffnungen erscheinen als ein laufendes Angebot mit konkreten
Öffnungstagen im ausgewählten Monat. Augustpause und bekannte Schließsonntage
werden berücksichtigt. Ohne tatsächlichen Öffnungstag erscheint das Angebot in
diesem Monat nicht. Grötzinger Schließzeiten gelten ausschließlich für Grötzingen.
Die LEGO-Ausstellung BauSteinWelten bleibt eine laufende Ausstellung mit dem
veröffentlichten letzten Ausstellungstag am 29. November.

Die Pfinztaler Tabelle enthält auch widersprüchliche bzw. unbestimmte Angaben.
Diese stehen getrennt mit Originalangabe, Grund und PDF-Seitenlink. Jahreszahlen
und unklare Daten werden nicht stillschweigend korrigiert.

Quellen und Kategorien lassen sich über Checkboxen mehrfach auswählen. Innerhalb
einer Gruppe werden passende Termine gemeinsam angezeigt; zwischen Quellen,
Kategorien und Suche müssen alle Bedingungen erfüllt sein. „Alle“ und „Keine“
steuern die ganze Gruppe, der Grötzingen-Schalter die beiden PDF-Programme.

Bei unbestimmten Terminen zeigt die Oberfläche belegte Alternativtage,
Monatsangaben oder den Kontext der Kalenderausgabe. Ist der letzte mögliche Tag,
Monat oder das betreffende Ausgabejahr abgelaufen, verschwindet der Eintrag aus
dieser Liste. Unbekannte Zeiträume ohne solche Belege bleiben offen. Das gilt
auch für die Offline-Kopie; die ursprünglichen Quelldaten bleiben nachvollziehbar.

## GitHub-Paket und Prüfungen

```sh
python scripts/build_github_bundle.py /absoluter/pfad/kalender-github.zip
```

Das Paket enthält den Workflow für die Repository-Wurzel und das Projekt unter
`veranstaltungen/`. Private Vorschau-Metadaten, Zugangsdaten und installierte
Abhängigkeiten sind ausgeschlossen. `prepare_pages.py` übernimmt die vorhandene
Website und ergänzt ausschließlich die fertige Kalenderausgabe unter
`/veranstaltungen/`.

Die Tests prüfen Datums- und Sommerzeitregeln, Mehrseitenabrufe, fehlerhafte
Strukturen, Ausnahmen, Ersatzdaten, Quellenkonflikte, Prüfintervalle,
Issue-Idempotenz sowie die GitHub-Pages-Paketierung. Bedienprüfungen verwenden
historische Fixtures mit festem Testtag und prüfen zusätzlich den aktuellen
Produktionsdatensatz. DOM-Prüfungen ersetzen keinen visuellen Browsertest.

CC0 gilt ausschließlich für den Karlsruher RSS-Datensatz. Andere Herausgeber
und Originalseiten sind getrennt gekennzeichnet. Fotos und längere Texte werden
nicht übernommen.
