"""Read Walzbachtal's public calendar request, including all explicit occurrences."""
from copy import deepcopy
from datetime import date,datetime
import json
import math
import re
from urllib.parse import urlencode,urljoin,urlsplit
from zoneinfo import ZoneInfo

from lxml import html

URL='https://www.walzbachtal.de/veranstaltung'
ENDPOINT='https://www.walzbachtal.de/api/event/list/event-appointment'
MONTHS=['januar','februar','märz','april','mai','juni','juli','august','september','oktober','november','dezember']


def text(node):return ' '.join(node.text_content().split())


def one(nodes,label):
    if len(nodes)!=1:raise ValueError('Walzbachtal: '+label+' fehlt oder ist mehrdeutig')
    return nodes[0]


def request_config(raw,today):
    tree=html.fromstring(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
    widget=one(tree.xpath('//disgovery-list-widget[@type="EventAppointment"]'),'Kalender-Widget')
    if widget.get('endpoint')!=ENDPOINT or widget.get('template')!='default' or widget.get('listview')!='grid':
        raise ValueError('Walzbachtal: öffentlicher Abrufvertrag hat sich geändert')
    limit=int(widget.get('limit','0'))
    if not 1<=limit<=500 or not widget.get('document') or len(widget.get('document'))>4096:
        raise ValueError('Walzbachtal: Abrufkennung oder Seitengröße ungültig')
    filters=json.loads(widget.get('filters','{}'))
    if 'date' not in filters or any(v for k,v in filters.items() if k!='date'):
        raise ValueError('Walzbachtal: unerwarteter Quellenfilter würde Termine ausblenden')
    options=json.loads(widget.get('options','{}'))
    if options.get('location_visible')!=1 or options.get('time_visible')!=1 or options.get('organizer_visible')!=1:
        raise ValueError('Walzbachtal: benötigte Terminangaben sind ausgeblendet')
    since=today.replace(day=1).isoformat()+' 00:00:00'
    params=[('src',widget.get('document')),('limit',str(limit)),('view','grid'),('template','default'),('filters[date][from]',since)]
    params += [('options['+k+']',str(v)) for k,v in options.items()]
    return params,limit


def info(card,icon):
    nodes=card.xpath('.//div[img[contains(@src,"/'+icon+'.svg")]]|.//div[span/img[contains(@src,"/'+icon+'.svg")]]')
    if not nodes:return ''
    node=deepcopy(one(nodes,icon))
    extra=[n.get('data-title','').strip() for n in node.xpath('.//*[@data-title]')]
    for n in node.xpath('.//span|.//img'):
        if n.getparent() is not None:n.drop_tree()
    return '; '.join(s for s in [text(node),*(' '.join(s.split()) for s in extra)] if s)


def end_date(start,value):
    if not value:return start
    match=re.fullmatch(r'bis (\d{1,2})\.\s*(\w+)(?: (20\d{2}))?',value,re.I)
    if not match or match[2].lower() not in MONTHS:raise ValueError('Walzbachtal: unbekanntes Enddatum '+value)
    month=MONTHS.index(match[2].lower())+1
    year=int(match[3]) if match[3] else start.year+(month<start.month)
    end=date(year,month,int(match[1]))
    if end<start:raise ValueError('Walzbachtal: umgekehrter Zeitraum')
    return end


def parse_response(raw):
    data=json.loads(raw)
    total=data['meta']['total'];count=data['results_length']
    if not isinstance(total,int) or isinstance(total,bool) or not 0<=total<=10000 or not isinstance(count,int) or not 0<=count<=total:
        raise ValueError('Walzbachtal: ungültige Ergebniszahl')
    tree=html.fromstring('<main>'+data['results']+'</main>')
    cards=tree.xpath('//article[contains(concat(" ",normalize-space(@class)," ")," card--event ")]')
    if len(cards)!=count:raise ValueError('Walzbachtal: Ergebniszahl widerspricht den Terminkarten')
    records=[]
    for card in cards:
        clock=one(card.xpath('.//time[@datetime]'),'Datumsfeld')
        begin=datetime.strptime(clock.get('datetime'),'%Y-%m-%d %H:%M:%S')
        last=card.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," card-date__to ")]')
        end=end_date(begin.date(),text(one(last,'Enddatum')) if last else '')
        link=one(card.xpath('.//h3//a[@href]'),'Titel und Originalseite');url=urljoin(URL,link.get('href'))
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='www.walzbachtal.de' or not re.fullmatch(r'/(?:veranstaltung|termin)/[^/?]+-event-appointment\d+',parsed.path) or parsed.query:
            raise ValueError('Walzbachtal: unerwartetes Terminlinkziel')
        record_id=re.search(r'appointment(\d+)$',parsed.path)[1]
        title=text(link)
        if not title:raise ValueError('Walzbachtal: leerer Titel')
        when=info(card,'schedule');clocks=re.findall(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)',when)
        if len(clocks)>2:raise ValueError('Walzbachtal: unklare Uhrzeit')
        times=[datetime.strptime(f'{int(h):02d}:{m}','%H:%M').strftime('%H:%M') for h,m in clocks]
        all_day=when.casefold()=='ganztägig'
        if when and not times and not all_day:raise ValueError('Walzbachtal: unbekannte Zeitangabe '+when)
        known=bool(times) or (not all_day and begin.strftime('%H:%M')!='00:00')
        start_time=times[0] if times else begin.strftime('%H:%M') if known else None
        if times and times[0]!=begin.strftime('%H:%M'):raise ValueError('Walzbachtal: Uhrzeit widerspricht dem Datumsfeld')
        location=info(card,'place');organizer=info(card,'perm_contact_calendar')
        region='Walzbachtal-Jöhlingen' if 'jöhlingen' in location.casefold() else 'Walzbachtal-Wössingen' if 'wössingen' in location.casefold() else 'Walzbachtal'
        records.append({'recordId':record_id,'title':title,'url':url,'startDate':begin.date().isoformat(),'endDate':end.isoformat(),
                        'startTime':start_time,'endTime':times[1] if len(times)==2 else None,'allDay':all_day,
                        'location':location,'organizer':organizer,'region':region,'dateText':text(clock),'timeText':when,
                        'status':'cancelled' if re.search(r'abgesagt|entfällt|fällt aus',text(card),re.I) else 'scheduled'})
    identities=[(r['recordId'],r['startDate'],r['startTime']) for r in records]
    if len(identities)!=len(set(identities)):raise ValueError('Walzbachtal: doppelte Vorkommen auf einer Seite')
    return records,total


def fetch_records(fetch,today=None):
    today=today or datetime.now(ZoneInfo('Europe/Berlin')).date()
    params,limit=request_config(fetch(URL),today)
    def page(number):return parse_response(fetch(ENDPOINT+'?'+urlencode([*params,('page',str(number))])))
    records,total=page(1)
    pages=max(1,math.ceil(total/limit))
    if pages>100 or len(records)!=min(total,limit):raise ValueError('Walzbachtal: erste Ergebnisseite ist unvollständig')
    seen={(r['recordId'],r['startDate'],r['startTime']) for r in records}
    for number in range(2,pages+1):
        rows,new_total=page(number)
        if new_total!=total or len(rows)!=min(limit,total-(number-1)*limit):raise ValueError('Walzbachtal: Ergebnisumfang während des Abrufs verändert')
        for record in rows:
            identity=(record['recordId'],record['startDate'],record['startTime'])
            if identity in seen:raise ValueError('Walzbachtal: Paginierung ignoriert oder Vorkommen wiederholt')
            seen.add(identity)
        records.extend(rows)
    if len(records)!=total:raise ValueError('Walzbachtal: nicht alle Termine übernommen')
    return records,{'coverageKind':'public-json-html','allowEmpty':True,'pagesFetched':pages,'publishedOccurrenceCount':total,
                    'requestedFrom':today.replace(day=1).isoformat(),'extractionMethod':'deterministic-public-calendar','llmRequired':False,
                    'coverageNote':f'Gemeinsamer Gemeindekalender für Jöhlingen und Wössingen ab Monatsanfang, alle {pages} Ergebnisseiten. Nur ausdrücklich gelieferte Vorkommen; keine selbst erzeugten Wiederholungen. Ortsteil nur bei ausdrücklicher Ortsangabe.'}
