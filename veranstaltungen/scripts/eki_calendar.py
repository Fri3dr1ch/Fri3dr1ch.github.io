"""Read explicitly dated events from the public EKI Grötzingen HTML calendar.

Follow the source's advertised pages and verify their complete result ranges.
Recurrences are never expanded: each imported record has an actual source row.
"""
from datetime import datetime
import re
from urllib.parse import parse_qsl,urljoin,urlsplit,urlunsplit

from lxml import html

URL='https://eki-groetzingen.de/terminkalender/'
CMS_ALIAS='eki-groetzingen.eki-musterhausen.de'
ORGANIZER='Evangelische Kirche in Grötzingen'

def title_location(title):
    # These titles name the actual venue. The CMS sometimes shows a general
    # church or town hall entry alongside them.
    if re.search(r'\bSenioren-Gottesdienst im Haus PAMINA\b', title, re.I):
        return 'Haus Pamina'
    if re.search(r'\bAdventsliedersingen auf dem Rathausplatz\b', title, re.I):
        return 'Rathausplatz Grötzingen'
    if re.fullmatch(r'Ökumenisches Hausgebet(?: im Advent)?', title, re.I):
        return 'Zu Hause'
    return ''

def has_class(token):
    return 'contains(concat(" ",normalize-space(@class)," ")," '+token+' ")'

def text(node):
    return ' '.join(node.text_content().split())

def one(nodes,label):
    if len(nodes)!=1:raise ValueError('EKI '+label+': erwartete Kalenderstruktur fehlt oder ist mehrdeutig')
    return nodes[0]

def parse_listing(raw, *, allow_pagination=False):
    tree=html.fromstring(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
    boxes=tree.xpath('//div['+has_class('v-listefe-item')+']')
    if not boxes:raise ValueError('EKI: keine ausdrücklich datierten Termine gefunden')
    records=[]
    for box in boxes:
        date_text=text(one(box.xpath('.//div['+has_class('v-listefe-item-datum')+']'),'Datumsfeld'))
        # Do not scan title/description for clocks: titles can mention obsolete
        # times, while this designated date field contains the corrected time.
        match=re.fullmatch(r'(?:Mo|Di|Mi|Do|Fr|Sa|So)\. (\d{2}\.\d{2}\.\d{4})(?:, (\d{2}:\d{2})(?: bis (\d{2}:\d{2}))? Uhr)?',date_text)
        if not match:raise ValueError('EKI: unbekanntes Datumsformat: '+date_text)
        day=datetime.strptime(match[1],'%d.%m.%Y').date().isoformat()
        for clock in (match[2],match[3]):
            if clock:datetime.strptime(clock,'%H:%M')
        heading=one(box.xpath('.//div['+has_class('titel')+']//h5'),'Titel')
        link=one(heading.xpath('ancestor::a[1]'),'Terminlink')
        target=urlsplit(urljoin(URL,link.get('href','')))
        origin=urlsplit(URL)
        if target.scheme!='https' or target.netloc not in {origin.netloc,CMS_ALIAS}:
            raise ValueError('EKI: unerwartetes Terminlinkziel')
        id_match=re.fullmatch(r'/terminkalender/detail/termin/id/(\d+)-[^/]+/?',target.path)
        if not id_match:raise ValueError('EKI: Veranstaltungs-ID fehlt')
        # The official listing sometimes emits its exact CMS tenant hostname.
        # Publish the equivalent official event route; never fetch that alias or
        # accept arbitrary tenant hosts. Event identity and CMS query stay intact.
        url=urlunsplit((origin.scheme,origin.netloc,target.path,target.query,''))
        subtitle_nodes=box.xpath('.//div['+has_class('untertitel')+']')
        subtitle=text(one(subtitle_nodes,'Untertitel')) if subtitle_nodes else ''
        # This is a generic subtitle field, not a location field. Only explicit
        # venue labels seen in the published calendar become a location; e.g.
        # "im Advent" must not be presented as a venue.
        venue=subtitle if re.match(
            r'^(?:Friedhof|Evangelisches Gemeindehaus|Gemeindehaus Kirchstraße|(?:Evang\.|Evangelische) Kirche|'
            r'Martin[- ]Luther[- ]Haus|MLH|Hanne-Landgraf-Haus|Bürgersaal)\b',subtitle,re.I) else ''
        note='Zusatzangabe im Kalender: '+subtitle if subtitle and not venue else ''
        records.append({'recordId':id_match[1],'title':text(heading),'url':url,
            'startDate':day,'endDate':day,'startTime':match[2],'endTime':match[3],
            'location':venue,'organizer':ORGANIZER,'dateText':date_text,
            'timeText':('–'.join(c for c in (match[2],match[3]) if c)+' Uhr') if match[2] else '',
            'scheduleNote':note})
    # A caller reading a single page must not mistake it for a complete feed.
    if not allow_pagination and tree.xpath('//*['+has_class('termine-pager')+']//a[@href]'):
        raise ValueError('EKI: Kalender besitzt neue Paginierung; Import bitte anpassen')
    ids=[(r['recordId'],r['startDate'],r['startTime']) for r in records]
    if len(ids)!=len(set(ids)):raise ValueError('EKI: mehrdeutige doppelte Termine')
    return records


def pagination(raw, url=URL):
    tree=html.fromstring(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
    pagers=tree.xpath('//*['+has_class('termine-pager')+']')
    if not pagers:return None
    pager=one(pagers,'Paginierung')
    links=pager.xpath('.//a[@href]')
    info=pager.xpath('.//*['+has_class('inner-content-pager-info')+']')
    if not links and not info:return None
    label=text(one(info,'Paginierungsumfang'))
    match=re.fullmatch(r'Ergebnisse (\d+)\s*[-–]\s*(\d+) von (\d+)',label)
    if not match:raise ValueError('EKI: unbekannter Paginierungsumfang')
    start,end,total=map(int,match.groups())
    if not 1 <= start <= end <= total <= 10_000:
        raise ValueError('EKI: ungültiger Paginierungsumfang')
    pages={};keys=set();active=[]
    origin=urlsplit(url)
    for link in links:
        target=urlsplit(urljoin(url,link.get('href')))
        # The CMS advertises the same calendar both with and without its final
        # slash. Accept those exact two paths, never prefixes or other routes.
        if ((target.scheme,target.netloc)!=(origin.scheme,origin.netloc)
                or target.path not in {origin.path,origin.path.rstrip('/')}):
            raise ValueError('EKI: unerwartetes Paginierungsziel')
        query=parse_qsl(target.query,keep_blank_values=True)
        if len(query)!=1 or not re.fullmatch(r'pagerPage_[A-Za-z0-9]+',query[0][0]) or not re.fullmatch(r'[1-9]\d*',query[0][1]):
            raise ValueError('EKI: unbekannte Paginierungsparameter')
        key,value=query[0];number=int(value)
        if number>100:raise ValueError('EKI: zu viele Ergebnisseiten')
        keys.add(key)
        pages[number]=urlunsplit((origin.scheme,origin.netloc,origin.path,target.query,''))
        if link.get('aria-current')=='page' or 'pager-active-page' in (link.get('class') or '').split():
            active.append(number)
    if links and (len(keys)!=1 or len(active)!=1):
        raise ValueError('EKI: Paginierungskennung oder aktive Seite ist mehrdeutig')
    if not links and (start!=1 or end!=total):
        raise ValueError('EKI: unvollständige Liste ohne verlinkte Ergebnisseiten')
    return {'start':start,'end':end,'total':total,'pages':pages,
            'key':next(iter(keys),None),'current':active[0] if active else 1}


def fetch_listing_pages(first, fetch, url, parse_page):
    info=pagination(first,url)
    first_records=parse_page(first)
    pages={1:first_records}
    if info:
        size=info['end']
        total=info['total']
        count=(total+size-1)//size
        if info['start']!=1 or info['current']!=1 or count>100:
            raise ValueError('EKI: erste Ergebnisseite oder Seitenzahl ist ungültig')
        pending={}
        def inspect(page,number,records):
            if (not page or page['total']!=total or page['key']!=info['key'] or page['current']!=number
                    or page['start']!=(number-1)*size+1 or page['end']!=min(number*size,total)
                    or len(records)!=page['end']-page['start']+1):
                raise ValueError('EKI: Ergebnisumfang verändert oder Paginierung ignoriert')
            for n,url in page['pages'].items():
                if not 1<=n<=count:raise ValueError('EKI: verlinkte Seite liegt außerhalb des Ergebnisumfangs')
                if n not in pages:pending[n]=url
        inspect(info,1,first_records)
        while pending:
            number=min(pending)
            raw=fetch(pending.pop(number))
            records=parse_page(raw)
            page=pagination(raw,url)
            pages[number]=records
            inspect(page,number,records)
        if set(pages)!=set(range(1,count+1)):
            raise ValueError('EKI: nicht alle Ergebnisseiten sind verlinkt oder erreichbar')
    return [record for number in sorted(pages) for record in pages[number]],len(pages)


def fetch_records(fetch):
    records,pages_fetched=fetch_listing_pages(fetch(URL),fetch,URL,
                                             lambda raw:parse_listing(raw,allow_pagination=True))
    identities=[(r['recordId'],r['startDate'],r['startTime']) for r in records]
    if len(identities)!=len(set(identities)):
        raise ValueError('EKI: doppelte Termine über mehrere Ergebnisseiten')
    metadata={'coverageKind':'html-list','pagesFetched':pages_fetched,'refreshHours':24,
        'coverageStart':min(r['startDate'] for r in records),
        'coverageEnd':max(r['endDate'] for r in records),
        'coverageNote':f'Alle {len(records)} ausdrücklich datierten Einträge von {pages_fetched} veröffentlichten Ergebnisseiten. Ortsangaben aus Übersicht und Termindetails.',
        'extractionMethod':'deterministic-html','llmRequired':False}
    return records,metadata
