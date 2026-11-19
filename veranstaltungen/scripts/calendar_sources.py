"""Combine saved regional sources without changing the reviewed PDF import."""
from copy import deepcopy
from collections import defaultdict
from datetime import datetime
import json
import re
import unicodedata
from zoneinfo import ZoneInfo

PUBLISHER_SOURCE_IDS=('naturtreff-groetzingen','vhs-karlsruhe-land-pfinztal',
                      'vhs-karlsruhe-land-weingarten','vhs-karlsruhe-land-walzbachtal',
                      'hagsfeld','stutensee','bergdoerfer','karlsbad','koenigsbach-stein')
LOCAL_SOURCE_IDS=('durlach','weingarten','pfinztal','pfinztal-vereine','remchingen',
                  'kulturhalle-remchingen','eki-groetzingen','pfinztal-haus-begegnungen',
                  'naturfreunde-berghausen','naturfreunde-soellingen','naturfreunde-groetzingen','walzbachtal',
                  'waldpaedagogik-karlsruhe',*PUBLISHER_SOURCE_IDS)

def normalized(text):
    text=unicodedata.normalize('NFKD',(text or '').replace('ß','ss')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',text).strip()


GENERIC_VENUES={'hasenheim','gemeindehaus','evang gemeindehaus','evangelisches gemeindehaus','naturfreundehaus',
                'clubhaus','kirche','sporthalle','turnhalle','kulturhalle','friedhof','friedhofskapelle','pfarrsaal','sangerheim','burgersaal'}


def is_duplicate(a,b):
    """Automatic matches require positive evidence; similarity alone never merges."""
    if {s['sourceId'] for s in a['sources']} & {s['sourceId'] for s in b['sources']}:return False
    if not a.get('startTime') or a.get('startTime')!=b.get('startTime'):return False
    if a['startDate']!=b['startDate'] or a['endDate']!=b['endDate']:return False
    if a.get('endTime') and b.get('endTime') and a['endTime']!=b['endTime']:return False
    if normalized(a['title'].replace('&',' und '))!=normalized(b['title'].replace('&',' und ')):return False
    aliases={'karlsburg stadtbibliothek pfinztalstrasse 9 76227 karlsruhe':'bibliothek durlach',
             'stadtbibliothek karlsruhe stadtteilbibliothek durlach':'bibliothek durlach'}
    va=aliases.get(normalized(a.get('location')),normalized(a.get('location')))
    vb=aliases.get(normalized(b.get('location')),normalized(b.get('location')))
    if not va or sorted(va.split())!=sorted(vb.split()):return False
    if va in GENERIC_VENUES or vb in GENERIC_VENUES:
        scope=lambda e:normalized(e.get('region')).removesuffix(' und umgebung')
        if not scope(a) or scope(a)!=scope(b):return False
    oa,ob=normalized(a.get('organizer')),normalized(b.get('organizer'))
    if oa and ob and oa!=ob:return False
    if any(s['sourceId']=='pfinztal-vereine' for e in (a,b) for s in e['sources']) and (not oa or oa!=ob):return False
    return True


def reviewed_match_valid(rule,target,incoming):
    if target['startDate']!=incoming['startDate'] or target['endDate']!=incoming['endDate']:return False
    titles={normalized(s.get('reportedTitle') or s.get('title')) for s in target['sources']}|{normalized(target['title'])}
    if rule.get('targetTitle') and normalized(rule['targetTitle']) not in titles:return False
    if rule.get('reportedTitle') and normalized(rule['reportedTitle'])!=normalized(incoming['title']):return False
    for prefix,event in (('target',target),('reported',incoming)):
        for field in ('StartTime','Location'):
            key=prefix+field
            if key in rule:
                actual=event.get(field[0].lower()+field[1:])
                if (normalized(actual) if field=='Location' else actual)!=(normalized(rule[key]) if field=='Location' else rule[key]):return False
    return True

def saved_sources(root):
    rules_path=root/'data/reviewed-overlaps.json'
    rules=json.loads(rules_path.read_text()) if rules_path.exists() else []
    snapshots=[]
    for name in LOCAL_SOURCE_IDS:
        path=root/'data/local-calendars'/(name+'.json')
        if path.exists():
            snapshot=json.loads(path.read_text())
            snapshot['reviewedMatches']=[r for r in rules if r['sourceId']==name]
            snapshots.append(snapshot)
    return snapshots

def combine_local_sources(data,snapshots):
    result=deepcopy(data)
    events=result['events']
    by_day=defaultdict(list)
    by_id={e['id']:e for e in events}
    for e in events:by_day[e['startDate']].append(e)
    # An individually checked overlap: same workshop, venue and day, but the
    # publishers disagree on its start time. Preserve both reported times.
    reviewed_matches={('durlach','12042','2026-11-12'):'karlsruhe-36a6e4e681588441',
                      ('durlach','12041','2026-11-18'):'karlsruhe-26f1d703c8e58795'}
    for snapshot in snapshots:
        result['sources'].append(deepcopy(snapshot['source']))
        if snapshot['source']['id']=='naturfreunde-groetzingen':
            s=snapshot['source']
            result.setdefault('maintenance',{})['naturfreunde-groetzingen']={
                'lastReviewedAt':s['lastReviewedAt'],'nextReviewOn':s['nextReviewOn'],
                'reviewRequired':s.get('reviewRequired',False),'url':s['url'],
                'note':'Monatlich aktuelle Meldungen und Bildaushänge prüfen; neue Termine erst nach Kontrolle übernehmen.'}
        result.setdefault('undatedEvents',[]).extend(deepcopy(snapshot.get('undatedEvents',[])))
        # Karlsruhe publishes one two-day HWK market; Durlach lists each day's
        # opening separately. Only merge this checked pair if both days and
        # their equal opening hours are still present in the source snapshot.
        hwk=[e for e in snapshot['events'] if e['sources'][0]['sourceId']=='durlach'
             and e['sources'][0]['recordId'] in ('12081','12082')]
        daily_pair=(len(hwk)==2 and {e['startDate'] for e in hwk}=={'2026-11-13','2026-11-14'}
                    and all(e['startDate']==e['endDate'] and e['startTime']=='08:00' and e['endTime']=='18:00' for e in hwk))
        for incoming in snapshot['events']:
            event=deepcopy(incoming)
            prov=event['sources'][0]
            reviewed=reviewed_matches.get((prov['sourceId'],prov['recordId'],event['startDate']))
            rule=next((r for r in snapshot.get('reviewedMatches',[]) if r['recordId']==prov['recordId'] and r['startDate']==event['startDate']),None)
            if rule:reviewed=rule['targetId']
            if rule and reviewed in by_id and not reviewed_match_valid(rule,by_id[reviewed],event):
                rule=None;reviewed=None
            if reviewed in by_id and not rule and by_id[reviewed]['startDate']!=event['startDate']:reviewed=None
            if daily_pair and prov['recordId'] in ('12081','12082'):
                reviewed='karlsruhe-8e0b2415d47b375e'
                prov['coverageRelation']='daily-opening'
            candidates=([by_id[reviewed]] if reviewed in by_id else []) if reviewed else [e for e in by_day[event['startDate']] if is_duplicate(e,event)]
            if len(candidates)!=1:
                events.append(event);by_id[event['id']]=event;by_day[event['startDate']].append(event);continue
            winner=candidates[0]
            winner['sources'].extend(event['sources'])
            if rule and rule.get('preferIncoming'):
                # Keep the incumbent identity and its more detailed venue/title,
                # but prefer the current organizer's date/time and original link.
                for field in ('sourceId','sourceName','url','startTime','endTime','start','end','categoryKey','category','dataQuality','detailCheckedAt'):
                    if event.get(field) is not None:winner[field]=event[field]
            if event.get('availability')=='sold-out':winner['availability']='sold-out'
            if prov.get('coverageRelation')=='daily-opening':
                winner['dailyTimes']=True
                winner['scheduleNote']='An beiden Tagen jeweils 08:00–18:00 Uhr laut Durlacher Kalender.'
            for field in ('location','organizer','description','startTime','endTime'):
                if not winner.get(field) and event.get(field):winner[field]=event[field]
            if event['status']=='cancelled':winner['status']='cancelled'
            winner['timeKnown']=bool(winner.get('startTime'))
            for clock,iso,day in [('startTime','start','startDate'),('endTime','end','endDate')]:
                if winner.get(clock):winner[iso]=datetime.fromisoformat(winner[day]+'T'+winner[clock]).replace(tzinfo=ZoneInfo('Europe/Berlin')).isoformat()
            conflicts=[]
            for field in ('startTime','endTime','endDate'):
                reports=[s for s in winner['sources'] if s.get(field) and not (field=='endDate' and s.get('coverageRelation')=='daily-opening')]
                if len({s[field] for s in reports})>1:
                    conflicts.append({'field':field,'selected':winner.get(field),'alternatives':[
                        {'sourceName':s['sourceName'],'value':s[field]} for s in reports]})
            winner['conflicts']=conflicts
    result['events']=sorted(events,key=lambda e:(e['startDate'],e.get('startTime') or '99:99',e['title'].lower()))
    inputs=data['importStats']['inputRecords']+sum(len(s['events']) for s in snapshots)
    result['importStats']={'inputRecords':inputs,'eventCount':len(events),
        'deduplicatedRecords':inputs-len(events),'undatedCount':len(result.get('undatedEvents',[]))}
    return result
