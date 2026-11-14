#!/usr/bin/env python3
"""Refresh regional calendars; retain the last valid source on failure."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin,urlsplit,parse_qs
from zoneinfo import ZoneInfo
from lxml import html

from import_groetzingen import ROOT,build_dataset,save_dataset,check_review,category,CATEGORIES
from calendar_sources import combine_local_sources,saved_sources

BERLIN=ZoneInfo('Europe/Berlin')
SOURCES={
 'durlach':{'id':'durlach','name':'Durlacher.de · Durlacher Kalender','region':'Durlach und Umgebung',
            'url':'https://www.durlacher.de/durlacher-kalender'},
 'weingarten':{'id':'weingarten','name':'Gemeinde Weingarten (Baden)','region':'Weingarten (Baden)',
            'url':'https://www.weingarten-baden.de/freizeit-tourismus/veranstaltungen/seite-1/suche-none'},
 'pfinztal':{'id':'pfinztal','name':'Pfinztal · Gemeindetermine','region':'Pfinztal',
            'url':'https://pfinztal.de/aktuelles-blog/termine-und-veranstaltungen/'},
 'pfinztal-vereine':{'id':'pfinztal-vereine','name':'Pfinztal · Vereinskalender','region':'Pfinztal',
            'url':'https://pfinztal.de/aktuelles-blog/termine-und-veranstaltungen/'},
 'remchingen':{'id':'remchingen','name':'Gemeinde Remchingen','region':'Remchingen',
            'url':'https://www.remchingen.de/veranstaltungen/'},
 'kulturhalle-remchingen':{'id':'kulturhalle-remchingen','name':'Kulturhalle Remchingen','region':'Remchingen',
            'url':'https://www.kulturhalle-remchingen.de/events/'},
 'eki-groetzingen':{'id':'eki-groetzingen','name':'Evangelische Kirche Grötzingen','region':'Grötzingen',
            'url':'https://eki-groetzingen.de/terminkalender/'},
 'pfinztal-haus-begegnungen':{'id':'pfinztal-haus-begegnungen','name':'Pfinztal · Haus der Begegnungen','region':'Pfinztal-Söllingen',
            'url':'https://pfinztal.de/familie-soziales/familien/haus-der-begegnungen/'},
 'naturfreunde-berghausen':{'id':'naturfreunde-berghausen','name':'NaturFreunde Berghausen','region':'Pfinztal-Berghausen',
            'url':'https://www.naturfreunde-berghausen.de/'},
 'naturfreunde-soellingen':{'id':'naturfreunde-soellingen','name':'NaturFreunde Söllingen','region':'Pfinztal-Söllingen',
            'url':'https://www.naturfreunde-soellingen.de/kalender/'},
 'naturfreunde-groetzingen':{'id':'naturfreunde-groetzingen','name':'NaturFreunde Grötzingen','region':'Grötzingen',
            'url':'https://www.naturfreunde-groetzingen.de/'},
}

def cls(token):return 'contains(concat(" ",normalize-space(@class)," ")," '+token+' ")'
def text(node):return ' '.join(node.text_content().split())
def document(raw):return html.fromstring(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
def only(nodes,label):
    if len(nodes)!=1:raise ValueError(label+': erwartete Struktur fehlt oder ist mehrdeutig')
    return nodes[0]
def dates(value):
    found=re.findall(r'\b\d{2}\.\d{2}\.\d{4}\b',value)
    if not found or len(found)>2:raise ValueError('Datum nicht eindeutig: '+value)
    return [datetime.strptime(d,'%d.%m.%Y').date().isoformat() for d in found]
def clocks(value):
    found=re.findall(r'\b(\d{1,2})[:.](\d{2})\b(?!\.\d)',value)
    result=[]
    for h,m in found:
        value=f'{int(h):02d}:{int(m):02d}';datetime.strptime(value,'%H:%M');result.append(value)
    if len(result)>2:raise ValueError('Uhrzeit nicht eindeutig: '+value)
    return result
def source_url(base,href):
    url=urljoin(base,href)
    if urlsplit(url).scheme!='https' or urlsplit(url).netloc!=urlsplit(base).netloc:
        raise ValueError('Unerwartetes Linkziel')
    return url
from http_client import HttpClient
_HTTP=HttpClient()
def download(url):
    return _HTTP(url)

def event(source,record,stamp):
    start=record['startDate'];end=record.get('endDate') or start
    begin=record.get('startTime');finish=record.get('endTime')
    if begin and finish and end==start and finish<begin and not record.get('endIso'):
        end=(date.fromisoformat(end)+timedelta(days=1)).isoformat()
    if end<start:raise ValueError('Umgekehrter Zeitraum')
    record_id=record['recordId'];identity=f"{source['id']}-{record_id}-{start}-{(begin or 'unknown').replace(':','')}"
    cat=record.get('categoryKey') or category(record)
    duration=(date.fromisoformat(end)-date.fromisoformat(start)).days
    prov={k:record.get(k) for k in ('url','recordId','title','startDate','endDate','startTime','endTime','dateText','timeText')}
    prov.update({'sourceId':source['id'],'sourceName':source['name'],'reportedTitle':record['title'],
                 'checkedAt':stamp,'endDate':end,'priority':2})
    for field in ('sourcePage','edition','district'):
        if record.get(field):prov[field]=record[field]
    note=record.get('scheduleNote','')
    if duration:note=(note+' Zeitraum laut Quelle; konkrete Öffnungszeiten bitte auf der Originalseite prüfen.').strip()
    return {'id':identity,'sourceId':source['id'],'sourceName':source['name'],
        'title':record['title'],'url':record['url'],'icalUrl':record.get('icalUrl'),'region':record.get('region') or source['region'],'city':None,
        'categoryKey':cat,'category':CATEGORIES.get(cat,{'theater':'Theater & Bühne'}.get(cat,'Stadtleben')),'description':record.get('description') or (('Veranstalter: '+record['organizer']) if record.get('organizer') else ''),
        'organizer':record.get('organizer',''),'location':record.get('location',''),
        'start':datetime.fromisoformat(start+'T'+(begin or '00:00')).replace(tzinfo=BERLIN).isoformat(),
        'end':record.get('endIso') or (datetime.fromisoformat(end+'T'+(finish or '00:00')).replace(tzinfo=BERLIN).isoformat() if finish or end!=start else None),
        'startDate':start,'endDate':end,'startTime':begin,'endTime':finish,'timeKnown':bool(begin),
        'allDay':record.get('allDay',False),'longRunning':record.get('longRunning',duration>=7),'scheduleNote':note,
        'openingSchedule':record.get('openingSchedule'),'openingOccurrences':record.get('openingOccurrences'),
        'status':record.get('status') or ('cancelled' if re.search(r'fällt aus|abgesagt|entfällt',record['title'],re.I) else 'scheduled'),
        'dataQuality':record.get('dataQuality') or ('pdf-table' if source['id']=='pfinztal-vereine' else 'html-calendar'),
        'availability':record.get('availability'),'detailCheckedAt':stamp,'sources':[prov],'conflicts':[]}

def undated_event(source,record,stamp):
    cat=record.get('categoryKey') or category(record)
    return {'id':source['id']+'-'+record['recordId'],'title':record['title'],
        'location':record.get('location',''),'region':record.get('region') or source['region'],
        'organizer':record.get('organizer',''),'dateText':record.get('dateText','Datum unklar'),
        'dateCandidates':record.get('dateCandidates',[]),
        'uncertaintyReason':record.get('uncertaintyReason') or record.get('scheduleNote') or record.get('notes',''),
        'sourceId':source['id'],'sourceName':source['name'],'categoryKey':cat,'category':CATEGORIES[cat],
        'sources':[{'sourceId':source['id'],'sourceName':source['name'],'url':record['url'],
                    'recordId':record['recordId'],'sourcePage':record.get('sourcePage'),
                    'edition':source.get('edition'),'checkedAt':stamp}]}

def snapshot_from_records(source,records,stamp,extra=None):
    extra=extra or {};events=[];undated=[]
    for record in records:
        (events if record.get('startDate') else undated).append(
            event(source,record,stamp) if record.get('startDate') else undated_event({**source,**extra},record,stamp))
    if not events and not undated and not extra.get('allowEmpty'):
        raise ValueError('Keine Termine erkannt; bisherigen Stand beibehalten')
    unique={}
    for e in events:
        if e['id'] in unique and e!=unique[e['id']]:raise ValueError('Mehrdeutige Veranstaltungs-ID')
        unique[e['id']]=e
    events=list(unique.values())
    metadata={**source,'status':'ok','lastAttemptAt':stamp,'lastSuccessAt':stamp,'refreshHours':24,
        'recordCount':len(events)+len(undated),'eventCount':len(events),'undatedCount':len(undated),
        'coverageKind':'html-list','license':None,**extra}
    if events:
        metadata.setdefault('coverageStart',min(e['startDate'] for e in events))
        metadata.setdefault('coverageEnd',max(e['endDate'] for e in events))
    if source['id']=='pfinztal-vereine':metadata.pop('refreshHours',None)
    return {'source':metadata,'events':events,'undatedEvents':undated}

def durlach_entries(raw):
    tree=document(raw);entries=[]
    container=only(tree.xpath('//div['+cls('nopaginator')+']'),'Durlacher Terminliste')
    for node in container.xpath('./p'):
        links=node.xpath('./a[contains(@href,"ereignis")]')
        if not links:continue
        link=only(links,'Durlacher Terminlink');url=source_url(SOURCES['durlach']['url'],link.get('href'))
        query=parse_qs(urlsplit(url).query);record_id=only(query.get('tx_events2_events[event]',[]),'Durlacher Veranstaltungs-ID')
        value=' '.join(node.xpath('./text()')).strip();days=dates(value)
        entries.append({'url':url,'recordId':record_id,'title':text(link),'startDate':days[0],'endDate':days[-1],'dateText':value})
    if not entries:raise ValueError('Keine Durlacher Termine erkannt; bisherigen Stand beibehalten')
    return entries

def durlach_detail(raw,entry):
    tree=document(raw);box=only(tree.xpath('//div['+cls('tx-events2')+'][h2 and dl]'),'Durlacher Detailseite')
    title=text(only(box.xpath('./h2[1]'),'Durlacher Titel'))
    fields={text(dt):text(only(dt.xpath('following-sibling::dd[1]'),'Durlacher Datenfeld')) for dt in box.xpath('./dl/dt')}
    when=fields.get('Datum / Uhrzeit','');days=dates(when)
    if days not in ([entry['startDate']],[entry['startDate'],entry['endDate']]):
        raise ValueError('Durlacher Übersicht und Detaildatum widersprechen sich')
    times=clocks(re.sub(r'\b\d{2}\.\d{2}\.\d{4}\b','',when))
    return {**entry,'title':title,'location':fields.get('Veranstaltungsort',''),'organizer':fields.get('Veranstalter',''),
            'startTime':times[0] if times else None,'endTime':times[1] if len(times)>1 else None,
            'timeText':when,'allDay':'ganztägig' in when.lower()}

def weingarten_entries(raw):
    tree=document(raw);entries=[]
    for node in tree.xpath('//*['+cls('hwveranstaltung__record')+']'):
        def field(name):
            values=node.xpath('.//*['+cls('hw_record__'+name)+']/*['+cls('hw_record__value__text')+']')
            return text(only(values,name)) if values else ''
        title=text(only(node.xpath('.//h3['+cls('hw_record__title')+']'),'Weingartener Titel'))
        link=only(node.xpath('.//a['+cls('hw_record__more__show')+']'),'Weingartener Terminlink')
        url=source_url(SOURCES['weingarten']['url'],link.get('href'));record_id=only(re.findall(r'/veranstaltungen/(\d+)/',url),'Weingartener Veranstaltungs-ID')
        date_text=field('date');days=dates(date_text);time_text=field('time');times=clocks(time_text)
        # A closing time by itself is not a known start time.
        end_only=bool(re.match(r'^bis\b',time_text,re.I))
        entries.append({'recordId':record_id,'title':title,'url':url,'dateText':date_text,'timeText':time_text,
            'startDate':days[0],'endDate':days[-1], 'startTime':times[0] if times and not end_only else None,
            'endTime':times[-1] if len(times)>1 or (times and end_only) else None,
            'location':field('simpleLocation'),'organizer':field('organizer'),'allDay':'ganztägig' in time_text.lower()})
    if not entries:raise ValueError('Keine Weingartener Termine erkannt; bisherigen Stand beibehalten')
    return entries

def fetch_source(source_id,stamp,fetch=download,root=ROOT):
    source=SOURCES[source_id]
    if source_id not in ('durlach','weingarten'):
        if source_id.startswith('naturfreunde-'):
            from naturfreunde import fetch_berghausen,fetch_soellingen,fetch_groetzingen_reviewed
            loader={'naturfreunde-berghausen':fetch_berghausen,'naturfreunde-soellingen':fetch_soellingen,
                    'naturfreunde-groetzingen':fetch_groetzingen_reviewed}[source_id]
            records,extra=(loader(fetch,review_file=root/'data/reviewed/naturfreunde-groetzingen.json')
                           if source_id=='naturfreunde-groetzingen' else loader(fetch))
        elif source_id=='remchingen':
            from regional_calendars import fetch_remchingen
            records,extra=fetch_remchingen(fetch)
        elif source_id=='kulturhalle-remchingen':
            from regional_calendars import fetch_kulturhalle
            records,extra=fetch_kulturhalle(fetch)
        elif source_id=='eki-groetzingen':
            from eki_calendar import fetch_records
            records,extra=fetch_records(fetch)
        elif source_id=='pfinztal-vereine':
            from pfinztal_pdf import fetch_records
            records,extra=fetch_records(fetch)
        else:
            from pfinztal_online import fetch_records
            records,extra=fetch_records(fetch,source_id=source_id)
        return snapshot_from_records(source,records,stamp,extra)
    raw=fetch(source['url']);pages=1
    if source_id=='durlach':
        entries=durlach_entries(raw)
        with ThreadPoolExecutor(max_workers=3) as pool:
            records=list(pool.map(lambda e:durlach_detail(fetch(e['url']),e),entries))
        scope='Veröffentlichte Vorschau der Durlacher Kalenderübersicht; kein vollständiger Jahreskalender.'
    else:
        tree=document(raw)
        page_numbers=[int(n) for href in tree.xpath('//a/@href') for n in re.findall(r'/veranstaltungen/seite-(\d+)/suche-none(?:$|[?#])',href)]
        pages=max(page_numbers,default=1)
        if pages>100:raise ValueError('Unerwartet viele Ergebnisseiten; Import bitte prüfen')
        urls=[source['url'].replace('seite-1/','seite-'+str(n)+'/') for n in range(2,pages+1)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            rest=list(pool.map(fetch,urls))
        records=[e for page in [raw,*rest] for e in weingarten_entries(page)]
        scope='Alle '+str(pages)+' zum Abrufzeitpunkt veröffentlichten Ergebnisseiten des Gemeindekalenders.'
    events=[event(source,r,stamp) for r in records]
    unique={}
    for e in events:
        if e['id'] in unique and e!=unique[e['id']]:raise ValueError('Mehrdeutige Veranstaltungs-ID')
        unique[e['id']]=e
    events=list(unique.values())
    metadata={**source,'status':'ok','lastAttemptAt':stamp,'lastSuccessAt':stamp,'refreshHours':24,
        'recordCount':len(events),'eventCount':len(events),'pagesFetched':pages,'coverageKind':'html-list',
        'coverageStart':min(e['startDate'] for e in events),'coverageEnd':max(e['endDate'] for e in events),
        'coverageNote':scope,'license':None}
    return {'source':metadata,'events':events}

def refresh(source_id,stamp,previous=None,fetch=download,root=ROOT):
    try:return fetch_source(source_id,stamp,fetch,root),None
    except Exception as exc:
        message=type(exc).__name__+': '+str(exc)
        if previous is None:return None,message
        retained=json.loads(json.dumps(previous))
        retained['source'].update({'status':'error','lastAttemptAt':stamp,'lastError':message})
        return retained,message

def build_current(root,stamp):
    base=json.loads((root/'data/karlsruhe-rss.json').read_text())
    from pdf_checks import load_extracts
    pdfs=load_extracts(root)
    # Reuse the actual last PDF import timestamp, not a fictitious fresh review.
    current=json.loads((root/'dist/events.json').read_text())
    pdf_stamp=next((s['lastSuccessAt'] for s in current['sources'] if s['id']=='groetzingen-jahresprogramm'),stamp)
    data=build_dataset(base,pdfs,pdf_stamp,date.fromisoformat(stamp[:10]))
    data['generatedAt']=stamp
    data=combine_local_sources(data,saved_sources(root))
    checks_path=root/'data/source-checks.json'
    if checks_path.exists():
        checks=json.loads(checks_path.read_text())
        for source in data['sources']:
            if source['id'] in checks:source.update(checks[source['id']])
    return data

def main():
    # Preserve the familiar command while sharing the production coordinator.
    from update_calendar import main as update_main
    return update_main()


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('Import abgebrochen: '+str(exc),file=sys.stderr);sys.exit(1)
