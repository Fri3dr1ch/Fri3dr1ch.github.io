# Übernahme nach GitHub und Betrieb

Vorbereitet für `Fri3dr1ch/Fri3dr1ch.github.io`, Standardbranch `main`.
Der Kalender erhält `/veranstaltungen/`; die bestehende Startseite und die
Mathematikseiten bleiben Teil derselben Pages-Veröffentlichung.
Dieses Paket führt keine GitHub-Änderungen aus.

## 1. Dateien übernehmen, Veröffentlichung zunächst ausgeschaltet lassen

Das ZIP in die Wurzel eines aktuellen Repository-Checkouts entpacken. Es ergänzt:

- `.github/workflows/calendar.yml`
- `veranstaltungen/` mit Skripten, geprüften Startdaten, Tests und fertigem Kalender

Vorhandene Root-Dateien wie `index.html` bleiben erhalten. Die Action muss in der
Repository-Wurzel liegen; GitHub entdeckt keinen Workflow unter
`veranstaltungen/.github/`.

Lokal aus `veranstaltungen/` prüfen:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts --no-audit --no-fund
python -u scripts/update_calendar.py
python -m unittest discover -s tests -v
python scripts/validate_calendar.py
npm test
python scripts/sync_github_issues.py --repo Fri3dr1ch/Fri3dr1ch.github.io --dry-run
```

Dann Dateien committen und pushen. Die Action startet bei jedem Push auf `main`
(damit auch Änderungen der bestehenden Website veröffentlicht werden), manuell über **Actions → Veranstaltungskalender
aktualisieren → Run workflow** und per Zeitplan. Ohne die unten genannte Variable
werden noch keine Pages-Dateien veröffentlicht. Die Action schreibt geprüfte
Datenstände ins Repository und gleicht Wartungs-Issues bereits ab.

## 2. Action und Benachrichtigungen prüfen

Repository **Settings → Actions → General**: Actions zulassen. Der Workflow
benötigt `contents: write` und `issues: write` im Update-Job; eine übergeordnete
Organisationsrichtlinie darf diese nicht blockieren. Repository-Issues müssen
aktiviert sein. Es ist kein PAT, OpenAI-Schlüssel oder anderes Secret nötig;
GitHub stellt den laufbezogenen `GITHUB_TOKEN` automatisch bereit.

Die Action erstellt das Label `calendar-maintenance` und weist neue Issues
standardmäßig dem Repository-Eigentümer `Fri3dr1ch` zu. Bei Bedarf die
Repository-Variable `CALENDAR_ISSUE_ASSIGNEE` auf einen zuweisbaren GitHub-Namen
setzen. Vorhandene, manuell angelegte Issues werden nicht verändert.

**So kommt der Prüfhinweis bei dir an:**

1. Im Repository **Watch → Custom → Issues** aktivieren, falls alle neuen
   Kalender-Issues zugestellt werden sollen. Die Zuweisung des eigenen Accounts
   macht das Issue zusätzlich direkt relevant.
2. Unter den persönlichen **Settings → Notifications** für **Participating,
   @mentions and custom** die gewünschten Zustellwege einstellen, insbesondere
   **Email** und die richtige verifizierte E-Mail-Adresse. GitHub-Inbox/App sind
   weitere mögliche Zustellwege. Das Skript selbst verschickt keine E-Mail.
3. Bei Bedarf zusätzlich Benachrichtigungen für fehlgeschlagene Actions aktivieren.
   Ein Fehler, bevor der Issue-Abgleich ausführbar ist, kann nur über den
   fehlgeschlagenen Workflow sichtbar werden.

Beim ersten Lauf nach Fälligkeit oder nach einer erkannten Änderung entsteht ein
zugewiesenes Issue mit Quellenlink, Grund und Prüfdatum. Wiederholte unveränderte
Läufe erzeugen keine weiteren Issues oder Kommentare. Veränderte Gründe
aktualisieren dasselbe Issue. Ein manuell geschlossenes, weiterhin fälliges
Issue wird wieder geöffnet. Nach dokumentierter Prüfung und erfolgreichem
Abgleich wird es geschlossen. Unveränderte offene Issues lösen keine tägliche
Erinnerungsmail aus; sie bleiben als offene Aufgabe sichtbar.

Der Zeitplan ist **00:17, 06:17, 12:17 und 18:17 UTC**, also im Winter 01:17,
07:17, 13:17 und 19:17 Uhr in Deutschland. GitHub kann geplante Läufe verzögern;
sie sind kein Echtzeitdienst. Zeitpläne laufen auf dem Standardbranch. GitHub
kann sie in öffentlichen Repositories nach längerer Inaktivität deaktivieren;
dann unter Actions erneut aktivieren oder manuell starten.

## 3. Pages gezielt aktivieren

Nach einem kontrollierten erfolgreichen Lauf:

1. **Settings → Pages → Build and deployment → Source: GitHub Actions** setzen.
   Der Workflow veröffentlicht die gesamte bestehende Website zusammen mit dem
   Kalender. Ein bisheriger Pages-Branch-Build wird dadurch ersetzt.
2. **Settings → Secrets and variables → Actions → Variables**:
   `CALENDAR_DEPLOY_ENABLED` mit dem exakten Wert `true` anlegen.
3. Die Action manuell starten und den separaten Job `deploy` kontrollieren.
4. `https://fri3dr1ch.github.io/` sowie
   `https://fri3dr1ch.github.io/veranstaltungen/` und vorhandene Mathematikseiten
   öffnen. Quellenstände, Filter und Detailansichten kontrollieren.

Die Pages-Ausgabe wird erst nach erfolgreicher Datenvalidierung und bestandenen
Tests erstellt und hochgeladen. Der Deploy-Job verlangt einen erfolgreich
hochgeladenen Pages-Artefakt. Er erhält getrennt `pages: write` und
`id-token: write`. Actions sind auf vollständige Commit-SHAs fixiert.

Ein vom Bot geschriebener Daten-Commit löst mit `GITHUB_TOKEN` keinen weiteren
Workflow und keinen normalen Branch-Pages-Build aus. Deshalb veröffentlicht
**derselbe Lauf** über die Pages-API; es gibt keinen rekursiven Update-Zyklus.
Die gemeinsame Concurrency-Gruppe verhindert konkurrierende Kalenderläufe.
Ein zwischenzeitlicher fremder Push wird nicht überschrieben: Ein abgelehnter
Git-Push lässt den Lauf fehlschlagen und blockiert dessen Veröffentlichung.

## Fehler, Berichte und Rollback

Jeder Lauf schreibt `.etl/report.json` und `.etl/summary.md`, ergänzt die
Actions-Zusammenfassung und sichert den Bericht für 14 Tage als Artefakt.
Die Berichte enthalten keine Tokens. `data/` enthält die letzten verwendeten
Quellenstände und bleibt versioniert.

- Quellenfehler: gültige frühere Angaben und echte alte Zeitstempel bleiben
  erhalten, Warnung in der Oberfläche, eigenes Issue und roter Lauf. Erfolgreiche
  andere Quellen werden trotzdem aktualisiert. Wenn Kalender und Tests gültig
  sind, kann dieser gekennzeichnete Ersatzstand veröffentlicht werden.
- Neue PDF/Bildaushänge oder fällige Sichtprüfung: Prüf-Issue; bestätigte Termine
  bleiben erhalten. Keine automatische LLM-Freigabe.
- Ungültiger gemeinsamer Kalender oder fehlgeschlagene Tests: keine Übernahme
  und kein Deployment; Pipeline-Issue, sofern der Issue-Schritt ausführbar ist.
- Issue-API-/Berechtigungsfehler: sichtbarer fehlgeschlagener Schritt. Die übrige
  Datenaktualisierung bleibt möglich; Benachrichtigung über Actions beachten.

Zum Pausieren öffentlicher Updates `CALENDAR_DEPLOY_ENABLED` entfernen oder auf
`false` setzen; der zuletzt veröffentlichte Stand bleibt online. Zum Stoppen
aller Läufe den Workflow deaktivieren. Für ein Rollback den betreffenden Code-
oder Daten-Commit gezielt zurücknehmen und den Workflow mit deaktivierter
Pages-Variable prüfen, anschließend wieder aktivieren. Ein neuer vollständiger
Abruf kann Daten bewusst erneut auf den Quellenstand aktualisieren.

Die private Sites-Vorschau hat keinen GitHub-Zeitplan. Nach der GitHub-Übernahme
wird die öffentliche Pages-Ausgabe automatisch aktualisiert; die private
Vorschau bleibt ein unabhängig veröffentlichter Prüfstand.
