import json
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import regional_calendars as regional
import import_local_calendars as imp
from urllib.parse import parse_qs,urlsplit
from lxml import etree,html

class RegionalTests(unittest.TestCase):
    def fixture(self,name):return (ROOT/'tests/fixtures'/name).read_bytes()

    def remchingen_page(self,number,shifted=False,total=10,missing=False):
        pages=[regional.doc(self.fixture(name)).xpath('//section') for name in
               ('regional-remchingen.html','regional-remchingen-2.html')]
        if shifted:
            # An expired first event shifts every page boundary by one row.
            extra=deepcopy(pages[1][-1]);anchor=extra.xpath('./a[h2]')[0]
            anchor.set('href',anchor.get('href').replace('-id_3294/','-id_9999/'))
            pages=[pages[0][1:]+pages[1][:1],pages[1][1:]+[extra]]
        tree=html.Element('main')
        for node in pages[number-1][:-1] if missing else pages[number-1]:tree.append(node)
        anchor=etree.SubElement(tree,'a',href='/veranstaltungen/?seite=2');anchor.text='2'
        # The same reference-table shape as the live public Nuxt metadata.
        table=[{'veranstaltungen':1,'filterVeranstaltungen':2},
               {'data':2,'anzahl':3,'seiten':4},[],total,2,
               {'endpoint':6,'params':7},'/api/cms/veranstaltungen',
               {'seite':8,'pro_seite':9},number,5]
        script=etree.SubElement(tree,'script',id='__NUXT_DATA__',type='application/json')
        script.text=json.dumps(table)
        return html.tostring(tree,encoding='utf-8')

    def test_remchingen_uses_utf8_date_fields_and_preserves_sold_out(self):
        records,pages=regional.remchingen_listing(self.fixture('regional-remchingen.html'))
        self.assertEqual(pages,2);self.assertEqual(len(records),5)
        repair=next(r for r in records if r['recordId']=='3347')
        self.assertEqual(repair['title'],'Repair-Café')
        self.assertEqual((repair['startTime'],repair['endTime']),('15:00','18:00'))
        self.assertEqual(repair['location'],'Alte RemBar (Altes Rathaus Nöttingen)')
        sold=next(r for r in records if r['recordId']=='3172')
        self.assertEqual(sold['availability'],'sold-out');self.assertEqual(sold['organizer'],'Kulturhalle Remchingen')

    def test_remchingen_loads_all_advertised_pages_and_rejects_ignored_pagination(self):
        seen=[]
        def fetch(url):
            seen.append(url)
            return self.fixture('regional-remchingen-2.html' if '?seite=2' in url else 'regional-remchingen.html')
        records,metadata=regional.fetch_remchingen(fetch)
        self.assertEqual(len(records),10);self.assertEqual(metadata['pagesFetched'],2);self.assertEqual(len(seen),2)
        with self.assertRaisesRegex(ValueError,'wiederholen'):
            regional.fetch_remchingen(lambda url:self.fixture('regional-remchingen.html'))

    def test_remchingen_retries_the_whole_list_when_cached_page_boundaries_overlap(self):
        seen=[]
        def fetch(url):
            query=parse_qs(urlsplit(url).query);number=int(query.get('seite',['1'])[0])
            seen.append(query)
            # The first page has expired an event, while page 2 is still cached.
            return self.remchingen_page(number,shifted=number==1 or '_calendar_refresh' in query)
        records,metadata=regional.fetch_remchingen(fetch)
        self.assertEqual(len(records),10);self.assertEqual(metadata['pagesFetched'],2)
        self.assertEqual(len({r['recordId'] for r in records}),10)
        self.assertIn('9999',{r['recordId'] for r in records})
        self.assertEqual(len(seen),4)
        self.assertNotIn('_calendar_refresh',seen[0]);self.assertNotIn('_calendar_refresh',seen[1])
        self.assertEqual(seen[2]['_calendar_refresh'],seen[3]['_calendar_refresh'])
        self.assertEqual([q['seite'] for q in seen[2:]],[['1'],['2']])

    def test_remchingen_retries_changed_totals_even_without_duplicate_rows(self):
        seen=[]
        def fetch(url):
            query=parse_qs(urlsplit(url).query);number=int(query.get('seite',['1'])[0]);seen.append(query)
            return self.remchingen_page(number,total=9 if number==2 and '_calendar_refresh' not in query else 10)
        records,_=regional.fetch_remchingen(fetch)
        self.assertEqual(len(records),10);self.assertEqual(len(seen),4)

    def test_remchingen_persistent_gaps_keep_the_last_valid_snapshot(self):
        records=regional.remchingen_listing(self.remchingen_page(1))[0]+regional.remchingen_listing(self.remchingen_page(2))[0]
        previous=imp.snapshot_from_records(imp.SOURCES['remchingen'],records,'2026-11-14T15:00:00Z')
        seen=[]
        def fetch(url):
            query=parse_qs(urlsplit(url).query);number=int(query.get('seite',['1'])[0]);seen.append(query)
            return self.remchingen_page(number,missing=number==2)
        retained,error=imp.refresh('remchingen','2026-11-14T20:00:00Z',previous,fetch)
        self.assertIn('unvollständige Ergebnisseite',error);self.assertEqual(len(seen),4)
        self.assertEqual(retained['source']['status'],'error')
        self.assertEqual(retained['source']['lastSuccessAt'],previous['source']['lastSuccessAt'])
        self.assertEqual(retained['events'],previous['events'])

    def test_remchingen_ignored_pagination_still_fails_after_one_fresh_retry(self):
        seen=[]
        def fetch(url):seen.append(url);return self.remchingen_page(1)
        with self.assertRaisesRegex(ValueError,'Seitennummer'):
            regional.fetch_remchingen(fetch)
        self.assertEqual(len(seen),4)

    def test_remchingen_malformed_public_counts_are_not_silently_ignored(self):
        raw=self.remchingen_page(1).replace(b'"anzahl": 3',b'"anzahl": -1')
        with self.assertRaisesRegex(ValueError,'Seitenmetadaten'):
            regional.fetch_remchingen(lambda url:raw)

    def test_kulturhalle_respects_structured_year_and_separate_performances(self):
        records=regional.kulturhalle_listing(self.fixture('kulturhalle-list.html'))
        dance=[r for r in records if r['recordId'] in ('dance-2027','dance-2028')]
        self.assertEqual(len(dance),2)
        self.assertEqual({r['startDate'] for r in dance},{'2027-06-05'})
        self.assertEqual({r['startTime'] for r in dance},{'10:00','15:00'})

    def test_kulturhalle_actual_venue_is_not_assumed_to_be_main_hall(self):
        entry=next(r for r in regional.kulturhalle_listing(self.fixture('kulturhalle-list.html')) if r['recordId']=='sing-a-song-2025')
        record=regional.kulturhalle_detail(self.fixture('kulturhalle-detail.html'),entry)
        self.assertEqual(record['location'],'Löwensaal Nöttingen')
        with self.assertRaisesRegex(ValueError,'Detaildatum'):
            regional.kulturhalle_detail(self.fixture('kulturhalle-detail.html').replace(b'2026-11-20',b'2025-11-20'),entry)

    def test_unknown_html_is_a_failed_import(self):
        for parser in (regional.remchingen_listing,regional.kulturhalle_listing):
            with self.assertRaises(ValueError):parser(b'<main>Unexpected markup</main>')

    def test_merged_groetzingen_memorial_uses_corrected_organizer_time(self):
        data=imp.build_current(ROOT,'2026-11-12T15:00:00+00:00')
        event=next(e for e in data['events'] if e['id']=='groetzingen-jahresprogramm-annual-2026-130')
        self.assertEqual(event['startTime'],'11:30');self.assertEqual(event['sourceId'],'eki-groetzingen')
        self.assertEqual({r['value'] for r in event['conflicts'][0]['alternatives']},{'11:00','11:30'})

    def test_culturehall_duplicates_merge_without_merging_simultaneous_council(self):
        data=imp.build_current(ROOT,'2026-11-12T15:00:00+00:00')
        sing=[e for e in data['events'] if 'Sing A Song' in e['title'] and e['startDate']=='2026-11-20']
        self.assertEqual(len(sing),1);self.assertEqual(len(sing[0]['sources']),2)
        self.assertEqual(sing[0]['availability'],'sold-out')
        gospel=[e for e in data['events'] if 'Black Gospel' in e['title']]
        council=[e for e in data['events'] if e['startDate']=='2026-12-17' and 'Gemeinderatssitzung' in e['title']]
        self.assertEqual(len(gospel),1);self.assertEqual(len(council),1);self.assertNotEqual(gospel[0]['id'],council[0]['id'])

    def test_confirmed_empty_source_is_retained_and_pdf_ambiguity_stays_separate(self):
        data=imp.build_current(ROOT,'2026-11-12T15:00:00+00:00')
        source=next(s for s in data['sources'] if s['id']=='pfinztal-haus-begegnungen')
        self.assertEqual(source['status'],'ok');self.assertEqual(source['recordCount'],0)
        unknown=[e for e in data['undatedEvents'] if e['sourceId']=='pfinztal-vereine']
        self.assertEqual(len(unknown),9)
        self.assertTrue(all('startDate' not in e for e in unknown))
        self.assertTrue(any('2025' in e['uncertaintyReason'] for e in unknown))

if __name__=='__main__':unittest.main()
