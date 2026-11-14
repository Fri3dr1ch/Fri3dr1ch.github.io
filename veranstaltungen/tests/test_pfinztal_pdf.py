import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pfinztal_pdf as imp

PDF_URL = 'https://pfinztal.de/vereinskalender.pdf'
HEADERS = [['Beginn', None, 'Ende', None, 'Veranstaltung', 'Ort', 'Verein'],
           ['Datum', 'Uhrzeit', 'Datum', 'Uhrzeit', None, None, None]]


class PfinztalPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT / 'tests/fixtures/pfinztal-table-cases.json').read_text())
        cls.table = HEADERS + [case['cells'] for case in cls.cases]
        cls.records, cls.year = imp.parse_rows([cls.table], PDF_URL)

    def find(self, title):
        return [r for r in self.records if r['title'] == title]

    def test_all_four_districts_and_missing_clock_are_preserved(self):
        self.assertEqual(self.year, 2026)
        self.assertEqual({r['district'] for r in self.records}, imp.DISTRICTS)
        event = self.find('Christbaumsammelaktion')[0]
        self.assertEqual(event['startTime'], '10:00')
        self.assertIsNone(event['endTime'])
        self.assertFalse(event['allDay'])
        self.assertEqual(event['location'], '')

    def test_three_alternative_dates_never_become_definite_calendar_dates(self):
        for title, dates in [('Herbstkonzert', ['2026-11-15', '2026-11-22']),
                             ('Schlachtfest', ['2026-11-14', '2026-11-21']),
                             ('Kirchenchor-Adventsfeier', ['2026-12-05', '2026-12-06'])]:
            record = self.find(title)[0]
            self.assertIsNone(record['startDate'])
            self.assertEqual(record['dateCandidates'], dates)
            self.assertIn('Alternativtermin', record['uncertaintyReason'])

    def test_reversed_year_is_reported_without_changing_the_source(self):
        record = self.find('Sommerfest')[0]
        self.assertIsNone(record['startDate'])
        self.assertIn('28.06.2025', record['dateText'])
        self.assertIn('Keine Jahreszahl automatisch korrigiert', record['notes'])

    def test_setup_and_dismantling_are_not_published_as_confirmed_visit_days(self):
        for title in ('Gottesdienst', 'Jahreshauptversammlung', 'Zirkusprojekt'):
            record = next(r for r in self.find(title) if 'Aufbau' in r['timeText'] or 'Abbau' in r['timeText'])
            self.assertIsNone(record['startDate'])
            self.assertIn('Besuchstermin bitte bestätigen', record['uncertaintyReason'])

    def test_two_family_concert_performances_are_not_one_four_hour_event(self):
        concerts = self.find('Familienkonzert')
        self.assertEqual(len(concerts), 2)
        self.assertEqual({r['startTime'] for r in concerts}, {'11:00', '15:00'})
        self.assertTrue(all(r['endTime'] is None for r in concerts))
        self.assertEqual(len({r['recordId'] for r in concerts}), 2)

    def test_explicit_daily_windows_produce_two_separate_day_occurrences(self):
        records = self.find('Altpapiersammlung')
        self.assertEqual({r['startDate'] for r in records}, {'2026-01-02', '2026-01-03'})
        self.assertTrue(all(r['startDate'] == r['endDate'] for r in records))
        self.assertTrue(all((r['startTime'], r['endTime']) == ('08:00', '15:00') for r in records))

    def test_time_window_in_start_cell_is_an_actual_single_day_window(self):
        record = self.find('Pflanzen-Tag')[0]
        self.assertEqual((record['startTime'], record['endTime']), ('08:00', '12:00'))

    def test_multi_day_window_is_not_invented_as_daily_opening_or_known_closing(self):
        for title in ('Glühweinmarkt', 'Glühweinstand'):
            record = self.find(title)[0]
            self.assertEqual(record['startTime'], '18:00')
            self.assertIsNone(record['endTime'])
            self.assertNotEqual(record['startDate'], record['endDate'])
            self.assertIn('keine täglichen Öffnungszeiten', record['scheduleNote'])

    def test_missing_title_and_open_date_remain_unconfirmed(self):
        missing = next(r for r in self.records if r['title'].startswith('Unbenannter Eintrag'))
        self.assertIsNone(missing['startDate'])
        self.assertIn('Spalte Veranstaltung ist im PDF leer', missing['notes'])
        blood = self.find('Blutspende')[0]
        self.assertIsNone(blood['startDate'])
        self.assertEqual(blood['dateCandidates'], [])

    def test_unrecognized_structure_or_time_fails_instead_of_skipping_records(self):
        wrong_header = json.loads(json.dumps(self.table))
        wrong_header[0][4] = 'Titel'
        with self.assertRaisesRegex(ValueError, 'Spaltenüberschrift'):
            imp.parse_rows([wrong_header], PDF_URL)
        wrong_time = json.loads(json.dumps(self.table))
        target = next(r for r in wrong_time if r[4] == 'Christbaumsammelaktion')
        target[1] = 'morgens irgendwann'
        with self.assertRaisesRegex(ValueError, 'Unbekannte Zeitangabe'):
            imp.parse_rows([wrong_time], PDF_URL)

    def test_source_pdf_discovery_rejects_ambiguous_links(self):
        page = '<a href="/wp-content/Veranstaltungskalender_2026.pdf">Kalender</a>'
        self.assertEqual(imp.pdf_link(page), 'https://pfinztal.de/wp-content/Veranstaltungskalender_2026.pdf')
        with self.assertRaisesRegex(ValueError, 'nicht eindeutig'):
            imp.pdf_link(page + '<a href="/Veranstaltungskalender_2027.pdf">Weitere Ausgabe</a>')

    def test_new_pdf_hash_is_not_claimed_as_new_human_review(self):
        page = SimpleNamespace(extract_tables=lambda: [self.table],
                               extract_text=lambda: 'Stand 09.12.2025')
        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [page]
        with patch.object(imp.pdfplumber, 'open', return_value=pdf):
            records, metadata = imp.parse_pdf(b'new hypothetical edition', PDF_URL)
        self.assertIsNone(metadata['reviewedOn'])
        self.assertFalse(metadata['requiresLLM'])
        self.assertEqual(metadata['unknownCount'], sum(r['startDate'] is None for r in records))


if __name__ == '__main__':
    unittest.main()
