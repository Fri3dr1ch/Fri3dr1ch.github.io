from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from venue_locations import (candidates_for,enrich,identity,parse_photon,query_for,read,render_review,
                             review_problem,validate_place,walzbachtal_places,write)
from review_venues import apply_decisions
from sync_github_issues import contents
import update_calendar

TODAY=date(2026,11,15)


def event(location='Kulturhalle Remchingen',region='Remchingen',sid='remchingen',eid='one'):
    return {'id':eid,'sourceId':sid,'sourceName':'Quelle '+sid,'location':location,'region':region,'title':'Konzert',
            'startDate':'2026-11-16','endDate':'2026-11-16','url':'https://www.remchingen.de/veranstaltungen/',
            'sources':[{'sourceId':sid,'url':'https://www.remchingen.de/veranstaltungen/'}]}


def point(name='Kulturhalle Remchingen',pid='osm-way-108146379',lat=48.9528061,lon=8.5701225):
    return {'id':pid,'name':name,'latitude':lat,'longitude':lon,'address':'San-Biagio-Platani-Platz 10, 75196 Remchingen',
            'url':'https://www.openstreetmap.org/way/108146379','category':'amenity','type':'community_centre',
            'street':'San-Biagio-Platani-Platz','houseNumber':'10'}


class VenueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'dist').mkdir();(self.root/'scripts').mkdir()
        (self.root/'scripts/place_review.html').write_text((ROOT/'scripts/place_review.html').read_text())
        write(self.root/'data/venues/config.json',{'enabled':True,'endpoint':'https://photon.komoot.io/api/','maxNewPerRun':2,'reviewUrl':'https://example.org/orte-pruefen.html'})

    def build(self,events,**kwargs):
        data={'events':deepcopy(events),'undatedEvents':[],'generatedAt':'2026-11-15T08:00:00Z'}
        return data,enrich(data,self.root,TODAY,**kwargs)

    def cache(self,e,points):
        write(self.root/'data/venues/lookup-cache.json',{'schemaVersion':1,'queries':{query_for(e['location'],identity(e)[2]):{'checkedAt':'2026-11-15T07:00:00Z','places':points}}})

    def test_same_venue_new_dates_and_publishers_reuse_one_lookup_without_changing_event_facts(self):
        a=event();b={**a,'id':'two','sourceId':'other','sourceName':'Other','startDate':'2026-12-16','endDate':'2026-12-16'}
        calls=[]
        def fetch(q,s):calls.append((q,s));return [point()]
        data,review=self.build([a,b],online=True,fetch=fetch)
        self.assertEqual(len(calls),1);self.assertEqual(review['stats']['venues'],1)
        for original,result in zip([a,b],data['events']):
            self.assertEqual({k:v for k,v in result.items() if k!='venue'},original)
            self.assertEqual(result['venue']['status'],'resolved')
        self.build([a,b],online=True,fetch=lambda *_:self.fail('known place queried again'))

    def test_identical_generic_venue_names_in_different_towns_and_unknown_contexts_never_auto_pin(self):
        a=event('Hasenheim','Grötzingen');b=event('Hasenheim','Pfinztal-Wöschbach',eid='two')
        self.assertNotEqual(identity(a)[0],identity(b)[0])
        self.assertNotEqual(identity({**a,'organizer':'Verein A'})[0],identity({**a,'organizer':'Verein B'})[0])
        p=point('Hasenheim',lat=49.02,lon=8.50)
        self.cache(a,[p]);data,review=self.build([a,b,event('Kulturhalle','Nicht bekannt',eid='three')])
        self.assertTrue(all(e['venue']['place'] is None for e in data['events']))

    def test_only_exact_full_address_can_resolve_and_ambiguous_objects_stay_open(self):
        for name in ('San-Biagio-Platani-Platz 10','San-Biagio-Platani-Platz'):
            e=event(name);self.cache(e,[point('Anderer Name')]);data,_=self.build([e])
            self.assertEqual(data['events'][0]['venue']['status'],'resolved' if name.endswith(' 10') else 'review')
        e=event();self.cache(e,[point(),point(pid='osm-node-other')]);data,_=self.build([e])
        self.assertEqual(data['events'][0]['venue']['status'],'review')

    def test_missing_online_and_multiple_possible_places_have_no_coordinates(self):
        items=[event('',eid='one'),event('Online-Veranstaltung',eid='two'),event('Rathaus oder Wartturm',eid='three')]
        self.cache(items[2],[point('Rathaus oder Wartturm')]);data,_=self.build(items)
        self.assertEqual([e['venue']['status'] for e in data['events']],['missing','online','review'])
        self.assertTrue(all(e['venue']['place'] is None for e in data['events']))

    def test_known_place_is_reused_offline_for_another_query_and_weak_unrelated_names_are_not_suggested(self):
        e=event();other=event('Halle im Ort');self.cache(other,[point()])
        data,_=self.build([e]);self.assertEqual(data['events'][0]['venue']['place']['id'],point()['id'])
        self.assertEqual(candidates_for('Imagine, Schauspielschule für Kinder und Jugendliche','karlsruhe',[
            {**point('Praxis für Kinder und Jugendlichenpsychotherapie'), 'latitude':49.01,'longitude':8.4}]),[])

    def test_confirmation_of_a_generic_clubhouse_does_not_assign_another_club(self):
        a={**event('Clubhaus','Pfinztal-Wöschbach'),'organizer':'Verein A'}
        b={**a,'id':'different-club','organizer':'Verein B'}
        p={**point('Clubhaus',lat=49.015,lon=8.59),'address':'Wöschbach'}
        self.cache(a,[p]);_,review=self.build([a,b]);render_review(self.root,review)
        row=next(r for r in review['rows'] if r['organizer']=='Verein A')
        d={k:row[k] for k in ('key','location','scope')};d.update(decision='place',placeId=p['id'])
        apply_decisions(self.root,{'schemaVersion':1,'decisions':[d]},TODAY)
        data,_=self.build([a,b]);self.assertEqual([e['venue']['status'] for e in data['events']],['resolved','review'])

    def test_source_coordinates_are_preferred_and_out_of_context_source_points_need_review(self):
        e=event('Jahnhalle Jöhlingen, Walzbachtal','Walzbachtal-Jöhlingen','walzbachtal');e['url']='https://www.walzbachtal.de/veranstaltung/test-event-appointment1'
        p={**point('Jahnhalle Jöhlingen',lat=49.0267938,lon=8.5820269),'provenance':'publisher'}
        write(self.root/'data/venues/publisher-cache.json',{'schemaVersion':1,'locations':{identity(e)[0]:{'checkedAt':'2026-11-15T07:00:00Z','places':[p]}}})
        data,_=self.build([e]);self.assertEqual(data['events'][0]['venue']['method'],'publisher')
        p['longitude']=8.545
        write(self.root/'data/venues/publisher-cache.json',{'schemaVersion':1,'locations':{identity(e)[0]:{'checkedAt':'2026-11-15T07:00:00Z','places':[p]}}})
        data,review=self.build([e]);self.assertIsNone(data['events'][0]['venue']['place']);self.assertTrue(review['rows'][0]['candidates'][0]['outsideContext'])

    def test_parser_checks_source_json_and_osm_coordinates(self):
        data={'baseLocations':[{'title':'Jahnhalle','url':'https://www.walzbachtal.de/adresse/Jahnhalle-address1','geoPosition':{'latitude':49.026,'longitude':8.582}}]}
        raw='const config = {baseLocations: '+json.dumps(data['baseLocations'])+', mapCenter:null};'
        self.assertEqual(walzbachtal_places(raw,'https://www.walzbachtal.de/veranstaltung/test')[0]['latitude'],49.026)
        with self.assertRaises(ValueError):walzbachtal_places(raw.replace('49.026','999'),'url')
        with self.assertRaises(ValueError):parse_photon('{"type":"not-a-feature-collection"}')
        with self.assertRaises(ValueError):validate_place({**point(),'latitude':float('nan')})

    def test_lookup_budget_empty_cache_and_error_backoff_are_bounded(self):
        items=[event('Ort '+str(i),eid=str(i)) for i in range(5)];calls=[]
        def fetch(q,s):calls.append(q);return []
        _,review=self.build(items,online=True,fetch=fetch);self.assertEqual(len(calls),2)
        self.assertEqual(sum(r['status']=='queued' for r in review['rows']),3)
        self.build(items,online=True,budget=0,fetch=fetch);self.assertEqual(len(calls),2)
        # Empty successful answers are cached too; no repeated query next run.
        cached=read(self.root/'data/venues/lookup-cache.json',{})['queries'];before=len(calls)
        known=[e for e in items if query_for(e['location'],identity(e)[2]) in cached]
        self.build(known,online=True,fetch=fetch);self.assertEqual(len(calls),before)
        def fail(*_):raise TimeoutError('offline')
        fresh=[event('New '+str(i),eid=str(i)) for i in range(5)]
        _,review=self.build(fresh,online=True,budget=20,fetch=fail)
        self.assertEqual(review['lookup']['requested'],2);self.assertEqual(len(review['lookup']['failures']),2)
        errors={q for q,v in read(self.root/'data/venues/lookup-cache.json',{})['queries'].items() if 'error' in v}
        _,review=self.build([e for e in fresh if query_for(e['location'],identity(e)[2]) in errors],online=True,fetch=fail)
        self.assertEqual(review['lookup']['requested'],0)

    def test_one_time_confirmation_survives_missing_lookup_cache_and_new_occurrences(self):
        e=event('Kulturhalle Remchingen, Foyer');self.cache(e,[point()]);data,review=self.build([e]);render_review(self.root,review)
        row=review['rows'][0];decision={k:row[k] for k in ('key','location','scope')}
        decision.update(decision='place',placeId=point()['id'])
        apply_decisions(self.root,{'schemaVersion':1,'decisions':[decision]},TODAY)
        (self.root/'data/venues/lookup-cache.json').unlink()
        data,_=self.build([{**e,'id':'future','startDate':'2027-01-01','endDate':'2027-01-01'}],online=True,fetch=lambda *_:self.fail('confirmed venue queried'))
        self.assertEqual(data['events'][0]['venue']['method'],'reviewed')
        self.assertEqual(data['events'][0]['venue']['place']['id'],point()['id'])

    def test_decision_import_rejects_stale_unknown_or_missing_context_atomically(self):
        e=event('Kulturhalle Remchingen, Foyer');self.cache(e,[point()]);_,review=self.build([e,event('',eid='missing')]);render_review(self.root,review)
        row=next(r for r in review['rows'] if r['location']);d={k:row[k] for k in ('key','location','scope')};d.update(decision='place',placeId=point()['id'])
        for invalid in ({**d,'scope':'groetzingen'},{**d,'placeId':'unknown'}, {**d,'key':'unknown'}):
            with self.assertRaises(ValueError):apply_decisions(self.root,{'schemaVersion':1,'decisions':[d,invalid]},TODAY)
            self.assertFalse((self.root/'data/venues/reviewed.json').exists())
        missing=next(r for r in review['rows'] if not r['location']);d={k:missing[k] for k in ('key','location','scope')};d['decision']='unmapped'
        with self.assertRaises(ValueError):apply_decisions(self.root,{'schemaVersion':1,'decisions':[d]},TODAY)

    def test_programme_suffixes_share_a_building_but_branches_and_rooms_remain_distinct(self):
        base='Stadtbibliothek Karlsruhe - Zentralbibliothek im Neuen Ständehaus'
        self.assertEqual(identity(event(base+' - Buchclub','Karlsruhe und Umgebung'))[0],identity(event(base+' - Aktionswoche','Karlsruhe und Umgebung'))[0])
        self.assertNotEqual(identity(event('Badisches Staatstheater, INSEL'))[0],identity(event('Badisches Staatstheater, Großes Haus'))[0])
        a=event('Stadtbibliothek Karlsruhe - Stadtteilbibliothek Durlach','Durlach')
        p=point('Stadtteilbibliothek Durlach',lat=49.001,lon=8.472)
        self.assertTrue(candidates_for(a['location'],'durlach',[p])[0]['exact'])
        self.assertFalse(candidates_for('Stadtbibliothek Karlsruhe - Stadtteilbibliothek Grötzingen','durlach',[p])[0]['exact'])
        z=point('ZKM | Zentrum für Kunst und Medien',lat=49.002,lon=8.39)
        self.assertTrue(candidates_for('Zentrum für Kunst und Medien (ZKM)','karlsruhe',[z])[0]['exact'])

    def test_notes_stay_pending_and_issue_fingerprint_only_changes_for_different_venue_keys(self):
        e=event('Unklarer Ort');self.cache(e,[]);_,review=self.build([e]);render_review(self.root,review)
        row=review['rows'][0];d={k:row[k] for k in ('key','location','scope')};d.update(decision='note',note='Gemeint ist Hauptstraße 1')
        apply_decisions(self.root,{'schemaVersion':1,'decisions':[d]},TODAY)
        _,again=self.build([e]);self.assertEqual(again['rows'][0]['status'],'review');self.assertIn('Hauptstraße',again['rows'][0]['reviewNote'])
        p=review_problem(review,'https://example.org/orte-pruefen.html');q=review_problem(again,'https://example.org/orte-pruefen.html')
        self.assertEqual(contents(p)[2],contents(q)[2])
        other=event('Anderer Ort');self.cache(other,[])
        _,different=self.build([other]);self.assertNotEqual(contents(p)[2],contents(review_problem(different,'url'))[2])

    def test_offline_pipeline_preserves_all_events_and_adds_review_to_existing_issue_flow(self):
        original=json.loads((ROOT/'dist/events.json').read_text());(self.root/'dist/index.html').write_text((ROOT/'dist/index.html').read_text())
        write(self.root/'dist/events.json',original)
        with patch.object(update_calendar,'build_current',return_value=deepcopy(original)):
            report=update_calendar.run(self.root,offline=True)
        actual=read(self.root/'dist/events.json',{})
        self.assertEqual([e['id'] for e in actual['events']],[e['id'] for e in original['events']])
        self.assertTrue(report['validationPassed']);self.assertIn('review:venues',[p['key'] for p in report['issues']])
        self.assertTrue((self.root/'dist/orte-pruefen.html').exists())


if __name__=='__main__':unittest.main()
