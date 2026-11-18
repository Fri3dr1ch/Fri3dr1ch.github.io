import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from import_local_calendars import SOURCES, fetch_source, refresh
from waldpaedagogik import month_records


SOURCE = 'waldpaedagogik-karlsruhe'
BASE = SOURCES[SOURCE]['url']
STAMP = '2026-11-18T12:00:00+00:00'
NOVEMBER = 'https://www.waldpaedagogik-karlsruhe.de/veranstaltungen-im-november'
DECEMBER = 'https://www.waldpaedagogik-karlsruhe.de/veranstaltungen-im-dezember'


def month_html(*columns):
    return '<div class="dmHomeRespTmpl"><div><div>' + ''.join(columns) + '</div></div></div>'


def column(anchor, title, details):
    return f'<div class="dmRespCol"><div id="{anchor}"><h3>{title}</h3></div><div>{details}</div></div>'


class WaldpaedagogikTests(unittest.TestCase):
    def test_source_imports_published_month_links_and_uses_last_valid_snapshot(self):
        index = '''<div class="dmHomeRespTmpl"><h2>Jahresprogramm 2026</h2>
        <a href="/veranstaltungen-im-november">November</a>
        <a href="/veranstaltungen-im-november">November mobil</a>
        <a href="/veranstaltungen-im-dezember">Dezember</a></div>'''
        pages = {
            BASE: index,
            NOVEMBER: month_html(column('100', 'Entdeckertag am Waldklassenzimmer', '''
                <h5>Samstag, 21. November 2026 von14 bis 18 Uhr</h5>
                <p>Hinweis: Bei einer Sturmwarnung muss die Veranstaltung leider ausfallen.</p>''')),
            DECEMBER: month_html(column('200', 'Laternenwanderung', '''
                <p>Dienstag bis Donnerstag, 8.-10. Dezember 2026</p><p>jeweils 17 Uhr bis 19.30 Uhr</p>
                <p>Der genaue Treffpunkt wird nach Anmeldung bekannt gegeben.</p>''')),
        }
        snapshot = fetch_source(SOURCE, STAMP, pages.__getitem__)
        events = snapshot['events']
        self.assertEqual(snapshot['source']['pagesFetched'], 3)
        self.assertEqual(events[0]['status'], 'scheduled')
        self.assertEqual(events[0]['location'], 'Waldklassenzimmer')
        self.assertEqual(events[0]['url'], NOVEMBER + '#100')
        self.assertEqual([(event['startDate'], event['startTime'], event['endTime']) for event in events[1:]],
                         [(f'2026-12-{day:02d}', '17:00', '19:30') for day in (8, 9, 10)])
        self.assertTrue(all(not event['location'] for event in events[1:]))

        pages[DECEMBER] = '<h1>Vorübergehend nicht erreichbar</h1>'
        updated, error = refresh(SOURCE, '2026-11-19T12:00:00+00:00', snapshot, pages.__getitem__)
        self.assertIn('Monatsprogramm', error)
        self.assertEqual(updated['source']['status'], 'error')
        self.assertEqual(updated['source']['lastSuccessAt'], STAMP)
        self.assertEqual(updated['events'], events)

    def test_separate_sessions_availability_and_record_without_heading(self):
        raw = month_html(column('200', 'Basteln', '''
            <p>Samstag, 5. Dezember 2026 von 10 bis 12 / 12 bis 14 Uhr</p>
            <p>Erster Durchgang von 10-12 Uhr ausgebucht.</p><p>Treffpunkt: Waldzentrum, Linkenheimer Allee 10</p>'''),
            '<div class="dmRespCol"><div id="300"><p>Samstag, 12. Dezember 2026, Beginn 9 Uhr</p>'
            '<p>Waldspaziergang</p><p>Die Veranstaltung wird ersatzlos gestrichen.</p></div></div>')
        first, second, cancelled = month_records(raw, DECEMBER, 2026, 12)
        self.assertEqual((first['startTime'], first['endTime'], first['availability']), ('10:00', '12:00', 'sold-out'))
        self.assertEqual((second['startTime'], second['endTime'], second['availability']), ('12:00', '14:00', None))
        self.assertEqual(second['location'], 'Waldzentrum, Linkenheimer Allee 10')
        self.assertEqual((cancelled['title'], cancelled['startTime'], cancelled['endTime'], cancelled['status']),
                         ('Waldspaziergang', '09:00', None, 'cancelled'))

    def test_rejects_stale_programme_page_at_year_rollover(self):
        raw = month_html(column('100', 'Waldspaziergang', '<p>Samstag, 5. Dezember 2026 von 10 bis 12 Uhr</p>'))
        with self.assertRaisesRegex(ValueError, 'außerhalb seiner Monatsseite'):
            month_records(raw, DECEMBER, 2027, 12)
