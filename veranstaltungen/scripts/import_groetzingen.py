#!/usr/bin/env python3
"""Import both reviewed Grötzingen PDF editions; retain RSS data and all provenance.

No automatic guesswork on new PDF layouts: changed documents require a new,
reviewed extract before publication. Network runs use the production coordinator.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urljoin, urldefrag
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
SOURCE_PAGE='https://www.karlsruhe.de/stadt-rathaus/stadtteile-ortsverwaltungen/groetzingen/kultur-in-groetzingen'
BERLIN=ZoneInfo('Europe/Berlin')
BEGIN='<script id="event-data" type="application/json">'
END='</script><!-- /event-data -->'
PDF_SOURCE_IDS={'groetzingen-jahresprogramm','groetzingen-kultur-natur'}
RSS_IDENTITIES={
 'karlsruhe-35df20f3f134b600':'neobiota',
 'karlsruhe-5c2d39707b07917f':'naturparadies-rheinauen-naturtreff-grotzingen',
 'karlsruhe-0fe323818688851f':'klavierfest',
 'karlsruhe-83a0e947559c0fbc':'wolfgang-rempfer',
 'karlsruhe-ff8b300436144abe':'wolfgang-rempfer',
}
REVIEWED_RSS_IDENTITIES={
 'karlsruhe-155ac6eca1525f72':{
   'identity':'mit-schwarzen-segeln-segelt-mein-schiff-claus-temps-und-heike-bleckmann',
   'startDate':'2026-11-27','startTime':'20:00','title':'Mit schwarzen Segeln segelt mein Schiff',
   'location':'Rathaus Grötzingen, Bürgersaal','reviewedOn':'2026-11-14'},
}
CATEGORIES={'stadtleben':'Stadtleben','musik':'Musik','literatur':'Literatur & Vorträge','ausstellungen':'Kunst & Ausstellungen','sportveranstaltung':'Sport'}

def normalize(text):
    return re.sub(r'[^a-z0-9]+','-',unicodedata.normalize('NFKD',text).encode('ascii','ignore').decode().lower()).strip('-')

def download(url):
    from http_client import HttpClient
    return HttpClient()(url)

class PdfLinks(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        href=dict(attrs).get('href','')
        if tag=='a' and '.pdf' in href.lower() and 'Groetzingen/' in href:
            self.links.append(urljoin(SOURCE_PAGE,href))

def next_review(today):
    """Fixed calendar half-years, not 180-day approximations."""
    for year in (today.year,today.year+1):
        for month in (1,7):
            candidate=date(year,month,15)
            if candidate>today:return candidate.isoformat()

def check_pdf_review(data,today):
    maintenance=data.get('maintenance',{}).get('groetzingen',{})
    due=maintenance.get('nextReviewOn')
    expired=[s['name'] for s in data.get('sources',[]) if s['id'] in PDF_SOURCE_IDS and s.get('coverageEnd','9999')<today.isoformat()]
    if due and due<=today.isoformat():
        return 'Erinnerung: Neue Ausgaben von Jahresprogramm und Kultur-Natur-Terminen prüfen und nach Sichtkontrolle importieren. '+SOURCE_PAGE
    if expired:return 'Erinnerung: Die importierten Grötzinger Programme sind abgelaufen. Aktuelle Ausgaben prüfen. '+SOURCE_PAGE
    return 'Nächste Prüfung der Grötzinger PDF-Ausgaben: '+(due or next_review(today))+' (danach jeweils 15. Januar und 15. Juli).'

def check_review(data,today):
    messages=[check_pdf_review(data,today)]
    review=data.get('maintenance',{}).get('naturfreunde-groetzingen')
    if review:
        due=review.get('nextReviewOn','')
        if review.get('reviewRequired') or (due and due<=today.isoformat()):
            reason='Neue oder geänderte Ankündigungen erkannt.' if review.get('reviewRequired') else 'Monatliche Prüfung ist fällig.'
            messages.append('Erinnerung: NaturFreunde Grötzingen – '+reason+' Meldungen und Bildaushänge mit OCR/LLM oder manuell prüfen, Extrakt kontrollieren und reviewedOn erst danach aktualisieren. '+review['url'])
        else:
            messages.append('NaturFreunde Grötzingen: nächste monatliche Prüfung am '+due+'. Ein erneuter Abruf verschiebt diesen Termin nicht.')
    return '\n'.join(messages)

def category(record):
    title=record['title'].lower()
    if re.search(r'ausstellung|vernissage|ateliers|malprojekt|zeichenspaß',title):return 'ausstellungen'
    if re.search(r'konzert|musiktage|klavierfest|blues|abendmusik|adventslieder|sopran|passion|haydn|nacht und träume|schwarzen segeln',title):return 'musik'
    if re.search(r'sportfest|turnier|juniorencup|parcour|motorradslalom|badeparty|schwimmen',title):return 'sportveranstaltung'
    if re.search(r'vortrag|neobiota|rheinauen|geschichtsst|geschichsst|invasive arten|unk(en)?[, ]|info veranstaltung|podiumsdiskussion|ökumenisches glaubensgespräch',title):return 'literatur'
    return 'stadtleben'

def provenance(source,record,stamp,priority):
    return {
        'sourceId':source['id'],'sourceName':source['name'],
        'url':source['pdfUrl']+'#page='+str(record['sourcePage']),
        'sourcePage':record['sourcePage'],'edition':source['edition'],'recordId':record['id'],
        'reportedTitle':record['title'],'startDate':record.get('startDate'),'endDate':record.get('endDate'),
        'startTime':record.get('startTime'),'endTime':record.get('endTime'),
        'timeText':record.get('timeText',''),'dateText':record.get('dateText',''),
        'checkedAt':stamp,'priority':priority,
    }

def make_event(source,record,stamp,priority):
    prov=provenance(source,record,stamp,priority)
    start=record.get('startDate')
    if not start:
        return {'id':source['id']+'-'+record['id'],'title':record['title'],'location':record['location'],
                'organizer':record['organizer'],'dateText':record['dateText'],'dateCandidates':record.get('dateCandidates',[]),
                'monthHint':record.get('monthHint'),'sourceId':source['id'],'sourceName':source['name'],'sources':[prov],
                'categoryKey':category(record),'category':CATEGORIES[category(record)]}
    end=record.get('endDate') or start
    if date.fromisoformat(end)<date.fromisoformat(start):raise ValueError('Umgekehrter Zeitraum: '+record['id'])
    for field in ('startTime','endTime'):
        if record.get(field):datetime.strptime(record[field],'%H:%M')
    cat=category(record)
    clock=record.get('startTime')
    start_iso=datetime.fromisoformat(start+'T'+(clock or '00:00')).replace(tzinfo=BERLIN).isoformat()
    end_iso=datetime.fromisoformat(end+'T'+(record.get('endTime') or '00:00')).replace(tzinfo=BERLIN).isoformat() if record.get('endTime') or end!=start else None
    duration=(date.fromisoformat(end)-date.fromisoformat(start)).days
    long_running=record.get('forceLongRunning',False) or duration>=7
    note=record.get('scheduleNote','')
    if not note and long_running:note='Laufzeit laut Programm. Konkrete Öffnungszeiten bitte im Quell-PDF prüfen.'
    if duration and record.get('timeText') and ('Freitag' in record['timeText'] or 'Samstag' in record['timeText']):note+=' Unterschiedliche Zeiten laut Jahresprogramm: '+record['timeText']+'.'
    title=record['title']
    if title in ('Jahreshauptversammlung','Mitgliederversammlung','Vernissage','Ausstellung','Info Veranstaltung') and record.get('organizer'):title+=' · '+record['organizer']
    description=('Veranstalter: '+record['organizer']+'. ' if record.get('organizer') else '')+record.get('notes','')
    return {
        'id':source['id']+'-'+record['id'],'sourceId':source['id'],'sourceName':source['name'],
        'title':title,'url':prov['url'],'icalUrl':None,'city':None,'region':'Grötzingen und Umgebung',
        'categoryKey':cat,'category':CATEGORIES[cat],'description':description.strip(),'location':record['location'],
        'start':start_iso,'end':end_iso,'startDate':start,'endDate':end,
        'startTime':clock,'endTime':record.get('endTime'),'allDay':bool(record.get('allDay')),
        'timeKnown':bool(clock),'longRunning':bool(long_running),'scheduleNote':note,
        'status':'scheduled','dataQuality':'reviewed-pdf','detailCheckedAt':stamp,'organizer':record.get('organizer',''),
        'sources':[prov],'conflicts':[],'_identity':record['identity'],'_priority':priority,
    }

def merge_events(events):
    groups={}
    for event in events:
        identity=event.get('_identity')
        key=identity+'|'+event['startDate'] if identity else 'single:'+event['id']
        if key not in groups:groups[key]=event.copy();continue
        old=groups[key]
        # Prefer current RSS over the September brochure over the July annual plan.
        newer,older=(event,old) if event.get('_priority',0)>old.get('_priority',0) else (old,event)
        winner=newer.copy()
        sources={ (s['sourceId'],s.get('recordId',s['url'])):s for s in old['sources']+event['sources'] }
        winner['sources']=list(sources.values())
        for field in ('startTime','endTime','location','description','scheduleNote','icalUrl'):
            if not winner.get(field) and older.get(field):winner[field]=older[field]
        winner['timeKnown']=bool(winner.get('startTime'))
        if winner.get('startTime'):
            winner['start']=datetime.fromisoformat(winner['startDate']+'T'+winner['startTime']).replace(tzinfo=BERLIN).isoformat()
        if winner.get('endTime'):
            winner['end']=datetime.fromisoformat(winner['endDate']+'T'+winner['endTime']).replace(tzinfo=BERLIN).isoformat()
        conflicts=[]
        for field in ('startTime','endTime','endDate'):
            variants={s.get(field) for s in winner['sources'] if s.get(field)}
            if len(variants)>1:
                conflicts.append({'field':field,'selected':winner.get(field),'alternatives':[
                    {'sourceName':s['sourceName'],'value':s.get(field)} for s in winner['sources'] if s.get(field)]})
        winner['conflicts']=conflicts
        groups[key]=winner
    result=[]
    for event in groups.values():
        event.pop('_identity',None);event.pop('_priority',None)
        result.append(event)
    return sorted(result,key=lambda e:(e['startDate'],e.get('startTime') or '99:99',e['title'].lower()))

def build_dataset(base,snapshots,stamp,today):
    events=[];undated=[];sources=list(base['sources'])
    # Downloading an unchanged PDF is not a new editorial review. A due reminder
    # remains due until the reviewed extracts themselves carry a new review date.
    reviewed_on=min(date.fromisoformat(s['reviewedOn']) for s in snapshots)
    for original in base['events']:
        event=original.copy()
        event['sources']=[{'sourceId':original['sourceId'],'sourceName':original['sourceName'],'url':original['url'],
            'recordId':original['id'],'reportedTitle':original['title'],'startDate':original['startDate'],'endDate':original['endDate'],
            'startTime':original.get('startTime'),'endTime':original.get('endTime'),'checkedAt':original.get('detailCheckedAt'),'priority':3}]
        event['_identity']=RSS_IDENTITIES.get(original['id'])
        reviewed=REVIEWED_RSS_IDENTITIES.get(original['id'])
        if reviewed and all((normalize(original.get(field,''))==normalize(reviewed[field]) if field in ('title','location') else original.get(field)==reviewed[field]) for field in ('startDate','startTime','title','location')):
            event['_identity']=reviewed['identity']
        event['_priority']=3;event['conflicts']=[]
        events.append(event)
    for priority,source in enumerate(snapshots,1):
        for record in source['records']:
            result=make_event(source,record,stamp,priority)
            (events if record.get('startDate') else undated).append(result)
        sources.append({k:v for k,v in source.items() if k not in ('records','filename','reviewNote') }|{
            'url':source['homepage'],'lastSuccessAt':stamp,'lastAttemptAt':stamp,'status':'ok','lastReviewedAt':source['reviewedOn'],
            'recordCount':len(source['records']),'eventCount':sum(bool(r.get('startDate')) for r in source['records']),
            'undatedCount':sum(not r.get('startDate') for r in source['records']),'license':None,
        })
    merged=merge_events(events)
    return {**base,'schemaVersion':2,'generatedAt':stamp,'sources':sources,'events':merged,'undatedEvents':undated,
        'importStats':{'inputRecords':len(events),'deduplicatedRecords':len(events)-len(merged),'eventCount':len(merged),'undatedCount':len(undated)},
        'maintenance':{'groetzingen':{'lastReviewedAt':reviewed_on.isoformat(),'nextReviewOn':next_review(reviewed_on),'reviewMonths':[1,7],'reviewDay':15,
          'note':'Neue Ausgaben des Jahresprogramms und der halbjährlichen Kultur-Natur-Termine prüfen und importieren.'}}}

def save_dataset(data,root):
    path=root/'dist/index.html';page=path.read_text()
    if page.count(BEGIN)!=1 or page.count(END)!=1:raise ValueError('JSON-Platzhalter fehlt')
    start=page.index(BEGIN)+len(BEGIN);end=page.index(END,start)
    inline=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    outputs={root/'dist/events.json':json.dumps(data,ensure_ascii=False,indent=2)+'\n',path:page[:start]+'\n'+inline+'\n'+page[end:]}
    for target,text in outputs.items():
        tmp=target.with_suffix(target.suffix+'.tmp');tmp.write_text(text);tmp.replace(target)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--pdf-dir',type=Path,help='Bereits heruntergeladene PDFs verwenden, ohne Netzabruf')
    parser.add_argument('--check-review',action='store_true',help='Nur halbjährlichen Prüfhinweis ausgeben')
    args=parser.parse_args()
    from update_calendar import main as update_main
    common=['--root',str(args.root),'--source','pdfs']
    if args.check_review:return update_main(common+['--check-review'])
    if not args.pdf_dir:return update_main(common)
    from pdf_checks import load_extracts
    snapshots=load_extracts(args.root)
    for source in snapshots:
        raw=(args.pdf_dir/source['filename']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source['sha256']:
            raise ValueError('PDF geändert: '+source['name']+'. Vor einem Import den Extrakt prüfen. Bisheriger Datenstand bleibt erhalten.')
    # An offline file check is not a new successful request to the live source.
    return update_main(common+['--offline'])

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('Import abgebrochen: '+str(exc),file=sys.stderr);sys.exit(1)
