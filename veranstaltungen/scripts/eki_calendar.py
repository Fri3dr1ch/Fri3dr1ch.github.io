"""Read explicitly dated events from the public EKI Grötzingen HTML calendar.

The current page contains the complete published upcoming list. Recurrences are
never expanded by this importer: each imported record has an actual source row.
"""
from datetime import datetime
import re
from urllib.parse import urljoin,urlsplit

from lxml import html

URL='https://eki-groetzingen.de/terminkalender/'
ORGANIZER='Evangelische Kirche in Grötzingen'

def has_class(token):
    return 'contains(concat(" ",normalize-space(@class)," ")," '+token+' ")'

def text(node):
    return ' '.join(node.text_content().split())

def one(nodes,label):
    if len(nodes)!=1:raise ValueError('EKI '+label+': erwartete Kalenderstruktur fehlt oder ist mehrdeutig')
    return nodes[0]

def parse_listing(raw):
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
        url=urljoin(URL,link.get('href',''))
        if urlsplit(url).scheme!='https' or urlsplit(url).netloc!=urlsplit(URL).netloc:
            raise ValueError('EKI: unerwartetes Terminlinkziel')
        id_match=re.search(r'/terminkalender/detail/termin/id/(\d+)-',url)
        if not id_match:raise ValueError('EKI: Veranstaltungs-ID fehlt')
        subtitle_nodes=box.xpath('.//div['+has_class('untertitel')+']')
        subtitle=text(one(subtitle_nodes,'Untertitel')) if subtitle_nodes else ''
        # This is a generic subtitle field, not a location field. Only explicit
        # venue labels seen in the published calendar become a location; e.g.
        # "im Advent" must not be presented as a venue.
        venue=subtitle if re.match(r'^(?:Friedhof|Evangelisches Gemeindehaus|Martin-Luther-Haus|Hanne-Landgraf-Haus|Bürgersaal)\b',subtitle) else ''
        note='Zusatzangabe im Kalender: '+subtitle if subtitle and not venue else ''
        records.append({'recordId':id_match[1],'title':text(heading),'url':url,
            'startDate':day,'endDate':day,'startTime':match[2],'endTime':match[3],
            'location':venue,'organizer':ORGANIZER,'dateText':date_text,
            'timeText':('–'.join(c for c in (match[2],match[3]) if c)+' Uhr') if match[2] else '',
            'scheduleNote':note})
    # Empty pager is expected today. A future paginated version needs an adapter
    # update rather than silently claiming that its first page is complete.
    if tree.xpath('//*['+has_class('termine-pager')+']//a[@href]'):
        raise ValueError('EKI: Kalender besitzt neue Paginierung; Import bitte anpassen')
    ids=[(r['recordId'],r['startDate'],r['startTime']) for r in records]
    if len(ids)!=len(set(ids)):raise ValueError('EKI: mehrdeutige doppelte Termine')
    return records

def fetch_records(fetch):
    records=parse_listing(fetch(URL))
    metadata={'coverageKind':'html-list','pagesFetched':1,'refreshHours':24,
        'coverageStart':min(r['startDate'] for r in records),
        'coverageEnd':max(r['endDate'] for r in records),
        'coverageNote':'Alle ausdrücklich datierten Einträge der veröffentlichten Terminliste. Keine selbst erzeugten Wiederholungen; Orte nur soweit ausdrücklich erkennbar.',
        'extractionMethod':'deterministic-html','llmRequired':False}
    return records,metadata
