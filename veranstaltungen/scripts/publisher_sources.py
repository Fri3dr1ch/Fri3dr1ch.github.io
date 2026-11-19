"""Small adapters for official regional event lists and publisher calendars."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import re
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from icalendar import Calendar
from lxml import html

BERLIN = ZoneInfo('Europe/Berlin')


def _doc(raw):
    return html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8') if isinstance(raw, bytes) else None)


def _text(node):
    return ' '.join(node.text_content().split())


def _one(nodes, label):
    if len(nodes) != 1:
        raise ValueError(label + ': erwartete Struktur fehlt oder ist mehrdeutig')
    return nodes[0]


def _same_host_url(base, href):
    url = urljoin(base, href)
    p, b = urlsplit(url), urlsplit(base)
    if p.scheme != 'https' or p.hostname != b.hostname or p.username or p.password or p.fragment:
        raise ValueError('Unerwartetes Linkziel in Veranstaltungsquelle')
    return url


def _parse_date(text, formats=('%d.%m.%Y',)):
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError('Datum nicht erkannt: ' + text)


def _times(text):
    found = re.findall(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)', text)
    values = []
    for hour, minute in found:
        value = f'{int(hour):02d}:{minute}'
        datetime.strptime(value, '%H:%M')
        if value not in values:
            values.append(value)
    if len(values) > 2:
        raise ValueError('Mehr als zwei Uhrzeiten in Veranstaltungsangabe')
    return values


def _ical_status(item):
    status = str(item.get('STATUS', '')).strip().upper()
    if status == 'CANCELLED':
        return 'cancelled'
    if status == 'POSTPONED' or str(item.get('X-STATUS', '')).upper() == 'POSTPONED':
        return 'postponed'
    return None


def _calendar_events(raw, source_name, prodid):
    calendar = Calendar.from_ical(raw)
    events = calendar.walk('VEVENT')
    known_empty = (str(calendar.get('VERSION', '')) == '2.0'
                   and str(calendar.get('PRODID', '')) == prodid
                   and all(component.name == 'VTIMEZONE' for component in calendar.subcomponents))
    if calendar.errors or (not events and not known_empty):
        raise ValueError(source_name + ': iCalendar-Feed nicht erkannt')
    return events


def naturtreff_records(raw):
    components = _calendar_events(raw, 'Naturtreff', '-//Naturtreff Veranstaltungen//DE')
    records, seen = [], set()
    for item in components:
        if item.errors or item.get('RRULE') or item.get('RDATE') or item.get('RECURRENCE-ID'):
            raise ValueError('Naturtreff: unbekannte oder wiederkehrende iCalendar-Struktur')
        uid = str(item.get('UID', '')).strip()
        title = str(item.get('SUMMARY', '')).strip()
        if not uid or not title or uid in seen:
            raise ValueError('Naturtreff: fehlende oder doppelte UID / Titel')
        seen.add(uid)
        start_value = item.decoded('DTSTART')
        finish_value = item.decoded('DTEND') if item.get('DTEND') else None
        start_iso = None
        if isinstance(start_value, datetime):
            start = start_value.replace(tzinfo=BERLIN) if start_value.tzinfo is None else start_value.astimezone(BERLIN)
            start_iso = start.isoformat()
            end = None
            if finish_value is not None:
                if not isinstance(finish_value, datetime):
                    raise ValueError('Naturtreff: widersprüchliche iCalendar-Datumstypen')
                finish = finish_value.replace(tzinfo=BERLIN) if finish_value.tzinfo is None else finish_value.astimezone(BERLIN)
                if finish.timestamp() < start.timestamp():
                    raise ValueError('Naturtreff: umgekehrte iCalendar-Zeitspanne')
                end = finish.isoformat()
            begin_date, begin_time = start.date(), start.strftime('%H:%M')
            finish_time = finish.strftime('%H:%M') if finish_value else None
            finish_date = finish.date() if finish_value else begin_date
        elif isinstance(start_value, date):
            begin_date, begin_time = start_value, None
            finish_date = finish_value - timedelta(days=1) if isinstance(finish_value, date) and finish_value > start_value else start_value
            finish_time, end = None, None
        else:
            raise ValueError('Naturtreff: unbekannter DTSTART-Typ')
        url = str(item.get('URL', '')).strip()
        if url and urlsplit(url).hostname != 'naturtreff-groetzingen.de':
            raise ValueError('Naturtreff: unerwartete Veranstaltungs-URL')
        records.append({'recordId': uid, 'title': title, 'url': url or 'https://naturtreff-groetzingen.de/veranstaltungen/',
            'icalUrl': 'https://naturtreff-groetzingen.de/?nt_ics=feed', 'startDate': begin_date.isoformat(),
            'endDate': finish_date.isoformat(), 'startTime': begin_time, 'endTime': finish_time,
            'startIso': start_iso, 'endIso': end, 'location': str(item.get('LOCATION', '')).strip(),
            'description': str(item.get('DESCRIPTION', '')).strip(), 'allDay': isinstance(start_value, date) and not isinstance(start_value, datetime),
            'status': _ical_status(item), 'categoryKey': 'stadtleben', 'dataQuality': 'ical-feed'})
    return records, {'coverageKind': 'ical-feed', 'allowEmpty': True,
                     'coverageNote': 'Alle einzeln veröffentlichten Termine im Naturtreff-iCalendar-Feed.'}


def vhs_land_records(raw, base_url):
    tree = _doc(raw)
    # KuferWeb changes wrapper class names between installations; use the card's
    # stable course-detail URL and require one exact start and duration field.
    candidates = []
    for link in tree.xpath('//a[contains(@href,"/kursdetails/kurs/")]'):
        href = link.get('href', '')
        detail_url = _same_host_url(base_url, href)
        course_id = re.search(r'/kurs/([A-Za-z0-9_-]+)(?:/|$)', urlsplit(detail_url).path)
        if not course_id:
            raise ValueError('vhs Karlsruhe Land: Kurs-ID fehlt')
        parent = link
        for _ in range(6):
            if parent.getparent() is None:
                break
            parent = parent.getparent()
            value = _text(parent)
            if 'Beginn' in value and 'Dauer' in value and 'Kursort' in value:
                candidates.append((parent, link, course_id.group(1), detail_url))
                break
    # Distinct course ids may be linked more than once from navigation/footer.
    unique = {}
    for card, link, course_id, detail_url in candidates:
        unique.setdefault(course_id, (card, link, detail_url))
    if not unique:
        raise ValueError('vhs Karlsruhe Land: Kursliste oder Kurskarten nicht erkannt')
    records = []
    for course_id, (card, link, detail_url) in unique.items():
        text = _text(card)
        title = _text(link)
        if not title or title.lower() in {'mehr erfahren', 'details', 'weitere infos'}:
            titles = card.xpath('.//*[self::h2 or self::h3 or self::h4]')
            title = _text(_one(titles, 'vhs Karlsruhe Land Titel'))
        if not title:
            raise ValueError('vhs Karlsruhe Land: leerer Kurstitel')
        start_match = re.search(r'Beginn\s*:?\s*(?:[A-Za-zÄÖÜäöüß]{2}\.,?\s*)?(\d{1,2}\.\d{1,2}\.\d{4})(.*?)(?=\s+Dauer\b)', text, re.I)
        duration_match = re.search(r'Dauer\s*:?\s*(\d+)\s*Termin', text, re.I)
        place_match = re.search(r'Kursort\s*:?\s*(.+?)(?:\s+Gebühr\b|\s+Preis\s*:|\s+Status\s*:|$)', text, re.I)
        if not start_match or not duration_match:
            raise ValueError('vhs Karlsruhe Land: Beginn oder Dauer nicht eindeutig')
        # A one-session filter is the low-maintenance boundary. Series remain
        # out even where their first meeting happens to be in the area.
        if int(duration_match.group(1)) != 1:
            continue
        day = _parse_date(start_match.group(1))
        clocks = _times(start_match.group(2))
        begin_time = clocks[0] if clocks else None
        location = re.sub(r'\s+', ' ', place_match.group(1)).strip(' :') if place_match else ''
        records.append({'recordId': course_id, 'title': title, 'url': detail_url,
            'startDate': day.isoformat(), 'startTime': begin_time, 'endTime': clocks[1] if len(clocks) > 1 else None, 'location': location,
            'description': 'Einzelveranstaltung aus dem veröffentlichten Kursangebot der vhs Karlsruhe Land.'})
    return records, {'coverageKind': 'html-list', 'coverageNote': 'Alle aktuell veröffentlichten Einzelveranstaltungen (Dauer: 1 Termin); mehrteilige Kurse bleiben außen vor.', 'allowEmpty': True, 'pagesFetched': 1}


def hagsfeld_records(raw, base_url):
    tree = _doc(raw)
    containers = tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," mod_eventlist ") and contains(concat(" ",normalize-space(@class)," ")," pb40 ")]')
    box = _one(containers, 'Hagsfelder Hauptterminliste')
    cards = box.xpath('./div[contains(concat(" ",normalize-space(@class)," ")," event ") and contains(concat(" ",normalize-space(@class)," ")," layout_upcoming ")]')
    if not 1 <= len(cards) <= 20:
        raise ValueError('Hagsfeld: sichtbare Terminliste leer oder unerwartet groß')
    records, seen = [], set()
    for card in cards:
        link = _one(card.xpath('./a[@href]'), 'Hagsfeld Terminlink')
        url = _same_host_url(base_url, link.get('href'))
        slug = urlsplit(url).path.rstrip('/').split('/')[-1]
        time_node = _one(card.xpath('./time[@datetime]'), 'Hagsfeld Termindatum')
        when = datetime.fromisoformat(time_node.get('datetime'))
        start = when.astimezone(BERLIN) if when.tzinfo else when.replace(tzinfo=BERLIN)
        label = link.get('title') or _text(link)
        date_range = re.findall(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b', label)
        end_date = start.date()
        if len(date_range) > 1:
            end_date = date(int(date_range[-1][2]), int(date_range[-1][1]), int(date_range[-1][0]))
        elif '–' in time_node.text_content() or '-' in time_node.text_content():
            # When the preview announces a range, retain it only if the second
            # date is explicit; malformed ranges fail closed below.
            dates = re.findall(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', time_node.text_content())
            if len(dates) > 1:
                end_date = date(int(dates[-1][2]), int(dates[-1][1]), int(dates[-1][0]))
        if end_date < start.date():
            raise ValueError('Hagsfeld: umgekehrter Veranstaltungszeitraum')
        if slug in seen:
            raise ValueError('Hagsfeld: doppelte Termin-ID in Vorschau')
        seen.add(slug)
        records.append({'recordId': slug, 'title': _text(link), 'url': url,
            'startDate': start.date().isoformat(), 'endDate': end_date.isoformat(),
            'startTime': start.strftime('%H:%M') if 'T' in time_node.get('datetime') else None,
            'dateText': label, 'location': '', 'categoryKey': 'stadtleben'})
    return records, {'coverageKind': 'html-list', 'coverageNote': f'Alle {len(records)} Einträge der offiziellen Vorschau „Nächste Termine“; die veröffentlichte Liste ist auf acht Einträge begrenzt.', 'pagesFetched': 1}


def _stutensee_date(text):
    found = re.findall(r'\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b', text)
    if not found or len(found) > 2:
        raise ValueError('Stutensee: Datum nicht eindeutig: ' + text)
    dates = []
    for d, m, y in found:
        y = int(y)
        if y < 100: y += 2000
        dates.append(date(y, int(m), int(d)))
    return dates


def stutensee_page(raw, base_url):
    tree = _doc(raw)
    cards = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," cVeka_box_eventDate ")]')
    if not cards:
        raise ValueError('Stutensee: Veranstaltungskarten nicht erkannt')
    entries = []
    for card in cards:
        title_link = _one(card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," cVeka_box_title ")]//a[@href]'), 'Stutensee Titel/Link')
        url = _same_host_url(base_url, title_link.get('href'))
        query = parse_qs(urlsplit(url).query)
        identity = _one(query.get('id', []), 'Stutensee Veranstaltungs-ID')
        date_nodes = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," cVeka_box_date ")]')
        time_nodes = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," cVeka_box_time ")]')
        location_nodes = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," cVeka_box_location ")]')
        organizer_nodes = card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," cVeka_box_organizer ")]')
        dates = _stutensee_date(_text(_one(date_nodes, 'Stutensee Datum')))
        if len(time_nodes) > 1: raise ValueError('Stutensee: mehrere Uhrzeitfelder')
        raw_time = time_nodes[0].text_content().strip() if time_nodes else ''
        # Some previews append a talk start after the event's opening interval
        # ("ab 09:00–12:00 Uhr ab 10:00 Uhr …"). Keep only the first labelled
        # interval; later clocks belong to the descriptive text.
        raw_interval = re.split(r'Uhr', raw_time, maxsplit=1, flags=re.I)[0]
        values = _times(raw_interval)
        # Do not interpret a lone closing-time note as a start.
        begin = values[0] if values and not re.match(r'^bis\b', raw_time, re.I) else None
        finish = values[-1] if len(values) > 1 or (values and not begin) else None
        if len(location_nodes) > 1: raise ValueError('Stutensee: mehrere Ortsfelder')
        location = re.sub(r'^Veranstaltungsort:\s*', '', _text(location_nodes[0]), flags=re.I) if location_nodes else ''
        organizer = re.sub(r'^Veranstalter:\s*', '', _text(organizer_nodes[0]), flags=re.I) if organizer_nodes else ''
        entries.append({'recordId': identity, 'title': _text(title_link), 'url': url,
            'startDate': dates[0].isoformat(), 'endDate': dates[-1].isoformat(), 'startTime': begin, 'endTime': finish,
            'location': location, 'organizer': organizer, 'categoryKey': 'stadtleben'})
    return entries


def stutensee_records(raw, base_url, fetch):
    records = list(stutensee_page(raw, base_url))
    queue, queued, offsets = [], set(), set()
    def links(page_raw, current_offset):
        tree = _doc(page_raw)
        for href in tree.xpath('//a/@href'):
            match = re.search(r'[?&](?:start|offset)=(\d+)(?:&|$)', href)
            if not match: continue
            offset = int(match.group(1))
            if offset <= current_offset or offset in offsets: continue
            url = _same_host_url(base_url, href)
            if url not in queued:
                queued.add(url); offsets.add(offset); queue.append((offset, url))
    links(raw, 0)
    while queue:
        if len(offsets) > 99: raise ValueError('Stutensee: unerwartet viele Ergebnisseiten')
        offset, url = min(queue); queue.remove((offset, url))
        page = fetch(url)
        records.extend(stutensee_page(page, base_url))
        links(page, offset)
    ordered_offsets = sorted(offsets)
    if ordered_offsets and ordered_offsets != list(range(15, ordered_offsets[-1] + 15, 15)):
        raise ValueError('Stutensee: unvollständige oder unerwartete Seitenfolge')
    ids = [r['recordId'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Stutensee: doppelte Veranstaltungs-ID über mehrere Ergebnisseiten')
    return records, {'coverageKind': 'html-list', 'coverageNote': 'Alle veröffentlichten Webtermine; der separate Jahres-PDF-Kalender wird nicht eingelesen.', 'pagesFetched': 1 + len(offsets)}


def bergdoerfer_page(raw, base_url):
    tree = _doc(raw)
    cards = tree.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," v-kachel ") and .//div[contains(concat(" ",normalize-space(@class)," ")," v-kachel-titel ")]]')
    if not cards:
        raise ValueError('Bergdörfer: Termin-Kacheln nicht erkannt')
    records, seen = [], set()
    for card in cards:
        links = card.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," v-kachel-titel ")]//a[@href]')
        link = _one(links, 'Bergdörfer Detail-Link')
        url = _same_host_url(base_url, link.get('href'))
        match = re.search(r'/id/(\d+)-', urlsplit(url).path)
        if not match:
            raise ValueError('Bergdörfer: stabile Termin-ID fehlt')
        record_id = match.group(1)
        date_text = _text(_one(card.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," date ")]'), 'Bergdörfer Datum'))
        date_match = re.search(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b', date_text)
        if not date_match: raise ValueError('Bergdörfer: Datum nicht erkannt')
        days = [_parse_date(value) for value in re.findall(r'\b\d{1,2}\.\d{1,2}\.\d{4}\b', date_text)]
        day = days[0]
        time_nodes = card.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," time ")]')
        time_text = _text(time_nodes[0]) if time_nodes else ''
        times = _times(time_text)
        title_node = _one(card.xpath('.//h4[contains(concat(" ",normalize-space(@class)," ")," title ")]'), 'Bergdörfer Titel')
        loc_nodes = card.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," oertlichkeit-name ")]')
        district_nodes = card.xpath('.//div[contains(concat(" ",normalize-space(@class)," ")," oertlichkeit-ort ")]')
        location = _text(loc_nodes[0]) if loc_nodes else ''
        district = _text(district_nodes[0]) if district_nodes else ''
        if district: location = f'{location}, {district}' if location else district
        if record_id in seen: raise ValueError('Bergdörfer: doppelte Termin-ID in Übersicht')
        seen.add(record_id)
        records.append({'recordId': record_id, 'title': _text(title_node), 'url': url,
            'startDate': day.isoformat(), 'endDate': days[-1].isoformat(), 'startTime': times[0] if times else None,
            'endTime': times[1] if len(times) > 1 else None, 'location': location, 'categoryKey': 'stadtleben'})
    return records


def bergdoerfer_records(raw, base_url, fetch):
    from eki_calendar import fetch_listing_pages

    records, pages = fetch_listing_pages(raw, fetch, base_url, lambda page: bergdoerfer_page(page, base_url))
    if len({r['recordId'] for r in records}) != len(records):
        raise ValueError('Bergdörfer: doppelte Termin-ID über mehrere Ergebnisseiten')
    return records, {'coverageKind': 'html-list', 'pagesFetched': pages,
                     'coverageNote': f'Alle {len(records)} veröffentlichten Termine auf {pages} Ergebnisseiten der zentralen Terminübersicht.'}


def karlsbad_records(raw):
    components = _calendar_events(raw, 'Karlsbad', '-//KLIK//EventCalendar//DE')
    records, ids = [], set()
    for item in components:
        if item.errors or item.get('RRULE') or item.get('RDATE') or item.get('RECURRENCE-ID'):
            raise ValueError('Karlsbad: neue oder wiederkehrende iCalendar-Struktur')
        uid = str(item.get('UID', '')).strip()
        title = str(item.get('SUMMARY', '')).strip()
        if not uid or uid in ids or not title: raise ValueError('Karlsbad: fehlende oder doppelte UID/Titel')
        ids.add(uid)
        begin_component = item['DTSTART']
        begin = item.decoded('DTSTART')
        date_only = str(begin_component.params.get('VALUE', '')).upper() == 'DATE'
        start_iso = end_iso = end_time = None
        if isinstance(begin, datetime) and not date_only:
            start = begin.replace(tzinfo=BERLIN) if begin.tzinfo is None else begin.astimezone(BERLIN)
            day, begin_time = start.date(), start.strftime('%H:%M')
            start_iso = start.isoformat()
        elif isinstance(begin, date) or date_only:
            day = begin.date() if isinstance(begin, datetime) else begin
            begin_time = None
        else: raise ValueError('Karlsbad: unbekannter DTSTART-Typ')
        end_value = item.decoded('DTEND') if item.get('DTEND') else None
        end_day = day
        if isinstance(end_value, datetime) and start_iso:
            end = end_value.replace(tzinfo=BERLIN) if end_value.tzinfo is None else end_value.astimezone(BERLIN)
            if end.timestamp() < start.timestamp():
                raise ValueError('Karlsbad: umgekehrte iCalendar-Zeitspanne')
            end_day, end_time, end_iso = end.date(), end.strftime('%H:%M'), end.isoformat()
        elif isinstance(end_value, date) and not isinstance(end_value, datetime) and end_value > day:
            end_day = end_value - timedelta(days=1)
        # The municipality's subscription exports all DTSTART values as dates;
        # infer a clock only from an explicit clock phrase in the published title.
        if begin_time is None:
            match = re.search(r'\b(?:ab|um)\s+(\d{1,2}[:.]\d{2})\s*Uhr\b', title, re.I)
            if match:
                begin_time = _times(match.group(1).replace('.', ':'))[0]
        location = str(item.get('LOCATION', '')).strip()
        url = str(item.get('URL', '')).strip() or 'https://www.karlsbad.de/eventcalendar'
        if urlsplit(url).hostname != 'www.karlsbad.de': raise ValueError('Karlsbad: unerwartete Original-URL')
        records.append({'recordId': uid, 'title': title, 'url': url, 'startDate': day.isoformat(),
            'endDate': end_day.isoformat(), 'startTime': begin_time, 'endTime': end_time,
            'startIso': start_iso, 'endIso': end_iso, 'location': location,
            'organizer': str(item.get('ORGANIZER', '')).strip(), 'categoryKey': 'stadtleben',
            'status': _ical_status(item), 'dataQuality': 'ical-feed',
            'scheduleNote': '' if begin_time else 'Die Kalender-Abo-Datei veröffentlicht keine Uhrzeit.'})
    return records, {'coverageKind': 'ical-feed', 'allowEmpty': True,
                     'coverageNote': 'Alle Termine des kommunalen iCalendar-Abos; Uhrzeiten werden aus Zeitwerten oder ausdrücklichen Angaben im Titel übernommen.'}


def koenigsbach_page(raw, base_url):
    tree = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8'))
    cards = tree.xpath('//article[.//h2]')
    if not cards: raise ValueError('Königsbach-Stein: Veranstaltungskarten nicht erkannt')
    records = []
    for card in cards:
        title = _text(_one(card.xpath('.//h2'), 'Königsbach-Stein Titel'))
        text = _text(card)
        match = re.search(r'\b(\d{1,2}\.\d{1,2}\.\d{4})\b', text)
        if not match: raise ValueError('Königsbach-Stein: Veranstaltungsdatum nicht erkannt')
        day = _parse_date(match.group(1))
        times = _times(text)
        venue_nodes = card.xpath('.//li[contains(@class,"items-center")][contains(.,"bx-map") or .//span[contains(@class,"bx-map")]]')
        if len(venue_nodes) > 1: raise ValueError('Königsbach-Stein: Veranstaltungsort mehrdeutig')
        venue = _text(venue_nodes[0]) if venue_nodes else ''
        organizers = card.xpath('.//a[@aria-label]')
        organizer = _text(organizers[0]) if organizers else ''
        composite = '\x1f'.join((title, day.isoformat(), times[0] if times else '', venue, organizer))
        record_id = 'listing-' + hashlib.sha256(composite.encode()).hexdigest()[:20]
        records.append({'recordId': record_id, 'title': title, 'url': base_url,
            'startDate': day.isoformat(), 'startTime': times[0] if times else None, 'location': venue,
            'organizer': organizer, 'categoryKey': 'stadtleben'})
    return records


def koenigsbach_records(raw, base_url, fetch):
    records = list(koenigsbach_page(raw, base_url))
    tree = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8'))
    pages = {1}
    def add_pages(page_raw):
        page_tree = html.fromstring(page_raw, parser=html.HTMLParser(encoding='utf-8'))
        pages.update(int(x) for href in page_tree.xpath('//a/@href') for x in re.findall(r'[?&]seite=(\d+)', href))
    add_pages(raw)
    loaded = {1}
    while True:
        pending = sorted(pages - loaded)
        if not pending: break
        if len(pages) > 100: raise ValueError('Königsbach-Stein: unerwartet viele Ergebnisseiten')
        number = pending[0]
        url = base_url.rstrip('/') + '/?seite=' + str(number)
        page = fetch(url)
        records.extend(koenigsbach_page(page, url))
        loaded.add(number); add_pages(page)
    if pages != set(range(1, max(pages) + 1)):
        raise ValueError('Königsbach-Stein: unvollständige Seitennavigation')
    ids = [r['recordId'] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Königsbach-Stein: doppelte Termine in der veröffentlichten Liste')
    return records, {'coverageKind': 'html-list', 'coverageNote': 'Alle veröffentlichten Einträge der paginierten Gemeindeliste; die Seite enthält keine stabilen Detail-IDs.', 'pagesFetched': len(loaded)}
