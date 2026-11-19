from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import unittest

from lxml import html
from icalendar import Calendar, Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import publisher_sources as ps
import update_calendar
from calendar_sources import combine_local_sources
import import_local_calendars as local
import import_groetzingen as groetzingen
from venue_locations import review_problem


def card(title, event_id, date_text, time_text='', location=''):
    return f'''<div class="cVeka_box_eventDate"><div class="cVeka_box_date">{date_text}</div>
    {f'<div class="cVeka_box_time">{time_text}</div>' if time_text else ''}
    <div class="cVeka_box_title"><a href="https://www.stutensee.de/Veranstaltung?view=publish&amp;item=eventDate&amp;id={event_id}">{title}</a></div>
    {f'<div class="cVeka_box_location">Veranstaltungsort: {location}</div>' if location else ''}</div>'''


class PublisherSourceTests(unittest.TestCase):
    def test_naturtreff_ics_uses_uid_and_real_berlin_time(self):
        raw = '''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:nt-1@naturtreff-groetzingen.de\r\nSUMMARY:Naturabend\r\nDTSTART:20261119T180000Z\r\nDTEND:20261119T200000Z\r\nLOCATION:Begegnungsstätte Grötzingen, Niddaraum\r\nURL:https://naturtreff-groetzingen.de/veranstaltungen/naturabend\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''.encode()
        records, meta = ps.naturtreff_records(raw)
        self.assertEqual(records[0]['recordId'], 'nt-1@naturtreff-groetzingen.de')
        self.assertEqual((records[0]['startDate'], records[0]['startTime'], records[0]['endTime']), ('2026-11-19', '19:00', '21:00'))
        self.assertEqual(meta['coverageKind'], 'ical-feed')
        with self.assertRaisesRegex(ValueError, 'wiederkehrend'):
            ps.naturtreff_records(raw.replace(b'END:VEVENT', b'RRULE:FREQ=DAILY\r\nEND:VEVENT'))

    def test_ical_updates_keep_uid_cancellation_and_last_good_snapshot(self):
        stamp = '2026-11-19T12:00:00+00:00'
        for sid in ('naturtreff-groetzingen', 'karlsbad'):
            with self.subTest(sid=sid):
                url = local.SOURCES[sid]['url']
                def feed(start, status, link=url):
                    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'BEGIN:VEVENT', 'UID:term-1',
                             'SUMMARY:Ausflug', 'DTSTART:' + start, 'URL:' + link,
                             'STATUS:' + status, 'END:VEVENT', 'END:VCALENDAR', '']
                    return '\r\n'.join(lines).encode()
                valid, error = local.refresh(sid, stamp, fetch=lambda _: feed('20261230T180000Z', 'CONFIRMED'))
                self.assertIsNone(error)
                updated, error = local.refresh(sid, stamp, valid, lambda _: feed('20270108T190000Z', 'CANCELLED'))
                self.assertIsNone(error)
                self.assertEqual(updated['events'][0]['status'], 'cancelled')
                self.assertEqual(updated['events'][0]['startDate'], '2027-01-08')
                self.assertEqual(updated['events'][0]['id'], valid['events'][0]['id'])
                retained, error = local.refresh(sid, '2026-11-20T12:00:00+00:00', updated,
                    lambda _: feed('20270108T190000Z', 'CONFIRMED', url.replace('https:', 'http:')))
                self.assertIn('unsichere Originalquelle', error)
                self.assertEqual(retained['source']['status'], 'error')
                self.assertEqual(retained['source']['lastSuccessAt'], stamp)
                self.assertEqual(retained['events'], updated['events'])

    def test_known_empty_ical_feed_replaces_stale_rows(self):
        stamp = '2026-11-19T12:00:00+00:00'
        for sid, prodid in [('naturtreff-groetzingen', '-//Naturtreff Veranstaltungen//DE'),
                            ('karlsbad', '-//KLIK//EventCalendar//DE')]:
            calendar = Calendar()
            calendar.add('version', '2.0')
            calendar.add('prodid', prodid)
            result, error = local.refresh(sid, stamp, fetch=lambda _: calendar.to_ical())
            self.assertIsNone(error)
            self.assertEqual(result['source']['status'], 'ok')
            self.assertEqual(result['events'], [])
            calendar['PRODID'] = '-//Unbekannter Kalender//DE'
            result, error = local.refresh(sid, stamp, fetch=lambda _: calendar.to_ical())
            self.assertIsNone(result)
            self.assertIn('nicht erkannt', error)

    def test_vhs_land_keeps_only_single_appointments_and_title_link(self):
        page = '''<main><div class="kw-ue"><h4 class="kw-ue-title"><a href="/programm/kw/bereich/kursdetails/kurs/M1">Naturführung</a></h4>
          <div>Beginn Do., 03.12.2026, 18:00 - 20:00 Uhr Dauer 1 Termin Kursort Pfinztal, Naturfreundehaus Gebühr 5,00 €</div>
          <a href="/programm/kw/bereich/kursdetails/kurs/M1">Weitere Infos</a></div>
          <div class="kw-ue"><h4 class="kw-ue-title"><a href="/programm/kw/bereich/kursdetails/kurs/M2">Yoga</a></h4>
          <div>Beginn Do., 03.12.2026, 18:00 Uhr Dauer 8 Termine Kursort Pfinztal, Saal Gebühr 80,00 €</div></div></main>'''.encode()
        rows, meta = ps.vhs_land_records(page, 'https://www.vhs-karlsruhe-land.de/programm/suche')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['title'], rows[0]['startTime'], rows[0]['endTime']), ('Naturführung', '18:00', '20:00'))
        self.assertEqual(rows[0]['location'], 'Pfinztal, Naturfreundehaus')
        self.assertEqual(rows[0]['url'], 'https://www.vhs-karlsruhe-land.de/programm/kw/bereich/kursdetails/kurs/M1')
        self.assertIn('Einzelveranstaltungen', meta['coverageNote'])

    def test_hagsfeld_keeps_distinct_performance_urls_and_dates(self):
        page = b'''<div class="mod_eventlist pb40 block">
        <div class="event layout_upcoming"><a href="/termine/musical-1" title="Weihnachtsmusical (Mittwoch, 23.12.2026, 17:00)">Weihnachtsmusical</a><time datetime="2026-12-23T17:00:00+01:00">23.12.2026, 17:00</time></div>
        <div class="event layout_upcoming"><a href="/termine/musical-2" title="Weihnachtsmusical (Donnerstag, 24.12.2026, 15:30)">Weihnachtsmusical</a><time datetime="2026-12-24T15:30:00+01:00">24.12.2026, 15:30</time></div>
        </div>'''
        rows, _ = ps.hagsfeld_records(page, 'https://www.hagsfeld.de/termine')
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['recordId'], rows[1]['recordId'])
        self.assertEqual([(r['startDate'], r['startTime']) for r in rows], [('2026-12-23', '17:00'), ('2026-12-24', '15:30')])

    def test_stutensee_follows_all_published_pages_and_allows_unknown_times(self):
        first = ('<html><main>' + card('Ohne Uhrzeit', '1', 'Do., 19.11.26', location='Gemeindehaus') +
                 '<a href="https://www.stutensee.de/Veranstaltungen?offset=15">2</a></main></html>').encode()
        second = ('<html><main>' + card('Abendtermin', '2', 'Sa., 21.11.26', '18:00 - 20:00 Uhr', 'Festhalle Blankenloch') + '</main></html>').encode()
        seen = []
        def fetch(url): seen.append(url); return second
        rows, meta = ps.stutensee_records(first, 'https://www.stutensee.de/veranstaltungen', fetch)
        self.assertEqual(len(rows), 2); self.assertEqual(meta['pagesFetched'], 2)
        self.assertEqual(rows[0]['startTime'], None)
        self.assertEqual((rows[1]['startTime'], rows[1]['endTime']), ('18:00', '20:00'))
        self.assertEqual(len(seen), 1)

    def test_bergdoerfer_uses_numeric_detail_identity_and_district(self):
        page = b'''<div class="v-kacheln-itemlist"><div class="v-kachel"><div class="date">Mi. 18.11.2026</div>
        <div class="time">16:00 bis 18:00 Uhr</div><div class="v-kachel-titel"><a href="https://www.bergdoerfer.info/termine-2/detail/termin/id/1047988-konfi"><h4 class="title">Konfi B Nachmittag</h4></a></div>
        <div class="oertlichkeit"><div class="oertlichkeit-name">Evang. Kirche Hohenwettersbach</div><div class="oertlichkeit-ort">Hohenwettersbach</div></div></div></div>'''
        rows, _ = ps.bergdoerfer_records(page, 'https://www.bergdoerfer.info/termine-2/',
                                        lambda url: self.fail('unexpected request: ' + url))
        self.assertEqual(rows[0]['recordId'], '1047988')
        self.assertEqual(rows[0]['location'], 'Evang. Kirche Hohenwettersbach, Hohenwettersbach')
        self.assertEqual((rows[0]['startTime'], rows[0]['endTime']), ('16:00', '18:00'))

    def test_bergdoerfer_does_not_save_only_first_page_of_cms_listing(self):
        url = 'https://www.bergdoerfer.info/termine-2/'
        def page(number, record_id, total=2):
            links = ''.join(
                f'<a href="/termine-2/?pagerPage_f1092ea5={n}#a-upos" class="{("pager-active-page" if n==number else "")}">{n}</a>'
                for n in (1, 2))
            return f'''<main><div class="v-kachel"><div class="date">Mi. 18.11.2026</div>
            <div class="v-kachel-titel"><a href="/termine-2/detail/termin/id/{record_id}-termin"><h4 class="title">Gottesdienst</h4></a></div></div>
            <div class="termine-pager"><div class="inner-content-pager-info">Ergebnisse {number} - {number} von {total}</div>{links}</div></main>'''.encode()
        rows, meta = ps.bergdoerfer_records(page(1, 101), url, lambda _:page(2, 102))
        self.assertEqual([r['recordId'] for r in rows], ['101', '102'])
        self.assertEqual(meta['pagesFetched'], 2)
        with self.assertRaisesRegex(ValueError, 'Ergebnisumfang'):
            ps.bergdoerfer_records(page(1, 101), url, lambda _:page(2, 102, total=3))

    def test_karlsbad_date_only_feed_does_not_fabricate_midnight(self):
        cal = Calendar(); cal.add('prodid', '-//test//EN'); cal.add('version', '2.0')
        for uid, summary, day in [('a', 'Familienforscher-Stammtisch ab 19:30 Uhr', '20261118'), ('b', 'Wochenmarkt Spielberg', '20261119')]:
            e = Event(); e.add('uid', uid); e.add('summary', summary); e.add('dtstart', datetime.strptime(day, '%Y%m%d').date()); cal.add_component(e)
        rows, _ = ps.karlsbad_records(cal.to_ical())
        self.assertEqual(rows[0]['startTime'], '19:30')
        self.assertIsNone(rows[1]['startTime'])

    def test_naturtreff_overlap_is_scoped_to_reviewed_city_identity(self):
        rules = json.loads((ROOT / 'data/reviewed-overlaps.json').read_text())
        rule = next(r for r in rules if r['sourceId'] == 'naturtreff-groetzingen')
        record = {'recordId': rule['recordId'], 'title': 'Naturparadies Rheinauen', 'url': 'https://naturtreff-groetzingen.de/?nt_ics=feed',
                  'startDate': '2026-11-19', 'startTime': '19:00', 'endTime': '21:00', 'location': 'Begegnungsstätte Grötzingen, Niddaraum', 'categoryKey': 'stadtleben'}
        local_event = local.event(local.SOURCES['naturtreff-groetzingen'], record, '2026-11-18T12:00:00+00:00')
        incumbent = {'id': rule['targetId'], 'sourceId': 'karlsruhe', 'sourceName': 'Stadt Karlsruhe', 'title': 'Naturparadies Rheinauen',
                     'url': 'https://kalender.karlsruhe.de/db/termine/literatur/naturparadies_rheinauen', 'region': 'Karlsruhe und Umgebung',
                     'startDate': '2026-11-19', 'endDate': '2026-11-19', 'startTime': '19:00', 'endTime': '21:00',
                     'start': '2026-11-19T19:00:00+01:00', 'end': '2026-11-19T21:00:00+01:00', 'categoryKey': 'literatur', 'category': 'Literatur & Vorträge',
                     'description': '', 'organizer': '', 'location': 'Begegnungsstätte Grötzingen', 'timeKnown': True, 'allDay': False, 'longRunning': False,
                     'scheduleNote': '', 'status': 'scheduled', 'dataQuality': 'complete', 'detailCheckedAt': '2026-11-18T12:00:00+00:00',
                     'sources': [{'sourceId': 'karlsruhe', 'sourceName': 'Stadt Karlsruhe', 'reportedTitle': 'Naturparadies Rheinauen', 'startDate': '2026-11-19', 'endDate': '2026-11-19', 'startTime': '19:00', 'endTime': '21:00'}], 'conflicts': []}
        snapshot = {'source': local.SOURCES['naturtreff-groetzingen'], 'events': [local_event], 'reviewedMatches': [rule]}
        base = {'events': [incumbent], 'undatedEvents': [], 'sources': [], 'importStats': {'inputRecords': 1}}
        result = combine_local_sources(base, [snapshot])
        self.assertEqual([e['id'] for e in result['events']], [rule['targetId']])
        self.assertEqual({s['sourceId'] for s in result['events'][0]['sources']}, {'karlsruhe', 'naturtreff-groetzingen'})
        changed = dict(local_event, startTime='19:30')
        changed['start'] = '2026-11-19T19:30:00+01:00'
        result = combine_local_sources(base, [{'source': snapshot['source'], 'events': [changed], 'reviewedMatches': [rule]}])
        self.assertEqual(len(result['events']), 2)

    def test_duplicate_karlsruhe_naturparadies_guid_keeps_canonical_id(self):
        identity = 'naturparadies-rheinauen-naturtreff-grotzingen'
        def row(event_id, source_id, priority):
            return {'id': event_id, 'title': 'Naturparadies Rheinauen', 'startDate': '2026-11-19', 'endDate': '2026-11-19',
                    'startTime': '19:00', 'endTime': '21:00', 'start': '2026-11-19T19:00:00+01:00', 'end': '2026-11-19T21:00:00+01:00',
                    'location': 'Begegnungsstätte Grötzingen', 'timeKnown': True, 'sources': [{'sourceId': source_id, 'recordId': event_id, 'sourceName': source_id, 'url': 'https://example.org/event'}],
                    '_identity': identity, '_priority': priority, 'conflicts': []}
        rows = [row('karlsruhe-ef3eb8362b74e956', 'karlsruhe', 2),
                row('groetzingen-kultur-natur-nature-2026-006', 'groetzingen-kultur-natur', 2),
                row('karlsruhe-5c2d39707b07917f', 'karlsruhe', 3)]
        merged = groetzingen.merge_events(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 'karlsruhe-5c2d39707b07917f')
        self.assertEqual(len(merged[0]['sources']), 3)

    def test_koenigsbach_identity_is_repeatable_across_pages(self):
        def page(title, day, venue, links=''):
            return f'''<html><article><h2>{title}</h2><ul><li>Sa. {day} 11:00 Uhr</li><li class="items-center"><span class="bx-map"></span>{venue}</li><li><a aria-label="Verein">Verein</a></li></ul></article>{links}</html>'''.encode()
        first = page('Fotoausstellung', '21.11.2026', 'Festhalle Königsbach', '<a href="/?seite=2">2</a>')
        second = page('Gesteckkurs', '22.11.2026', 'Vereinsheim Stein')
        def fetch(url): return second
        one, meta = ps.koenigsbach_records(first, 'https://www.koenigsbach-stein.de/veranstaltungen/', fetch)
        two, _ = ps.koenigsbach_records(first, 'https://www.koenigsbach-stein.de/veranstaltungen/', fetch)
        self.assertEqual([r['recordId'] for r in one], [r['recordId'] for r in two])
        self.assertEqual((len(one), meta['pagesFetched']), (2, 2))

    def test_source_refresh_interval_is_daily_but_errors_retry(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        fresh = {'status': 'ok', 'refreshHours': 24, 'lastSuccessAt': '2026-11-18T00:00:00+00:00'}
        old = {'status': 'ok', 'refreshHours': 24, 'lastSuccessAt': '2026-11-17T00:00:00+00:00'}
        broken = {**fresh, 'status': 'partial'}
        self.assertFalse(update_calendar.source_due(fresh, now))
        self.assertTrue(update_calendar.source_due(old, now))
        self.assertTrue(update_calendar.source_due(broken, now))

    def test_daily_schedule_grace_and_optional_sources_stay_in_run_report(self):
        cutoff = datetime(2026, 11, 19, 6, 19, 59, tzinfo=timezone.utc)
        source = {'id':'stutensee', 'name':'Stutensee', 'status':'ok',
                  'refreshHours':24, 'lastSuccessAt':'2026-11-18T06:20:00+00:00'}
        self.assertFalse(update_calendar.source_due(source, cutoff))
        at = cutoff.replace(minute=20, second=1)
        active, _ = update_calendar.problems({'sources':[source]}, date(2026, 11, 19), at)
        self.assertEqual(active, [])
        city = {'id':'karlsruhe', 'name':'Stadt Karlsruhe', 'status':'ok',
                'listingStatus':'error', 'listingLastError':'Beispielausfall'}
        failed = {**source, 'status':'error', 'lastError':'Beispielausfall'}
        report = update_calendar.report_for({'sources':[city, failed], 'events':[],
                                             'generatedAt':at.isoformat()}, at.isoformat(), [])
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['errors'], [])
        self.assertEqual({w['key'] for w in report['sourceWarnings']},
                         {'error:stutensee', 'error:stadtteilbibliothek-groetzingen'})
        self.assertIn('Stutensee', update_calendar.markdown(report))

    def test_optional_venues_do_not_add_to_personal_review(self):
        optional = {'key':'optional', 'status':'review', 'sourceIds':['stutensee']}
        legacy = {'key':'legacy', 'status':'review', 'sourceIds':['durlach']}
        excluded = {'stutensee'}
        self.assertIsNone(review_problem({'rows':[optional]}, 'https://example.org', excluded))
        legacy_only = review_problem({'rows':[legacy]}, 'https://example.org', excluded)
        self.assertEqual(review_problem({'rows':[legacy, optional]}, 'https://example.org', excluded),
                         legacy_only)


if __name__ == '__main__':
    unittest.main()
