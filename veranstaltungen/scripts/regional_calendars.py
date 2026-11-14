"""Public HTML adapters for Remchingen municipality and Kulturhalle.

Only structured event fields are used. Descriptions, prices and images are not
copied; a missing venue or clock is never inferred from the publisher's name.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime
import re
from urllib.parse import urljoin,urlsplit,parse_qs
from lxml import html

REMCHINGEN='https://www.remchingen.de/veranstaltungen/'
KULTURHALLE='https://www.kulturhalle-remchingen.de/events/'

def cls(token):return 'contains(concat(" ",normalize-space(@class)," ")," '+token+' ")'
def doc(raw):return html.fromstring(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
def text(node):return ' '.join(node.text_content().split())
def one(nodes,label):
    if len(nodes)!=1:raise ValueError(label+': erwartetes Datenfeld fehlt oder ist mehrdeutig')
    return nodes[0]
def link(base,href):
    url=urljoin(base,href)
    if urlsplit(url).scheme!='https' or urlsplit(url).netloc!=urlsplit(base).netloc:
        raise ValueError('Unerwartetes Linkziel')
    return url
def day_values(value):
    values=re.findall(r'\b\d{2}\.\d{2}\.\d{4}\b',value)
    if len(values) not in (1,2):raise ValueError('Datum fehlt oder ist mehrdeutig: '+value)
    return [datetime.strptime(v,'%d.%m.%Y').date().isoformat() for v in values]
def clock_values(value):
    values=re.findall(r'\b\d{1,2}:\d{2}\b',value)
    if len(values)>2:raise ValueError('Mehrdeutige Uhrzeit: '+value)
    for v in values:datetime.strptime(v,'%H:%M')
    return values

def remchingen_listing(raw):
    tree=doc(raw);records=[]
    for anchor in tree.xpath('//a[h2 and contains(@href,"-id_")]'):
        box=anchor.getparent()
        def field(icon):
            values=box.xpath('./ul/li//span['+cls(icon)+']')
            return text(one(values,icon).getparent()) if values else ''
        date_text=field('bx-calendar-event');days=day_values(date_text)
        time_text=field('bx-time');times=clock_values(time_text)
        title=text(anchor);url=link(REMCHINGEN,anchor.get('href',''))
        identifier=one(re.findall(r'-id_(\d+)/',url),'Remchinger ID')
        records.append({'recordId':identifier,'title':title,'url':url,
            'startDate':days[0],'endDate':days[-1],'startTime':times[0] if times else None,
            'endTime':times[1] if len(times)>1 else None,'location':field('bx-map'),
            'organizer':field('bx-user'),'dateText':date_text,'timeText':time_text,
            'allDay':'ganztägig' in time_text.lower(),
            'availability':'sold-out' if re.search('ausverkauft',title,re.I) else None})
    if not records:raise ValueError('Remchingen: keine Terminstruktur gefunden')
    pages=[]
    for href in tree.xpath('//a/@href'):
        if not href.startswith('/veranstaltungen/?'):continue
        numbers=parse_qs(urlsplit(href).query).get('seite',[])
        pages.extend(int(n) for n in numbers if n.isdigit())
    return records,max(pages,default=1)

def fetch_remchingen(fetch):
    records,pages=remchingen_listing(fetch(REMCHINGEN))
    if pages>100:raise ValueError('Remchingen: unerwartete Seitenzahl')
    with ThreadPoolExecutor(max_workers=3) as pool:
        rest=list(pool.map(fetch,[REMCHINGEN+'?seite='+str(n) for n in range(2,pages+1)]))
    for raw in rest:records.extend(remchingen_listing(raw)[0])
    identities=[(r['recordId'],r['startDate'],r['startTime']) for r in records]
    if len(identities)!=len(set(identities)):
        raise ValueError('Remchingen: Ergebnisseiten wiederholen dieselben Termine')
    return records,{'coverageKind':'html-list','pagesFetched':pages,'refreshHours':24,
        'coverageNote':'Alle '+str(pages)+' veröffentlichten Ergebnisseiten des Gemeindekalenders, einschließlich öffentlicher Ratstermine.',
        'extractionMethod':'deterministic-html','llmRequired':False}

def kulturhalle_listing(raw):
    tree=doc(raw);records=[]
    for box in tree.xpath('//*[@itemtype="http://schema.org/Event"]'):
        anchor=one(box.xpath('.//a[h2]'),'Kulturhallen-Titel')
        title=text(anchor);url=link(KULTURHALLE,anchor.get('href',''))
        day=one(box.xpath('.//meta[@itemprop="startDate"]/@content'),'Kulturhallen-Datum')
        date.fromisoformat(day)
        time_text=text(one(box.xpath('.//*['+cls('event-time')+']'),'Kulturhallen-Uhrzeit'))
        times=clock_values(time_text)
        records.append({'recordId':urlsplit(url).path.rstrip('/').split('/')[-1],
            'title':title,'url':url,'startDate':day,'endDate':day,
            'startTime':times[0] if times else None,'endTime':times[1] if len(times)>1 else None,
            'location':'','organizer':'Kulturhalle Remchingen','dateText':day,'timeText':time_text,
            'categoryKey':'musik' if '/konzerte/' in url else 'theater' if any(part in url for part in ('/theater/','/comedy-kabarett/')) else None})
    if not records:raise ValueError('Kulturhalle: keine Event-Mikrodaten gefunden')
    if tree.xpath('//a[contains(@href,"start=") or contains(@href,"limitstart=")]'):
        raise ValueError('Kulturhalle: neue Paginierung; Import bitte anpassen')
    return records

def kulturhalle_detail(raw,entry):
    tree=doc(raw)
    # The responsive layout repeats the metadata; repeats must agree.
    days=set(tree.xpath('//li['+cls('meta-date')+']//meta[@itemprop="startDate"]/@content'))
    if days!={entry['startDate']}:raise ValueError('Kulturhalle: Übersicht und Detaildatum widersprechen sich')
    whens={text(n) for n in tree.xpath('//li['+cls('meta-date')+']//*['+cls('termin-date')+']')}
    if len(whens)!=1:raise ValueError('Kulturhalle: Detaildatum fehlt oder ist mehrdeutig')
    when=whens.pop();times=clock_values(when)
    if (times[0] if times else None)!=entry['startTime']:
        raise ValueError('Kulturhalle: Übersicht und Detailbeginn widersprechen sich')
    locations={text(n) for n in tree.xpath('//li['+cls('meta-location')+']//span[@itemprop="name"]') if text(n)}
    if len(locations)>1:raise ValueError('Kulturhalle: Veranstaltungsort mehrdeutig')
    return {**entry,'location':next(iter(locations),'')}

def fetch_kulturhalle(fetch):
    entries=kulturhalle_listing(fetch(KULTURHALLE))
    with ThreadPoolExecutor(max_workers=3) as pool:
        records=list(pool.map(lambda e:kulturhalle_detail(fetch(e['url']),e),entries))
    return records,{'coverageKind':'html-list','pagesFetched':1+len(records),'refreshHours':24,
        'coverageNote':'Alle veröffentlichten Programmtermine; Datum und Uhrzeit mit den Detailseiten abgeglichen, Spielorte aus den Detailseiten.',
        'extractionMethod':'structured-microdata-and-html','llmRequired':False}
