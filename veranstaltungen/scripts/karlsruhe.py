"""Refresh Karlsruhe's RSS selection and its per-event iCalendar details."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from icalendar import Calendar
from lxml import etree, html

BERLIN = ZoneInfo('Europe/Berlin')
FEED = 'https://kalender.karlsruhe.de/db/termine/rss'
GROETZINGEN_LIBRARY = 'https://kalender.karlsruhe.de/db/termine/549983/'
LABELS = {'ausstellungen': 'Kunst & Ausstellungen', 'stadtleben': 'Stadtleben',
          'literatur': 'Literatur & Vorträge', 'sportveranstaltung': 'Sport', 'musik': 'Musik',
          'wirtschaft': 'Wirtschaft & Wissenschaft', 'theater': 'Theater & Tanz',
          'messen_und_kongresse': 'Messen & Kongresse', 'architektur': 'Architektur',
          'sonstiges': 'Sonstiges', 'festivals': 'Festivals'}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def event_url(value):
    parsed = urlsplit(value or '')
    if parsed.scheme != 'https' or parsed.netloc != 'kalender.karlsruhe.de' or not parsed.path.startswith('/db/termine/') or parsed.query or parsed.fragment:
        raise ValueError('Karlsruhe: unbekanntes Veranstaltungslinkziel')
    return value.rstrip('/')


def plain(value):
    return ' '.join(html.fromstring('<div>' + (value or '') + '</div>').text_content().split())


def cancelled(title):
    return bool(re.match(r'^abgesagt\s*:', title, re.IGNORECASE))


def parse_feed(raw, previous=None):
    tree = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True))
    channel = tree.find('channel')
    if channel is None or 'Karlsruhe' not in (channel.findtext('title') or ''):
        raise ValueError('Karlsruhe: RSS-Kanal nicht erkannt')
    nodes = channel.findall('item')
    if not 1 <= len(nodes) <= 10_000:
        raise ValueError('Karlsruhe: leere oder unerwartet große RSS-Auswahl')
    old = {e['sourceGuid']: e for e in (previous or {}).get('events', [])}
    records = []
    for node in nodes:
        guid = node.findtext('guid', '').strip()
        title = plain(node.findtext('title'))
        url = event_url(node.findtext('link'))
        when = parsedate_to_datetime(node.findtext('pubDate', ''))
        if not guid or not title or when.tzinfo is None:
            raise ValueError('Karlsruhe: Titel, GUID oder datierter Start fehlt')
        when = when.astimezone(BERLIN)
        summary = plain(node.findtext('description'))
        category = urlsplit(url).path.split('/')[3]
        category = category if category in LABELS else 'sonstiges'
        # Karlsruhe uses RSS pubDate for the event date, not editorial freshness.
        # Preserve existing identities when a source corrects its start time.
        identity = old.get(guid, {}).get('id') or 'karlsruhe-' + hashlib.sha256((guid + when.isoformat()).encode()).hexdigest()[:16]
        records.append({'id': identity, 'sourceId': 'karlsruhe', 'sourceName': 'Stadt Karlsruhe',
                        'sourceGuid': guid, 'title': title, 'url': url, 'icalUrl': url + '/ical',
                        'categoryKey': category, 'category': LABELS[category], 'description': summary,
                        'rssStart': when.isoformat(), 'region': 'Karlsruhe und Umgebung', 'city': None})
    if len({r['sourceGuid'] for r in records}) != len(records):
        raise ValueError('Karlsruhe: mehrdeutige doppelte RSS-GUID')
    return records, channel.findtext('lastBuildDate')


def local_datetime(value, hint=None):
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(BERLIN)
        result = value.replace(tzinfo=BERLIN)
        if hint and hint.replace(tzinfo=None) == value and hint.utcoffset() != result.utcoffset():
            result = value.replace(tzinfo=BERLIN, fold=1)
        if result.astimezone(timezone.utc).astimezone(BERLIN).replace(tzinfo=None) != value:
            raise ValueError('Karlsruhe: Ortszeit existiert wegen Zeitumstellung nicht')
        return result
    if isinstance(value, date):
        return datetime.combine(value, time.min, BERLIN)
    raise ValueError('Karlsruhe: unbekanntes Datumsformat')


def detail(record, raw, checked_at):
    # Organizer/contact fields are not imported. This publisher sometimes emits
    # invalid quoting there; exclude the unused property after RFC unfolding.
    raw = re.sub(br'\r?\n[ \t]', b'', raw)
    raw = re.sub(br'(?im)^ORGANIZER[;:][^\r\n]*(?:\r?\n|$)', b'', raw)
    # Karlsruhe emits an empty optional DTEND for events with no known finish.
    # Remove only that exact empty property; malformed nonempty dates still fail.
    raw = re.sub(br'(?m)^DTEND:[ \t]*\r?\n', b'', raw)
    calendar = Calendar.from_ical(raw)
    entries = calendar.walk('VEVENT')
    if len(entries) != 1 or calendar.errors or entries[0].errors:
        fields = sorted({str(field or 'Inhaltszeile') for component in [calendar, *entries] for field, _ in component.errors})
        raise ValueError('Karlsruhe: iCalendar enthält keine eindeutigen fehlerfreien Termindaten' + (' (' + ', '.join(fields) + ')' if fields else ''))
    item = entries[0]
    if str(item.get('UID', '')) != record['sourceGuid'].removeprefix('ka '):
        raise ValueError('Karlsruhe: iCalendar-UID widerspricht RSS-GUID')
    if item.get('RRULE') or item.get('RDATE'):
        raise ValueError('Karlsruhe: neue iCalendar-Wiederholungsstruktur; Adapter prüfen')
    original = item.decoded('DTSTART')
    all_day = not isinstance(original, datetime)
    rss_start = datetime.fromisoformat(record['rssStart'])
    begin = local_datetime(original, rss_start)
    finish = local_datetime(item.decoded('DTEND')) if item.get('DTEND') else None
    if finish and finish < begin:
        raise ValueError('Karlsruhe: umgekehrte iCalendar-Zeitspanne')
    # This publisher represents an exhibition's inclusive final date with a
    # floating midnight DTEND (e.g. BauSteinWelten: 29 November, not 28).
    # True DATE-valued all-day DTEND remains RFC-exclusive.
    explicit_midnight = bool(re.match(r'^(?:0|00)[.:]00\b|^0 Uhr\b|^00 Uhr\b', record['description']))
    time_known = not all_day and (begin.time() != time.min or explicit_midnight)
    end_day = finish.date() if finish else begin.date()
    if finish and finish > begin and (all_day or (time_known and finish.time() == time.min)):
        end_day -= timedelta(days=1)
    duration = (end_day - begin.date()).days
    result = {k: v for k, v in record.items() if k != 'rssStart'}
    result.update({'start': begin.isoformat(), 'end': finish.isoformat() if finish else None,
                   'startDate': begin.date().isoformat(), 'endDate': end_day.isoformat(),
                   'startTime': begin.strftime('%H:%M') if time_known else None,
                   'endTime': finish.strftime('%H:%M') if finish and time_known else None,
                   'allDay': all_day, 'timeKnown': time_known, 'longRunning': duration >= 7,
                   'location': str(item.get('LOCATION', '')).strip(),
                   'status': 'cancelled' if cancelled(record['title']) or str(item.get('STATUS', '')).upper() == 'CANCELLED' else 'scheduled',
                   'dataQuality': 'complete', 'detailCheckedAt': checked_at,
                   'scheduleNote': 'Laufzeit laut Quelle. Konkrete Termine und Öffnungszeiten bitte auf der Originalseite prüfen.' if duration >= 7 else ''})
    return result


def rss_fallback(record, checked_at):
    begin = datetime.fromisoformat(record['rssStart'])
    known = begin.time() != time.min or bool(re.match(r'^(?:0|00)[.:]00\b|^0 Uhr\b', record['description']))
    return {**{k: v for k, v in record.items() if k != 'rssStart'},
            'start': begin.isoformat(), 'end': None, 'startDate': begin.date().isoformat(),
            'endDate': begin.date().isoformat(), 'startTime': begin.strftime('%H:%M') if known else None,
            'endTime': None, 'allDay': False, 'timeKnown': known, 'longRunning': False,
            'location': '', 'status': 'cancelled' if cancelled(record['title']) else 'scheduled', 'dataQuality': 'rss-only',
            'detailCheckedAt': None, 'scheduleNote': 'Die vollständige Zeitspanne ist ohne iCalendar-Abruf unbestätigt.'}


def library_listing(raw):
    tree = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8'))
    title = tree.find('.//title')
    if title is None or 'Stadtteilbibliothek Grötzingen' not in title.text_content():
        raise ValueError('Karlsruhe: Grötzinger Bibliotheksliste nicht erkannt')
    records, seen = [], set()
    for link in tree.xpath('//a[@href]'):
        url = link.get('href', '').split('#', 1)[0]
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'kalender.karlsruhe.de' or not re.match(r'^/db/termine/[^/]+/[^/]+$', parsed.path):
            continue
        url = url.rstrip('/')
        title_text = plain(link.text_content())
        if title_text and url not in seen:
            seen.add(url)
            records.append({'url': url, 'title': title_text, 'categoryKey': parsed.path.split('/')[3]})
    if not records:
        raise ValueError('Karlsruhe: keine Termine auf der Grötzinger Bibliotheksliste erkannt')
    return records


def library_ical_record(record, raw, cached=None):
    calendar = Calendar.from_ical(raw)
    items = calendar.walk('VEVENT')
    if len(items) != 1 or calendar.errors or items[0].errors:
        raise ValueError('Karlsruhe: Bibliotheks iCalendar enthält keinen eindeutigen Termin')
    item = items[0]
    if item.get('RRULE') or item.get('RDATE') or item.get('RECURRENCE-ID'):
        raise ValueError('Karlsruhe: Bibliotheks iCalendar enthält eine unerwartete Serie')
    uid = str(item.get('UID', '')).strip()
    title = str(item.get('SUMMARY', '')).strip()
    original = item.decoded('DTSTART')
    if isinstance(original, datetime):
        when = local_datetime(original)
    elif isinstance(original, date):
        when = local_datetime(original)
    else:
        raise ValueError('Karlsruhe: Bibliotheks iCalendar hat kein DTSTART')
    if not uid or not title or title != record['title']:
        raise ValueError('Karlsruhe: Bibliotheksliste und iCalendar-Titel widersprechen sich')
    guid = 'ka ' + uid
    identity = (cached or {}).get('id') or 'karlsruhe-' + hashlib.sha256((guid + when.isoformat()).encode()).hexdigest()[:16]
    feed_record = {'id': identity, 'sourceId': 'karlsruhe', 'sourceName': 'Stadt Karlsruhe',
                   'sourceGuid': guid, 'title': title, 'url': record['url'], 'icalUrl': record['url'] + '/ical',
                   'categoryKey': record['categoryKey'] if record['categoryKey'] in LABELS else 'sonstiges',
                   'category': LABELS.get(record['categoryKey'], LABELS['sonstiges']),
                   'description': plain(str(item.get('DESCRIPTION', ''))), 'rssStart': when.isoformat(),
                   'region': 'Karlsruhe und Umgebung', 'city': None}
    event = detail(feed_record, raw, stamp())
    event['librarySupplement'] = 'stadtteilbibliothek-groetzingen'
    return event


def supplement_groetzingen_library(events, previous, client, source):
    old = {e.get('sourceGuid'): e for e in previous.get('events', []) if e.get('librarySupplement') == 'stadtteilbibliothek-groetzingen'}
    current_urls = {e['url'].rstrip('/') for e in events}
    by_guid = {e.get('sourceGuid'): e for e in events}
    retained = [e for guid, e in old.items() if guid not in by_guid and e['url'].rstrip('/') not in current_urls]
    source = {**previous['sources'][0], **source}
    source['listingUrl'] = GROETZINGEN_LIBRARY
    source['listingRefreshHours'] = 24
    cached_listing = source.get('listingLastSuccessAt')
    try:
        listing_age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached_listing.replace('Z', '+00:00'))).total_seconds() if cached_listing else -1
    except (ValueError, TypeError):
        listing_age = -1
    if source.get('listingStatus') == 'ok' and 0 <= listing_age < 24 * 3600:
        events.extend(deepcopy(retained))
        return source
    try:
        links = library_listing(client(GROETZINGEN_LIBRARY))
    except Exception as error:
        source.update(status='partial', listingStatus='error', listingLastAttemptAt=stamp(),
                      listingLastError=type(error).__name__ + ': ' + str(error))
        events.extend(deepcopy(retained))
        return source
    old_by_url = {e['url'].rstrip('/'): e for e in retained}
    updated, errors = [], []
    for record in links:
        if record['url'] in current_urls: continue
        cached = old_by_url.get(record['url'])
        if cached and cached.get('title') == record['title']:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached['detailCheckedAt'])).total_seconds()
            except (KeyError, ValueError, TypeError):
                age = -1
            if 0 <= age < 6 * 3600:
                updated.append(deepcopy(cached)); continue
        try:
            raw = client(record['url'] + '/ical')
            # First validate the source UID before looking up its last-known identity.
            calendar = Calendar.from_ical(raw)
            items = calendar.walk('VEVENT')
            uid = str(items[0].get('UID', '')).strip() if len(items) == 1 else ''
            event = library_ical_record(record, raw, old.get('ka ' + uid))
            if event['sourceGuid'] in by_guid:
                continue
            updated.append(event)
        except Exception as error:
            errors.append(record['url'] + ': ' + type(error).__name__ + ': ' + str(error))
            if cached: updated.append(deepcopy(cached))
    # If any detail is incomplete, retain cached rows missing from this attempt;
    # the next successful scan will remove genuinely withdrawn entries.
    if errors:
        retained_urls = {e.get('url', '').rstrip('/') for e in updated}
        retained_guids = {e['sourceGuid'] for e in updated}
        updated.extend(deepcopy(e) for e in retained
                       if e['url'].rstrip('/') not in retained_urls and e['sourceGuid'] not in retained_guids)
    ids = [e['id'] for e in updated]
    if len(ids) != len(set(ids)):
        source.update(status='partial', listingStatus='error', listingLastAttemptAt=stamp(),
                      listingLastError='Doppelte Identität in der Bibliotheksliste')
        events.extend(deepcopy(retained))
        return source
    events.extend(updated)
    source.update(listingUrl=GROETZINGEN_LIBRARY, listingRecordCount=len(links), listingRefreshHours=24,
                  listingLastAttemptAt=stamp(), listingStatus='partial' if errors else 'ok')
    source.pop('listingLastError', None)
    if errors:
        source['status'] = 'partial'
        source['listingLastError'] = '; '.join(errors[:5])
    else:
        source['listingLastSuccessAt'] = stamp()
    return source


def refresh(previous, client):
    attempted = stamp()
    try:
        records, built_at = parse_feed(client(FEED), previous)
    except Exception as error:
        retained = deepcopy(previous)
        retained['sources'][0].update(status='error', lastAttemptAt=attempted,
                                      lastError=type(error).__name__ + ': ' + str(error))
        return retained
    old = {e['sourceGuid']: e for e in previous.get('events', [])}
    def load(record):
        cached = old.get(record['sourceGuid'])
        try:
            # Reuse an unchanged, successfully checked detail for at most six
            # hours. RSS is still fetched every run. Keep its real check time.
            # Incomplete records and changed RSS metadata are always retried.
            checked = cached.get('detailCheckedAt') if cached else None
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(checked)).total_seconds() if checked else -1
            except (ValueError, TypeError):
                age = -1
            if (cached and cached.get('dataQuality') == 'complete' and 0 <= age < 6 * 3600
                    and cached.get('start') == record['rssStart']
                    and all(cached.get(k) == v for k, v in record.items() if k != 'rssStart')):
                return deepcopy(cached), None
            headers = {}
            if cached:
                if cached.get('icalEtag'): headers['If-None-Match'] = cached['icalEtag']
                if cached.get('icalLastModified'): headers['If-Modified-Since'] = cached['icalLastModified']
            response = client.request(record['icalUrl'], headers=headers)
            if response.status == 304:
                if not cached or cached.get('dataQuality') == 'rss-only':
                    raise ValueError('iCalendar 304 ohne gültige Detaildaten')
                result = deepcopy(cached)
                result.update({k: v for k, v in record.items() if k != 'rssStart'})
                result.update(dataQuality='complete', detailCheckedAt=stamp())
            else:
                result = detail(record, response.body, stamp())
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                result['icalEtag'] = response_headers.get('etag')
                result['icalLastModified'] = response_headers.get('last-modified')
            return result, None
        except Exception as error:
            result = deepcopy(cached) if cached and cached.get('dataQuality') != 'rss-only' else rss_fallback(record, attempted)
            result.update({k: v for k, v in record.items() if k != 'rssStart'})
            if cached and cached.get('dataQuality') != 'rss-only': result['dataQuality'] = 'cached'
            return result, record['sourceGuid'] + ': ' + type(error).__name__ + ': ' + str(error)
    with ThreadPoolExecutor(max_workers=3) as pool:
        loaded = []
        futures = [pool.submit(load, record) for record in records]
        for future in as_completed(futures):
            loaded.append(future.result())
            if len(loaded) % 50 == 0 or len(loaded) == len(records):
                print(f'Karlsruhe: {len(loaded)}/{len(records)} Detailabrufe geprüft', flush=True)
    errors = [error for _, error in loaded if error]
    events = [event for event, _ in loaded]
    for event in events:
        if cancelled(event['title']): event['status'] = 'cancelled'
    source = {**previous['sources'][0], 'lastAttemptAt': attempted, 'lastSuccessAt': stamp(),
              'status': 'partial' if errors else 'ok', 'feedBuiltAt': built_at,
              'feedItemCount': len(records), 'eventCount': len(events), 'detailFailures': len(errors),
              'refreshHours': 24, 'upcomingStartThrough': max(r['rssStart'][:10] for r in records)}
    source.pop('lastError', None)
    if errors: source['lastError'] = '; '.join(errors[:5])
    source = supplement_groetzingen_library(events, previous, client, source)
    source['eventCount'] = len(events)
    if source.get('listingStatus') in {'error', 'partial'}:
        source['lastError'] = '; '.join(filter(None, [source.get('lastError'), source.get('listingLastError')]))
    # Preserve the main RSS health state while reflecting any partial listing.
    return {**previous, 'generatedAt': stamp(), 'sources': [source],
            'events': sorted(events, key=lambda e: (e['startDate'], e.get('startTime') or '99:99', e['title']))}
