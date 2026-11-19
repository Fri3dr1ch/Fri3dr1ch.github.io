from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import karlsruhe


def ics(uid='library-1', title='Vorlesezeit in Grötzingen'):
    return (f'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{title}\r\nDTSTART;TZID=Europe/Berlin:20261123T150000\r\nDTEND;TZID=Europe/Berlin:20261123T160000\r\nLOCATION:Stadtteilbibliothek Grötzingen\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n''').encode()


class KarlsruheLibraryTests(unittest.TestCase):
    def test_listing_and_future_rss_share_guid_identity(self):
        page = b'''<html><head><title>Karlsruhe: Veranstaltungskalender - Stadtteilbibliothek Gr\xc3\xb6tzingen</title></head><body>
        <a href="https://kalender.karlsruhe.de/db/termine/literatur/vorlesezeit_in_grotzingen-2">Vorlesezeit in Gr\xc3\xb6tzingen</a></body></html>'''
        record = karlsruhe.library_listing(page)[0]
        event = karlsruhe.library_ical_record(record, ics())
        rss = b'''<rss><channel><title>Karlsruhe Kalender</title><item><title>Vorlesezeit in Grotzingen</title>
        <link>https://kalender.karlsruhe.de/db/termine/literatur/vorlesezeit_in_grotzingen-2</link><guid>ka library-1</guid>
        <pubDate>Mon, 23 Nov 2026 15:00:00 +0100</pubDate><description>Vorlesen</description></item></channel></rss>'''
        feed_record = karlsruhe.parse_feed(rss, {'events': [event]})[0][0]
        self.assertEqual(event['sourceGuid'], feed_record['sourceGuid'])
        self.assertEqual(event['id'], feed_record['id'])

    def test_listing_failure_retains_last_confirmed_supplement(self):
        old_event = karlsruhe.library_ical_record({'url': 'https://kalender.karlsruhe.de/db/termine/literatur/vorlesezeit_in_grotzingen-2', 'title': 'Vorlesezeit in Grötzingen', 'categoryKey': 'literatur'}, ics())
        old_source = {'id': 'karlsruhe', 'status': 'ok', 'lastSuccessAt': '2026-11-18T10:00:00+00:00',
                      'listingStatus': 'ok', 'listingLastSuccessAt': '2026-11-17T10:00:00+00:00'}
        previous = {'sources': [old_source], 'events': [old_event]}
        def fail(_url): raise OSError('temporarily unavailable')
        current_events = []
        new_source = {'id': 'karlsruhe', 'status': 'ok', 'lastSuccessAt': '2026-11-18T12:00:00+00:00'}
        result = karlsruhe.supplement_groetzingen_library(current_events, previous, fail, new_source)
        self.assertEqual(result['listingStatus'], 'error')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([e['id'] for e in current_events], [old_event['id']])
        # Once the event appears in RSS it wins even if the supplement fails.
        current_events = [deepcopy(old_event)]
        current_events[0].pop('librarySupplement')
        karlsruhe.supplement_groetzingen_library(current_events, previous, fail, new_source)
        self.assertEqual([e['id'] for e in current_events], [old_event['id']])

    def test_rss_guid_prevents_duplicate_after_listing_slug_changes(self):
        current = karlsruhe.library_ical_record({'url': 'https://kalender.karlsruhe.de/db/termine/literatur/old-slug', 'title': 'Vorlesezeit in Grötzingen', 'categoryKey': 'literatur'}, ics())
        page = b'''<html><head><title>Karlsruhe - Stadtteilbibliothek Gr\xc3\xb6tzingen</title></head><body>
        <a href="https://kalender.karlsruhe.de/db/termine/literatur/new-slug">Vorlesezeit in Gr\xc3\xb6tzingen</a></body></html>'''
        def fetch(url):
            return page if url == karlsruhe.GROETZINGEN_LIBRARY else ics()
        previous = {'sources': [{'id': 'karlsruhe', 'status': 'ok'}], 'events': [current]}
        events = [deepcopy(current)]
        source = karlsruhe.supplement_groetzingen_library(events, previous, fetch, {'id': 'karlsruhe', 'status': 'ok'})
        self.assertEqual(source['listingStatus'], 'ok')
        self.assertEqual([e['id'] for e in events], [current['id']])


if __name__ == '__main__':
    unittest.main()
