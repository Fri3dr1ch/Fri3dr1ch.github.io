"""Public HTML adapters for Remchingen municipality and Kulturhalle.

Only structured event fields are used. Descriptions, prices and images are not
copied; a missing venue or clock is never inferred from the publisher's name.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime
import json
import re
from urllib.parse import urljoin,urlsplit,parse_qs,urlencode
from urllib.error import URLError
from uuid import uuid4
from zoneinfo import ZoneInfo
from lxml import html

REMCHINGEN='https://www.remchingen.de/veranstaltungen/'
REMCHINGEN_API=urljoin(REMCHINGEN,'/api/cms/veranstaltungen')
REMCHINGEN_PAGE_SIZE=500
BERLIN=ZoneInfo('Europe/Berlin')
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


def remchingen_api_records(raw):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('Remchingen: Antwort des Veranstaltungskalenders ist ungültig')
    rows = payload['data']
    # A single complete response avoids mixing separately cached pages.
    if str(payload.get('seiten')) != '1' or str(payload.get('anzahl')) != str(len(rows)) or not rows:
        raise RemchingenPaginationError('Remchingen: API liefert keine vollständige Ergebnisseite')

    def local_datetime(value):
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError('Remchingen: Datum ohne Zeitzone')
        return stamp.astimezone(BERLIN)

    def label(value):
        return ' '.join(value.split()) if value else ''

    records = []
    for row in rows:
        identifier = str(row['id'])
        title = label(row['titel'])
        if row['freigabe'] is not True or not identifier.isdigit() or not title:
            raise ValueError('Remchingen: ungültiger Veranstaltungseintrag in der API')
        category_links = {
            link(REMCHINGEN, item['detailURL']) for item in row['rubriken']
            if item.get('taggingpfad') in {'/veranstaltungen/', '/ratstermine/'}
        }
        url = one(sorted(category_links), 'Remchingen: Veranstaltungslink aus der API')
        if not re.fullmatch(r'/(?:veranstaltungen|ratstermine)/[^/]+-id_'+identifier+r'/', urlsplit(url).path):
            raise ValueError('Remchingen: Veranstaltungslink passt nicht zur ID')

        start = local_datetime(row['datum_von'])
        end_value = row.get('datum_bis')
        end = local_datetime(end_value) if end_value else start
        # The CMS encodes an unspecified time as local midnight. An equal
        # end timestamp represents an event with a start time only.
        start_time = start.strftime('%H:%M') if (start.hour, start.minute) != (0, 0) else None
        end_time = end.strftime('%H:%M') if end > start and (end.hour, end.minute) != (0, 0) else None
        days = [start.date(), max(start.date(), end.date())]
        records.append({
            'recordId': identifier, 'title': title, 'url': url,
            'startDate': days[0].isoformat(), 'endDate': days[1].isoformat(),
            'startTime': start_time, 'endTime': end_time,
            'dateText': ' - '.join(day.strftime('%d.%m.%Y') for day in dict.fromkeys(days)),
            'timeText': ' - '.join(clock+' Uhr' for clock in (start_time,end_time) if clock),
            'location': label(row.get('veranstaltungsort')) or label((row.get('brancheneintrag_veranstaltungsort') or {}).get('titel')),
            'organizer': label(row.get('veranstalter')) or label((row.get('brancheneintrag_veranstalter') or {}).get('titel')),
            'allDay': False,
            'availability': 'sold-out' if re.search('ausverkauft',title,re.I) else None,
        })
    if len({row['recordId'] for row in records}) != len(records):
        raise RemchingenPaginationError('Remchingen: API liefert doppelte Veranstaltungs-IDs')
    return records

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

class RemchingenPaginationError(ValueError):
    """The source's separately cached result pages do not form one complete list."""


def remchingen_page_state(raw):
    # Nuxt embeds the public result count and the request's page/size in its
    # reference table. Read only those scalars, never execute source scripts.
    scripts=doc(raw).xpath('//script[@id="__NUXT_DATA__"]/text()')
    if not scripts:return None  # Older HTML exports have no hydration metadata.
    try:
        table=json.loads(one(scripts,'Remchingen: Seitenmetadaten'))
        def resolve(index):
            if type(index) is not int or not 0<=index<len(table):raise ValueError()
            return table[index]
        result=resolve(one([v['veranstaltungen'] for v in table if isinstance(v,dict)
            and 'veranstaltungen' in v and 'filterVeranstaltungen' in v],'Remchingen: Ergebniszahl'))
        request=one([v for v in table if isinstance(v,dict) and 'endpoint' in v
            and resolve(v['endpoint'])=='/api/cms/veranstaltungen'],'Remchingen: Seitenabruf')
        params=resolve(request['params'])
        state={key:resolve(value) for key,value in {
            'total':result['anzahl'],'pages':result['seiten'],
            'page':params['seite'],'size':params['pro_seite']}.items()}
        if any(type(v) not in (int,str) or not str(v).isdigit() for v in state.values()):raise ValueError()
        state={k:int(v) for k,v in state.items()}
        if min(state.values())<1 or state['pages']!=(state['total']+state['size']-1)//state['size']:raise ValueError()
        return state
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        raise ValueError('Remchingen: Ergebniszahl oder Seitenmetadaten nicht eindeutig') from exc


def remchingen_snapshot(fetch,refresh_token=None):
    def page_url(number):
        if refresh_token is None:return REMCHINGEN if number==1 else REMCHINGEN+'?seite='+str(number)
        return REMCHINGEN+'?'+urlencode({'seite':number,'_calendar_refresh':refresh_token})
    raw=fetch(page_url(1));records,pages=remchingen_listing(raw)
    state=remchingen_page_state(raw)
    if state:pages=state['pages']
    if pages>100:raise ValueError('Remchingen: unerwartete Seitenzahl')
    def checked_page(raw,number):
        rows,_=remchingen_listing(raw);current=remchingen_page_state(raw)
        if state:
            if not current or current!={**state,'page':number}:
                raise RemchingenPaginationError('Remchingen: Ergebniszahl oder Seitennummer während des Abrufs geändert')
            expected=min(state['size'],state['total']-(number-1)*state['size'])
            if len(rows)!=expected:
                raise RemchingenPaginationError('Remchingen: unvollständige Ergebnisseite')
        return rows
    records=checked_page(raw,1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rest=list(pool.map(fetch,[page_url(n) for n in range(2,pages+1)]))
    for number,raw in enumerate(rest,2):records.extend(checked_page(raw,number))
    identities=[(r['recordId'],r['startDate'],r['startTime']) for r in records]
    if len(identities)!=len(set(identities)):
        raise RemchingenPaginationError('Remchingen: Ergebnisseiten wiederholen dieselben Termine')
    if state and len(records)!=state['total']:
        raise RemchingenPaginationError('Remchingen: veröffentlichte Ergebniszahl nicht vollständig abgerufen')
    return records,{'coverageKind':'html-list','pagesFetched':pages,'refreshHours':24,
        'coverageNote':'Alle '+str(pages)+' veröffentlichten Ergebnisseiten des Gemeindekalenders, einschließlich öffentlicher Ratstermine.',
        'extractionMethod':'deterministic-html','llmRequired':False}


def fetch_remchingen_html(fetch):
    try:return remchingen_snapshot(fetch)
    except RemchingenPaginationError:
        # An event expiring can shift page boundaries while the CDN still
        # serves older neighbouring pages. Retry the WHOLE list once with a
        # shared fresh cache key; never repair it by dropping duplicate rows.
        return remchingen_snapshot(fetch,uuid4().hex)


def fetch_remchingen(fetch):
    # The municipality's own public calendar calls this CMS endpoint. Keep
    # its publication and category filters and request the entire list.
    params = {
        'aktiv': 'true', 'sprache': 'de', 'exclude_rubriken[]': 75,
        'order[]': '`datum_von` ASC', 'seite': 1, 'pro_seite': REMCHINGEN_PAGE_SIZE,
    }
    try:
        records = remchingen_api_records(fetch(REMCHINGEN_API+'?'+urlencode(params)))
    except (ValueError, KeyError, TypeError, AttributeError, URLError, TimeoutError, ConnectionError) as api_error:
        print('Remchingen: CMS-Abruf fehlgeschlagen, HTML-Ersatzabruf:', api_error, flush=True)
        return fetch_remchingen_html(fetch)
    return records, {
        'coverageKind': 'public-json', 'pagesFetched': 1, 'refreshHours': 24,
        'coverageNote': 'Vollständige Ergebnisliste der Gemeinde, einschließlich öffentlicher Ratstermine.',
        'extractionMethod': 'structured-json', 'llmRequired': False,
    }

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
