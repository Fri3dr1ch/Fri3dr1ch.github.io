import json
from datetime import date
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import import_local_calendars as imp
from import_groetzingen import build_dataset, NATURPARADIES_RSS_ALIAS, NATURPARADIES_RSS_CANONICAL
from calendar_sources import combine_local_sources,saved_sources

STAMP='2026-11-12T09:00:00+00:00'

class LocalCalendarTests(unittest.TestCase):
    def fixture(self,name):return (ROOT/'tests/fixtures'/name).read_bytes()

    def test_durlach_listing_retains_date_ranges_and_unknown_times(self):
        entries=imp.durlach_entries(self.fixture('durlach-list.html'))
        self.assertEqual(len(entries),33)
        market=next(e for e in entries if e['recordId']=='12026')
        record=imp.durlach_detail(self.fixture('durlach-range.html'),market)
        event=imp.event(imp.SOURCES['durlach'],record,STAMP)
        self.assertEqual((event['startDate'],event['endDate']),('2026-11-25','2026-12-21'))
        self.assertTrue(event['longRunning']);self.assertEqual(event['endTime'],'21:00')

    def test_durlach_details_use_event_heading_and_skip_other_headings(self):
        entry=next(e for e in imp.durlach_entries(self.fixture('durlach-list.html')) if e['recordId']=='12041')
        record=imp.durlach_detail(self.fixture('durlach-detail.html'),entry)
        self.assertEqual(record['title'],'Durlacher Lesecafé')
        self.assertEqual((record['startTime'],record['endTime']),('10:00','11:30'))
        self.assertIn('Pfinztalstraße 9',record['location'])

    def test_durlach_cancellation_is_not_lost(self):
        entry=next(e for e in imp.durlach_entries(self.fixture('durlach-list.html')) if e['recordId']=='11763')
        record=imp.durlach_detail(self.fixture('durlach-cancelled.html'),entry)
        self.assertEqual(imp.event(imp.SOURCES['durlach'],record,STAMP)['status'],'cancelled')

    def test_weingarten_fields_and_repeated_titles_have_distinct_occurrences(self):
        records=imp.weingarten_entries(self.fixture('weingarten-1.html'))
        self.assertEqual(len(records),10)
        first=records[0]
        self.assertEqual((first['startDate'],first['startTime'],first['endTime']),('2026-11-12','10:00','11:00'))
        self.assertEqual(first['location'],'im Allerdings Familienzentrum')
        self.assertEqual(first['organizer'],'Ortsseniorenrat Gemeinde Weingarten (Baden)')
        events=[imp.event(imp.SOURCES['weingarten'],r,STAMP) for r in records]
        choir=[e for e in events if 'Swinging' in e['title']]
        self.assertEqual(len(choir),3);self.assertEqual(len({e['id'] for e in choir}),3)

    def test_overnight_events_keep_explicit_end_date(self):
        records=imp.weingarten_entries(self.fixture('weingarten-11.html'))
        events=[imp.event(imp.SOURCES['weingarten'],r,STAMP) for r in records]
        fish=[e for e in events if "Fischer's Fest" in e['title']]
        self.assertEqual(len(fish),2)
        self.assertEqual([(e['startDate'],e['endDate']) for e in fish],[('2027-07-09','2027-07-10'),('2027-07-10','2027-07-11')])
        self.assertTrue(all(e['endTime']=='01:00' for e in fish))

    def test_all_advertised_weingarten_pages_are_fetched(self):
        raw=self.fixture('weingarten-1.html')+b'<a href="/freizeit-tourismus/veranstaltungen/seite-2/suche-none">2</a>'
        seen=[]
        def fetch(url):
            seen.append(url)
            if '/seite-' not in url:
                from urllib.parse import urlsplit
                record_id = urlsplit(url).path.rstrip('/').split('/')[-2]
                return f'<div id="hwveranstaltung__show__{record_id}"></div>'.encode()
            return raw if 'seite-1/' in url else self.fixture('weingarten-11.html')
        data=imp.fetch_source('weingarten',STAMP,fetch)
        self.assertEqual(len([url for url in seen if '/seite-' in url]),2)
        self.assertEqual(len(data['events']),20)
        self.assertEqual(data['source']['pagesFetched'],2)

    def test_location_refresh_combines_detail_fields_and_keeps_cached_places_on_error(self):
        base = 'https://www.walzbachtal.de/termin/Lesung-event-appointment123'
        raw = b'''<main><article class="card--horizontal"><h1>Lesung</h1></article>
            <div class="dgf-description-list location" hidden><dl><dd>Heimatstube</dd></dl></div></main>'''
        def records():
            return [{'url':base,'recordId':'123','startDate':'2026-12-12','location':''},
                    {'url':base,'recordId':'123','startDate':'2026-12-13','location':''},
                    {'url':base+'4','recordId':'1234','startDate':'2026-12-12','location':'Pfarrsaal'}]
        from detail_locations import enrich_locations
        items = records()
        seen = []
        def fetch(url):
            seen.append(url)
            return raw
        cached = enrich_locations('walzbachtal',items,fetch,STAMP,'2026-11-12',None)
        self.assertEqual(seen,[base])
        self.assertEqual([r['location'] for r in items],['Heimatstube','Heimatstube','Pfarrsaal'])
        def offline(url):
            seen.append(url)
            raise TimeoutError()
        fresh = records()
        enrich_locations('walzbachtal',fresh,offline,'2026-11-13T09:00:00+00:00','2026-11-13',
                         {'detailLocations':cached})
        self.assertEqual(seen,[base])
        enrich_locations('walzbachtal',fresh := records(),offline,'2026-11-22T09:00:00+00:00',
                         '2026-11-22',{'detailLocations':cached})
        self.assertEqual(seen,[base,base])
        self.assertEqual(fresh[0]['location'],'Heimatstube')

    def test_detail_location_parsers_keep_event_context(self):
        from detail_locations import eki_location,hagsfeld_location,weingarten_location,karlsbad_location
        from venue_locations import identity
        raw = b'''<h1 class="v-detail-titel">Treffen</h1><div class="v-detail-box-oertlichkeit">
            <div class="v-detail-less-content"><div class="ortsname">MLH</div></div></div>'''
        self.assertEqual(eki_location(raw, {}),'MLH')
        event = {'sourceId':'eki-groetzingen','region':'Grötzingen'}
        self.assertEqual(identity({**event,'location':'MLH'})[0],
                         identity({**event,'location':'Martin-Luther-Haus'})[0])
        self.assertEqual(hagsfeld_location(b'<main><div class="event layout_full"><p class="location">Laurentiuskirche</p></div></main>',{}),'Laurentiuskirche')
        raw = b'''<div id="hwveranstaltung__show__966"><div id="hw_map_location__966"
          data-strasse="Sohlsiedlung 7" data-plz="76356" data-ort="Weingarten"></div></div>
          <div id="hw_map_location__123" data-strasse="Rathausplatz"></div>'''
        self.assertEqual(weingarten_location(raw,{'recordId':'966'}),'Sohlsiedlung 7, 76356 Weingarten')
        self.assertEqual(weingarten_location(raw.replace(b'hw_map_location__966', b'hw_map_location__777'),
                                            {'recordId':'966'}),'')
        raw = b'''<div id="event_12550"><div itemprop="description"><p>Treffpunkt: Gemeindehaus</p></div></div>'''
        self.assertEqual(karlsbad_location(raw,{'url':'https://www.karlsbad.de/eventcalendar?event_id=12550'}),
                         'Gemeindehaus')

    def test_church_default_is_applied_only_to_missing_eki_locations(self):
        record = {'url':'https://eki-groetzingen.de/terminkalender/','recordId':'sample',
                  'title':'Treffen','startDate':'2026-12-01','startTime':'15:00','location':''}
        source = imp.SOURCES['eki-groetzingen']
        snapshot = imp.snapshot_from_records(source,[record,{**record,'recordId':'mlh','location':'MLH'}],STAMP)
        data = {'events':[],'sources':[],'importStats':{'inputRecords':0}}
        defaults = {e['sources'][0]['recordId']:e for e in combine_local_sources(data,[snapshot])['events']}
        self.assertEqual(defaults['sample']['location'],'Evangelische Kirche Grötzingen')
        self.assertEqual(defaults['sample']['sources'][0]['location'],'')
        self.assertEqual(defaults['mlh']['location'],'MLH')

    def test_failed_or_changed_source_preserves_previous_events_and_success_date(self):
        previous=json.loads((ROOT/'data/local-calendars/weingarten.json').read_text())
        for fetch in (lambda url:(_ for _ in ()).throw(TimeoutError('offline')),lambda url:b'<html><p>Layout changed</p></html>'):
            retained,error=imp.refresh('weingarten',STAMP,previous,fetch)
            self.assertTrue(error);self.assertEqual(retained['events'],previous['events'])
            self.assertEqual(retained['source']['lastSuccessAt'],previous['source']['lastSuccessAt'])
            self.assertEqual(retained['source']['lastAttemptAt'],STAMP)
            self.assertEqual(retained['source']['status'],'error')
            self.assertEqual(previous['source']['status'],'ok')

    def test_failed_initial_fetch_does_not_publish_an_empty_success(self):
        result,error=imp.refresh('durlach',STAMP,fetch=lambda url:b'<html>maintenance</html>')
        self.assertIsNone(result);self.assertTrue(error)

    def test_rebuild_preserves_old_identities_and_pdf_review_and_source_timestamp(self):
        data=imp.build_current(ROOT,STAMP);again=imp.build_current(ROOT,STAMP)
        self.assertEqual(data,again)
        old=json.loads((ROOT/'data/karlsruhe-rss.json').read_text())
        output_ids={e['id'] for e in data['events']}
        removed={e['id'] for e in old['events']}-output_ids
        self.assertTrue(removed<={NATURPARADIES_RSS_ALIAS})
        if removed:
            original=next(e for e in data['events'] if e['id']==NATURPARADIES_RSS_CANONICAL)
            self.assertIn(NATURPARADIES_RSS_ALIAS,{s.get('recordId') for s in original['sources']})
        self.assertEqual(len({e['id'] for e in data['events']}),len(data['events']))
        current=json.loads((ROOT/'dist/events.json').read_text())
        for name in ('groetzingen-jahresprogramm','groetzingen-kultur-natur'):
            self.assertEqual(next(s['lastSuccessAt'] for s in data['sources'] if s['id']==name),next(s['lastSuccessAt'] for s in current['sources'] if s['id']==name))
        self.assertEqual(data['maintenance'],current['maintenance'])
        self.assertEqual(len(data['sources']),3+len(saved_sources(ROOT)))

    def test_combined_calendar_recognizes_markets_across_sources(self):
        def event(title, source='karlsruhe', path='/db/termine/stadtleben/test', category='stadtleben'):
            return {'title':title, 'categoryKey':category,
                    'sources':[{'sourceId':source, 'url':'https://example.org'+path}]}

        markets = [
            event('Wochenmarkt Rüppurr'),
            event('Bücherflohmarkt', category='literatur'),
            event('Kloistoibacher Weihnachtsmärktle'),
            event('Radbasar'),
            {**event('Remchinger Weihnachtsdorf', 'remchingen'), 'sources':[
                *event('Remchinger Weihnachtsdorf', 'remchingen')['sources'],
                *event('Remchinger Weihnachtsdorf', 'kulturhalle-remchingen', '/events/maerkte/weihnachtsdorf')['sources'],
            ]},
        ]
        other = [event('Vortrag zum Arbeitsmarkt', category='literatur'), event('Konzert am Marktplatz', category='musik')]
        undated = event('Adventsmarkt')
        data = {'events':markets+other, 'undatedEvents':[undated], 'sources':[]}
        with patch.object(imp, 'combine_local_sources', return_value=data):
            result = imp.build_current(ROOT, STAMP)
        self.assertEqual([e['categoryKey'] for e in result['events']], ['maerkte']*5+['literatur','musik'])
        self.assertEqual((undated['categoryKey'], undated['category']), ('maerkte','Märkte'))

    def test_checked_overlaps_keep_provenance_and_real_time_conflicts(self):
        # Rolling source lists legitimately remove old dates. Test historical
        # disagreements against fixed source facts, independent of today's feed.
        base=json.loads(self.fixture('karlsruhe-snapshot-2026-11-12.json'))
        pdfs=[json.loads((ROOT/'data/groetzingen'/name).read_text()) for name in
              ('groetzingen-jahresprogramm-2026.json','groetzingen-kultur-natur-2026.json')]
        data=build_dataset(base,pdfs,STAMP,date(2026,11,12))
        cases=json.loads(self.fixture('durlach-merge-cases.json'))
        data=combine_local_sources(data,[cases])
        book=next(e for e in data['events'] if e['id']=='karlsruhe-36a6e4e681588441')
        self.assertEqual(len(book['sources']),2);self.assertEqual(book['startTime'],'15:30')
        self.assertEqual({s['value'] for s in book['conflicts'][0]['alternatives']},{'15:30','16:00'})
        hwk=next(e for e in data['events'] if e['id']=='karlsruhe-8e0b2415d47b375e')
        self.assertEqual(len(hwk['sources']),3);self.assertTrue(hwk['dailyTimes']);self.assertEqual(hwk['conflicts'],[])
        self.assertEqual({s['startDate'] for s in hwk['sources'] if s['sourceId']=='durlach'},{'2026-11-13','2026-11-14'})

    def test_same_name_and_day_at_different_venues_do_not_merge(self):
        empty={'events':[],'sources':[],'undatedEvents':[],'importStats':{'inputRecords':0}}
        record={'recordId':'venue-case','title':'Öffentlicher Vortrag','startDate':'2026-11-15',
                'startTime':'11:00','location':'Bürgersaal','url':'https://www.durlacher.de/'}
        a=imp.event(imp.SOURCES['durlach'],record,STAMP)
        b=imp.event(imp.SOURCES['weingarten'],{**record,'location':'Gemeindesaal'},STAMP)
        snapshots=[{'source':imp.SOURCES['durlach'],'events':[a]},
                   {'source':imp.SOURCES['weingarten'],'events':[b]}]
        self.assertEqual(len(combine_local_sources(empty,snapshots)['events']),2)
        # Positive control: otherwise identical reports at the same venue merge.
        b['location']=a['location']
        b['region']=a['region']
        self.assertEqual(len(combine_local_sources(empty,snapshots)['events']),1)

    def test_unknown_time_is_not_claimed_to_be_all_day(self):
        record={'recordId':'unknown-time','title':'Termin ohne Uhrzeit','startDate':'2026-11-15',
                'location':'Karlsburg','url':'https://www.durlacher.de/durlacher-kalender'}
        event=imp.event(imp.SOURCES['durlach'],record,STAMP)
        self.assertIsNone(event['startTime'])
        self.assertFalse(event['timeKnown']);self.assertFalse(event['allDay'])

if __name__=='__main__':unittest.main()
