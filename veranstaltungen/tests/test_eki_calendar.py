import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('eki_calendar',ROOT/'scripts/eki_calendar.py')
eki=importlib.util.module_from_spec(spec);spec.loader.exec_module(eki)

class EkiCalendarTests(unittest.TestCase):
    def fixture(self):return (ROOT/'tests/fixtures/eki-list.html').read_bytes()

    def test_event_rows_keep_distinct_occurrences_and_explicit_end_time(self):
        rows=eki.parse_listing(self.fixture())
        choirs=[r for r in rows if r['title']=='Kirchenchor']
        self.assertEqual(len(choirs),2)
        self.assertNotEqual(choirs[0]['recordId'],choirs[1]['recordId'])
        self.assertEqual((choirs[0]['startDate'],choirs[0]['startTime'],choirs[0]['endTime']),('2026-11-12','20:00','22:00'))

    def test_corrected_time_is_read_from_date_field_and_preserved_in_title(self):
        row=next(r for r in eki.parse_listing(self.fixture()) if r['recordId']=='1103214')
        self.assertEqual(row['startTime'],'11:30')
        self.assertIn('11.15',row['title'])
        self.assertIn('korrigiert',row['title'])
        self.assertEqual(row['location'],'Friedhof Grötzingen')

    def test_subtitle_is_not_always_a_venue(self):
        row=next(r for r in eki.parse_listing(self.fixture()) if r['title']=='Ökumenisches Hausgebet')
        self.assertEqual(row['location'],'')
        self.assertEqual(row['scheduleNote'],'Zusatzangabe im Kalender: im Advent')

    def test_unknown_venue_and_end_time_are_not_invented(self):
        row=next(r for r in eki.parse_listing(self.fixture()) if r['recordId']=='1068063')
        self.assertEqual(row['location'],'');self.assertIsNone(row['endTime'])

    def test_changed_structure_or_pagination_aborts(self):
        with self.assertRaises(ValueError):eki.parse_listing(b'<html>calendar temporarily unavailable</html>')
        with self.assertRaisesRegex(ValueError,'Paginierung'):
            eki.parse_listing(self.fixture()+b'<div class="termine-pager"><a href="?page=2">2</a></div>')
        with self.assertRaisesRegex(ValueError,'Datumsformat'):
            eki.parse_listing(self.fixture().replace(b'12.11.2026, 20:00',b'Datum offen'))

    def test_fetch_records_calls_only_the_published_listing(self):
        seen=[]
        def fetch(url):seen.append(url);return self.fixture()
        rows,metadata=eki.fetch_records(fetch)
        self.assertEqual(seen,[eki.URL]);self.assertEqual(metadata['pagesFetched'],1)
        self.assertFalse(metadata['llmRequired']);self.assertEqual(metadata['coverageStart'],'2026-11-12')

if __name__=='__main__':unittest.main()
