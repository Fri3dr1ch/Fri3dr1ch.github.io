"""Reusable venue assignments. Uncertain matches never become map coordinates."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ATTRIBUTION = {'text': '© OpenStreetMap contributors · ODbL', 'url': 'https://www.openstreetmap.org/copyright'}
# Search windows, not administrative borders. Narrow contexts keep e.g. different
# Naturfreundehäuser apart; unknown locations never fall back to a town centre.
SCOPES = {
    'groetzingen': ('Grötzingen', (8.47,49.00,8.53,49.06)),
    'durlach': ('Durlach', (8.43,48.96,8.51,49.04)),
    'berghausen': ('Pfinztal-Berghausen', (8.50,48.99,8.56,49.04)),
    'soellingen': ('Pfinztal-Söllingen', (8.53,48.97,8.59,49.01)),
    'woeschbach': ('Pfinztal-Wöschbach', (8.55,48.99,8.62,49.04)),
    'kleinsteinbach': ('Pfinztal-Kleinsteinbach', (8.53,48.95,8.60,48.99)),
    'joehlingen': ('Walzbachtal-Jöhlingen', (8.55,49.015,8.605,49.06)),
    'woessingen': ('Walzbachtal-Wössingen', (8.59,49.00,8.65,49.05)),
    'weingarten': ('Weingarten (Baden)', (8.48,49.03,8.58,49.10)),
    'remchingen': ('Remchingen', (8.53,48.92,8.65,48.99)),
    'pfinztal': ('Pfinztal', (8.50,48.95,8.62,49.04)),
    'walzbachtal': ('Walzbachtal', (8.55,49.00,8.65,49.06)),
    'karlsruhe': ('Karlsruhe und Umgebung', (8.27,48.93,8.54,49.12)),
}
GENERIC = {'kirche','evangelische kirche','katholische kirche','gemeindehaus','evangelisches gemeindehaus',
           'katholisches gemeindehaus','naturfreundehaus','clubhaus','vereinsheim','sporthalle','mehrzweckhalle',
           'kulturhalle','rathaus','ratssaal','burgersaal','friedhof','hasenheim','restaurant','sportplatz'}
SEARCH_FILLER={'der','die','das','im','in','am','an','fur','und','e','v','st','kath','ev','vor','beim',
               'kinder','jugendliche','jugendlichen','zentrum','schule','haus','halle','karlsruhe','nottingen',
               'wilferdingen','singen','darmsbach','aue','neureut','waldstadt','waldbrucke','weingarten','durlach','grotzingen',
               'schloss','kirche','theater','saal','museum','restaurant','gaststatte'}


def norm(value):
    value = unicodedata.normalize('NFKD', (value or '').casefold().replace('ß','ss')).encode('ascii','ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def canonical_label(location):
    # A building with varying programme suffixes emitted by this source. Keep
    # branches and named rooms distinct; never truncate arbitrary place labels.
    match=re.match(r'^(Stadtbibliothek Karlsruhe - (?:Zentralbibliothek im Neuen Ständehaus|Kinder- und Jugendbibliothek(?: Karlsruhe)? im Prinz-Max-Palais|Stadtteilbibliothek \w+))(?: - .*)?$',location,re.I)
    return match[1] if match else location


def scope_for(event):
    location, region = norm(event.get('location')), norm(event.get('region'))
    for value in (location, region):
        for key, (label, _) in SCOPES.items():
            names = [norm(label.split('-')[-1]), norm(label)]
            if any(re.search(r'\b'+re.escape(n)+r'\b',value) for n in names if n): return key
    return None


def identity(event):
    scope = scope_for(event)
    location = ' '.join((event.get('location') or '').split())
    # Empty locations may represent many venues within a publisher. Never allow
    # one review decision to assign all of these unrelated events to one point.
    label=norm(canonical_label(location))
    basis=[label,scope] if location else ['missing',event['sourceId'],scope]
    # "Clubhaus" may mean different clubs within the same district. A saved
    # decision must never silently propagate across those organisers.
    if label in GENERIC:basis += [norm(event.get('organizer')),None if event.get('organizer') else event['sourceId']]
    return 'venue-' + hashlib.sha256(json.dumps(basis,ensure_ascii=False).encode()).hexdigest()[:20], location, scope


def query_for(location, scope):
    city={'karlsruhe':'Karlsruhe','soellingen':'Söllingen Pfinztal','berghausen':'Berghausen Pfinztal',
          'kleinsteinbach':'Kleinsteinbach Pfinztal','woeschbach':'Wöschbach Pfinztal',
          'joehlingen':'Jöhlingen','woessingen':'Wössingen'}.get(scope,SCOPES[scope][0])
    return canonical_label(location) + ', ' + city


def inside(place, scope):
    if not scope:return False
    west,south,east,north = SCOPES[scope][1]
    return west <= place['longitude'] <= east and south <= place['latitude'] <= north


def validate_place(place):
    for key,lo,hi in (('latitude',-90,90),('longitude',-180,180)):
        v = place.get(key)
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError('Ungültige Ortskoordinate')
    if not place.get('id') or not place.get('name') or urlsplit(place.get('url','')).scheme != 'https':
        raise ValueError('Ortsbeleg oder Ortsname fehlt')
    return place


def parse_photon(raw):
    data = json.loads(raw)
    if data.get('type') != 'FeatureCollection' or not isinstance(data.get('features'),list):
        raise ValueError('Unbekanntes Ortsantwortformat')
    result,seen = [],set()
    for feature in data['features']:
        p,g = feature['properties'],feature['geometry']
        if g.get('type') != 'Point' or p.get('osm_type') not in {'N','W','R'} or not isinstance(p.get('osm_id'),int):
            raise ValueError('Ungültiges OSM-Ortsergebnis')
        if p.get('countrycode','').upper() != 'DE': continue
        kind = {'N':'node','W':'way','R':'relation'}[p['osm_type']]
        lon,lat = g['coordinates']
        name = p.get('name') or ' '.join(str(p.get(k,'')) for k in ('street','housenumber')).strip()
        place = validate_place({'id':f'osm-{kind}-{p["osm_id"]}','name':name,'latitude':lat,'longitude':lon,
                 'address':', '.join(v for v in [' '.join(str(p.get(k,'')) for k in ('street','housenumber')).strip(),
                                               ' '.join(str(p.get(k,'')) for k in ('postcode','city')).strip()] if v),
                 'url':f'https://www.openstreetmap.org/{kind}/{p["osm_id"]}',
                 'category':p.get('osm_key',''),'type':p.get('osm_value',''),
                 'street':p.get('street',''),'houseNumber':p.get('housenumber','')})
        if place['id'] not in seen:result.append(place);seen.add(place['id'])
    return result


def name_core(value, scope):
    # Only harmless city/legal/venue-type qualifiers. Room names, churches and
    # district names are deliberately not removed as general fuzzy synonyms.
    value = norm(value.replace('&',' und '))
    for word in ('deutschland','walzbachtal','karlsruhe','remchingen','pfinztal','kulturzentrum','galerie','naturfreundehaus','planetarium','stadtbibliothek','zentralbibliothek','e v'):
        value = re.sub(r'\b'+word+r'\b',' ',value)
    return ' '.join(value.split())


def exact_address(location, place):
    street,number = norm(place.get('street')),norm(place.get('houseNumber'))
    # Both street and number must be present as complete words; no street-centre
    # matches and no interpretation of an isolated year as a house number.
    return bool(street and number and re.search(r'\b'+re.escape(street)+r'\s+'+re.escape(number)+r'\b',norm(location)))


def candidates_for(location, scope, places):
    core = name_core(location,scope)
    result = []
    for place in places:
        outside=not inside(place,scope)
        other = name_core(place['name'],scope)
        if outside and place.get('provenance') not in {'publisher','publisher-multiple'}:
            # A named hall can be in a neighbouring district even when the
            # publisher groups it under the organising club's district. Surface
            # exact nearby names for review, never as automatic coordinates.
            if sorted(core.split())!=sorted(other.split()) or not (8.20<=place['longitude']<=8.70 and 48.90<=place['latitude']<=49.20):continue
        a,b = set(core.split()),set(other.split())
        score = max(SequenceMatcher(None,core,other).ratio(),len(a&b)/max(len(a|b),1))
        full_exact=norm(location)==norm(place['name']) and norm(location) not in GENERIC
        context_names={norm(label.split('-')[-1]) for label,_ in SCOPES.values()}
        exact = bool(core and (sorted(core.split()) == sorted(other.split()) and core not in GENERIC|context_names or full_exact) and
                     place['category'] not in {'highway','boundary','place'} and place['type'] not in {'administrative','city','town','village','suburb'})
        if place.get('provenance')=='publisher':exact=True
        address = exact_address(location,place) and place.get('houseNumber')
        if place.get('provenance')=='publisher-multiple':exact=address=False
        if place['category'] in {'boundary','place'} and not address:continue
        if not (a&b)-SEARCH_FILLER and not exact and not address and core not in GENERIC and score<0.8:continue
        if b and b<=a and b-SEARCH_FILLER:score=max(score,0.93)
        if score<0.35 and not exact and not address and not (core in GENERIC and a&b):continue
        result.append({**deepcopy(place),'score':round(score,3),'exact':bool(exact or address) and not outside,'outsideContext':outside})
    return sorted(result,key=lambda p:(not p['exact'],-p['score'],p['id']))[:5]


def walzbachtal_places(raw,event_url):
    """Read JSON literals, never execute source scripts. Preserve event binding."""
    raw=raw.decode('utf-8') if isinstance(raw,bytes) else raw
    matches=list(re.finditer(r'\bbaseLocations\s*:\s*',raw))
    if len(matches)!=1:return []
    values,_=json.JSONDecoder().raw_decode(raw[matches[0].end():])
    if not isinstance(values,list):raise ValueError('Unbekanntes Walzbachtaler Ortsformat')
    result=[]
    for p in values:
        geo=p.get('geoPosition') or {};url=p.get('url','');parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='www.walzbachtal.de' or not re.fullmatch(r'/adresse/[^/]+-address\d+',parsed.path):
            raise ValueError('Unbekannter Walzbachtaler Ortsbeleg')
        place=validate_place({'id':'walzbachtal-address-'+re.search(r'address(\d+)$',parsed.path)[1],
                 'name':p['title'],'address':p.get('street',''),'latitude':geo.get('latitude'),'longitude':geo.get('longitude'),
                 'url':url,'eventUrl':event_url,'category':'publisher','type':'venue',
                 'provenance':'publisher' if len(values)==1 else 'publisher-multiple'})
        result.append(place)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Ortsdienst leitet unerwartet weiter')


class Photon:
    def __init__(self,endpoint,sleep=time.sleep):
        parsed = urlsplit(endpoint)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Ungültiger öffentlicher Ortsdienst')
        self.endpoint,self.sleep,self.last = endpoint,sleep,None

    def __call__(self,query,scope):
        if self.last is not None:self.sleep(max(0,1.1-(time.monotonic()-self.last)))
        url = self.endpoint+'?'+urlencode({'q':query,'limit':5,'lang':'de','bbox':','.join(map(str,SCOPES[scope][1]))})
        self.last = time.monotonic()
        request = Request(url,headers={'User-Agent':'RegionalCalendarVenueImport/1.0 (+https://github.com/Fri3dr1ch/Fri3dr1ch.github.io)',
                                      'Accept':'application/json'})
        with build_opener(NoRedirect()).open(request,timeout=12) as response:raw=response.read(1_000_001)
        if len(raw)>1_000_000:raise ValueError('Ortsantwort zu groß')
        return parse_photon(raw)


def read(path,default):return json.loads(path.read_text()) if path.exists() else deepcopy(default)


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def enrich(data,root,today,online=False,budget=None,fetch=None,source_fetch=None):
    """Assign all events, but request/review only distinct current venue contexts."""
    folder=root/'data/venues';config=read(folder/'config.json',None)
    if config is None:return None
    registry=read(folder/'reviewed.json',{'schemaVersion':1,'rules':[]})
    rules=registry['rules']
    if len({r['key'] for r in rules})!=len(rules):raise ValueError('Doppelte Ortsprüfentscheidung')
    reviewed={r['key']:r for r in rules}
    cache=read(folder/'lookup-cache.json',{'schemaVersion':1,'queries':{}})
    publishers=read(folder/'publisher-cache.json',{'schemaVersion':1,'locations':{}})
    groups={}
    for e in [*data['events'],*data.get('undatedEvents',[])]:
        key,location,scope=identity(e)
        group=groups.setdefault(key,{'key':key,'location':location,'scope':scope,'events':[]})
        group['events'].append(e)
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    active=lambda e:not e.get('endDate') or e['endDate']>=today.isoformat()
    eligible=[g for g in groups.values() if any(active(e) for e in g['events'])]
    eligible.sort(key=lambda g:(-sum(active(e) for e in g['events']),g['key']))
    requested,failures=0,[]
    if online and config.get('enabled'):
        fetch=fetch or Photon(config['endpoint'])
        limit=config.get('maxNewPerRun',12) if budget is None else budget
        for g in eligible:
            if requested>=limit or len(failures)>=2:break
            if not g['location'] or not g['scope'] or g['key'] in reviewed or re.search(r'\bonline\b',norm(g['location'])):continue
            if re.search(r'\b(oder|verschiedene|mehrere|beschreibung)\b',norm(g['location'])):continue
            query=query_for(g['location'],g['scope'])
            publisher=publishers['locations'].get(g['key'])
            if publisher and publisher.get('places'):continue
            source_event=next((e for e in g['events'] if e['sourceId']=='walzbachtal'),None)
            retry_publisher=not publisher or ('error' in publisher and datetime.fromisoformat(publisher['checkedAt'])+timedelta(days=1)<=datetime.now(timezone.utc))
            if source_event and retry_publisher:
                from http_client import HttpClient
                source_fetch=source_fetch or HttpClient(timeout=12,attempts=1)
                requested+=1
                try:
                    places=walzbachtal_places(source_fetch(source_event['url']),source_event['url'])
                    publishers['locations'][g['key']]={'checkedAt':now,'places':places}
                except Exception as error:
                    failures.append(type(error).__name__+': '+str(error))
                    publishers['locations'][g['key']]={'checkedAt':now,'error':failures[-1]}
                write(folder/'publisher-cache.json',publishers)
                if publishers['locations'][g['key']].get('places'):continue
                if requested>=limit or len(failures)>=2:break
            old=cache['queries'].get(query)
            if old:
                # Successful results, including empty lists, are durable. Only
                # transient errors are retried, at most once per day.
                if 'places' in old:continue
                if datetime.fromisoformat(old['checkedAt'])+timedelta(days=1)>datetime.now(timezone.utc):continue
            requested+=1
            try:
                cache['queries'][query]={'checkedAt':now,'places':fetch(query,g['scope'])}
            except Exception as error:
                message=type(error).__name__+': '+str(error)
                failures.append(message);cache['queries'][query]={'checkedAt':now,'error':message}
            write(folder/'lookup-cache.json',cache)
    # Reuse known places across name variants and publishers. This also exposes
    # competing same-name objects already found elsewhere instead of hiding them.
    known={p['id']:p for entry in sorted(cache['queries'].values(),key=lambda x:x.get('checkedAt','')) for p in entry.get('places',[])}
    rows=[]
    for g in groups.values():
        location,scope,key=g['location'],g['scope'],g['key']
        query=query_for(location,scope) if location and scope else None
        entry=cache['queries'].get(query,{})
        publisher=publishers['locations'].get(key,{})
        pool=list(entry.get('places',[]))
        if query and not publisher.get('places'):
            words=set(name_core(location,scope).split())-SEARCH_FILLER
            pool += [p for p in known.values() if words & set(name_core(p['name'],scope).split())]
        unique={p['id']:p for p in pool}
        candidates=candidates_for(location,scope,publisher.get('places') or unique.values()) if query else []
        status,reason,place,method='review','Kein eindeutig passender Spielort gefunden.',None,None
        rule=reviewed.get(key)
        if rule:
            if not location:raise ValueError('Leere Ortsangaben dürfen keine gemeinsame Zuordnung erhalten')
            # Raw spelling variants share a key; all semantic context must still
            # match, including the organiser for an otherwise generic clubhouse.
            if identity({'location':rule.get('location'),'region':SCOPES.get(rule.get('scope'),('',))[0],
                         'sourceId':rule.get('sourceContext') or '', 'organizer':rule.get('organizer','')})[0]!=key:
                raise ValueError('Ortsregel widerspricht ihrem Kontext')
            if rule['decision']=='place':
                place=validate_place(deepcopy(rule['place']));status='resolved';reason='Einmalig bestätigte Zuordnung.';method='reviewed'
            elif rule['decision']=='unmapped':status='excluded';reason=rule.get('note') or 'Bewusst ohne festen Kartenpunkt.'
            else:raise ValueError('Unbekannte Ortsentscheidung')
        elif not location:status='missing';reason='Die Quelle nennt keinen Veranstaltungsort. Keine Zuordnung aus dem Herausgeber ableiten.'
        elif re.search(r'\bonline\b',norm(location)):
            status='online';reason='Als Online-Ort angegeben; kein physischer Kartenpunkt.'
        elif not scope:reason='Kein ausreichend bestimmter Ortskontext.'
        elif re.search(r'\b(oder|verschiedene|mehrere|beschreibung)\b',norm(location)):
            reason='Mehrere mögliche Orte oder Verweis auf eine Beschreibung; Treffpunkt prüfen.'
        else:
            exact=[p for p in candidates if p['exact']]
            if len(exact)==1:
                place={k:v for k,v in exact[0].items() if k not in {'score','exact'}}
                method='publisher' if place.get('provenance')=='publisher' else 'automatic'
                status='resolved';reason='Koordinate ausdrücklich auf der Originalseite dieses Spielorts angegeben.' if method=='publisher' else 'Eindeutiger Name bzw. vollständige Adresse im passenden Ortskontext.'
            elif len(exact)>1:reason='Mehrere gleich gut passende Kartenobjekte; Auswahl erforderlich.'
            elif entry.get('error'):status='queued';reason='Ortsdienst nicht erreichbar; wird frühestens am nächsten Tag erneut versucht.'
            elif any(p.get('outsideContext') for p in candidates):reason='Ein Ortsbeleg liegt außerhalb des erwarteten Ortskontexts. Ortsteil und Koordinate ausdrücklich prüfen.'
            elif not entry and not publisher.get('places') and config.get('enabled'):
                status='queued';reason='Automatischer Abruf vorgemerkt; zuerst einen der nächsten Aktualisierungsläufe abwarten.'
            elif candidates:reason='Nur ähnliche Namen gefunden; Vorschlag bitte prüfen.'
        for e in g['events']:
            e['venue']={'key':key,'status':status,'method':method,'place':deepcopy(place)}
        current=[e for e in g['events'] if active(e)]
        if not current:continue
        rows.append({'key':key,'location':location,'scope':scope,'scopeLabel':SCOPES[scope][0] if scope else 'Ortskontext offen',
                     'status':status,'reason':reason,'method':method,'place':place,'candidates':candidates,
                     'eventCount':len(current),'reviewNote':registry.get('notes',{}).get(key,{}).get('note',''),
                     'organizer':current[0].get('organizer','') if norm(canonical_label(location)) in GENERIC else '',
                     'sourceContext':current[0]['sourceId'] if norm(canonical_label(location)) in GENERIC and not current[0].get('organizer') else None,
                     'sources':sorted({e['sourceName'] for e in current}),
                     'examples':[{'id':e['id'],'title':e['title'],'date':e.get('startDate'),'url':e.get('url') or e['sources'][0]['url']} for e in sorted(current,key=lambda e:e.get('startDate') or '9999')[:4]]})
    rows.sort(key=lambda r:(r['status']=='resolved',-r['eventCount'],r['location']))
    counts=Counter(r['status'] for r in rows)
    event_counts=Counter(e['venue']['status'] for e in data['events'] if active(e))
    review={'schemaVersion':1,'generatedAt':data['generatedAt'],'attribution':ATTRIBUTION,
            'stats':{'venues':len(rows),'venueStatuses':dict(counts),'upcomingEvents':sum(event_counts.values()),'eventStatuses':dict(event_counts)},
            'lookup':{'requested':requested,'failures':failures},'rows':rows}
    data['venueStats']=review['stats'];data['venueAttribution']=ATTRIBUTION
    return review


def render_review(root,review):
    template=(root/'scripts/place_review.html').read_text()
    payload=json.dumps(review,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    if template.count('/*PLACE_DATA*/')!=1:raise ValueError('Ortsprüfvorlage fehlt oder ist mehrdeutig')
    page=template.replace('/*PLACE_DATA*/',payload)
    (root/'dist/orte-pruefen.html').write_text(page)
    write(root/'dist/place-review.json',review)


def review_problem(review,review_url):
    pending=sorted(r['key'] for r in review['rows'] if r['status']=='review')
    if not pending:return None
    return {'key':'review:venues','kind':'review','sourceId':'venues','sourceName':'Ortszuordnung',
            'url':review_url,
            'reason':f'{len(pending)} unterschiedliche Ortsangaben brauchen eine Bestätigung. Bereits bestätigte Zuordnungen werden automatisch wiederverwendet. Prüfliste: /veranstaltungen/orte-pruefen.html',
            'candidateUrls':[], 'reviewFingerprint':hashlib.sha256('\n'.join(pending).encode()).hexdigest()}
