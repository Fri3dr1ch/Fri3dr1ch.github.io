"""Reusable venue assignments. Uncertain matches never become map coordinates."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import hashlib
from html.parser import HTMLParser
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


def local_name(scope):
    return norm(re.sub(r'\s*\([^)]*\)', '', SCOPES[scope][0].split('-')[-1]))


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


def building_label(location):
    """Only named, known buildings; never strip arbitrary branches or campuses.

    These parent names are supported by the reviewed places and venue labels.
    Identity deliberately keeps the original room, so an explicit room decision
    (including an exclusion) continues to override the building fallback.
    """
    label = canonical_label(location)
    patterns = [
        (r'^(?:Badisches )?Staatstheater(?:, (?:Großes Haus|Kleines Haus|Studio|Foyer|Neues Entrée|siehe Beschreibung))?$', 'Badisches Staatstheater'),
        (r'^Hochschule für Musik(?: Karlsruhe)?(?:,.*)?$', 'Hochschule für Musik Karlsruhe'),
        (r'^(?:ZKM\s*[|│]\s*Zentrum für Kunst und Medien(?: Karlsruhe)?|Zentrum für Kunst und Medien \(ZKM\))(?:\s*[|│,].*)?$', 'ZKM | Zentrum für Kunst und Medien'),
        (r'^(?:Augustaraum|Niddaraum), Begegnungsstätte Grötzingen$', 'Begegnungsstätte Grötzingen'),
        (r'^Kulturhalle Remchingen\s*-\s*Foyer$', 'Kulturhalle Remchingen'),
        (r'^Karlsburg \(Vorplatz\)$', 'Karlsburg'),
    ]
    for pattern, parent in patterns:
        if re.fullmatch(pattern, label, re.I):return parent
    return label


def postal_city(place):
    match = re.search(r'\b\d{5}\s+([^,]+)', place.get('address', ''))
    return norm(place.get('city') or (match[1] if match else ''))


def context_match(place, scope, source_city=None):
    """Bounding boxes overlap; explicit municipality names take precedence."""
    if not inside(place, scope):return False
    if place.get('provenance'):return True
    city = postal_city(place)
    if scope == 'karlsruhe':
        # The feed covers the surrounding area too. A different postal city
        # needs a matching source address or a reviewed venue.
        return not city or city == (norm(source_city) or 'karlsruhe')
    text = norm(' '.join(str(place.get(k, '')) for k in ('address', 'name', 'district', 'locality')))
    named = {key for key in SCOPES
             if re.search(r'\b' + re.escape(local_name(key)) + r'\b', text)}
    if scope in {'remchingen', 'pfinztal', 'walzbachtal'}:return True
    allowed = {'groetzingen': {'karlsruhe', 'grotzingen'}, 'durlach': {'karlsruhe', 'durlach'},
               'weingarten': {'weingarten', 'weingarten baden'},
               'joehlingen': {'johlingen', 'walzbachtal'}, 'woessingen': {'wossingen', 'walzbachtal'}}
    expected = allowed.get(scope, {local_name(scope), 'pfinztal'})
    if city and city not in expected:return False
    # Karlsruhe is the postal city of Durlach and Grötzingen. The municipality
    # Pfinztal/Walzbachtal also does not conflict with one of its own districts.
    named -= {'karlsruhe', 'pfinztal', 'walzbachtal'}
    return not named or scope in named


def venue_object(place, location):
    category, kind = place.get('category'), place.get('type')
    if category in {'railway', 'public_transport', 'information', 'emergency'}:return False
    if kind in {'toilets', 'ticket', 'bus_stop', 'elevator', 'bicycle_parking', 'bench'}:return False
    if category == 'highway':
        return kind in {'pedestrian', 'living_street', 'footway'} and bool(re.search(r'(platz|markt|strasse|brucke)\b', norm(location)))
    if kind == 'parking':return 'parkplatz' in norm(location)
    return category not in {'boundary', 'place'} or (category=='place' and kind=='square')


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
                 'city':p.get('city',''),'district':p.get('district',''),'locality':p.get('locality',''),
                 'street':p.get('street',''),'houseNumber':p.get('housenumber','')})
        if place['id'] not in seen:result.append(place);seen.add(place['id'])
    return result


def name_core(value, scope):
    # Only harmless city/legal/venue-type qualifiers. Room names, churches and
    # district names are deliberately not removed as general fuzzy synonyms.
    value = norm(building_label(value).replace('&',' und '))
    value = re.sub(r'\bvhs\b', 'volkshochschule', value)
    for word in ('deutschland','walzbachtal','karlsruhe','remchingen','pfinztal','kulturzentrum','galerie','naturfreundehaus','planetarium','stadtbibliothek','zentralbibliothek','e v'):
        value = re.sub(r'\b'+word+r'\b',' ',value)
    if scope:
        local = local_name(scope)
        value = re.sub(r'\b'+re.escape(local)+r'\b', ' ', value)
    return ' '.join(value.split())


def address_match(location, place):
    street,number = street_norm(place.get('street')),norm(place.get('houseNumber'))
    # Both street and number must be present as complete words; no street-centre
    # matches and no interpretation of an isolated year as a house number.
    if not street or not number:
        return None
    return re.search(r'\b'+re.escape(street)+r'\s*'+re.escape(number)+r'\b',street_norm(location))


def exact_address(location, place):
    return address_match(location, place) is not None


def expand_street_abbreviation(value):
    return re.sub(r'(?i)str\.(?=\s|\d|$)', lambda m: 'Straße ' if m[0][0].isupper() else 'straße ', value)


def street_norm(value):
    # Str.5, Straße 5 and the OSM spelling with a space before Straße agree.
    value=norm(expand_street_abbreviation(value or ''))
    return re.sub(r'\s+(strasse|str)\b', r'\1', value)


def address_source_url(event):
    parsed=urlsplit(event.get('url',''))
    return (parsed.scheme=='https' and not parsed.query and not parsed.fragment and
            ((event['sourceId']=='karlsruhe' and parsed.hostname=='kalender.karlsruhe.de' and parsed.path.startswith('/db/termine/')) or
             (event['sourceId']=='remchingen' and parsed.hostname=='www.remchingen.de' and re.fullmatch(r'/veranstaltungen/[^/]+-id_\d+/?',parsed.path))))


def source_addresses(raw,event):
    """Read the venue block, never the publisher's contact/footer address."""
    if not address_source_url(event):return []
    from lxml import html
    tree=html.fromstring(raw)
    text=lambda node:' '.join(node.text_content().split()).strip(' ,')
    result=[]
    if event['sourceId']=='karlsruhe':
        for box in tree.xpath('//*[@id="box_ort"]'):
            names=box.xpath('.//h5');streets=box.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," street-address ")]')
            if len(names)!=1 or len(streets)!=1:continue
            if norm(canonical_label(text(names[0])))!=norm(canonical_label(event['location'])):continue
            def field(cls):
                nodes=box.xpath('.//*[contains(concat(" ",normalize-space(@class)," ")," '+cls+' ")]')
                return text(nodes[0]) if len(nodes)==1 else ''
            street,city,postal=text(streets[0]),field('locality'),field('postal-code')
            if street and city:result.append({'street':street,'city':city,'postalCode':postal})
    else:
        for script in tree.xpath('//script[@type="application/ld+json"]/text()'):
            try:values=json.loads(script)
            except (ValueError,TypeError):continue
            values=values if isinstance(values,list) else [values]
            for value in values:
                if not isinstance(value,dict) or value.get('@type')!='Event':continue
                place=value.get('location') or {}
                if not isinstance(place,dict) or norm(place.get('name'))!=norm(event['location']):continue
                address=place.get('address') or {}
                if isinstance(address,dict) and address.get('streetAddress') and address.get('addressLocality'):
                    result.append({'street':address['streetAddress'],'city':address['addressLocality'],'postalCode':address.get('postalCode','')})
    unique={}
    for a in result:
        if not re.search(r'\d',a['street']):continue
        a.update(address=a['street']+', '+' '.join(filter(None,[a['postalCode'],a['city']])),url=event['url'])
        unique[street_norm(a['street'])+'|'+norm(a['city'])]=a
    # Conflicting source blocks require review rather than an arbitrary address.
    return list(unique.values())


def address_queries(addresses):
    if len(addresses)!=1:return []
    a=addresses[0];street=expand_street_abbreviation(a['street'])
    street=' '.join(street.split())
    variants=[street,re.sub(r'(?i)(?<=\w)(straße)\b',r' \1',street)]
    return list(dict.fromkeys(v+', '+a['city'] for v in variants))


def candidates_for(location, scope, places, addresses=None):
    core = name_core(location,scope)
    result = []
    address_name_matches = set()
    source = addresses[0] if addresses else {}
    context_names = {norm(label.split('-')[-1]) for label,_ in SCOPES.values()}
    for place in places:
        outside=not context_match(place,scope,source.get('city'))
        other = name_core(place['name'],scope)
        if outside and place.get('provenance') not in {'publisher','publisher-multiple'}:
            # A named hall can be in a neighbouring district even when the
            # publisher groups it under the organising club's district. Surface
            # exact nearby names for review, never as automatic coordinates.
            if sorted(core.split())!=sorted(other.split()) or not (8.20<=place['longitude']<=8.70 and 48.90<=place['latitude']<=49.20):continue
        a,b = set(core.split()),set(other.split())
        score = max(SequenceMatcher(None,core,other).ratio(),len(a&b)/max(len(a|b),1))
        contextual_generic = core in {'evangelisches gemeindehaus', 'kulturhalle', 'friedhof'} and scope and bool(
            re.search(r'\b'+re.escape(local_name(scope))+r'\b', norm(place.get('address',''))))
        full_exact=norm(location)==norm(place['name']) and (norm(location) not in GENERIC or contextual_generic)
        usable = venue_object(place,location)
        exact = bool(core and ((sorted(core.split()) == sorted(other.split()) or core.replace(' ','') == other.replace(' ','')) and core not in GENERIC|context_names or full_exact) and
                     usable and place['type'] not in {'administrative','city','town','village','suburb'})
        if place.get('provenance')=='publisher':exact=True
        inline_address = address_match(location,place)
        address = bool(inline_address and usable)
        if inline_address:
            raw_label = inline_address.string[:inline_address.start()] + ' ' + inline_address.string[inline_address.end():]
            name_without_address = name_core(re.sub(r'\b\d{5}\b', '', raw_label),scope)
        else:
            name_without_address = core
        if addresses:
            # A matching name cannot overrule a different published house address.
            postal=re.search(r'\b\d{5}\b',place.get('address',''))
            city=postal_city(place)
            address=(len(addresses)==1 and exact_address(source['street'],place) and usable and
                     (not city or not source.get('city') or city==norm(source['city'])) and
                     (not postal or not source.get('postalCode') or postal[0]==source['postalCode']))
            if not address:continue
        if place.get('provenance')=='publisher-multiple':exact=address=False
        if place['category'] in {'boundary','place'} and place['type']!='square' and not address:continue
        if not (a&b)-SEARCH_FILLER and not exact and not address and core not in GENERIC and score<0.8:continue
        if b and b<=a and b-SEARCH_FILLER:score=max(score,0.93)
        if score<0.35 and not exact and not address and not (core in GENERIC and a&b):continue
        if address and not outside and (exact or (name_without_address and name_without_address == other)):
            address_name_matches.add(place['id'])
        result.append({**deepcopy(place),'score':round(score,3),'exact':bool(exact or address) and not outside,'outsideContext':outside})
    if address_name_matches:
        # Several tenants can share an address. Prefer a matching venue name
        # at that address, while keeping equally named objects for review.
        for candidate in result:
            candidate['exact'] = candidate['id'] in address_name_matches
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


def weingarten_places(raw, event_url, location):
    """Use only the marker bound to this event, never the page's general map."""
    parsed=urlsplit(event_url)
    match=re.fullmatch(r'/freizeit-tourismus/veranstaltungen/(\d+)/[^/]+/?',parsed.path)
    if parsed.scheme!='https' or parsed.hostname!='www.weingarten-baden.de' or not match:
        raise ValueError('Unbekannter Weingartener Ortsbeleg')
    event_id=match[1]
    class Markers(HTMLParser):
        def __init__(self):super().__init__();self.markers=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='div' and attrs.get('id')=='hw_map_location__'+event_id:
                self.markers.append(attrs)
    parser=Markers();parser.feed(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
    # Duplicate IDs and missing coordinates must not turn into an arbitrary pin.
    if len(parser.markers)!=1:return []
    marker=parser.markers[0]
    if not marker.get('data-lat') or not marker.get('data-lng'):return []
    return [validate_place({'id':'weingarten-location-'+event_id,'name':location,
             'latitude':float(marker['data-lat']),'longitude':float(marker['data-lng']),
             'address':' '.join(marker.get('data-'+k,'') for k in ('strasse','hausnummer','plz','ort')).strip(),
             'url':event_url,'eventUrl':event_url,'category':'publisher','type':'venue','provenance':'publisher'})]


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
    event_overrides=registry.get('eventOverrides',{})
    confirmed_buildings={}
    for rule in rules:
        if rule['decision']=='place' and building_label(rule['location']) != canonical_label(rule['location']):
            parent=(norm(building_label(rule['location'])),rule['scope'])
            confirmed_buildings.setdefault(parent,{})[rule['place']['id']]=rule['place']
    cache=read(folder/'lookup-cache.json',{'schemaVersion':1,'queries':{}})
    publishers=read(folder/'publisher-cache.json',{'schemaVersion':1,'locations':{}})
    groups={}
    for e in [*data['events'],*data.get('undatedEvents',[])]:
        key,location,scope=identity(e)
        override=event_overrides.get(e['id'])
        if override and key == override['originalVenueKey']:
            # A source can name the institution while the organiser specifies
            # a different building for just this event.
            target=reviewed[override['reviewedVenueKey']]
            key,location,scope=target['key'],target['location'],target['scope']
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
            if len(confirmed_buildings.get((norm(building_label(g['location'])),g['scope']),{}))==1:continue
            if re.search(r'\b(oder|verschiedene|mehrere|beschreibung)\b',norm(g['location'])) or norm(g['location']) in {'im ort','wird noch bekannt gegeben','ort wird noch bekannt gegeben','siehe homepage'}:continue
            publisher=publishers['locations'].get(g['key'])
            if publisher and publisher.get('places'):continue
            source_event=next((e for e in g['events'] if e['sourceId'] in {'walzbachtal','weingarten'} or address_source_url(e)),None)
            retry_publisher=not publisher or ('error' in publisher and datetime.fromisoformat(publisher['checkedAt'])+timedelta(days=1)<=datetime.now(timezone.utc))
            if source_event and retry_publisher:
                from http_client import HttpClient
                source_fetch=source_fetch or HttpClient(timeout=12,attempts=1)
                requested+=1
                try:
                    raw=source_fetch(source_event['url'])
                    places=(walzbachtal_places(raw,source_event['url']) if source_event['sourceId']=='walzbachtal'
                            else weingarten_places(raw,source_event['url'],g['location']) if source_event['sourceId']=='weingarten' else [])
                    addresses=source_addresses(raw,source_event) if address_source_url(source_event) else []
                    publishers['locations'][g['key']]={'checkedAt':now,'places':places,'addresses':addresses}
                except Exception as error:
                    failures.append(type(error).__name__+': '+str(error))
                    publishers['locations'][g['key']]={'checkedAt':now,'error':failures[-1]}
                write(folder/'publisher-cache.json',publishers)
                if publishers['locations'][g['key']].get('places'):continue
                if requested>=limit or len(failures)>=2:break
            publisher=publishers['locations'].get(g['key'],{})
            queries=address_queries(publisher.get('addresses',[])) or [query_for(g['location'],g['scope'])]
            query=next((q for q in queries if q not in cache['queries']),queries[-1])
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
    # Confirmations remain usable even when their original lookup has expired.
    # Room aliases use the same confirmed building, never a freshly guessed one.
    known.update({r['place']['id']:r['place'] for r in rules if r['decision']=='place'})
    rows=[]
    for g in groups.values():
        location,scope,key=g['location'],g['scope'],g['key']
        query=query_for(location,scope) if location and scope else None
        entry=cache['queries'].get(query,{})
        publisher=publishers['locations'].get(key,{})
        addresses=publisher.get('addresses',[])
        pool=list(entry.get('places',[]))
        for q in address_queries(addresses):pool+=cache['queries'].get(q,{}).get('places',[])
        if query and not publisher.get('places'):
            words=set(name_core(location,scope).split())-SEARCH_FILLER
            pool += [p for p in known.values() if words & set(name_core(p['name'],scope).split())]
        unique={p['id']:p for p in pool}
        candidates=candidates_for(location,scope,publisher.get('places') or unique.values(),addresses) if query else []
        status,reason,place,method='review','Kein eindeutig passender Spielort gefunden.',None,None
        rule=reviewed.get(key)
        parent=confirmed_buildings.get((norm(building_label(location)),scope),{})
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
        elif re.search(r'\bonline\b',norm(location)) or norm(location) in {'digital','virtuell','livestream'}:
            status='online';reason='Als Online-Ort angegeben; kein physischer Kartenpunkt.'
        elif not scope:reason='Kein ausreichend bestimmter Ortskontext.'
        elif len(parent)==1:
            place=validate_place(deepcopy(next(iter(parent.values()))))
            status='resolved';method='building';reason='Bestätigtes Veranstaltungsgebäude; Kartenpunkt gilt für das Gebäude, nicht den einzelnen Saal.'
        elif len(addresses)>1:reason='Widersprüchliche Quellenadressen; Zuordnung bitte prüfen.'
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
                     'eventCount':len(current),'reviewNote':registry.get('notes',{}).get(key,{}).get('note',''),'sourceAddresses':addresses,
                     'organizer':current[0].get('organizer','') if norm(canonical_label(location)) in GENERIC else '',
                     'sourceContext':current[0]['sourceId'] if norm(canonical_label(location)) in GENERIC and not current[0].get('organizer') else None,
                     'sourceIds':sorted({e.get('librarySupplement') or e['sourceId'] for e in current}),
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


def review_problem(review,review_url,optional_sources=frozenset()):
    pending=sorted(r['key'] for r in review['rows'] if r['status']=='review'
                   and (not r.get('sourceIds') or set(r['sourceIds'])-optional_sources))
    if not pending:return None
    return {'key':'review:venues','kind':'review','sourceId':'venues','sourceName':'Ortszuordnung',
            'url':review_url,
            'reason':f'{len(pending)} unterschiedliche Ortsangaben brauchen eine Bestätigung. Bereits bestätigte Zuordnungen werden automatisch wiederverwendet. Prüfliste: /veranstaltungen/orte-pruefen.html',
            'candidateUrls':[], 'reviewFingerprint':hashlib.sha256('\n'.join(pending).encode()).hexdigest()}
