from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs,urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import walzbachtal as w
import import_local_calendars as imp


class WalzbachtalTests(unittest.TestCase):
    def fixture(self,name):return (ROOT/'tests/fixtures'/name).read_bytes()
    def fetcher(self,change=None):
        seen=[]
        def fetch(url):
            seen.append(url)
            if url==w.URL:return self.fixture('walzbachtal-widget.html')
            query=parse_qs(urlsplit(url).query)
            self.assertEqual(query['filters[date][from]'],['2026-11-01 00:00:00'])
            self.assertEqual(query['src'],['fixture-public-document'])
            page=int(query['page'][0]);data=json.loads(self.fixture(f'walzbachtal-page-{page}.json'))
            if change:data=change(data,page)
            return json.dumps(data).encode()
        return fetch,seen

    def test_public_request_reads_all_pages_dates_places_and_coorganizers(self):
        fetch,seen=self.fetcher();records,metadata=w.fetch_records(fetch,date(2026,11,14))
        self.assertEqual(len(records),4);self.assertEqual(len(seen),3);self.assertEqual(metadata['pagesFetched'],2)
        concert=next(r for r in records if r['recordId']=='4815')
        self.assertEqual((concert['startTime'],concert['endTime']),('17:00','19:00'))
        self.assertEqual(concert['region'],'Walzbachtal-Jöhlingen');self.assertIn('Aussegnungshalle Jöhlingen',concert['location'])
        walk=next(r for r in records if r['recordId']=='5558')
        self.assertIn('Volkshochschule',walk['organizer']);self.assertNotIn('+1',walk['organizer']);self.assertEqual(walk['location'],'')
        self.assertTrue(next(r for r in records if r['recordId']=='4795')['allDay'])
        festival=next(r for r in records if r['recordId']=='6131')
        self.assertEqual((festival['startDate'],festival['endDate']),('2027-04-30','2027-05-01'))

    def test_ignored_pages_or_changed_total_are_rejected(self):
        for change in (lambda d,p:json.loads(self.fixture('walzbachtal-page-1.json')),
                       lambda d,p:{**d,'meta':{'total':5}} if p==2 else d,
                       lambda d,p:{**d,'results_length':1} if p==2 else d):
            fetch,_=self.fetcher(change)
            with self.assertRaises(ValueError):w.fetch_records(fetch,date(2026,11,14))

    def test_explicit_empty_answer_is_success_but_unknown_structure_is_not(self):
        def fetch(url):
            return self.fixture('walzbachtal-widget.html') if url==w.URL else b'{"meta":{"total":0},"results_length":0,"results":""}'
        rows,metadata=w.fetch_records(fetch,date(2026,11,14));self.assertEqual(rows,[]);self.assertTrue(metadata['allowEmpty'])
        with self.assertRaises((ValueError,KeyError)):w.parse_response(b'{}')

    def test_midnight_and_unknown_time_remain_distinct_from_all_day(self):
        data=json.loads(self.fixture('walzbachtal-page-2.json'))
        unknown=deepcopy(data);unknown['results']=unknown['results'].replace('ganztägig','')
        rows,_=w.parse_response(json.dumps(unknown));party=next(r for r in rows if r['recordId']=='4795')
        self.assertIsNone(party['startTime']);self.assertFalse(party['allDay'])
        for raw in (data['results'].replace('2027-04-30 17:00:00','2027-04-30 18:00:00'),data['results'].replace('bis 01. Mai','bis Datum offen')):
            with self.assertRaises(ValueError):w.parse_response(json.dumps({**data,'results':raw}))

    def test_source_contract_prevents_hidden_filters_or_foreign_endpoints(self):
        raw=self.fixture('walzbachtal-widget.html')
        from lxml import html
        variants=[raw.replace(w.ENDPOINT.encode(),b'https://example.org/api')]
        for field,value in (('options',{'location_visible':0,'time_visible':1,'organizer_visible':1}),('filters',{'date':['2026-11-14',None],'district':['707']})):
            tree=html.fromstring(raw.decode());tree.set(field,json.dumps(value));variants.append(html.tostring(tree))
        for changed in variants:
            with self.assertRaises(ValueError):w.request_config(changed,date(2026,11,14))
        self.assertEqual(w.end_date(date(2026,12,31),'bis 01. Januar'),date(2027,1,1))

    def test_failed_page_retains_the_complete_previous_snapshot(self):
        fetch,_=self.fetcher();rows,metadata=w.fetch_records(fetch,date(2026,11,14))
        old=imp.snapshot_from_records(imp.SOURCES['walzbachtal'],rows,'2026-11-14T10:00:00Z',metadata)
        def broken(url):
            if 'page=2' in url:raise TimeoutError('page offline')
            return fetch(url)
        # Pin the adapter date to avoid changing its requested month in this fixture.
        from unittest.mock import patch
        with patch('walzbachtal.datetime') as clock:
            from datetime import datetime
            clock.now.return_value=datetime(2026,11,14);clock.strptime=datetime.strptime
            retained,error=imp.refresh('walzbachtal','2026-11-14T12:00:00Z',old,broken)
        self.assertTrue(error);self.assertEqual(retained['events'],old['events']);self.assertEqual(retained['source']['lastSuccessAt'],old['source']['lastSuccessAt'])


if __name__=='__main__':unittest.main()
