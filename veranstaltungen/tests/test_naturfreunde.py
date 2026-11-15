from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import naturfreunde as nf
import import_local_calendars as imp
from import_groetzingen import check_review
from fixture_sources import historical_dataset


ICS = b'''BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Naturfreunde S\xc3\xb6llingen
X-WR-TIMEZONE:Europe/Berlin
BEGIN:VEVENT
UID:walking
DTSTART;TZID=Europe/Berlin:20261021T153000
DTEND;TZID=Europe/Berlin:20261021T170000
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=Europe/Berlin:20261104T153000
SUMMARY:Nordic Walking
LOCATION:Pfinztalblick
END:VEVENT
BEGIN:VEVENT
UID:walking
RECURRENCE-ID;TZID=Europe/Berlin:20261028T153000
DTSTART;TZID=Europe/Berlin:20261029T160000
DTEND;TZID=Europe/Berlin:20261029T173000
SUMMARY:Nordic Walking verschoben
END:VEVENT
BEGIN:VEVENT
UID:opening
DTSTART;TZID=Europe/Berlin:20261025T120000
DTEND;TZID=Europe/Berlin:20261025T180000
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE;TZID=Europe/Berlin:20261108T120000
SUMMARY:NFH ge\xc3\xb6ffnet
END:VEVENT
BEGIN:VEVENT
UID:all-day
DTSTART;VALUE=DATE:20261113
DTEND;VALUE=DATE:20261115
SUMMARY:Zweit\xc3\xa4giges Fest
END:VEVENT
BEGIN:VEVENT
UID:cancelled
DTSTART;TZID=Europe/Berlin:20261118T140000
DTEND;TZID=Europe/Berlin:20261118T150000
SUMMARY:Abgesagter Treff
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
'''


class NaturfreundeTests(unittest.TestCase):
    def fixture(self, name):
        return (ROOT / 'tests/fixtures' / name).read_bytes()

    def test_berghausen_dates_meeting_point_and_registration(self):
        listing = nf.berghausen_listing(self.fixture('naturfreunde-berghausen-list.html'))
        self.assertEqual(len(listing), 2)
        entry = next(r for r in listing if r['recordId'] == '993')
        r = nf.berghausen_detail(self.fixture('naturfreunde-berghausen-993.html'), entry)
        self.assertEqual((r['startDate'], r['startTime'], r['endTime']), ('2026-11-17', '09:45', '17:00'))
        self.assertIn('Bahnhofsvorplatz Berghausen', r['location'])
        self.assertIn('Anmeldung bis Sonntag 15.11.26', r['scheduleNote'])

    def test_postponed_event_is_never_marked_scheduled_or_midnight(self):
        entry = next(r for r in nf.berghausen_listing(self.fixture('naturfreunde-berghausen-list.html')) if r['recordId'] == '991')
        r = nf.berghausen_detail(self.fixture('naturfreunde-berghausen-991.html'), entry)
        e = imp.event(imp.SOURCES['naturfreunde-berghausen'], r, '2026-11-13T09:00:00+00:00')
        self.assertEqual(e['status'], 'postponed')
        self.assertIsNone(e['startTime'])
        self.assertFalse(e['timeKnown'])
        self.assertFalse(e['allDay'])

    def test_next_calendar_year_is_checked_and_confirmed_empty_year_is_valid(self):
        fixture = self.fixture('naturfreunde-berghausen-empty-2027.html')
        self.assertEqual(nf.berghausen_listing(fixture, 2027), [])
        with self.assertRaises(ValueError): nf.berghausen_listing(fixture, 2028)
        seen = []
        def fetch(url):
            seen.append(url)
            if url == nf.BERG + '/':
                return b'<a href="/programm/year.listevents/2026/11/13/-">Programm</a>'
            if '/2027/' in url: return fixture
            if '/year.listevents/' in url: return self.fixture('naturfreunde-berghausen-list.html')
            return self.fixture('naturfreunde-berghausen-' + ('993' if '/993/' in url else '991') + '.html')
        rows, _ = nf.fetch_berghausen(fetch)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any('/2027/01/01/' in u for u in seen))

    def test_layout_change_keeps_last_valid_snapshot(self):
        old = json.loads((ROOT / 'data/local-calendars/naturfreunde-berghausen.json').read_text())
        retained, error = imp.refresh('naturfreunde-berghausen', '2026-12-01T00:00:00+00:00', old, lambda u: b'<html>Wartung</html>')
        self.assertTrue(error)
        self.assertEqual(retained['events'], old['events'])
        self.assertEqual(retained['source']['lastSuccessAt'], old['source']['lastSuccessAt'])

    def test_ical_recurrences_dst_exclusion_and_moved_instance(self):
        rows, openings = nf.soellingen_records(ICS, date(2026, 10, 1), date(2026, 12, 1), 'https://calendar.google.com/a.ics')
        walks = [r for r in rows if 'Walking' in r['title']]
        self.assertEqual({(r['startDate'], r['startTime']) for r in walks}, {
            ('2026-10-21', '15:30'), ('2026-10-29', '16:00'), ('2026-11-11', '15:30')})
        events = [imp.event(imp.SOURCES['naturfreunde-soellingen'], r, '2026-11-13T00:00:00+00:00') for r in walks]
        self.assertTrue(next(e for e in events if e['startDate'] == '2026-10-21')['start'].endswith('+02:00'))
        self.assertTrue(next(e for e in events if e['startDate'] == '2026-11-11')['start'].endswith('+01:00'))
        self.assertEqual({r['startDate'] for r in openings}, {'2026-10-25', '2026-11-01', '2026-11-15'})

    def test_ical_all_day_end_is_exclusive(self):
        rows, _ = nf.soellingen_records(ICS, date(2026, 11, 1), date(2026, 12, 1), 'https://calendar.google.com/a.ics')
        r = next(r for r in rows if r['title'] == 'Zweitägiges Fest')
        self.assertEqual((r['startDate'], r['endDate']), ('2026-11-13', '2026-11-14'))
        self.assertTrue(r['allDay'])
        self.assertIsNone(r['startTime'])

    def test_cancelled_ical_event_is_not_shown_as_scheduled(self):
        rows, _ = nf.soellingen_records(ICS, date(2026, 11, 1), date(2026, 12, 1), 'https://calendar.google.com/a.ics')
        self.assertTrue(all(r['status'] != 'scheduled' for r in rows if r['title'] == 'Abgesagter Treff'))

    def test_midnight_end_does_not_create_an_extra_calendar_day(self):
        midnight = ICS.replace(b'DTSTART;VALUE=DATE:20261113\nDTEND;VALUE=DATE:20261115',
                               b'DTSTART;TZID=Europe/Berlin:20261113T190000\nDTEND;TZID=Europe/Berlin:20261114T000000')
        rows, _ = nf.soellingen_records(midnight, date(2026, 11, 1), date(2026, 12, 1), 'https://calendar.google.com/a.ics')
        record = next(r for r in rows if r['title'] == 'Zweitägiges Fest')
        event = imp.event(imp.SOURCES['naturfreunde-soellingen'], record, '2026-11-13T00:00:00+00:00')
        self.assertEqual(event['endDate'], '2026-11-13')
        self.assertEqual(event['end'], '2026-11-14T00:00:00+01:00')

    def test_event_started_before_import_window_remains_visible_if_running(self):
        rows, _ = nf.soellingen_records(ICS, date(2026, 11, 14), date(2026, 12, 1), 'https://calendar.google.com/a.ics')
        self.assertTrue(any(r['title'] == 'Zweitägiges Fest' for r in rows))

    def test_openings_are_one_series_with_only_actual_opening_dates(self):
        _, openings = nf.soellingen_records(ICS, date(2026, 10, 1), date(2026, 12, 1), 'https://calendar.google.com/a.ics')
        grouped = nf.group_openings(openings)
        self.assertEqual(len(grouped), 1)
        self.assertTrue(grouped[0]['longRunning'])
        self.assertEqual(len(grouped[0]['openingOccurrences']), 3)
        self.assertNotIn('2026-11-08', {r['startDate'] for r in grouped[0]['openingOccurrences']})
        self.assertIn('12:00–18:00', grouped[0]['openingSchedule'])

    def test_public_calendar_id_change_is_rejected(self):
        page = b'<iframe src="https://calendar.google.com/calendar/embed?src=other%40gmail.com"></iframe>'
        with self.assertRaises(ValueError): nf.calendar_url(page)

    def test_august_and_sunday_after_festival_are_excluded(self):
        raw = b'''BEGIN:VCALENDAR\nVERSION:2.0\nX-WR-CALNAME:Naturfreunde S\xc3\xb6llingen\nBEGIN:VEVENT\nUID:open\nDTSTART;TZID=Europe/Berlin:20261011T120000\nDTEND;TZID=Europe/Berlin:20261011T180000\nRRULE:FREQ=WEEKLY\nSUMMARY:NFH ge\xc3\xb6ffnet\nEND:VEVENT\nBEGIN:VEVENT\nUID:fest\nDTSTART;VALUE=DATE:20261017\nDTEND;VALUE=DATE:20261018\nSUMMARY:Oktoberfest\nEND:VEVENT\nEND:VCALENDAR\n'''
        page = '<p>im August bleibt das Naturfreundehaus geschlossen.</p><iframe src="https://calendar.google.com/calendar/embed?src=natureloverssoellingen%40gmail.com"></iframe>'.encode()
        rows, meta = nf.fetch_soellingen(lambda u: raw if u.endswith('.ics') else page, date(2026, 10, 13))
        series = next(r for r in rows if r.get('openingOccurrences'))
        days = {r['startDate'] for r in series['openingOccurrences']}
        self.assertNotIn('2026-10-18', days)
        self.assertFalse(any(date.fromisoformat(d).month == 8 for d in days))
        self.assertGreater(meta['excludedOpeningCount'], 0)

    def test_monthly_review_is_due_on_first_of_next_month_including_year_end(self):
        self.assertEqual(nf.monthly_review('2026-11-13'), '2026-12-01')
        self.assertEqual(nf.monthly_review('2026-12-31'), '2027-01-01')
        data = historical_dataset(stamp='2026-11-13T09:00:00+00:00')
        future = historical_dataset(stamp='2026-12-05T09:00:00+00:00')
        self.assertNotEqual(data['generatedAt'], future['generatedAt'])
        self.assertEqual(data['maintenance']['naturfreunde-groetzingen'], future['maintenance']['naturfreunde-groetzingen'])
        self.assertIn('Monatliche Prüfung ist fällig', check_review(future, date(2026, 12, 5)))
        self.assertEqual(future['maintenance']['naturfreunde-groetzingen']['lastReviewedAt'], '2026-11-13')

    def test_changed_image_and_new_leading_announcement_trigger_review(self):
        html = b'<div id="main-content"><div class="items-leading"><div><h2>Neu</h2><img src="/images/new.jpg"></div></div></div>'
        a = nf.groetzingen_evidence(html, lambda u: b'old image')
        b = nf.groetzingen_evidence(html, lambda u: b'new image')
        self.assertEqual(len(a), 1)
        self.assertNotEqual(a, b)
        self.assertEqual(a[0]['title'], 'Neu')
        self.assertEqual(a, nf.groetzingen_evidence(html, lambda u: b'old image'))

    def test_manual_import_has_no_fabricated_sunday_or_clock_and_preserves_overlap(self):
        data = historical_dataset()
        events = [e for e in data['events'] if any(s['sourceId'] == 'naturfreunde-groetzingen' for s in e['sources'])]
        stew = next(e for e in events if 'Eintopf' in e['title'])
        self.assertEqual(stew['startDate'], '2026-11-08')
        self.assertIsNone(stew['startTime'])
        self.assertFalse(any('Eintopf' in e['title'] and e['startDate'] == '2026-11-15' for e in events))
        feast = next(e for e in events if e['title'] == 'Herbstfest FC Körble')
        self.assertEqual(len(feast['sources']), 2)
        self.assertEqual(len([e for e in data['events'] if e['title'] == 'Herbstfest FC Körble']), 1)


if __name__ == '__main__':
    unittest.main()
