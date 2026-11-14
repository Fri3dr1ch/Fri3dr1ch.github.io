#!/usr/bin/env python3
"""Production ETL entry point: refresh, retain valid fallbacks, validate, report."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys

from http_client import HttpClient
from import_groetzingen import ROOT, BERLIN, check_review, next_review, save_dataset
from import_local_calendars import SOURCES, refresh, build_current
import karlsruhe
from pdf_checks import check as check_pdfs, load_extracts
from validate_calendar import validate, validate_files


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def problems(data, today):
    active, resolved = [], []
    pdf_ids = {'groetzingen-jahresprogramm', 'groetzingen-kultur-natur'}
    for source in data['sources']:
        sid = source['id']
        base = {'sourceId': sid, 'sourceName': source['name'], 'url': source.get('url') or source.get('homepage'),
                'lastSuccessAt': source.get('lastSuccessAt')}
        error_key, review_key = 'error:' + sid, 'review:' + sid
        last_check = source.get('lastCheckedAt') or source.get('lastSuccessAt')
        stale = bool(source.get('refreshHours') and last_check and
                     (datetime.now(timezone.utc) - datetime.fromisoformat(last_check)).total_seconds() > source['refreshHours'] * 3600)
        if source['status'] in {'error', 'partial'} or stale:
            active.append({**base, 'key': error_key, 'kind': 'error',
                           'reason': source.get('lastError') or ('Quellenstand ist älter als das vorgesehene Abrufintervall.' if stale else 'Quellenabruf unvollständig.')})
        else:
            resolved.append(error_key)
        due = next_review(date.fromisoformat(source['lastReviewedAt'])) if sid in pdf_ids else source.get('nextReviewOn')
        expired = bool((sid in pdf_ids or sid == 'naturfreunde-groetzingen') and
                       source.get('coverageEnd') and source['coverageEnd'] < today.isoformat())
        review = bool(source.get('reviewRequired')) or bool(due and due <= today.isoformat()) or expired
        if review:
            active.append({**base, 'key': review_key, 'kind': 'review', 'dueOn': due,
                           'reason': source.get('reviewReason') or ('Neue oder geänderte Ankündigungen erkannt.' if source.get('reviewRequired') else 'Der dokumentierte Programmzeitraum ist abgelaufen.' if expired else 'Regelmäßige Quellenprüfung ist fällig.'),
                           'candidateUrls': source.get('candidateUrls', []),
                           'lastReviewedAt': source.get('lastReviewedAt')})
        elif source['status'] not in {'error', 'partial'}:
            resolved.append(review_key)
    return active, resolved


def report_for(data, started, attempted):
    active, resolved = problems(data, datetime.now(BERLIN).date())
    return {'schemaVersion': 1, 'startedAt': started, 'finishedAt': now(),
            'generatedAt': data['generatedAt'], 'eventCount': len(data['events']),
            'attemptedSources': attempted, 'sources': data['sources'],
            'issues': active, 'resolvedKeys': resolved,
            'errors': [i for i in active if i['kind'] == 'error'], 'validationPassed': True}


def markdown(report):
    lines = ['# Veranstaltungskalender: Aktualisierung', '',
             f"Datenstand: {report['generatedAt']} · {report['eventCount']} Einträge", '',
             '| Quelle | Status | Letzter erfolgreicher Abruf |', '| --- | --- | --- |']
    for s in report['sources']:
        lines.append(f"| {s['name'].replace('|', '/')} | {s['status']} | {s.get('lastSuccessAt') or 'kein gültiger Stand'} |")
    if report['issues']:
        lines += ['', '## Offene Prüfungen und Fehler', '']
        for item in report['issues']:
            reason = item['reason'].replace('\n', ' ').replace('@', '@\u200b')[:1200]
            lines.append('- **' + item['sourceName'] + '**: ' + reason)
    else:
        lines += ['', 'Keine fälligen Quellenprüfungen und keine Abruffehler.']
    return '\n'.join(lines) + '\n'


def run(root, selected='all', offline=False, jobs=3, client=None, report_dir=None):
    started = now()
    client = client or HttpClient()
    current = read_json(root / 'dist/events.json')
    if current is None:
        raise ValueError('Startdatenstand fehlt; geprüfte Startdaten mit ausliefern')
    attempted = []
    if not offline:
        def load(sid):
            print('Lade ' + sid + ' …', flush=True)
            if sid == 'karlsruhe':
                path = root / 'data/karlsruhe-rss.json'
                result = karlsruhe.refresh(read_json(path), client)
                return sid, path, result
            path = root / 'data/local-calendars' / (sid + '.json')
            result, error = refresh(sid, now(), read_json(path), client, root=root)
            if result is None:
                # The delivered seed data normally prevents this case. A missing
                # first source is still a failure, never an empty successful feed.
                result = {'source': {**SOURCES[sid], 'status': 'error', 'lastAttemptAt': now(),
                                     'lastError': error}, 'events': [], 'undatedEvents': []}
            return sid, path, result
        wanted = ['karlsruhe', *SOURCES] if selected == 'all' else list(SOURCES) if selected == 'regional' else ([] if selected == 'pdfs' else [selected])
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(load, sid) for sid in wanted]
            for future in as_completed(futures):
                sid, path, result = future.result()
                write_json(path, result)
                attempted.append(sid)
                source = result['sources'][0] if sid == 'karlsruhe' else result['source']
                print(sid + ': ' + source['status'] + ', ' + str(len(result['events'])) + ' Einträge', flush=True)
        if selected in {'all', 'regional', 'pdfs'}:
            path = root / 'data/source-checks.json'
            checks = check_pdfs(root, client, current['sources'], read_json(path, {}))
            write_json(path, checks)
            attempted += list(checks)
            for sid, result in checks.items(): print(sid + ': ' + result['status'], flush=True)
    data = build_current(root, now())
    validate(data)
    save_dataset(data, root)
    validate_files(root)
    report = report_for(data, started, attempted)
    folder = report_dir or root / '.etl'
    write_json(folder / 'report.json', report)
    summary = markdown(report)
    (folder / 'summary.md').write_text(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output: output.write(summary)
    print(json.dumps(data['importStats'], ensure_ascii=False))
    print(check_review(data, datetime.now(BERLIN).date()))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--source', choices=['all', 'regional', 'karlsruhe', 'pdfs', *SOURCES], default='all')
    parser.add_argument('--offline', action='store_true', help='Nur gespeicherte Daten validieren und zusammensetzen')
    parser.add_argument('--check-review', action='store_true', help='Fällige Prüfungen ohne Netzabruf ausgeben')
    parser.add_argument('--jobs', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--report-dir', type=Path)
    args = parser.parse_args(argv)
    if args.check_review:
        data = read_json(args.root / 'dist/events.json')
        print(check_review(data, datetime.now(BERLIN).date()))
        return 0
    try:
        report = run(args.root, args.source, args.offline, args.jobs, report_dir=args.report_dir)
        return 1 if report['errors'] else 0
    except Exception as error:
        folder = args.report_dir or args.root / '.etl'
        # Do not allow a report left over from a prior run to authorize a deploy.
        write_json(folder / 'report.json', {'schemaVersion': 1, 'validationPassed': False,
                   'finishedAt': now(), 'issues': [], 'resolvedKeys': [],
                   'errors': [{'reason': type(error).__name__ + ': ' + str(error)}]})
        summary = '# Kalender-Aktualisierung abgebrochen\n\n' + type(error).__name__ + ': ' + str(error).replace('@', '@\u200b') + '\n'
        (folder / 'summary.md').write_text(summary)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output: output.write(summary)
        print('Aktualisierung abgebrochen: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
