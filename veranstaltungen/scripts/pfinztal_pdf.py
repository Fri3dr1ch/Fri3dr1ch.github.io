"""Read Pfinztal's ruled Vereinskalender tables, preserving editorial ambiguity.

The public PDF contains real table cells, so no LLM or OCR is involved. Unknown
layouts abort. Alternative dates, reversed date ranges and booking periods with
setup/dismantling labels are returned without a confirmed startDate.
"""
from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
import re
from urllib.parse import urljoin, urlsplit

from lxml import html
import pdfplumber

SOURCE_URL = 'https://pfinztal.de/aktuelles-blog/termine-und-veranstaltungen/'
DISTRICTS = {'Berghausen', 'Söllingen', 'Wöschbach', 'Kleinsteinbach'}
MONTHS = {'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
          'August', 'September', 'Oktober', 'November', 'Dezember'}
DATE = re.compile(r'\d{2}\.\d{2}\.\d{4}')
CLOCK = re.compile(r'(?:ab )?(\d{1,2})[:.](\d{2})(?:\s*[–-]\s*(\d{1,2})[:.](\d{2}))?')
OPERATIONAL = {'Aufbau', 'Aufbau/Probe', 'Versammlung', 'Abbau'}
REVIEWED_EDITIONS = {'b83c774890092c719faae51f451dc4a3bf59ddd3ee6b8cc358a908447dda045f': '2026-11-12'}


def clean(value):
    return ' '.join((value or '').split())


def iso_date(value):
    compact = re.sub(r'\s+', '', value)
    if not DATE.fullmatch(compact):
        raise ValueError('Unbekanntes Datumsformat im Pfinztaler PDF: ' + value)
    return datetime.strptime(compact, '%d.%m.%Y').date().isoformat()


def clock_values(value):
    if not value or value in OPERATIONAL or value == 'ab Vormittag':
        return []
    match = CLOCK.fullmatch(value)
    if not match:
        raise ValueError('Unbekannte Zeitangabe im Pfinztaler PDF: ' + value)
    values = []
    for hour, minute in (match.groups()[:2], match.groups()[2:]):
        if hour is not None:
            clock = f'{int(hour):02d}:{int(minute):02d}'
            datetime.strptime(clock, '%H:%M')
            values.append(clock)
    return values


def alternative_dates(value):
    """Only expand the two unambiguous written forms with omitted suffixes."""
    compact = clean(value)
    match = re.fullmatch(r'(\d{1,2})\.(?:(\d{1,2})\.)?\s*oder\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', compact)
    if not match:
        raise ValueError('Unbekannter Alternativtermin im Pfinztaler PDF: ' + value)
    first_day, first_month, second_day, second_month, year = match.groups()
    return [date(int(year), int(first_month or second_month), int(first_day)).isoformat(),
            date(int(year), int(second_month), int(second_day)).isoformat()]


def pdf_link(homepage):
    tree = html.fromstring(homepage.decode('utf-8') if isinstance(homepage, bytes) else homepage)
    links = {urljoin(SOURCE_URL, a.get('href')) for a in tree.xpath('//a[@href]')
             if '.pdf' in a.get('href', '').lower()
             and 'veranstaltungskalender' in a.get('href', '').lower()}
    links = {url for url in links if urlsplit(url).scheme == 'https'
             and urlsplit(url).hostname == 'pfinztal.de'}
    if len(links) != 1:
        raise ValueError('Pfinztaler Vereinskalender-PDF nicht eindeutig verlinkt')
    return links.pop()


def parse_rows(pages, pdf_url):
    """Parse already extracted 7-column pages; useful independently for QA."""
    records = []
    district = None
    edition_year = None
    seen_districts = set()
    seen_headers = 0
    month = None
    for page_number, table in enumerate(pages, 1):
        if len(table) < 3 or any(len(row) != 7 for row in table):
            raise ValueError(f'Pfinztal PDF Seite {page_number}: unbekannte Tabellenstruktur')
        rows = [[clean(cell) for cell in row] for row in table]
        if rows[0] != ['Beginn', '', 'Ende', '', 'Veranstaltung', 'Ort', 'Verein']:
            raise ValueError(f'Pfinztal PDF Seite {page_number}: Spaltenüberschrift geändert')
        if rows[1] != ['Datum', 'Uhrzeit', 'Datum', 'Uhrzeit', '', '', '']:
            raise ValueError(f'Pfinztal PDF Seite {page_number}: Datums-/Zeitspalten geändert')
        seen_headers += 1
        for row_number, row in enumerate(rows[2:], 3):
            begin, begin_clock, finish, finish_clock, title, location, organizer = row
            if not any(row):
                continue
            section = re.fullmatch(r'Termine (20\d{2}) (.+)', begin)
            if section and not any(row[1:]):
                year, district = section.groups()
                if district not in DISTRICTS:
                    raise ValueError('Unbekannter Pfinztaler Ortsteil: ' + district)
                if edition_year is not None and edition_year != int(year):
                    raise ValueError('Widersprüchliche Ausgabejahre im Pfinztaler PDF')
                edition_year = int(year)
                seen_districts.add(district)
                continue
            if begin in MONTHS and not any(row[2:]) and (not begin_clock or re.fullmatch(r'20\d{2}', begin_clock)):
                month = list(('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
                              'August', 'September', 'Oktober', 'November', 'Dezember')).index(begin) + 1
                continue
            if begin.startswith('Änderungen der einzelnen Termine bleiben vorbehalten') and not any(row[1:]):
                continue
            missing_title = not title
            if district is None or (missing_title and not DATE.fullmatch(re.sub(r'\s+', '', begin))):
                raise ValueError(f'Pfinztal PDF Seite {page_number}, Zeile {row_number}: unerkannte Zeile')
            identifier = f'pdf-{edition_year}-{district.lower()}-p{page_number:02d}-r{row_number:03d}'
            date_text = begin + ((' oder ' if begin_clock == 'oder' else ' bis ') + finish if finish else '')
            time_text = 'Beginn-Spalte: ' + (begin_clock or 'keine Uhrzeit') + '; Ende-Spalte: ' + (finish_clock or 'keine Uhrzeit')
            base = {'recordId': identifier, 'title': title, 'url': pdf_url + '#page=' + str(page_number),
                    'sourcePage': page_number, 'sourceRow': row_number, 'location': location,
                    'organizer': organizer, 'district': district, 'region': 'Pfinztal-' + district,
                    'dateText': date_text, 'timeText': time_text, 'allDay': False,
                    'startDate': None, 'endDate': None, 'startTime': None, 'endTime': None,
                    'monthHint': f'{edition_year}-{month:02d}' if month else None}
            if missing_title:
                if not location or not organizer:
                    raise ValueError('Unbenannter PDF-Eintrag ohne prüfbare Metadaten')
                base['title'] = 'Unbenannter Eintrag · ' + organizer
                base['dateCandidates'] = [iso_date(begin)]
                base['scheduleNote'] = 'Die Spalte Veranstaltung ist im PDF leer. Datum und Raum sind angegeben; eine benannte öffentliche Veranstaltung ist nicht bestätigt.'
                records.append(base)
                continue
            if 'oder' in begin:
                base['dateCandidates'] = alternative_dates(begin)
                base['scheduleNote'] = 'Alternativtermin im PDF; kein festes Datum bestätigt: ' + date_text + '.'
                records.append(base)
                continue
            if begin_clock == 'oder':
                base['dateCandidates'] = [iso_date(begin), iso_date(finish)]
                base['scheduleNote'] = 'Alternativtermin im PDF; kein festes Datum bestätigt: ' + date_text + '.'
                records.append(base)
                continue
            if begin.lower() == 'noch offen':
                base['dateCandidates'] = []
                base['scheduleNote'] = 'Termin im Vereinskalender ausdrücklich noch offen.'
                records.append(base)
                continue
            start = iso_date(begin)
            end = iso_date(finish) if finish and finish != 'und' else start
            if end < start:
                base['dateCandidates'] = [start, end]
                base['scheduleNote'] = 'Fehlerhafte Quellenangabe: Der Zeitraum ' + date_text + ' endet vor seinem Beginn. Keine Jahreszahl automatisch korrigiert.'
                records.append(base)
                continue
            if begin_clock in OPERATIONAL or finish_clock in OPERATIONAL:
                base['dateCandidates'] = [start] if start == end else [start, end]
                base['scheduleNote'] = 'Die PDF-Zeile enthält Aufbau, Probe oder Abbau statt eindeutiger Veranstaltungszeiten: ' + date_text + '; ' + time_text + '. Öffentlichen Besuchstermin bitte bestätigen.'
                records.append(base)
                continue
            first_times = clock_values(begin_clock)
            last_times = clock_values(finish_clock)
            base.update({'startDate': start, 'endDate': end})
            if finish == 'und':
                if len(first_times) != 1 or len(last_times) != 1:
                    raise ValueError('Mehrfachvorstellung im PDF ohne zwei eindeutige Uhrzeiten')
                for suffix, clock in enumerate([first_times[0], last_times[0]], 1):
                    records.append({**base, 'recordId': identifier + '-show-' + str(suffix),
                                    'startTime': clock, 'scheduleNote': 'Zwei Vorstellungen am selben Tag: ' + first_times[0] + ' und ' + last_times[0] + ' Uhr.'})
                continue
            if len(first_times) == 2 and len(last_times) == 2 and start != end:
                # Explicit clock windows on both the start and end day. Do not
                # invent openings on intermediate days of a longer period.
                for suffix, day, window in [('first', start, first_times), ('last', end, last_times)]:
                    records.append({**base, 'recordId': identifier + '-' + suffix,
                                    'startDate': day, 'endDate': day, 'startTime': window[0], 'endTime': window[1],
                                    'scheduleNote': 'Eigenes Zeitfenster für diesen Tag in der PDF-Tabelle: ' + window[0] + '–' + window[1] + ' Uhr.'})
                continue
            if len(last_times) > 1:
                raise ValueError('Endzeitfenster im PDF ohne passendes Anfangszeitfenster')
            base['startTime'] = first_times[0] if first_times else None
            if start == end:
                if len(first_times) > 1 and last_times and first_times[1] != last_times[0]:
                    raise ValueError('Widersprüchliche Endzeiten in einer PDF-Zeile')
                base['endTime'] = last_times[0] if last_times else (first_times[1] if len(first_times) > 1 else None)
            else:
                base['endTime'] = last_times[0] if last_times else None
                if len(first_times) > 1:
                    base['scheduleNote'] = ('Beginn-Spalte nennt ' + begin_clock + ', Enddatum ' + finish +
                                            ' ohne Enduhrzeit. Daraus werden keine täglichen Öffnungszeiten abgeleitet.')
            if begin_clock == 'ab Vormittag':
                base['scheduleNote'] = 'Beginn laut PDF: ab Vormittag; keine genaue Uhrzeit angegeben.'
            records.append(base)
    if not records or seen_headers != len(pages) or seen_districts != DISTRICTS:
        raise ValueError('Pfinztaler PDF nicht vollständig erkannt: alle vier Ortsteile werden erwartet')
    for record in records:
        if record['startDate'] is None:
            record['uncertaintyReason'] = record['scheduleNote']
            record['notes'] = record['scheduleNote']
    return records, edition_year


def parse_pdf(raw, pdf_url):
    with pdfplumber.open(BytesIO(raw)) as pdf:
        if not 1 <= len(pdf.pages) <= 40:
            raise ValueError('Unerwartete Seitenzahl im Pfinztaler Vereinskalender')
        pages = []
        for number, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            if len(tables) != 1:
                raise ValueError(f'Pfinztal PDF Seite {number}: genau eine Tabelle erwartet')
            pages.append(tables[0])
        footer = pdf.pages[-1].extract_text() or ''
    versions = re.findall(r'Stand\s+(\d{2}\.\d{2}\.\d{4})', footer)
    if len(versions) != 1:
        raise ValueError('Redaktionsstand des Pfinztaler PDFs nicht eindeutig')
    records, year = parse_rows(pages, pdf_url)
    known = [record for record in records if record['startDate']]
    unknown = len(records) - len(known)
    digest = sha256(raw).hexdigest()
    reviewed_on = REVIEWED_EDITIONS.get(digest)
    metadata = {'pdfUrl': pdf_url, 'sha256': digest, 'reviewedOn': reviewed_on,
                'edition': f'Vereinskalender {year} · Stand {versions[0]}',
                'publishedOn': iso_date(versions[0]), 'coverageKind': 'pdf-table',
                'coverageStart': min(r['startDate'] for r in known),
                'coverageEnd': max(r['endDate'] for r in known), 'pagesFetched': len(pages),
                'coverageNote': f'Vereinskalender aller vier Pfinztaler Ortsteile; {len(pages)} PDF-Seiten. Eigener Redaktionsstand, keine tägliche Aktualisierung.',
                'unknownCount': unknown, 'recordCount': len(records),
                'automationMethod': 'pdfplumber-table', 'requiresLLM': False,
                'reviewNotes': [('Diese Ausgabe wurde gegen alle PDF-Seiten geprüft.' if reviewed_on else
                                 'Neue Ausgabe automatisch aus vollständigen Tabellen gelesen; für diesen Hash ist keine neue Sichtprüfung dokumentiert.'),
                                'Alternative Termine, rückwärts datierte Zeiträume und Auf-/Abbauangaben bleiben ohne bestätigtes Datum.',
                                'Tageszeitfenster und mehrere Vorstellungen werden getrennt übernommen; fehlende Uhrzeiten bleiben offen.']}
    return records, metadata


def fetch_records(fetch):
    pdf_url = pdf_link(fetch(SOURCE_URL))
    return parse_pdf(fetch(pdf_url), pdf_url)
