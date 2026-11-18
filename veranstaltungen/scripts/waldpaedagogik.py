"""Read the public Waldpädagogik Karlsruhe annual programme and month pages."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
import re
from urllib.parse import quote, unquote, urlsplit

from import_local_calendars import SOURCES, cls, document, only, source_url

MONTHS = 'Januar Februar März April Mai Juni Juli August September Oktober November Dezember'.split()
WEEKDAY = r'(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)'
DATE_LINE = re.compile(
    rf'^(?P<course>ab\s+)?(?:{WEEKDAY}(?:\s+(?P<weekdays>bis|und)\s+{WEEKDAY})?,?\s+)?'
    rf'(?P<day>\d{{1,2}})\.\s*(?:(?P<days>bis|und|[-–])\s*(?P<end>\d{{1,2}})\.\s*)?'
    rf'(?P<month>{"|".join(MONTHS)})\s+(?P<year>\d{{4}})\b', re.I,
)
CLOCK = r'\d{1,2}(?:[.:]\d{2})?'
TIME_RANGE = re.compile(rf'(?P<start>{CLOCK})(?:\s*Uhr)?\s*(?:bis|[-–])\s*(?:etwa\s+)?(?P<end>{CLOCK})(?![\d.:])', re.I)
CANCELLED = re.compile(r'\b(?:abgesagt|ersatzlos\s+gestrichen|entfällt|entfallen|ausfallen)\b', re.I)
CONDITIONAL = re.compile(r'\b(?:bei\s+(?:einer|starkregen|sturm|wind|unwetter)|wenn\b|falls\b)', re.I)
SOLD_OUT = re.compile(r'\bausgebucht\b|\bnur(?:\s+noch)?\s+(?:auf\s+der\s+)?Warteliste\b', re.I)
PLACE = re.compile(r'(?:\bTreffpunkt|^\s*(?:Veranstaltungsort|Ort))\s*:\s*(.+)', re.I)


def clean(value):
    return ' '.join(value.replace('\ufeff', '').split())


def paragraphs(node):
    # The site uses both paragraph tags and small headings for schedule and status.
    elements = node.xpath('.//*[self::p or self::h3 or self::h4 or self::h5 or self::li]')
    return [line for element in elements if (line := clean(element.text_content()))]


def month_pages(raw, base):
    tree = document(raw)
    main = only(tree.xpath('//div[' + cls('dmHomeRespTmpl') + ']'), 'Waldpädagogik-Jahresprogramm')
    year = only([
        match[1] for heading in main.xpath('.//h2')
        if (match := re.fullmatch(r'Jahresprogramm\s+(\d{4})', clean(heading.text_content())))
    ], 'Waldpädagogik-Programmjahr')
    months = {}
    for href in main.xpath('.//a[contains(@href, "veranstaltungen-im-")]/@href'):
        parts = urlsplit(source_url(base, href))
        path = unquote(parts.path).rstrip('/')
        month = path.removeprefix('/veranstaltungen-im-').capitalize()
        if month not in MONTHS or parts.query:
            raise ValueError('Unbekannte Waldpädagogik-Monatsseite: ' + path)
        months[MONTHS.index(month) + 1] = parts._replace(path=quote(path), fragment='').geturl()
    if not months:
        raise ValueError('Keine Waldpädagogik-Monatsseiten gefunden')
    return int(year), sorted(months.items())


def clock(value):
    hour, _, minute = value.replace('.', ':').partition(':')
    return time(int(hour), int(minute or '0')).isoformat(timespec='minutes')


def schedule(date_line, continuation):
    match = DATE_LINE.match(date_line)
    month = MONTHS.index(match['month'].capitalize()) + 1
    start = date(int(match['year']), month, int(match['day']))
    end = start.replace(day=int(match['end'])) if match['end'] else start
    if end < start:
        raise ValueError('Umgekehrter Waldpädagogik-Zeitraum: ' + date_line)

    when = date_line[match.end():].lstrip(' ,')
    follow = []
    for line in continuation:
        if not re.match(r'^(?:jeweils\b|von\s*\d|an insgesamt\b|\d+\s+Termine\b|\d{1,2}\s+Uhr\b)', line, re.I):
            break
        follow.append(line)
    when = ' '.join(part for part in [when, *follow] if part)
    first_time = re.search(rf'(?:^|\b(?:von|Beginn|ab|jeweils)\s*)({CLOCK})(?=\s*(?:Uhr\b|bis\b|[-–]))', when, re.I)
    slots = []
    if first_time:
        rest = when[first_time.start(1):]
        slots = [(clock(m['start']), clock(m['end'])) for m in TIME_RANGE.finditer(rest)]
        if not slots:
            slots = [(clock(first_time[1]), None)]
    days = None
    if end > start and re.match(r'^jeweils\b', when, re.I):
        if 'und' in (match['weekdays'], match['days']):
            days = [start, end]
        else:
            days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    return start, end, slots or [(None, None)], when, bool(match['course']), days


def location_for(title, lines):
    places = list(dict.fromkeys(match[1] for line in lines if (match := PLACE.search(line))))
    if len(places) == 1 and not re.search(r'bekannt\s*gegeben|nach\s+(?:der\s+)?Anmeldung', places[0], re.I):
        return places[0]
    if places or any(re.search(r'Treffpunkt.{0,100}(?:bekannt\s*gegeben|nach Anmeldung)', line, re.I) for line in lines):
        return ''
    if re.search(r'\b(?:am|im|um das) Waldklassenzimmer\b', title, re.I) or '(Waldklassenzimmer)' in title:
        return 'Waldklassenzimmer'
    # The organizer identifies its Holzwerkstatt as part of the Waldklassenzimmer site.
    if re.search(r'\bin der Holzwerkstatt\b', title, re.I):
        return 'Waldklassenzimmer'
    if re.search(r'\b(?:am|im|um das) Waldzentrum\b', title, re.I):
        return 'Waldzentrum'
    return ''


def sold_out_for(lines, slot, multiple_slots):
    for line in lines:
        if not SOLD_OUT.search(line):
            continue
        reported = [(clock(m['start']), clock(m['end'])) for m in TIME_RANGE.finditer(line)]
        if not multiple_slots or not reported or slot in reported:
            return 'sold-out'
    return None


def month_records(raw, url, year, month):
    main = only(document(raw).xpath('//div[' + cls('dmHomeRespTmpl') + ']'), 'Waldpädagogik-Monatsprogramm')
    records = []
    for col in main.xpath('./div/div/div[' + cls('dmRespCol') + ']'):
        headings = col.xpath('./div/h3')
        lines = paragraphs(col)
        dated = [(index, line) for index, line in enumerate(lines) if DATE_LINE.match(line)]
        if not headings and not dated:
            continue
        index, date_line = only(dated, 'Waldpädagogik-Termin in ' + url)
        heading = headings[0].getparent() if headings else None
        anchor = (heading if heading is not None else col.xpath('./div[.//p]')[0]).get('id')
        if not anchor or not re.fullmatch(r'[\w-]+', anchor):
            raise ValueError('Waldpädagogik-Termin ohne gültige Sprungmarke: ' + url)
        title = clean(' '.join(h.text_content() for h in headings)) if headings else lines[index + 1]
        start, end, slots, when, course, days = schedule(date_line, lines[index + 1:])
        if (start.year, start.month) != (year, month):
            raise ValueError('Waldpädagogik-Termin liegt außerhalb seiner Monatsseite: ' + date_line)
        cancelled = any(CANCELLED.search(line) and not CONDITIONAL.search(line) for line in lines)
        record = {
            'recordId': anchor, 'title': title, 'url': url + '#' + anchor,
            'dateText': date_line, 'timeText': when, 'location': location_for(title, lines),
            'organizer': SOURCES['waldpaedagogik-karlsruhe']['name'],
            'status': 'cancelled' if cancelled else 'scheduled',
        }
        if course:
            record['scheduleNote'] = 'Kursbeginn laut Jahresprogramm. Folgetermine bitte auf der Originalseite prüfen.'
        # Daily sessions have separate calendar dates and a shared source entry.
        # A date range without stated daily hours remains one multi-day event.
        for day in days or [start]:
            for slot in slots:
                records.append({
                    **record, 'startDate': day.isoformat(), 'endDate': (day if days else end).isoformat(),
                    'startTime': slot[0], 'endTime': slot[1],
                    'availability': sold_out_for(lines, slot, len(slots) > 1),
                })
    if not records and not re.search(r'\bkeine\s+(?:Veranstaltungen|Termine)\b', clean(main.text_content()), re.I):
        raise ValueError('Keine Waldpädagogik-Termine erkannt: ' + url)
    return records


def fetch_records(fetch):
    base = SOURCES['waldpaedagogik-karlsruhe']['url']
    year, pages = month_pages(fetch(base), base)

    def load(page):
        month, url = page
        return month_records(fetch(url), url, year, month)

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = [record for month in pool.map(load, pages) for record in month]
    return records, {
        'pagesFetched': len(pages) + 1,
        'coverageNote': f'Jahresprogramm {year} mit allen {len(pages)} verlinkten Monatsseiten.',
    }
