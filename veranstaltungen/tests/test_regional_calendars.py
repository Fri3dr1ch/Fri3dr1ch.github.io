import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import regional_calendars as regional
import import_local_calendars as imp

class RegionalTests(unittest.TestCase):
    def fixture(self,name):return (ROOT/'tests/fixtures'/name).read_bytes()

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
