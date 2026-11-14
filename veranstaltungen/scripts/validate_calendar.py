"""Validate the published calendar and its embedded/offline data agree."""
import argparse
from datetime import date, datetime
import json
from pathlib import Path
from urllib.parse import urlsplit

from import_groetzingen import BEGIN, END, ROOT


def validate(data):
    if data.get('schemaVersion') != 2 or data.get('timezone') != 'Europe/Berlin':
        raise ValueError('Kalenderschema oder Zeitzone ist ungültig')
    generated = datetime.fromisoformat(data['generatedAt'])
    if generated.tzinfo is None:
        raise ValueError('Datenstand hat keine Zeitzone')
    sources = data['sources']
    ids = {s['id'] for s in sources}
    if len(ids) != len(sources) or not ids:
        raise ValueError('Quellen-IDs sind nicht eindeutig')
    for source in sources:
        if source.get('status') not in {'ok', 'partial', 'error', 'review-required'}:
            raise ValueError('Unbekannter Quellenstatus')
        if source.get('lastSuccessAt') and datetime.fromisoformat(source['lastSuccessAt']).tzinfo is None:
            raise ValueError('Quellenabruf hat keine Zeitzone')
    events = data['events']
    if not events or len({e['id'] for e in events}) != len(events):
        raise ValueError('Kalender ist leer oder enthält doppelte IDs')
    for event in events:
        start, end = date.fromisoformat(event['startDate']), date.fromisoformat(event['endDate'])
        if not event['title'].strip() or end < start:
            raise ValueError('Leerer Titel oder umgekehrter Veranstaltungszeitraum')
        if event.get('status') not in {'scheduled', 'cancelled', 'postponed'}:
            raise ValueError('Unbekannter Terminstatus')
        if event.get('timeKnown') != bool(event.get('startTime')):
            raise ValueError('Uhrzeitstatus widerspricht der bekannten Uhrzeit')
        for key in ('startTime', 'endTime'):
            if event.get(key): datetime.strptime(event[key], '%H:%M')
        for report in event['sources']:
            if report['sourceId'] not in ids or urlsplit(report['url']).scheme != 'https':
                raise ValueError('Unbekannte oder unsichere Originalquelle')
        for opening in event.get('openingOccurrences') or []:
            day = date.fromisoformat(opening['startDate'])
            if not start <= day <= end:
                raise ValueError('Hausöffnung liegt außerhalb ihrer Laufzeit')
            for key in ('startTime', 'endTime'): datetime.strptime(opening[key], '%H:%M')
    if data['importStats']['eventCount'] != len(events):
        raise ValueError('Eintragszahl stimmt nicht mit dem Kalender überein')
    return data


def validate_files(root):
    data = validate(json.loads((root / 'dist/events.json').read_text()))
    page = (root / 'dist/index.html').read_text()
    if page.count(BEGIN) != 1 or page.count(END) != 1:
        raise ValueError('Eingebettete Daten fehlen oder sind mehrdeutig')
    embedded = json.loads(page[page.index(BEGIN) + len(BEGIN):page.index(END)])
    if embedded != data:
        raise ValueError('HTML und events.json enthalten unterschiedliche Datenstände')
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    data = validate_files(args.root)
    print(f"Validiert: {len(data['events'])} Einträge, {len(data['sources'])} Quellen, Stand {data['generatedAt']}")
