# Produktionsprüfung vom 13. November 2026

Vollständiger Standardaufruf `python -u scripts/update_calendar.py`: erfolgreich (Exit 0), 2026-11-13T11:35:36+00:00 bis 2026-11-13T11:39:35+00:00.

Ergebnis: 1380 datierte Einträge aus 14 Quellen, elf unbestimmte Angaben; 73 zusammengeführte Mehrfachnennungen. Keine offenen Abruffehler oder fälligen Quellenprüfungen.

Geprüft: 99 Python-Tests, 26 DOM-/Aktualisierungsprüfungen, Schema und Gleichheit von eingebettetem HTML/JSON, fixierte Abhängigkeiten (`pip check`, `npm ci`), Actionlint 1.7.7 ohne Fehler. Die Paketprüfungen kontrollieren den Erhalt der vorhandenen Startseite und Mathematikseiten sowie den Ausschluss privater Vorschau-Metadaten.

Der echte GitHub-Workflow und GitHub-Benachrichtigungen werden erst nach Übernahme ins Repository und Aktivierung geprüft. Der Issue-Abgleich wurde ohne Schreibzugriff sowie mit simulierten API-Antworten geprüft. Der öffentliche GitHub-Push und Pages-Start gehören zum nächsten Deployment-Schritt.

Nächste dokumentierte Sichtprüfungen: NaturFreunde Grötzingen am 01.12.2026, Grötzinger PDF-Ausgaben am 15.01.2027. Frühere Änderungen lösen bereits vorher Prüfhinweise aus.

| Quelle | Status | Erfolgreicher Quellenabruf |
| --- | --- | --- |
| Stadt Karlsruhe | ok | 2026-11-13T11:35:45+00:00 |
| Grötzingen · Jahresprogramm | ok | 2026-11-13T11:39:26+00:00 |
| Grötzingen · Kultur-Natur-Termine | ok | 2026-11-13T11:39:34+00:00 |
| Durlacher.de · Durlacher Kalender | ok | 2026-11-13T11:35:37+00:00 |
| Gemeinde Weingarten (Baden) | ok | 2026-11-13T11:35:37+00:00 |
| Pfinztal · Gemeindetermine | ok | 2026-11-13T11:35:45+00:00 |
| Pfinztal · Vereinskalender | ok | 2026-11-13T11:36:00+00:00 |
| Gemeinde Remchingen | ok | 2026-11-13T11:36:16+00:00 |
| Kulturhalle Remchingen | ok | 2026-11-13T11:36:24+00:00 |
| Evangelische Kirche Grötzingen | ok | 2026-11-13T11:36:38+00:00 |
| Pfinztal · Haus der Begegnungen | ok | 2026-11-13T11:36:44+00:00 |
| NaturFreunde Berghausen | ok | 2026-11-13T11:36:57+00:00 |
| NaturFreunde Söllingen | ok | 2026-11-13T11:36:58+00:00 |
| NaturFreunde Grötzingen | ok | 2026-11-13T11:37:08+00:00 |
