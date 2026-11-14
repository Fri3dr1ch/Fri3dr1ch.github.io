"""Public NaturFreunde calendars and the separately reviewed Grötzingen extract."""
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import html

BERLIN = ZoneInfo('Europe/Berlin')
BERG = 'https://www.naturfreunde-berghausen.de'
SOELL = 'https://www.naturfreunde-soellingen.de/kalender/'
GROETZ = 'https://www.naturfreunde-groetzingen.de/'
REVIEW_FILE = Path(__file__).resolve().parents[1] / 'data/reviewed/naturfreunde-groetzingen.json'
MONTHS = {m: i for i, m in enumerate(('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
                                    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'), 1)}


def text(node):
    return ' '.join(node.text_content().split())


def cls(token):
    return 'contains(concat(" ", normalize-space(@class), " "), " ' + token + ' ")'


def one(nodes, label):
    if len(nodes) != 1:
        raise ValueError(label + ': erwartete Struktur fehlt oder ist mehrdeutig')
    return nodes[0]


def document(raw):
    return html.fromstring(raw.decode('utf-8') if isinstance(raw, bytes) else raw)


def safe_url(base, ref):
    url = urljoin(base, ref)
    if urlsplit(url).scheme != 'https' or urlsplit(url).netloc != urlsplit(base).netloc:
        raise ValueError('NaturFreunde: unerwartetes Quellziel')
    return url


def german_date(value):
    m = re.search(r'\b(\d{1,2})\. (' + '|'.join(MONTHS) + r') (\d{4})\b', value)
    if not m:
        raise ValueError('NaturFreunde: Datum nicht erkannt: ' + value)
    return date(int(m[3]), MONTHS[m[2]], int(m[1])).isoformat()


def berghausen_listing(raw, expected_year=None):
    box = one(document(raw).xpath('//*[@id="jevents_body"]'), 'Berghausener Kalender')
    # The list currently selects "Alle". Refuse a newly paginated layout.
    selected = box.xpath('.//select[@name="limit"]/option[@selected]/@value')
    if selected and selected != ['0']:
        raise ValueError('Berghausen: begrenzte Ergebnisliste; Seitenfolge prüfen')
    if box.xpath('.//a[contains(@href,"limitstart") or contains(@href,"start=")]'):
        raise ValueError('Berghausen: neue Paginierung')
    records = []
    for node in box.xpath('.//li[' + cls('ev_td_li') + ']'):
        a = one(node.xpath('.//a[' + cls('ev_link_row') + ']'), 'Berghausener Terminlink')
        url = safe_url(BERG, a.get('href', ''))
        match = re.search(r'/icalrepeat.detail/(\d{4})/(\d{2})/(\d{2})/(\d+)/', url)
        if not match:
            raise ValueError('Berghausen: unbekannte Veranstaltungs-ID')
        day = date(int(match[1]), int(match[2]), int(match[3])).isoformat()
        if german_date(text(node.xpath('./p')[0])) != day:
            raise ValueError('Berghausen: Linkdatum widerspricht dem Listendatum')
        records.append({'recordId': match[4], 'title': text(a), 'url': url, 'startDate': day})
    tables = box.xpath('.//table[' + cls('ev_table') + ']')
    # JEvents represents an empty year by an intact table containing only its
    # year heading, with the "Alle" selection and pager still present.
    empty_year = (expected_year is not None and len(tables) == 1 and
                  len(tables[0].xpath('.//tr')) == 1 and
                  text(tables[0]) == str(expected_year) and selected == ['0'])
    if not records and not empty_year and not re.search(r'Keine (?:Termine|Ereignisse|Veranstaltungen)', text(box), re.I):
        raise ValueError('Berghausen: weder Termine noch bestätigte leere Liste')
    if expected_year is not None and any(not r['startDate'].startswith(str(expected_year) + '-') for r in records):
        raise ValueError('Berghausen: angefragtes Kalenderjahr wurde nicht beachtet')
    return records


def berghausen_detail(raw, entry):
    box = one(document(raw).xpath('//*[' + cls('jev_evdt') + ']'), 'Berghausener Detailseite')
    title = text(one(box.xpath('.//*[' + cls('jev_evdt_title') + ']'), 'Titel'))
    when = text(one(box.xpath('.//*[' + cls('jev_evdt_summary') + ']'), 'Datum'))
    if german_date(when) != entry['startDate']:
        raise ValueError('Berghausen: Detaildatum widerspricht der Übersicht')
    clocks = re.findall(r'\b\d{2}:\d{2}\b', when)
    if len(clocks) > 2:
        raise ValueError('Berghausen: mehrdeutige Uhrzeit')
    desc = ' '.join(text(n) for n in box.xpath('.//*[' + cls('jev_evdt_desc') + ']'))
    extra = ' '.join(text(n) for n in box.xpath('.//*[' + cls('jev_evdt_extrainf') + ']'))
    location = ' '.join(text(n) for n in box.xpath('.//*[' + cls('jev_evdt_location') + ']'))
    location = re.sub(r'^Ort\s*', '', location)
    meeting = re.search(r'Treffpunkt:\s*(.+?)(?:\s+Da nur|$)', desc)
    if meeting:
        location = re.sub(r'^\d{1,2}[.:]\d{2}\s*', '', meeting[1]).strip()
    postponed = bool(re.search(r'verschoben', title + ' ' + desc, re.I))
    cancelled = bool(re.search(r'abgesagt|entfällt|fällt aus', title + ' ' + desc, re.I))
    return {**entry, 'title': title, 'endDate': entry['startDate'],
            'startTime': clocks[0] if clocks else None, 'endTime': clocks[1] if len(clocks) == 2 else None,
            'location': location, 'organizer': 'NaturFreunde Berghausen', 'dateText': when,
            'categoryKey': 'sportveranstaltung' if 'wander' in (title + desc).lower() else 'stadtleben',
            'status': 'postponed' if postponed else 'cancelled' if cancelled else 'scheduled',
            'scheduleNote': ('Termin verschoben; neuer Termin noch nicht bekannt. ' if postponed else '') + extra}


def fetch_berghausen(fetch, today=None):
    today = today or datetime.now(BERLIN).date()
    # Follow the published program link, which carries the calendar's current date.
    homepage = document(fetch(BERG + '/'))
    links = homepage.xpath('//a[contains(@href,"/programm/year.listevents/")]/@href')
    program = safe_url(BERG, one(list(dict.fromkeys(links)), 'Berghausener Jahresprogramm'))
    match = re.search(r'/year.listevents/(\d{4})/\d{1,2}/\d{1,2}/', program)
    if not match:
        raise ValueError('Berghausen: unbekannter Jahresprogrammlink')
    year = int(match[1])
    next_program = re.sub(r'(/year.listevents/)\d{4}/\d{1,2}/\d{1,2}/',
                          r'\g<1>' + str(year + 1) + '/01/01/', program)
    entries = berghausen_listing(fetch(program), year) + berghausen_listing(fetch(next_program), year + 1)
    records = [berghausen_detail(fetch(e['url']), e) for e in entries]
    return records, {'coverageKind': 'html-list', 'allowEmpty': True, 'pagesFetched': 3 + len(entries),
                     'coverageNote': 'Veröffentlichte Programmlisten des laufenden und des nächsten Kalenderjahres einschließlich verschobener Termine. Derzeit kurze Vorschau, kein vollständiger Jahresplan.',
                     'extractionMethod': 'deterministic-html', 'llmRequired': False}


def calendar_url(raw):
    frames = document(raw).xpath('//iframe[contains(@src,"calendar.google.com/calendar")]/@src')
    src = one(frames, 'Söllinger Google-Kalender')
    ids = parse_qs(urlsplit(src).query).get('src', [])
    calendar_id = one(ids, 'Söllinger Kalender-ID')
    if calendar_id != 'natureloverssoellingen@gmail.com':
        raise ValueError('Söllingen: Kalender-ID geändert; neue Quelle prüfen')
    return 'https://calendar.google.com/calendar/ical/' + quote(calendar_id, safe='') + '/public/basic.ics'


def soellingen_records(raw, start, stop, ical_url):
    from icalendar import Calendar
    import recurring_ical_events

    cal = Calendar.from_ical(raw)
    if str(cal.get('X-WR-CALNAME', '')) != 'Naturfreunde Söllingen':
        raise ValueError('Söllingen: unerwarteter iCalendar-Name')
    if cal.errors or any(e.errors for e in cal.walk('VEVENT')):
        raise ValueError('Söllingen: fehlerhafte iCalendar-Felder')
    if not cal.walk('VEVENT'):
        raise ValueError('Söllingen: Kalender enthält keine VEVENT-Daten')
    items = recurring_ical_events.of(cal).between(
        datetime.combine(start, time.min, BERLIN), datetime.combine(stop, time.min, BERLIN))
    if len(items) > 3000:
        raise ValueError('Söllingen: unerwartet viele Wiederholungen')
    records, openings = [], []
    for e in items:
        begin = e.decoded('DTSTART')
        end = e.decoded('DTEND') if e.get('DTEND') else begin
        all_day = not isinstance(begin, datetime)
        if not all_day:
            if begin.tzinfo is None or not isinstance(end, datetime) or end.tzinfo is None:
                raise ValueError('Söllingen: Zeitzone oder Endzeit fehlt')
            begin, end = begin.astimezone(BERLIN), end.astimezone(BERLIN)
        day = begin if all_day else begin.date()
        if end < begin:
            raise ValueError('Söllingen: umgekehrter Zeitraum')
        title = str(e.get('SUMMARY', '')).strip()
        uid = str(e.get('UID', ''))
        if not title or not uid:
            raise ValueError('Söllingen: UID oder Titel fehlt')
        status = 'cancelled' if str(e.get('STATUS', '')).upper() == 'CANCELLED' else 'scheduled'
        end_day = end if all_day else end.date()
        # RFC 5545 DTEND is exclusive for both all-day and midnight endings.
        if end > begin and (all_day or end.timetz().replace(tzinfo=None) == time.min):
            end_day -= timedelta(days=1)
        if day >= stop or end_day < start:
            continue
        rec_id = e.decoded('RECURRENCE-ID') if e.get('RECURRENCE-ID') else begin
        identity = hashlib.sha256((uid + '|' + rec_id.isoformat()).encode()).hexdigest()[:24]
        record = {'recordId': identity, 'sourceUid': uid, 'title': title, 'url': SOELL,
                  'icalUrl': ical_url, 'startDate': day.isoformat(), 'endDate': end_day.isoformat(),
                  'startTime': None if all_day else begin.strftime('%H:%M'),
                  'endTime': None if all_day else end.strftime('%H:%M'), 'allDay': all_day,
                  'endIso': None if all_day else end.isoformat(),
                  'status': status, 'location': str(e.get('LOCATION', '')),
                  'organizer': 'NaturFreunde Söllingen', 'dataQuality': 'ical-calendar',
                  'categoryKey': 'sportveranstaltung' if 'walking' in title.lower() or 'wander' in title.lower() else 'stadtleben'}
        if re.fullmatch(r'NFH geöffnet', title, re.I) and status == 'scheduled':
            if all_day or begin.date() != end.date():
                raise ValueError('Söllingen: unklare Hausöffnungszeit')
            openings.append(record)
        else:
            records.append(record)
    return records, openings


def group_openings(openings):
    if not openings:
        return []
    ordered = sorted(openings, key=lambda r: (r['startDate'], r['startTime']))
    times = {(r['startTime'], r['endTime']) for r in ordered}
    sundays = all(date.fromisoformat(r['startDate']).weekday() == 6 for r in ordered)
    note = ('Sonntags ' if sundays else 'Öffnungstage laut Kalender: ') + '–'.join(next(iter(times))) + ' Uhr' if len(times) == 1 else 'Öffnungstermine laut Kalender'
    return [{**ordered[0], 'recordId': 'hausoeffnung', 'title': 'Naturfreundehaus Söllingen · Hausöffnung',
             'location': 'Naturfreundehaus Pfinztalblick, Am Neuen Berg, Pfinztal-Söllingen',
             'endDate': ordered[-1]['endDate'], 'endIso': ordered[-1].get('endIso'), 'longRunning': True, 'openingSchedule': note,
             'openingOccurrences': [{k: r[k] for k in ('startDate', 'startTime', 'endTime')} for r in ordered],
             'scheduleNote': 'Reguläre Bewirtung, keine Sonderveranstaltung. Nur die unten aufgeführten Kalendertermine gelten als Öffnungstage; im August geschlossen. Weitere Ausnahmen laut Originalseite.',
             'categoryKey': 'stadtleben'}]


def fetch_soellingen(fetch, today=None):
    today = today or datetime.now(BERLIN).date()
    start = today.replace(day=1)
    stop = start.replace(year=start.year + 1)
    page = fetch(SOELL)
    ical_url = calendar_url(page)
    records, openings = soellingen_records(fetch(ical_url), start, stop, ical_url)
    # The source page explicitly excludes August, even if an old open-ended
    # Calendar rule still generates Sundays in that month.
    if 'im August bleibt das Naturfreundehaus geschlossen' not in text(document(page)):
        raise ValueError('Söllingen: Schließzeitenregel geändert; Hausöffnungen prüfen')
    closures = set()
    for r in records:
        if re.search(r'sonn?en?wende?|sonnwend|oktoberfest', r['title'], re.I) and r['status'] == 'scheduled':
            end = date.fromisoformat(r['endDate'])
            closures.add((end + timedelta(days=7 - end.weekday() - 1 if end.weekday() < 6 else 7)).isoformat())
    valid = [r for r in openings if date.fromisoformat(r['startDate']).month != 8 and r['startDate'] not in closures]
    records += group_openings(valid)
    return records, {'coverageKind': 'ical-recurrences', 'allowEmpty': True, 'feedUrl': ical_url,
                     'coverageStart': start.isoformat(), 'coverageEnd': (stop - timedelta(days=1)).isoformat(),
                     'openingOccurrenceCount': len(valid), 'excludedOpeningCount': len(openings) - len(valid),
                     'coverageNote': 'Zwölf Kalendermonate ab Monatsanfang; iCalendar-Wiederholungen einschließlich Ausnahmen und Einzeländerungen. Hausöffnungen als ein laufendes Angebot; August und bekannte Schließsonntage ausgeschlossen.',
                     'extractionMethod': 'icalendar-recurrences', 'llmRequired': False}


def monthly_review(reviewed_on):
    day = date.fromisoformat(reviewed_on)
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1).isoformat()


def groetzingen_evidence(raw, fetch):
    box = one(document(raw).xpath('//*[@id="main-content"]'), 'Grötzinger Ankündigungen')
    # Ignore dynamic calendars, view counters, mail cloaking, and navigation.
    evidence = []
    for heading in box.xpath('.//h2'):
        article = heading.getparent()
        item = {'title': text(heading)}
        # Text is normalized by the CMS content blocks, excluding metadata/scripts.
        clone = html.fromstring(html.tostring(article))
        for unwanted in clone.xpath('.//script | .//style | .//h2 | .//dl | .//ul[' + cls('actions') + ']'):
            unwanted.drop_tree()
        item['textSha256'] = hashlib.sha256(text(clone).encode()).hexdigest()
        item['images'] = []
        for ref in article.xpath('.//img/@src'):
            url = safe_url(GROETZ, ref)
            if not urlsplit(url).path.startswith('/images/'):
                continue
            item['images'].append({'url': url, 'sha256': hashlib.sha256(fetch(url)).hexdigest()})
        evidence.append(item)
    if not evidence:
        raise ValueError('Grötzingen: Ankündigungen nicht erkannt')
    return evidence


def fetch_groetzingen_reviewed(fetch, review_file=REVIEW_FILE):
    reviewed = json.loads(Path(review_file).read_text())
    observed = groetzingen_evidence(fetch(GROETZ), fetch)
    changed = observed != reviewed['evidence']
    return reviewed['records'], {'coverageKind': 'reviewed-announcements', 'refreshHours': 24,
                                 'lastReviewedAt': reviewed['reviewedOn'],
                                 'nextReviewOn': monthly_review(reviewed['reviewedOn']),
                                 'reviewRequired': changed, 'observedEvidence': observed,
                                 'coverageNote': 'Ankündigungen einschließlich Bildaushängen manuell geprüft am ' + reviewed['reviewedOn'] + '. Monatliche Prüfung; Änderungen lösen sofort einen Prüfhinweis aus. Neue Inhalte werden erst nach Kontrolle übernommen.',
                                 'extractionMethod': 'reviewed-manual', 'llmRequired': False}
