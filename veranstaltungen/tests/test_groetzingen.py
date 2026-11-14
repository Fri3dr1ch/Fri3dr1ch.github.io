import importlib.util,json,unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('groetzingen_import',ROOT/'scripts/import_groetzingen.py')
imp=importlib.util.module_from_spec(spec);spec.loader.exec_module(imp)

class GroetzingenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=json.loads((ROOT/'tests/fixtures/karlsruhe-snapshot-2026-11-12.json').read_text())
        cls.snapshots=[json.loads((ROOT/'data/groetzingen'/name).read_text()) for name in ['groetzingen-jahresprogramm-2026.json','groetzingen-kultur-natur-2026.json']]
        cls.data=imp.build_dataset(cls.base,cls.snapshots,'2026-11-12T08:00:00+00:00',date(2026,11,12))

    def find(self,text,day):
        return [e for e in self.data['events'] if text.lower() in e['title'].lower() and e['startDate']==day]

    def test_all_original_rss_identities_survive(self):
        self.assertTrue({e['id'] for e in self.base['events']}<={e['id'] for e in self.data['events']})

    def test_reimport_produces_stable_ids_and_no_duplicates(self):
        again=imp.build_dataset(self.base,self.snapshots,'2026-11-12T09:00:00+00:00',date(2026,11,12))
        ids=[e['id'] for e in self.data['events']]
        self.assertEqual(ids,[e['id'] for e in again['events']]);self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(len(ids),595)

    def test_rss_and_pdf_overlap_is_one_event_with_all_sources(self):
        events=self.find('Naturparadies Rheinauen','2026-11-19')
        self.assertEqual(len(events),1)
        self.assertEqual({s['sourceId'] for s in events[0]['sources']},{'karlsruhe','groetzingen-kultur-natur'})
        self.assertEqual(events[0]['endTime'],'21:00')

    def test_photo_exhibition_uses_newer_closing_time_and_records_difference(self):
        event=self.find('23. Grötzinger Fotoausstellung','2026-12-31')[0]
        self.assertEqual(event['endTime'],'17:30')
        self.assertTrue(event['end'].endswith('17:30:00+01:00'))
        self.assertEqual(event['conflicts'][0]['field'],'endTime')
        self.assertEqual({x['value'] for x in event['conflicts'][0]['alternatives']},{'17:00','17:30'})

    def test_klavierfest_keeps_current_rss_time_and_pdf_disagreement(self):
        events=self.find('KLAVIERFEST','2026-11-21');self.assertEqual(len(events),1)
        self.assertEqual(events[0]['startTime'],'18:00')
        self.assertEqual(len(events[0]['sources']),2)
        self.assertIn('19:00',[v['value'] for v in events[0]['conflicts'][0]['alternatives']])

    def test_two_tours_same_day_remain_two_separate_occurrences(self):
        events=[e for e in self.data['events'] if e['startDate']=='2026-09-13' and any(s.get('recordId','').startswith('annual') and '11 und 15' in s.get('timeText','') for s in e['sources'])]
        self.assertEqual(len(events),2)
        self.assertEqual({e['startTime'] for e in events},{'11:00','15:00'})
        self.assertTrue(all(len(e['sources'])==2 for e in events))

    def test_unknown_and_alternative_dates_are_never_calendar_dates(self):
        self.assertEqual(len(self.data['undatedEvents']),2)
        self.assertFalse(any('Brennballturnier' in e['title'] or 'Vereinsausfug' in e['title'] for e in self.data['events']))

    def test_explicit_sunday_openings_match_the_brochure(self):
        dates={e['startDate'] for e in self.data['events'] if e['title']=='Wolfgang Rempfer' and any(s['sourceId']=='groetzingen-kultur-natur' for s in e['sources'])}
        self.assertEqual(dates,{'2026-11-08','2026-11-15','2026-11-22','2026-11-29','2026-12-06','2026-12-13'})
        self.assertTrue(all(date.fromisoformat(d).weekday()==6 for d in dates))

    def test_regular_meetings_are_not_fabricated_as_daily_events(self):
        events=[e for e in self.data['events'] if any('regular-' in s.get('recordId','') for s in e['sources'])]
        self.assertEqual(len(events),2);self.assertTrue(all(e['longRunning'] for e in events))
        self.assertTrue(all('Sommerferien' in e['scheduleNote'] for e in events))

    def test_missing_clock_time_is_not_midnight_or_all_day(self):
        event=self.find('DLRG Lichterschwimmen','2026-11-14')[0]
        self.assertIsNone(event['startTime']);self.assertFalse(event['timeKnown']);self.assertFalse(event['allDay'])

    def test_half_year_review_and_overdue_state(self):
        self.assertEqual(imp.next_review(date(2026,11,12)),'2027-01-15')
        self.assertEqual(imp.next_review(date(2027,1,15)),'2027-07-15')
        self.assertEqual(imp.next_review(date(2027,7,15)),'2028-01-15')
        self.assertIn('Erinnerung',imp.check_review(self.data,date(2027,1,15)))

    def test_running_import_again_does_not_silence_a_due_pdf_review(self):
        later=imp.build_dataset(self.base,self.snapshots,'2027-01-16T08:00:00+00:00',date(2027,1,16))
        self.assertEqual(later['maintenance']['groetzingen']['nextReviewOn'],'2027-01-15')
        self.assertIn('Erinnerung',imp.check_review(later,date(2027,1,16)))
        self.assertEqual(later['maintenance']['groetzingen']['lastReviewedAt'],'2026-11-12')

    def test_changed_pdf_is_rejected_before_output_is_written(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'dist').mkdir();(root/'data/groetzingen').mkdir(parents=True);(root/'pdfs').mkdir()
            sentinel='{"events":[],"sources":[]}'
            (root/'dist/events.json').write_text(sentinel)
            for source in self.snapshots:
                (root/'data/groetzingen'/(source['id']+'-2026.json')).write_text(json.dumps(source))
                (root/'pdfs'/source['filename']).write_bytes(b'changed PDF')
            with patch('sys.argv',['import','--root',str(root),'--pdf-dir',str(root/'pdfs')]):
                with self.assertRaisesRegex(ValueError,'PDF geändert'):imp.main()
            self.assertEqual((root/'dist/events.json').read_text(),sentinel)

    def test_only_one_json_placeholder_and_html_matches_json(self):
        page=(ROOT/'dist/index.html').read_text()
        self.assertEqual(page.count(imp.BEGIN),1)
        embedded=json.loads(page.split(imp.BEGIN)[1].split(imp.END)[0])
        self.assertEqual(embedded,json.loads((ROOT/'dist/events.json').read_text()))

if __name__=='__main__':unittest.main()
