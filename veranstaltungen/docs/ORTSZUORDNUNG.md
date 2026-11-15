# Ortszuordnung vor der Kartenansicht

Der Import ordnet Spielorte automatisch zu, ohne Termindaten zu entfernen oder
die veröffentlichte Ortsangabe zu ersetzen. Eine Karte ist noch nicht eingebaut.
Nur `venue.status = resolved` enthält einen Kartenpunkt. Fehlende Angaben,
ähnliche Namen und mehrere mögliche Orte werden ohne Koordinate veröffentlicht.
Es gibt keinen Ersatzpunkt am Rathaus oder in der Ortsmitte.

## Einmalige Prüfung

Die fertige Prüfliste liegt unter `/veranstaltungen/orte-pruefen.html` und ist
auch als einzelne HTML-Datei offline benutzbar. Die Ansicht beginnt mit den
unsicheren Orten; weitere Filter zeigen fehlende Angaben oder bereits zugeordnete
Spielorte. Jeder Vorschlag hat einen Ortsbeleg, Koordinaten und Beispieltermine
mit Originalquellen. Verschiedene Kartenobjekte werden nicht stillschweigend
zu einem scheinbar eindeutigen Treffer zusammengezogen.

1. Passenden Vorschlag auswählen oder „Kein fester Kartenpunkt“ wählen.
2. Falls kein Vorschlag passt, die richtige Adresse als Hinweis eintragen.
3. Mit „Entscheidungen herunterladen“ die Datei `ortsentscheidungen.json` speichern.
4. Die Datei zur Übernahme zurückgeben oder im Projektverzeichnis importieren:

   ```sh
   python scripts/review_venues.py --apply /pfad/ortsentscheidungen.json
   ```

Die Übernahme schreibt `data/venues/reviewed.json`. Diese Änderung ins Repository
übernehmen; danach nutzt jede reguläre GitHub Action die Entscheidungen. Eine
Browserauswahl allein ändert das Repository nicht. Die Datei wird vollständig
validiert, bevor Entscheidungen geschrieben werden: Kontext und gewählter
Kartenkandidat müssen noch zur aktuellen Prüfliste passen. Hinweise ohne
bestätigten Ort bleiben offen; sie erfinden keine Koordinate.

Eine Zuordnung gilt für die normalisierte Ortsangabe und ihren Ortskontext,
unabhängig von Datum, Titel und Herausgeber eines neuen Termins. Bei allgemeinen
Bezeichnungen wie „Clubhaus“ gehört auch der Veranstalter zum Kontext, bei
fehlendem Veranstalter zusätzlich die Quelle. So werden zwei Vereinsheime im
gleichen Ort nicht durch eine Entscheidung miteinander verwechselt. Bekannte
Programmzusätze derselben Zentralbibliothek werden als dasselbe Gebäude behandelt;
andere Filialen und benannte Theaterspielstätten bleiben getrennt.

## Automatischer Ablauf

`update_calendar.py` führt die Zuordnung nach dem Zusammenführen der Quellen aus.
Die bestehende Action braucht keine weiteren Secrets oder einen zusätzlichen
Zeitplan. Nach dem Merge werden Ortsdaten, Prüfliste und Caches mit `data/` und
`dist/` durch den vorhandenen Workflow gespeichert und mit veröffentlicht.

- Bestätigte Zuordnungen werden direkt aus dem Register übernommen. Die Auswahl
  hängt nicht davon ab, ob der Suchdienst gerade erreichbar ist.
- Bei Walzbachtal werden zuerst die ausdrücklich veröffentlichten Kartenpunkte
  der Originalseiten gelesen. Mehrere Punkte und Punkte außerhalb des erwarteten
  Ortsbereichs müssen ebenfalls geprüft werden. Die Ortsbereiche sind vorsichtige
  Suchfenster und keine amtlichen Verwaltungsgrenzen.
- Andere neue Ortsangaben werden über Photon gesucht. Nur ein einzelner genauer
  Namens- oder vollständiger Adresstreffer im passenden Ortsbereich darf automatisch
  einen Punkt liefern. Ein Straßenname ohne Hausnummer reicht nicht. Unscharfe
  Namensähnlichkeit dient nur als Vorschlag für die Prüfung.
- Suchergebnisse einschließlich leerer Antworten werden dauerhaft gespeichert.
  Pro regulärem Lauf werden höchstens zwölf neue Abfragen versucht, seriell und
  mit Abstand. Nach zwei Netzfehlern stoppt die zusätzliche Suche. Fehlversuche
  werden frühestens nach einem Tag wiederholt. Die Kalenderquellen laufen weiter.
  Noch nicht versuchte Ortsangaben stehen als `queued` in der automatischen
  Warteschlange und werden nicht schon als manuelle Prüfaufgabe ausgegeben.
- Neue unklare Orte erscheinen in einem einzigen verwalteten GitHub Issue
  `Quelle prüfen: Ortszuordnung`. Solange die Liste unverändert ist, erfolgt kein
  erneutes Update. Schließen des Issues bestätigt keine Ortszuordnung.

Die Suchabfragen laufen ausschließlich beim Import, nicht im Browser der
Kalenderbesucher. Normale Tagesaktualisierungen brauchen keine LLM-Aufrufe.
Manuelle Arbeit fällt für echte Mehrdeutigkeiten und neue, nicht eindeutig
zuordenbare Spielorte an, nicht für jeden neuen Termin.

## Dateien und Aufrufe

| Datei | Zweck |
| --- | --- |
| `data/venues/config.json` | Suche aktivieren/abschalten, Photon-Endpunkt, Abrufbudget und Prüflink. |
| `data/venues/reviewed.json` | Dauerhafte Bestätigungen, bewusste Auslassungen und offene Hinweise. |
| `data/venues/lookup-cache.json` | Photon-Ergebnisse mit tatsächlichem Abrufdatum, Originalabfrage und OSM-Beleg. |
| `data/venues/publisher-cache.json` | Ortskoordinaten der Originalseiten mit Beleg und Abrufdatum. |
| `dist/place-review.json` | Aktuelle gruppierte Prüfliste und Abdeckungszahlen. |
| `dist/orte-pruefen.html` | Selbstständige Prüfansicht mit eingebetteten Daten und Entscheidungsexport. |

```sh
# Prüfliste ohne zusätzliche Netzabrufe neu erzeugen
python scripts/review_venues.py

# Bis zu zwölf neue Ortsangaben versuchen und Ergebnisse speichern
python scripts/review_venues.py --online --max-new 12

# Gesamten Kalender aus gespeicherten Quellen und Ortsdaten bauen
python scripts/update_calendar.py --offline
```

Das optionale Feld `event.venue` enthält `key`, `status`, `method` und `place`.
`place` ist nur bei `resolved` gesetzt und enthält unter anderem stabile
Orts-ID, Name, Adresse, `latitude`, `longitude` und Beleg-URL. `method` unterscheidet
`publisher`, `automatic` und `reviewed`. Darauf kann die spätere Karte aufbauen.
Die ursprünglichen Quellen, Konflikte und Ortsangaben bleiben erhalten.

Photon erlaubt eine maßvolle Nutzung des öffentlichen Servers, bietet aber keine
Verfügbarkeitsgarantie. Der Endpunkt ist austauschbar; für größere Datenmengen
ist ein eigener Dienst vorgesehen. OSM-Ergebnisse und ihre Koordinaten stehen
unter ODbL und werden getrennt von der Lizenz der Veranstaltungstexte ausgewiesen:
[Photon](https://github.com/komoot/photon#demo-server),
[OpenStreetMap-Mitwirkende und Lizenz](https://www.openstreetmap.org/copyright).
