"""Read Pfinztal's public EventON calendar endpoint, using its advertised contract.

The POST below is the same read-only calendar-initialization request used by the
public page. It creates no event or subscription. Only explicit returned event
occurrences are imported; private-looking plugin metadata is discarded.
"""
from datetime import date, datetime
from html import unescape
import json
import re
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import html

BERLIN=ZoneInfo('Europe/Berlin')
URLS={
    'pfinztal':'https://pfinztal.de/aktuelles-blog/termine-und-veranstaltungen/',
    'pfinztal-haus-begegnungen':'https://pfinztal.de/familie-soziales/familien/haus-der-begegnungen/',
}

def token(name):
    return 'contains(concat(" ",normalize-space(@class)," ")," '+name+' ")'

def clean(node):
    return ' '.join(node.text_content().split())

def one(nodes,label):
    if len(nodes)!=1:raise ValueError(label+': erwartete eindeutige Struktur fehlt')
    return nodes[0]

def text_document(raw):
    return html.fromstring(raw.decode('utf-8') if isinstance(raw,bytes) else raw)

def public_url(value):
    url=urljoin('https://pfinztal.de/',value)
    split=urlsplit(url)
    if split.scheme!='https' or split.netloc!='pfinztal.de':
        raise ValueError('Unerwartetes Pfinztaler Linkziel')
    return url

def request_data(raw,today=None,months=6):
    """Discover endpoint and fresh public nonce from the source page."""
    if today is None:today=datetime.now(BERLIN).date()
    if not 1<=months<=12:raise ValueError('Ungültiges Importfenster')
    source=raw.decode('utf-8') if isinstance(raw,bytes) else raw
    tree=text_document(source)
    config=one(tree.xpath('//*['+token('evo_cal_data')+']'),'EventON-Kalenderkonfiguration')
    sc=json.loads(config.get('data-sc',''))
    calendar=one(config.xpath('ancestor::*['+token('ajde_evcal_calendar')+']'),'EventON-Kalender')
    calendar_id=calendar.get('id')
    if not calendar_id or not isinstance(sc,dict):raise ValueError('EventON-Konfiguration unvollständig')
    match=re.search(r'var\s+evo_general_params\s*=\s*(\{.*?\});',source,re.S)
    if not match:raise ValueError('Öffentliche EventON-Abrufparameter fehlen')
    parameters=json.loads(match.group(1))
    endpoint=parameters.get('evo_ajax_url','')
    if parameters.get('ajax_method')!='endpoint' or '%%endpoint%%' not in endpoint:
        raise ValueError('EventON-Abrufvertrag geändert')
    endpoint=public_url(endpoint.replace('%%endpoint%%','eventon_init_load'))
    nonce=parameters.get('n')
    if not isinstance(nonce,str) or not nonce:raise ValueError('EventON-Abrufparameter unvollständig')
    start=datetime(today.year,today.month,1,tzinfo=BERLIN)
    end_month=today.year*12+today.month-1+months
    end=datetime(end_month//12,end_month%12+1,1,tzinfo=BERLIN)
    # This is the same calendar date-range/filter data as the page's navigation.
    # Convert the HDB weekly display to a monthly range; retain its category.
    sc.update({'calendar_type':'default','number_of_months':str(months),
               'fixed_month':str(today.month),'fixed_year':str(today.year),
               'focus_start_date_range':str(int(start.timestamp())),
               'focus_end_date_range':str(int(end.timestamp())-1)})
    fields={'nonce':nonce}
    fields.update({'cals['+calendar_id+'][sc]['+key+']':str(value) for key,value in sc.items()})
    window={'start':start.date().isoformat(),
            'end':datetime.fromtimestamp(int(end.timestamp())-1,BERLIN).date().isoformat(),
            'unixStart':int(start.timestamp()),'unixEnd':int(end.timestamp())-1}
    return endpoint,fields,calendar_id,window

def post_calendar(url,fields):
    from http_client import HttpClient
    return HttpClient().request(url,data=urlencode(fields).encode(),headers={
        'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}).body

def parse_response(raw,calendar_id,window):
    payload=json.loads(raw)
    if not isinstance(payload,dict):raise ValueError('EventON-Antwort ist kein Kalenderobjekt')
    data=payload.get('cals',{}).get(calendar_id)
    if not isinstance(data,dict) or not isinstance(data.get('html'),str) or not isinstance(data.get('json'),list):
        raise ValueError('EventON-Antwortstruktur geändert')
    sc=data.get('sc',{})
    if (str(sc.get('focus_start_date_range'))!=str(window['unixStart']) or
        str(sc.get('focus_end_date_range'))!=str(window['unixEnd'])):
        raise ValueError('EventON hat ein abweichendes Zeitfenster zurückgegeben')
    tree=text_document('<main>'+data['html']+'</main>')
    nodes=tree.xpath('//*['+token('eventon_list_event')+' and @data-event_id and @data-time]')
    if len(nodes)!=len(data['json']):raise ValueError('EventON-Terminzahlen widersprechen sich')
    if not nodes:
        if not tree.xpath('//*['+token('no_events')+']'):
            raise ValueError('Leere EventON-Antwort ohne bestätigten Keine-Termine-Status')
        return []
    records=[]
    for node in nodes:
        pair=re.fullmatch(r'(\d+)-(\d+)',node.get('data-time',''))
        if not pair:raise ValueError('EventON-Zeitspanne fehlt')
        # data-time/unix_start are real UTC timestamps. event_start_unix is a
        # different, wall-clock-shifted EventON field and must not be used.
        begin,finish=[datetime.fromtimestamp(int(v),BERLIN) for v in pair.groups()]
        if finish<begin:raise ValueError('Umgekehrte EventON-Zeitspanne')
        if int(pair[2])<window['unixStart'] or int(pair[1])>window['unixEnd']:
            raise ValueError('EventON-Termin außerhalb des angefragten Fensters')
        title=clean(one(node.xpath('.//*['+token('evcal_event_title')+']'),'EventON-Titel'))
        link=one(node.xpath('.//*[@itemprop="url"]/@href'),'EventON-Original-Link')
        time_nodes=node.xpath('.//*['+token('evcal_time')+']')
        time_text=clean(time_nodes[0]) if time_nodes else ''
        time_no_offset=re.sub(r'\(GMT[^)]*\)','',time_text)
        times=re.findall(r'\b\d{1,2}:\d{2}\b',time_no_offset)
        all_day=bool(re.search(r'ganztägig|all day',time_text,re.I))
        location=node.xpath('.//*['+token('evcal_location')+']')
        status=node.xpath('.//meta[@itemprop="eventStatus"]/@content')
        records.append({'recordId':node.get('data-event_id')+'-'+node.get('data-ri','0'),
            'title':unescape(title),'url':public_url(link),
            'startDate':begin.date().isoformat(),'endDate':finish.date().isoformat(),
            'startTime':begin.strftime('%H:%M') if times and not all_day else None,
            'endTime':finish.strftime('%H:%M') if len(times)>1 and not all_day else None,
            'allDay':all_day,'location':clean(location[0]) if location else '',
            'organizer':'','dateText':begin.strftime('%d.%m.%Y')+(' bis '+finish.strftime('%d.%m.%Y') if begin.date()!=finish.date() else ''),
            'timeText':time_text,'status':'cancelled' if any('EventCancelled' in s for s in status) else 'scheduled'})
    return records

def fetch_records(fetch,source_id='pfinztal',post=post_calendar,today=None,months=6):
    url=URLS[source_id]
    endpoint,fields,calendar_id,window=request_data(fetch(url),today,months)
    expected='164' if source_id=='pfinztal-haus-begegnungen' else '46'
    if fields.get('cals['+calendar_id+'][sc][event_type]')!=expected:
        raise ValueError('Pfinztaler Quellenfilter hat sich geändert')
    records=parse_response(post(endpoint,fields),calendar_id,window)
    return records,{'coverageKind':'eventon-json','coverageStart':window['start'],'coverageEnd':window['end'],
        'coverageNote':('Öffentlicher Onlinekalender; abgefragtes Fenster '+window['start']+' bis '+window['end']+'. '+
                        ('Keine veröffentlichten Termine im abgerufenen Fenster.' if not records else
                         'Nur ausdrücklich von der Quelle gelieferte Vorkommen; keine selbst berechneten Wiederholungen.')),
        'allowEmpty':True,'pagesFetched':1,'refreshHours':24}
