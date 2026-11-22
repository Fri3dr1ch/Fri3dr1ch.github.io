"""Read event-specific location fields, including collapsed details."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import re
from urllib.parse import parse_qs, urlsplit

from lxml import etree, html


def cls(name):
    return 'contains(concat(" ",normalize-space(@class)," ")," ' + name + ' ")'


def tree(raw):
    return html.fromstring(raw.decode('utf-8') if isinstance(raw, bytes) else raw)


def location(nodes):
    values = list(dict.fromkeys(' '.join(n.text_content().split()) for n in nodes))
    if len(values) > 1:
        raise ValueError('Mehrdeutiges Ortsfeld')
    value = values[0] if values else ''
    if re.fullmatch(r'[-–—]+|folgt(?: noch)?|noch offen|noch in Klärung|(?:Ort )?wird noch bekannt gegeben', value, re.I):
        return ''
    return value


def eki_location(raw, record):
    page = tree(raw)
    if not page.xpath('//h1[' + cls('v-detail-titel') + ']'):
        raise ValueError('EKI-Termindetail fehlt')
    return location(page.xpath('//*[' + cls('v-detail-box-oertlichkeit') + ']//*[' + cls('ortsname') + ']'))


def walzbachtal_location(raw, record):
    page = tree(raw)
    if not page.xpath('//main//article[' + cls('card--horizontal') + ']//h1'):
        raise ValueError('Walzbachtaler Termindetail fehlt')
    return location(page.xpath('//main//div[' + cls('dgf-description-list') + ' and ' + cls('location') + ']//dd'))


def hagsfeld_location(raw, record):
    [event] = tree(raw).xpath('//main//div[' + cls('event') + ' and ' + cls('layout_full') + ']')
    return location(event.xpath('./p[' + cls('location') + ']'))


def weingarten_location(raw, record):
    event_id = record['recordId']
    page = tree(raw)
    boxes = page.xpath('//*[@id=$id]', id='hwveranstaltung__show__' + event_id)
    if len(boxes) != 1:
        raise ValueError('Weingartener Termindetail fehlt')
    box = boxes[0]
    fields = ['hw_record__simpleLocation', 'hw_record__location']
    for field in fields:
        published = location(box.xpath('.//*[' + cls(field) + ']//*[' + cls('hw_record__value__text') + ']'))
        if published:
            return published
    markers = box.xpath('.//*[@id=$id]', id='hw_map_location__' + event_id)
    if len(markers) != 1:
        return ''
    marker = markers[0]
    street = ' '.join(filter(None, (marker.get('data-strasse'), marker.get('data-hausnummer'))))
    town = ' '.join(filter(None, (marker.get('data-plz'), marker.get('data-ort'))))
    return ', '.join(filter(None, (street, town))) if street else ''


def karlsbad_location(raw, record):
    ids = parse_qs(urlsplit(record['url']).query).get('event_id', [])
    if len(ids) != 1 or not ids[0].isdigit():
        return ''
    nodes = tree(raw).xpath('//*[@id=$id]', id='event_' + ids[0])
    if len(nodes) != 1:
        raise ValueError('Karlsbader Termindetail fehlt')
    box = nodes[0]
    published = location(box.xpath('.//*[@itemprop="location"]'))
    if published:
        return published
    # The description can publish a labelled meeting point. Organiser and
    # municipal footer addresses belong to different fields.
    paragraphs = box.xpath('.//*[@itemprop="description"]/*[self::p or self::div]')
    for paragraph in paragraphs:
        if match := re.fullmatch(r'(?:Ort|Wo|Treffpunkt|Veranstaltungsort)\s*:\s*(.+)',
                                 location([paragraph]), re.I):
            return match[1]
    return ''


PARSERS = {
    'eki-groetzingen': eki_location,
    'walzbachtal': walzbachtal_location,
    'hagsfeld': hagsfeld_location,
    'weingarten': weingarten_location,
    'karlsbad': karlsbad_location,
}


def enrich_locations(source, records, fetch, stamp, today, previous):
    """Retry individual failures without invalidating the source's schedule."""
    when = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    cutoff = when - timedelta(days=7)
    previous = (previous or {}).get('detailLocations', {})
    pending = {
        r['url']: r for r in records
        if not r.get('location') and (r.get('endDate') or r['startDate']) >= today
        and (source != 'karlsbad' or parse_qs(urlsplit(r['url']).query).get('event_id'))
    }

    def check(record):
        old = previous.get(record['url'])
        if old and datetime.fromisoformat(old['checkedAt']) >= cutoff:
            return old, False
        try:
            found = PARSERS[source](fetch(record['url']), record)
            return {'checkedAt': when.isoformat(), 'location': found}, False
        except (OSError, ValueError, etree.Error):
            return old, True

    with ThreadPoolExecutor(max_workers=3) as pool:
        checked = dict(zip(pending, pool.map(check, pending.values())))
    for record in records:
        if not record.get('location') and record['url'] in checked:
            entry, _ = checked[record['url']]
            if entry:
                record['location'] = entry['location']
    failures = sum(failed for _, failed in checked.values())
    if failures:
        print(f'{source}: {failures} Ortsdetails beim nächsten Import erneut prüfen', flush=True)
    return {url: entry for url, (entry, _) in checked.items() if entry is not None}
