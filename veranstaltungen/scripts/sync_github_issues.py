#!/usr/bin/env python3
"""Synchronize bot-owned calendar review/error Issues from a validated ETL report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

LABEL = 'calendar-maintenance'
MARKER = re.compile(r'<!-- calendar-maintenance:((?:error|review):[a-z0-9-]+) -->')
BOT = 'github-actions[bot]'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('GitHub-API-Weiterleitung wird nicht mit Zugangsdaten verfolgt')


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Ungültiger Repository-Name')
        if not token:
            raise ValueError('GITHUB_TOKEN fehlt')
        self.base = 'https://api.github.com/repos/' + repository
        self.token = token

    def request(self, method, path, payload=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Ungültiger GitHub-API-Pfad')
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'RegionalCalendar-IssueSync/1.0'}
        if body is not None: headers['Content-Type'] = 'application/json'
        # Creating an Issue is deliberately not retried after an ambiguous
        # network failure. The next run first lists existing managed Issues.
        with build_opener(NoRedirect()).open(Request(self.base + path, data=body, headers=headers, method=method), timeout=30) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000: raise ValueError('GitHub-Antwort zu groß')
            return json.loads(raw) if raw else None

    def issues(self):
        result = []
        for page in range(1, 101):
            batch = self.request('GET', '/issues?' + urlencode({'state': 'all', 'labels': LABEL, 'per_page': 100, 'page': page}))
            if not isinstance(batch, list): raise ValueError('GitHub-Issueliste hat unbekanntes Format')
            result += [issue for issue in batch if not issue.get('pull_request')]
            if len(batch) < 100: return result
        raise ValueError('Zu viele Kalender-Issues; Abgleich abgebrochen')

    def ensure_label(self):
        try:
            self.request('GET', '/labels/' + quote(LABEL))
        except HTTPError as error:
            if error.code != 404: raise
            self.request('POST', '/labels', {'name': LABEL, 'color': 'd4a72c', 'description': 'Automatische Quellenprüfungen und Importfehler des Veranstaltungskalenders'})


def safe_text(value):
    # Source text must not notify unrelated GitHub users or inject Markdown.
    return str(value or '').replace('@', '@\u200b').replace('`', "'").replace('<', '‹').replace('>', '›')[:2400]


def contents(problem, run_url=''):
    key = problem['key']
    if not MARKER.fullmatch('<!-- calendar-maintenance:' + key + ' -->'):
        raise ValueError('Ungültiger Wartungsschlüssel')
    prefix = 'Quelle prüfen' if problem['kind'] == 'review' else 'Importfehler'
    title = prefix + ': ' + safe_text(problem['sourceName'])
    stable = {k: problem.get(k) for k in ('key', 'kind', 'reason', 'dueOn', 'lastReviewedAt', 'candidateUrls', 'reviewFingerprint')}
    if problem['kind'] == 'error': stable['lastSuccessAt'] = problem.get('lastSuccessAt')
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    lines = ['<!-- calendar-maintenance:' + key + ' -->', '<!-- calendar-fingerprint:' + digest + ' -->', '',
             safe_text(problem['reason']), '', 'Quelle: ' + safe_text(problem.get('url'))]
    if problem.get('dueOn'): lines += ['Prüfung fällig seit: ' + safe_text(problem['dueOn'])]
    if problem.get('lastReviewedAt'): lines += ['Letzte dokumentierte Sichtprüfung: ' + safe_text(problem['lastReviewedAt'])]
    if problem['kind'] == 'error' and problem.get('lastSuccessAt'):
        lines += ['Letzter erfolgreicher Datenabruf: ' + safe_text(problem['lastSuccessAt'])]
    if problem.get('candidateUrls'):
        lines += ['', 'Gefundene PDF-Ausgaben:'] + ['- ' + safe_text(url) for url in problem['candidateUrls'][:12]]
    if problem.get('sourceId') == 'venues':
        lines += ['', 'Die Ortsprüfliste unter dem Quellenlink öffnen. Unsichere Spielorte einmalig bestätigen oder bewusst ohne Kartenpunkt lassen. Entscheidungen exportieren und mit `python scripts/review_venues.py --apply ortsentscheidungen.json` ins Repository übernehmen. Bestätigte Zuordnungen gelten automatisch für weitere Termine mit derselben Ortsangabe und demselben Ortskontext. Leere Ortsangaben müssen bei der Originalquelle geklärt werden.']
    elif problem['kind'] == 'review':
        lines += ['', 'Termine und Bildaushänge bzw. neue PDF-Ausgabe gegen die Originalquelle prüfen, den geprüften Extrakt aktualisieren und das tatsächliche Prüfdatum dokumentieren. Ein erneuter Download oder das bloße Schließen dieses Issues ersetzt die Prüfung nicht.']
    lines += ['', 'Dieses Issue wird vom Kalender-Workflow verwaltet und nach bestätigter Behebung automatisch geschlossen.']
    if run_url: lines += ['', 'Erzeugender Workflow-Lauf: ' + run_url]
    return title, '\n'.join(lines), digest


def synchronize(report, api, assignee, run_url='', dry_run=False):
    active = list(report.get('issues', []))
    resolved = set(report.get('resolvedKeys', []))
    if report.get('validationPassed'):
        resolved.add('error:pipeline')
    else:
        active = [{'key': 'error:pipeline', 'kind': 'error', 'sourceId': 'pipeline',
                   'sourceName': 'Gesamter Kalenderlauf', 'reason': '; '.join(e.get('reason', '') for e in report.get('errors', [])) or 'ETL oder Datenvalidierung fehlgeschlagen.', 'url': run_url}]
        resolved.clear()
    if len({p['key'] for p in active}) != len(active):
        raise ValueError('Report enthält doppelte Wartungsschlüssel')
    existing = {}
    for issue in api.issues():
        match = MARKER.search(issue.get('body') or '')
        if issue.get('user', {}).get('login') != BOT or not match:
            continue
        # Manual Issues, even with the same label, are never edited or closed.
        existing.setdefault(match[1], []).append(issue)
    if active and not dry_run: api.ensure_label()
    actions = []
    for problem in active:
        title, body, digest = contents(problem, run_url)
        found = existing.get(problem['key'], [])
        if len([i for i in found if i['state'] == 'open']) > 1:
            raise ValueError('Mehrere offene Bot-Issues für ' + problem['key'] + '; bitte einmalig zusammenführen')
        issue = next((i for i in found if i['state'] == 'open'), found[0] if found else None)
        if issue is None:
            actions.append({'action': 'create', 'key': problem['key']})
            if not dry_run: api.request('POST', '/issues', {'title': title, 'body': body, 'labels': [LABEL], 'assignees': [assignee]})
        else:
            patch = {}
            if issue['state'] != 'open': patch['state'] = 'open'
            if '<!-- calendar-fingerprint:' + digest + ' -->' not in (issue.get('body') or ''):
                patch.update(title=title, body=body)
            if patch:
                actions.append({'action': 'update', 'key': problem['key'], 'number': issue['number']})
                if not dry_run: api.request('PATCH', '/issues/' + str(int(issue['number'])), patch)
    active_keys = {p['key'] for p in active}
    for key in resolved - active_keys:
        for issue in existing.get(key, []):
            if issue['state'] == 'open':
                actions.append({'action': 'close', 'key': key, 'number': issue['number']})
                if not dry_run: api.request('PATCH', '/issues/' + str(int(issue['number'])), {'state': 'closed', 'state_reason': 'completed'})
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=Path('.etl/report.json'))
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--assignee')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.repo: parser.error('--repo oder GITHUB_REPOSITORY fehlt')
    owner = args.assignee or args.repo.split('/')[0]
    if not re.fullmatch(r'[A-Za-z0-9-]+', owner): parser.error('Ungültiger Issue-Verantwortlicher')
    report = json.loads(args.report.read_text())
    if args.dry_run:
        class Preview:
            def issues(self): return []
        api = Preview()
    else:
        api = GitHub(args.repo, os.environ.get('GITHUB_TOKEN'))
    run_url = os.environ.get('GITHUB_SERVER_URL', 'https://github.com') + '/' + args.repo + '/actions/runs/' + os.environ.get('GITHUB_RUN_ID', '')
    if not run_url.startswith('https://github.com/'): raise ValueError('Unerwarteter GitHub-Server')
    print(json.dumps(synchronize(report, api, owner, run_url, args.dry_run), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error:
        print('Issue-Abgleich fehlgeschlagen: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        sys.exit(1)
