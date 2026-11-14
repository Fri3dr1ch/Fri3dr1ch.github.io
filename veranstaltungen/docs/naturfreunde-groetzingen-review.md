# Monatliche Prüfung der NaturFreunde Grötzingen

Die Prüfung wird am ersten Tag des nächsten Kalendermonats fällig. Beim normalen
ETL-Aufruf werden außerdem die aktuellen Startseitenmeldungen und die Inhalte der
dort verlinkten Aushangbilder mit dem geprüften Stand verglichen. Neue Meldungen,
geänderte Texte oder ausgetauschte Bilder lösen sofort einen Prüfhinweis aus.
Ein unveränderter Abruf verschiebt die Monatsfrist nicht.

Die GitHub Action gleicht diesen Hinweis mit einem zugewiesenen Issue mit dem
Label `calendar-maintenance` ab. Es bleibt bis zur dokumentierten Prüfung offen;
erneute unveränderte Läufe erstellen keine Duplikate. Nach dem Commit des
geprüften Extrakts schließt der nächste erfolgreiche Abgleich das Issue.
Benachrichtigungen werden über die eigenen GitHub-Einstellungen zugestellt;
siehe [DEPLOYMENT.md](../DEPLOYMENT.md).

```sh
python scripts/import_local_calendars.py --source naturfreunde-groetzingen
python scripts/import_local_calendars.py --check-review
```

Der erste Aufruf verwendet weiterhin die geprüften Termine; er übernimmt keine
ungeprüften Meldungen. Abruffehler erhalten den letzten gültigen Quellenstand.
Es gibt keinen ChatGPT-Task und keinen automatischen LLM-Aufruf.

## Wiederverwendbarer Prüfauftrag

> Prüfe die aktuellen Ankündigungen auf https://www.naturfreunde-groetzingen.de/
> einschließlich der Monatsprogramme und aller Bildaushänge im Hauptinhalt.
> Vergleiche sie mit data/reviewed/naturfreunde-groetzingen.json. Lies Bilder mit
> OCR oder visuell aus. Übernimm nur ausdrücklich belegte Daten, Uhrzeiten und
> Treffpunkte. Unbekannte Uhrzeiten bleiben null; fehlende Orte bleiben leer.
> Behalte Absagen und Verschiebungen. Prüfe Datumswidersprüche zwischen Titel,
> Bild und Wochentag und dokumentiere die gewählte Belegstelle. Erfinde keine
> Ersatztermine. Gleiche Überschneidungen mit vorhandenen Quellen einzeln ab.
> Verwende keine Fotos, Telefonnummern oder längeren Beschreibungstexte als
> Kalenderinhalt. Kontrolliere die fertigen Termine nochmals gegen die Aushänge.

## Übernahme nach der Prüfung

1. In `data/reviewed/naturfreunde-groetzingen.json` die `records` aktualisieren.
   Die Felder sind `recordId`, `title`, `startDate`, `endDate`, `startTime`,
   `endTime`, `location`, `url`, `organizer`, `status`, `scheduleNote`,
   `categoryKey` und `dataQuality`. Stabile IDs bestehender Termine beibehalten.
2. Die `evidence` mit `naturfreunde.groetzingen_evidence(download(GROETZ), download)`
   neu erfassen und gegen die tatsächlich geprüften Inhalte abgleichen.
   Erst nach dieser Sichtkontrolle `reviewedOn` und `reviewNote` aktualisieren.
   Die Bilder selbst müssen nicht im Repository abgelegt werden.
3. Den Import für diese Quelle erneut ausführen, Tests prüfen und die Vorschau
   aktualisieren. `reviewRequired` muss bei unveränderter Quelle false sein.

Der erste geprüfte Stand vom 13.11.2026 umfasst elf Termine aus den aktuell
sichtbaren Oktober-, November- und Dezember-Ankündigungen. Die Winterpause
20.12.2026–05.01.2027 ist eine Schließzeit, keine zusätzliche Veranstaltung.
Die Hausöffnungsserie im Kalender gehört zu Söllingen; diese Grötzinger
Winterpause darf nicht auf ein anderes Naturfreundehaus übertragen werden.
