#!/usr/bin/env python3
"""Build the venue review list or import one-time venue confirmations."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from import_groetzingen import ROOT
from venue_locations import enrich,read,render_review,write


def apply_decisions(root,payload,today,replace_reviewed=False):
    if payload.get('schemaVersion')!=1 or not isinstance(payload.get('decisions'),list):
        raise ValueError('Unbekanntes Entscheidungsformat')
    report=read(root/'dist/place-review.json',None)
    if report is None:raise ValueError('Aktuelle Ortsprüfliste fehlt')
    rows={r['key']:r for r in report['rows']}
    path=root/'data/venues/reviewed.json'
    registry=read(path,{'schemaVersion':1,'rules':[]})
    rules={r['key']:r for r in registry['rules']}
    notes=registry.get('notes',{})
    seen=set();applied=0
    for decision in payload['decisions']:
        key=decision['key']
        if key in seen:raise ValueError('Doppelte Entscheidung')
        seen.add(key)
        # Old browser exports must not undo already reviewed/corrected points.
        if key in rules and not replace_reviewed:continue
        row=rows.get(key)
        if not row or not row['location'] or any(decision.get(k)!=row[k] for k in ('location','scope')):
            raise ValueError('Ortskontext hat sich geändert; aktuelle Prüfliste verwenden')
        kind=decision['decision']
        applied+=1
        if kind=='note':
            if not str(decision.get('note') or '').strip():raise ValueError('Hinweis enthält keinen Text')
            notes[key]={'location':row['location'],'scope':row['scope'],'note':str(decision['note'])[:1000],'reviewedOn':today.isoformat()}
            continue
        rule={'key':key,'location':row['location'],'scope':row['scope'],'decision':kind,
              'organizer':row.get('organizer',''),'sourceContext':row.get('sourceContext'),
              'reviewedOn':today.isoformat(),'note':str(decision.get('note') or '')[:1000]}
        if kind=='place':
            candidates=[*row['candidates'],*([row['place']] if row.get('place') else [])]
            place=next((p for p in candidates if p['id']==decision.get('placeId')),None)
            if not place:raise ValueError('Gewählter Ortsvorschlag ist nicht mehr vorhanden')
            rule['place']={k:v for k,v in place.items() if k not in {'score','exact'}}
        elif kind!='unmapped':raise ValueError('Unbekannte Entscheidung')
        rules[key]=rule
        notes.pop(key,None)
    registry['rules']=sorted(rules.values(),key=lambda r:r['key'])
    registry['notes']=notes
    write(path,registry)
    return applied


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--online',action='store_true',help='Neue Ortsangaben begrenzt nachschlagen; bekannte Ergebnisse wiederverwenden')
    parser.add_argument('--max-new',type=int,default=None)
    parser.add_argument('--apply',type=Path,help='Aus der Prüfliste exportierte Entscheidungen importieren')
    parser.add_argument('--replace-reviewed',action='store_true',help='Bereits geprüfte Zuordnungen ausdrücklich ersetzen statt alte Exportauswahlen zu überspringen')
    args=parser.parse_args(argv)
    if args.max_new is not None and not 0<=args.max_new<=500:parser.error('--max-new muss zwischen 0 und 500 liegen')
    today=datetime.now(ZoneInfo('Europe/Berlin')).date()
    if args.replace_reviewed and not args.apply:parser.error('--replace-reviewed benötigt --apply')
    if args.apply:print('Übernommene Entscheidungen:',apply_decisions(args.root,json.loads(args.apply.read_text()),today,args.replace_reviewed))
    data=read(args.root/'dist/events.json',None)
    if data is None:raise ValueError('Kalenderdaten fehlen')
    review=enrich(data,args.root,today,online=args.online,budget=args.max_new)
    if review is None:raise ValueError('Ortskonfiguration fehlt')
    render_review(args.root,review)
    print(json.dumps(review['stats'],ensure_ascii=False))
    print('Prüfliste:',args.root/'dist/orte-pruefen.html')
    return 0


if __name__=='__main__':raise SystemExit(main())
