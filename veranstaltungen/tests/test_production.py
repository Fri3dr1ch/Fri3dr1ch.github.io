"""Production failures, source-specific date semantics and GitHub hand-off."""
from copy import deepcopy
from contextlib import redirect_stderr
from datetime import date
from io import BytesIO, StringIO
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from http_client import HttpClient, Response, SourceRedirects, public_url
import karlsruhe
import pdf_checks
import update_calendar
from prepare_pages import stage
from build_github_bundle import build, workflow_path
from sync_github_issues import BOT, contents, synchronize


def rss(start='Fri, 13 Nov 2026 19:00:00 +0100', duplicate=False):
    item = f'''<item><title>Ausstellung</title><link>https://kalender.karlsruhe.de/db/termine/ausstellungen/test</link>
    <guid>ka 123</guid><pubDate>{start}</pubDate><description>Kurze Angabe</description></item>'''
    return ('<rss><channel><title>Karlsruhe Kalender</title>' + item * (2 if duplicate else 1) + '</channel></rss>').encode()


def ics(start='20261113T190000', finish='20261113T210000', extra='', uid='123'):
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:' + uid + '\r\nDTSTART' +
            (';VALUE=DATE:' if len(start) == 8 else ':') + start + '\r\nDTEND' +
            (';VALUE=DATE:' if len(finish) == 8 else ':') + finish + '\r\n' + extra +
            'END:VEVENT\r\nEND:VCALENDAR\r\n').encode()


def library_listing():
    return ('<html><title>Stadtteilbibliothek Grötzingen</title>'
            '<a href="https://kalender.karlsruhe.de/db/termine/ausstellungen/test">Ausstellung</a></html>').encode()


class KarlsruheTests(unittest.TestCase):
    def setUp(self):
        self.record = karlsruhe.parse_feed(rss())[0][0]

    def test_floating_time_is_local_and_event_date_is_not_freshness(self):
        event = karlsruhe.detail(self.record, ics(), '2026-11-13T12:00:00Z')
        self.assertEqual(event['start'], '2026-11-13T19:00:00+01:00')
        self.assertEqual(event['detailCheckedAt'], '2026-11-13T12:00:00Z')
        self.assertTrue(event['timeKnown'])

    def test_exhibition_midnight_end_is_publishers_inclusive_final_day(self):
        event = karlsruhe.detail(self.record, ics('20261030T000000', '20261129T000000'), 'now')
        self.assertEqual(event['endDate'], '2026-11-29')
        self.assertTrue(event['longRunning'])
        self.assertFalse(event['timeKnown'])
        self.assertFalse(event['allDay'])

    def test_true_date_end_is_exclusive_and_midnight_concert_end_is_previous_day(self):
        event = karlsruhe.detail(self.record, ics('20261113', '20261115'), 'now')
        self.assertTrue(event['allDay'])
        self.assertEqual(event['endDate'], '2026-11-14')
        event = karlsruhe.detail(self.record, ics('20261113T190000', '20261114T000000'), 'now')
        self.assertEqual(event['endDate'], '2026-11-13')

    def test_publishers_empty_optional_end_is_unknown_but_nonempty_errors_fail(self):
        raw = ics().replace(b'DTEND:20261113T210000', b'DTEND:')
        event = karlsruhe.detail(self.record, raw, 'now')
        self.assertIsNone(event['end'])
        self.assertIsNone(event['endTime'])
        self.assertEqual(event['endDate'], '2026-11-13')
        with self.assertRaises(ValueError):
            karlsruhe.detail(self.record, raw.replace(b'DTEND:', b'DTEND:invalid'), 'now')

    def test_identity_survives_correction_of_start(self):
        previous = {'events': [{**self.record, 'id': 'stable-id'}]}
        corrected = karlsruhe.parse_feed(rss('Sat, 14 Nov 2026 20:00:00 +0100'), previous)[0][0]
        self.assertEqual(corrected['id'], 'stable-id')

    def test_explicit_rss_cancellation_survives_missing_ical_status(self):
        record = {**self.record, 'title': 'Abgesagt: Konzert'}
        self.assertEqual(karlsruhe.detail(record, ics(), 'now')['status'], 'cancelled')
        self.assertEqual(karlsruhe.rss_fallback(record, 'now')['status'], 'cancelled')

    def test_unused_malformed_organizer_does_not_invalidate_dates_or_copy_contacts(self):
        raw = ics(extra='ORGANIZER;CN=Initiative "Test":MAILTO:public@example.org\r\n')
        result = karlsruhe.detail(self.record, raw, 'now')
        self.assertEqual(result['startTime'], '19:00')
        self.assertNotIn('public@example.org', json.dumps(result))

    def test_fail_closed_for_duplicates_uid_recurrence_and_nonexistent_time(self):
        with self.assertRaises(ValueError): karlsruhe.parse_feed(rss(duplicate=True))
        with self.assertRaises(ValueError): karlsruhe.detail(self.record, ics(uid='other'), 'now')
        with self.assertRaises(ValueError): karlsruhe.detail(self.record, ics(extra='RRULE:FREQ=DAILY\r\n'), 'now')
        with self.assertRaises(ValueError): karlsruhe.detail(self.record, ics('20260329T023000', '20260329T040000'), 'now')

    def test_network_failures_keep_valid_details_and_original_check_time(self):
        event = karlsruhe.detail(self.record, ics(), '2026-11-12T10:00:00Z')
        previous = {'sources': [{'id': 'karlsruhe', 'lastSuccessAt': '2026-11-12T10:00:00Z'}], 'events': [event]}
        class Client:
            def __call__(self, url): return rss()
            def request(self, *args, **kwargs): raise TimeoutError('detail timeout')
        result = karlsruhe.refresh(previous, Client())
        self.assertEqual(result['sources'][0]['status'], 'partial')
        self.assertEqual(result['events'][0]['start'], event['start'])
        self.assertEqual(result['events'][0]['detailCheckedAt'], event['detailCheckedAt'])
        self.assertEqual(result['events'][0]['dataQuality'], 'cached')
        def fail(url): raise TimeoutError('feed timeout')
        result = karlsruhe.refresh(previous, fail)
        self.assertEqual(result['sources'][0]['lastSuccessAt'], previous['sources'][0]['lastSuccessAt'])
        self.assertEqual(result['events'], previous['events'])
        self.assertEqual(result['sources'][0]['status'], 'error')

    def test_conditional_304_retains_details_but_updates_rss_title(self):
        event = karlsruhe.detail(self.record, ics(), 'old')
        event['icalEtag'] = 'etag1'
        previous = {'sources': [{'id': 'karlsruhe'}], 'events': [event]}
        class Client:
            def __call__(self, url):
                return library_listing() if url == karlsruhe.GROETZINGEN_LIBRARY else rss().replace(b'Ausstellung', b'Neuer Titel')
            def request(inner, url, headers):
                self.assertEqual(headers['If-None-Match'], 'etag1')
                return Response(b'', 304, {})
        result = karlsruhe.refresh(previous, Client())
        self.assertEqual(result['sources'][0]['status'], 'ok')
        self.assertEqual(result['events'][0]['title'], 'Neuer Titel')
        self.assertEqual(result['events'][0]['end'], event['end'])

    def test_recent_unchanged_detail_reuses_real_check_time_without_network(self):
        event = karlsruhe.detail(self.record, ics(), karlsruhe.stamp())
        previous = {'sources': [{'id': 'karlsruhe'}], 'events': [event]}
        class Client:
            def __call__(self, url): return library_listing() if url == karlsruhe.GROETZINGEN_LIBRARY else rss()
            def request(self, *args, **kwargs): raise AssertionError('recent unchanged detail must be reused')
        result = karlsruhe.refresh(previous, Client())
        self.assertEqual(result['sources'][0]['status'], 'ok')
        self.assertEqual(result['events'][0]['detailCheckedAt'], event['detailCheckedAt'])


class HTTPTests(unittest.TestCase):
    class Reply(BytesIO):
        status = 200
        headers = {}

    def test_retry_is_bounded_and_retry_after_is_capped(self):
        class Opener:
            calls = 0
            def open(inner, *args, **kwargs):
                inner.calls += 1
                if inner.calls < 3: raise HTTPError('url', 429, 'rate limited', {'Retry-After': '900'}, None)
                return HTTPTests.Reply(b'valid')
        opener, waits = Opener(), []
        client = HttpClient(opener=opener, sleep=waits.append)
        self.assertEqual(client('https://kalender.karlsruhe.de/db/termine/rss'), b'valid')
        self.assertEqual(waits, [5, 5])
        self.assertEqual(opener.calls, 3)

    def test_no_credentials_or_unapproved_redirects_and_size_limit(self):
        for url in ('http://kalender.karlsruhe.de/', 'https://evil.example/', 'https://name@kalender.karlsruhe.de/', 'https://kalender.karlsruhe.de:444/'):
            with self.assertRaises(ValueError): public_url(url)
        with self.assertRaises(ValueError):
            SourceRedirects().redirect_request(None, None, 302, '', {}, 'https://evil.example/')
        with self.assertRaises(ValueError): HttpClient().request(karlsruhe.FEED, headers={'Cookie': 'private'})
        class Opener:
            def open(self, *args, **kwargs): return HTTPTests.Reply(b'0123456789')
        with self.assertRaises(ValueError): HttpClient(opener=Opener(), max_bytes=4)(karlsruhe.FEED)

    def test_304_and_host_failure_circuit(self):
        class Opener:
            def open(self, *args, **kwargs): raise HTTPError('url', 304, '', {}, None)
        self.assertEqual(HttpClient(opener=Opener()).request(karlsruhe.FEED).status, 304)
        class Broken:
            calls = 0
            def open(inner, *args, **kwargs):
                inner.calls += 1
                raise URLError('offline')
        opener = Broken()
        client = HttpClient(opener=opener, attempts=1)
        for _ in range(5):
            with self.assertRaises(URLError): client(karlsruhe.FEED)
        with self.assertRaises(ConnectionError): client(karlsruhe.FEED)
        self.assertEqual(opener.calls, 5)


class FakeGitHub:
    def __init__(self, issues=()): self.saved, self.calls = deepcopy(list(issues)), []
    def issues(self): return deepcopy(self.saved)
    def ensure_label(self): pass
    def request(self, method, path, payload):
        self.calls.append((method, path, payload))
        if method == 'POST':
            self.saved.append({**payload, 'number': len(self.saved) + 1, 'state': 'open', 'user': {'login': BOT}})
        else:
            issue = next(i for i in self.saved if i['number'] == int(path.split('/')[-1]))
            issue.update(payload)


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.problem = {'key': 'review:naturfreunde-groetzingen', 'kind': 'review', 'sourceName': 'NFH Grötzingen', 'reason': 'Monatsprüfung', 'dueOn': '2026-12-01', 'lastReviewedAt': '2026-11-13'}
        self.report = {'validationPassed': True, 'issues': [self.problem], 'resolvedKeys': [], 'errors': []}

    def test_one_issue_repeated_runs_no_spam_then_close_and_reopen(self):
        api = FakeGitHub()
        self.assertEqual(synchronize(self.report, api, 'owner')[0]['action'], 'create')
        again = deepcopy(self.report)
        again['issues'][0]['lastSuccessAt'] = '2026-12-02T12:00:00Z'
        self.assertEqual(synchronize(again, api, 'owner', 'different-run'), [])
        resolved = {'validationPassed': True, 'issues': [], 'resolvedKeys': [self.problem['key']]}
        self.assertEqual(synchronize(resolved, api, 'owner')[0]['action'], 'close')
        self.assertEqual(synchronize(self.report, api, 'owner')[0]['action'], 'update')
        self.assertEqual(len(api.saved), 1)
        self.assertEqual(api.saved[0]['state'], 'open')

    def test_manual_issue_untouched_and_invalid_run_closes_nothing(self):
        title, body, _ = contents(self.problem)
        manual = {'number': 9, 'title': title, 'body': body, 'state': 'open', 'user': {'login': 'owner'}}
        api = FakeGitHub([manual])
        synchronize(self.report, api, 'owner')
        self.assertEqual(api.saved[0], manual)
        bad = {'validationPassed': False, 'resolvedKeys': [self.problem['key']], 'errors': [{'reason': 'invalid JSON'}]}
        actions = synchronize(bad, api, 'owner')
        self.assertEqual([a['key'] for a in actions], ['error:pipeline'])
        self.assertEqual(api.saved[1]['state'], 'open')

    def test_mentions_are_neutralized_and_dry_run_has_no_writes(self):
        problem = {**self.problem, 'reason': '@someone <script> `test`'}
        _, body, _ = contents(problem)
        self.assertNotIn('@someone', body)
        self.assertNotIn('<script>', body)
        api = FakeGitHub()
        synchronize(self.report, api, 'owner', dry_run=True)
        self.assertEqual(api.calls, [])


class PDFAndPipelineTests(unittest.TestCase):
    def test_new_pdf_triggers_review_even_while_old_pdf_remains_linked(self):
        extracts = pdf_checks.load_extracts(ROOT)
        import hashlib
        for item in extracts: item['sha256'] = hashlib.sha256(b'approved').hexdigest()
        links = [s['pdfUrl'] for s in extracts] + [extracts[0]['pdfUrl'].replace('2026', '2027')]
        page = ''.join('<a href="' + u + '">PDF</a>' for u in links).encode()
        def fetch(url): return page if url == pdf_checks.SOURCE_PAGE else b'approved'
        old = [{'id': s['id'], 'lastSuccessAt': '2026-11-12T10:00:00Z'} for s in extracts]
        with patch.object(pdf_checks, 'load_extracts', return_value=extracts):
            result = pdf_checks.check(ROOT, fetch, old)
        first = result[extracts[0]['id']]
        self.assertTrue(first['reviewRequired'])
        self.assertEqual(first['lastSuccessAt'], '2026-11-12T10:00:00Z')
        self.assertEqual(first['lastReviewedAt'], extracts[0]['reviewedOn'])
        self.assertFalse(result[extracts[1]['id']]['reviewRequired'])

    def test_expired_manual_coverage_triggers_issue_even_before_regular_due(self):
        data = {'sources': [{'id': 'groetzingen-jahresprogramm', 'name': 'PDF', 'status': 'ok',
                            'lastReviewedAt': '2026-11-13', 'coverageEnd': '2026-12-31'}]}
        active, _ = update_calendar.problems(data, date(2027, 1, 1))
        self.assertEqual(active[0]['kind'], 'review')
        self.assertIn('abgelaufen', active[0]['reason'])

    def test_fatal_validation_does_not_authorize_publish_or_replace_public_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'dist').mkdir()
            target = root / 'dist/events.json'
            target.write_text('{"seed":true}')
            # Tests inherit the runner's GITHUB_STEP_SUMMARY. Route the expected
            # failure into a private test file, never the real run summary.
            summary = root / 'test-summary.md'
            errors = StringIO()
            with patch.object(update_calendar, 'build_current', side_effect=ValueError('broken source')), \
                    patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}), \
                    redirect_stderr(errors):
                code = update_calendar.main(['--root', str(root), '--offline'])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads((root / '.etl/report.json').read_text())['validationPassed'])
            self.assertEqual(target.read_text(), '{"seed":true}')
            self.assertIn('ValueError: broken source', summary.read_text())
            self.assertIn('ValueError: broken source', errors.getvalue())


class DeliveryTests(unittest.TestCase):
    def test_pages_keeps_existing_homepage_and_maths_but_only_calendar_dist(self):
        with tempfile.TemporaryDirectory() as folder:
            root, output = Path(folder) / 'repo', Path(folder) / 'public'
            root.mkdir()
            (root / 'index.html').write_text('existing homepage')
            (root / 'math snapshots').mkdir()
            (root / 'math snapshots/example.html').write_text('existing maths')
            calendar = root / 'veranstaltungen'
            (calendar / 'dist').mkdir(parents=True)
            (calendar / 'dist/index.html').write_text('calendar')
            (calendar / 'dist/events.json').write_text('{}')
            (calendar / 'private.py').write_text('source only')
            (root / '.github').mkdir()
            stage(root, calendar, output)
            self.assertEqual((output / 'index.html').read_text(), 'existing homepage')
            self.assertEqual((output / 'math snapshots/example.html').read_text(), 'existing maths')
            self.assertTrue((output / 'veranstaltungen/events.json').is_file())
            self.assertFalse((output / 'veranstaltungen/private.py').exists())
            self.assertFalse((output / '.github').exists())
            with self.assertRaises(ValueError): stage(root, calendar, output)
            with self.assertRaises(ValueError): stage(root, calendar, root / 'output')

    def test_bundle_has_repo_root_workflow_and_no_private_site_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            target = build(ROOT, Path(folder) / 'calendar.zip')
            with ZipFile(target) as archive:
                names = archive.namelist()
                self.assertIn('.github/workflows/calendar.yml', names)
                self.assertIn('veranstaltungen/scripts/update_calendar.py', names)
                self.assertIn('veranstaltungen/DEPLOYMENT.md', names)
                self.assertFalse(any('/.openai/' in n or '/node_modules/' in n or '/.etl/' in n for n in names))

    def test_workflow_deploys_only_uploaded_validated_pages_with_minimal_permissions(self):
        import yaml
        workflow = yaml.load(workflow_path(ROOT).read_text(), Loader=yaml.BaseLoader)
        self.assertIn('schedule', workflow['on'])
        self.assertNotIn('paths', workflow['on']['push'])
        update = workflow['jobs']['update']
        steps = {s['id']: s for s in update['steps'] if 'id' in s}
        self.assertIn("steps.checks.outcome == 'success'", steps['persist']['if'])
        self.assertIn("steps.report.outputs.valid == 'true'", steps['persist']['if'])
        self.assertIn("steps.etl.outcome != 'success'", steps['final']['if'])
        self.assertIn("|| steps.checks.outcome != 'success'", steps['final']['if'])
        self.assertIn("|| steps.report.outputs.valid != 'true'", steps['final']['if'])
        self.assertEqual(steps['final']['run'], 'exit 1')
        self.assertNotIn('continue-on-error', steps['final'])
        self.assertIn("steps.stage.outcome == 'success'", steps['pages-upload']['if'])
        self.assertIn("needs.update.outputs.pages_ready == 'true'", workflow['jobs']['deploy']['if'])
        self.assertNotIn('pages', update['permissions'])
        self.assertEqual(workflow['jobs']['deploy']['permissions']['pages'], 'write')
        import re
        for step in update['steps'] + workflow['jobs']['deploy']['steps']:
            if 'uses' in step: self.assertRegex(step['uses'], r'@' + '[a-f0-9]{40}$')

    def test_workflow_report_distinguishes_failed_checks_and_keeps_import_errors(self):
        import yaml
        workflow=yaml.load(workflow_path(ROOT).read_text(),Loader=yaml.BaseLoader)
        step=next(s for s in workflow['jobs']['update']['steps'] if s.get('id')=='report')
        script=step['run'].split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
        for etl,checks,initial,expected,reason in (
            ('success','failure',True,False,'Daten- und Anwendungsprüfungen'),
            ('skipped','skipped',True,False,'Installation'),
            ('success','success',True,True,None),
            ('failure','success',False,False,None),
        ):
            with self.subTest(etl=etl,checks=checks), tempfile.TemporaryDirectory() as folder:
                folder=Path(folder);(folder/'.etl').mkdir()
                report={'validationPassed':initial,'issues':[{'key':'review:venues'}],'errors':[] if initial else [{'reason':'Original import error'}]}
                target=folder/'.etl/report.json';target.write_text(json.dumps(report))
                output,summary=folder/'output',folder/'gha-summary.md'
                env={**os.environ,'ETL_OUTCOME':etl,'CHECK_OUTCOME':checks,'GITHUB_OUTPUT':str(output),'GITHUB_STEP_SUMMARY':str(summary)}
                subprocess.run([sys.executable,'-c',script],cwd=folder,env=env,check=True,capture_output=True,text=True)
                actual=json.loads(target.read_text())
                self.assertEqual(actual['validationPassed'],expected)
                self.assertEqual(output.read_text(),'valid='+str(expected).lower()+'\n')
                self.assertEqual(actual['issues'],report['issues'])
                self.assertEqual(actual['workflow'],{'etlOutcome':etl,'checkOutcome':checks})
                if reason:
                    self.assertIn(reason,actual['errors'][-1]['reason'])
                    self.assertIn('Veröffentlichung gestoppt',summary.read_text())
                    self.assertIn('Veröffentlichung gestoppt',(folder/'.etl/summary.md').read_text())
                else:self.assertEqual(actual['errors'],report['errors'])

    def test_installed_github_layout_can_resolve_and_rebuild_its_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            initial = build(ROOT, folder / 'initial.zip')
            repo = folder / 'repository'
            with ZipFile(initial) as archive:
                archive.extractall(repo)  # Archive just built from this trusted source.
            project = repo / 'veranstaltungen'
            self.assertEqual(workflow_path(project), repo / '.github/workflows/calendar.yml')
            self.assertFalse((project / '.github').exists())
            rebuilt = build(project, folder / 'rebuilt.zip')
            with ZipFile(initial) as a, ZipFile(rebuilt) as b:
                self.assertEqual(set(a.namelist()), set(b.namelist()))
                for name in a.namelist(): self.assertEqual(a.read(name), b.read(name), name)

    def test_missing_or_ambiguous_workflow_does_not_silently_package_wrong_file(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / 'veranstaltungen'
            project.mkdir()
            with self.assertRaises(ValueError): workflow_path(project)
            parent = project.parent / '.github/workflows/calendar.yml'
            child = project / '.github/workflows/calendar.yml'
            for path in (parent, child):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('name: calendar')
            with self.assertRaises(ValueError): workflow_path(project)


if __name__ == '__main__': unittest.main()
