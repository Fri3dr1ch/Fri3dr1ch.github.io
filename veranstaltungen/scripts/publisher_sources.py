"""Small adapters for official regional event lists and publisher calendars."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
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


def naturtreff_records(raw):
    cal = Calendar.from_ical(raw)
    components = cal.walk('VEVENT')
    if not components or cal.errors:
        raise ValueError('Naturtreff: iCalendar enthält keine verlässlichen Termine')
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
        if isinstance(start_value, datetime):
            start = start_value.replace(tzinfo=BERLIN) if start_value.tzinfo is None else start_value.astimezone(BERLIN)
            end = None
            if finish_value:
                finish = finish_value.replace(tzinfo=BERLIN) if finish_value.tzinfo is None else finish_value.astimezone(BERLIN)
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
            'endIso': end, 'location': str(item.get('LOCATION', '')).strip(),
            'description': str(item.get('DESCRIPTION', '')).strip(), 'allDay': isinstance(start_value, date) and not isinstance(start_value, datetime),
            'categoryKey': 'stadtleben', 'dataQuality': 'ical-feed'})
    return records, {'coverageKind': 'ical-feed', 'coverageNote': 'Alle einzeln veröffentlichten Termine im Naturtreff-iCalendar-Feed.'}


def vhs_land_records(raw, base_url):
    tree = _doc(raw)
    cards = tree.xpath('//div[contains(@class,"course") or contains(@class,"kurs")]')
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
                candidates.append((parent, link, course_id.group(1)))
                break
    # Distinct course ids may be linked more than once from navigation/footer.
    unique = {}
    for card, link, course_id in candidates:
        unique.setdefault(course_id, (card, link))
    if not unique:
        raise ValueError('vhs Karlsruhe Land: Kursliste oder Kurskarten nicht erkannt')
    records = []
    for course_id, (card, link) in unique.items():
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
        day = _parse_date(start_match.group(1))
        clocks = _times(start_match.group(2))
        begin_time = clocks[0] if clocks else None
        # A one-session filter is the low-maintenance boundary. Series remain
        # out even where their first meeting happens to be in the area.
        if int(duration_match.group(1)) != 1:
            continue
        location = re.sub(r'\s+', ' ', place_match.group(1)).strip(' :') if place_match else ''
        records.append({'recordId': course_id, 'title': title, 'url': detail_url,
            'startDate': day.isoformat(), 'startTime': begin_time, 'endTime': clocks[1] if len(clocks) > 1 else None, 'location': location,
            'description': 'Einzelveranstaltung aus dem veröffentlichten Kursangebot der vhs Karlsruhe Land.'})
    if not records and not unique:
        raise ValueError('vhs Karlsruhe Land: leere oder nicht erkannte Kursliste')
    return records, {'coverageKind': 'html-list', 'coverageNote': 'Alle aktuell veröffentlichten Einzelveranstaltungen (Dauer: 1 Termin); mehrteilige Kurse bleiben außen vor.', 'allowEmpty': True, 'pagesFetched': 1}


def awo_records(raw, source_url):
    tree = _doc(raw)
    body = ' '.join(tree.text_content().split())
    # The public page exposes two finite, explicitly dated lists. Do not expand
    # its indefinite weekly/third-Tuesday descriptions into future recurrences.
    match = re.search(r'Stuhl-Yoga\s*[–-]\s*jeden\s+3\.\s+Dienstag\s+im\s+Monat\s+um\s+(\d{1,2}:\d{2})\s+Uhr\s*:\s*(.+?)\s+Donnerstags', body, re.I)
    if not match:
        raise ValueError('AWO Grötzingen: ausdrücklich datierte Yoga-Liste nicht erkannt')
    year_matches = re.findall(r'\b(20\d{2})\b', match.group(2))
    if not year_matches:
        raise ValueError('AWO Grötzingen: Bezugsjahr der Yoga-Liste fehlt')
    yoga_year = int(year_matches[-1])
    yoga_dates = re.findall(r'(?<!\d)(\d{1,2})\s*\.\s*(\d{1,2})\s*\.(?:\s*(20\d{2}))?', match.group(2))
    if len(yoga_dates) < 2:
        raise ValueError('AWO Grötzingen: datierte Yoga-Termine nicht erkannt')
    records = []
    for day, month, explicit_year in yoga_dates:
        parsed = date(int(explicit_year or yoga_year), int(month), int(day))
        records.append({'recordId': f'stuhl-yoga-{parsed.isoformat()}', 'title': 'Stuhl-Yoga', 'url': source_url,
            'startDate': parsed.isoformat(), 'startTime': match.group(1), 'location': 'Begegnungsstätte Grötzingen, Niddastraße 9',
            'organizer': 'AWO Ortsverein Grötzingen', 'categoryKey': 'stadtleben'})
    history = re.search(r'Grötzinger\s+AWO\s+Geschichtsstammtisch\s*[–-]\s*immer\s+dienstags,\s*von\s+(\d{1,2}:\d{2})\s+bis\s+(\d{1,2}:\d{2})\s+Uhr\s*:\s*(.+?)\s+Ausflüge', body, re.I)
    if not history:
        raise ValueError('AWO Grötzingen: datierte Geschichtsstammtisch-Liste nicht erkannt')
    for date_text, title in re.findall(r'(\d{2}\.\d{2}\.\d{4})\s*:\s*([^:]+?)(?=\s+\d{2}\.\d{2}\.\d{4}\s*:|$)', history.group(3)):
        parsed = _parse_date(date_text)
        records.append({'recordId': f'geschichtsstammtisch-{parsed.isoformat()}', 'title': 'Grötzinger AWO Geschichtsstammtisch: ' + title.strip(),
            'url': source_url, 'startDate': parsed.isoformat(), 'startTime': history.group(1), 'endTime': history.group(2),
            'location': 'Begegnungsstätte Grötzingen, Niddastraße 9', 'organizer': 'AWO Ortsverein Grötzingen', 'categoryKey': 'literatur'})
    if len(records) < 4:
        raise ValueError('AWO Grötzingen: datierte Veranstaltungslisten unerwartet kurz')
    return records, {'coverageKind': 'html-list', 'coverageNote': 'Nur die endlich und ausdrücklich datierten Yoga- und Geschichtsstammtisch-Termine; keine wöchentlichen Reihen.'}


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
            'startTime': start.strftime('%H:%M') if when.time() != time.min else None,
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


def bergdoerfer_records(raw, base_url):
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
        day = _parse_date(date_match.group(1))
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
            'startDate': day.isoformat(), 'startTime': times[0] if times else None,
            'endTime': times[1] if len(times) > 1 else None, 'location': location, 'categoryKey': 'stadtleben'})
    return records, {'coverageKind': 'html-list', 'coverageNote': f'Alle {len(records)} ausdrücklich veröffentlichten Termine der zentralen Terminübersicht.'}


def karlsbad_records(raw):
    cal = Calendar.from_ical(raw)
    components = cal.walk('VEVENT')
    if not components or cal.errors:
        raise ValueError('Karlsbad: iCalendar-Feed leer oder nicht lesbar')
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
        if isinstance(begin, datetime) and not date_only:
            start = begin.replace(tzinfo=BERLIN) if begin.tzinfo is None else begin.astimezone(BERLIN)
            day, begin_time = start.date(), start.strftime('%H:%M')
        elif isinstance(begin, date) or date_only:
            day = begin.date() if isinstance(begin, datetime) else begin
            begin_time = None
        else: raise ValueError('Karlsbad: unbekannter DTSTART-Typ')
        end_value = item.decoded('DTEND') if item.get('DTEND') else None
        end_day = day
        if isinstance(end_value, date) and not isinstance(end_value, datetime) and end_value > day:
            end_day = end_value - timedelta(days=1)
        # The municipality's subscription exports all DTSTART values as dates;
        # infer a clock only from an explicit clock phrase in the published title.
        if begin_time is None:
            match = re.search(r'\b(?:ab|um)\s+(\d{1,2}[:.]\d{2})\s*Uhr\b', title, re.I)
            if match:
                begin_time = match.group(1).replace('.', ':')
        location = str(item.get('LOCATION', '')).strip()
        url = str(item.get('URL', '')).strip() or 'https://www.karlsbad.de/eventcalendar'
        if urlsplit(url).hostname != 'www.karlsbad.de': raise ValueError('Karlsbad: unerwartete Original-URL')
        records.append({'recordId': uid, 'title': title, 'url': url, 'startDate': day.isoformat(),
            'endDate': end_day.isoformat(), 'startTime': begin_time, 'location': location,
            'organizer': str(item.get('ORGANIZER', '')).strip(), 'categoryKey': 'stadtleben',
            'dataQuality': 'ical-feed',
            'scheduleNote': '' if begin_time else 'Die Kalender-Abo-Datei veröffentlicht keine Uhrzeit.'})
    return records, {'coverageKind': 'ical-feed', 'coverageNote': 'Alle Termine des kommunalen iCalendar-Abos; Uhrzeiten werden nur übernommen, wenn sie ausdrücklich im Titel stehen.'}


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
        if not venue_nodes:
            venue_nodes = card.xpath('.//li[position()=2]')
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
