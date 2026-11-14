from datetime import date
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import pfinztal_online as imp


class PfinztalOnlineTests(unittest.TestCase):
    def fixture(self,name):return (ROOT/'tests/fixtures'/name).read_bytes()

    def get(self,sid='pfinztal'):
        stem='pfinztal-hdb-online' if sid=='pfinztal-haus-begegnungen' else 'pfinztal-online'
        calls=[]
        def fetch(url):
            calls.append(('get',url))
            return self.fixture(stem+'-source.html')
        def post(url,fields):
            calls.append(('post',url,fields))
            return self.fixture(stem+'-response.json')
        records,metadata=imp.fetch_records(fetch,sid,post,today=date(2026,11,12))
        return records,metadata,calls

    def test_real_calendar_facts_and_correct_berlin_timestamp(self):
        records,metadata,calls=self.get()
        self.assertEqual(len(records),5)
        senior=next(r for r in records if r['title']=='Seniorennachmittag')
        self.assertEqual((senior['startDate'],senior['startTime'],senior['endTime']),('2026-11-12','15:00','18:00'))
        self.assertEqual(senior['location'],'Hagwaldhalle Kleinsteinbach, Industriestr. 4')
        self.assertEqual(senior['url'],'https://pfinztal.de/events/seniorennachmittag-2/')
        self.assertEqual(calls[1][1],'https://pfinztal.de/?evo-ajax=eventon_init_load')
        self.assertEqual(metadata['coverageEnd'],'2027-04-30')

    def test_overnight_period_and_source_record_ids_are_explicit(self):
        records,_,_=self.get()
        market=next(r for r in records if r['title']=='Pfinztaler Weihnachtsmarkt')
        self.assertEqual((market['startDate'],market['endDate']),('2026-11-28','2026-11-29'))
        self.assertEqual((market['startTime'],market['endTime']),('15:00','19:00'))
        self.assertEqual(len({r['recordId'] for r in records}),5)

    def test_hdb_empty_is_confirmed_not_a_structure_failure(self):
        records,metadata,calls=self.get('pfinztal-haus-begegnungen')
        self.assertEqual(records,[]);self.assertTrue(metadata['allowEmpty'])
        self.assertIn('Keine veröffentlichten Termine',metadata['coverageNote'])
        self.assertEqual((metadata['coverageStart'],metadata['coverageEnd']),('2026-11-01','2027-04-30'))
        self.assertEqual(calls[1][2]['cals[evcal_calendar_517][sc][event_type]'],'164')

    def test_calendar_window_crosses_dst_without_fixed_second_approximation(self):
        raw=self.fixture('pfinztal-online-source.html')
        _,_,_,window=imp.request_data(raw,date(2026,11,12))
        self.assertEqual(window['unixStart'],1793487600)
        self.assertEqual(window['unixEnd'],1809122399)
        self.assertEqual((window['end'],window['unixEnd']-window['unixStart']+1),('2027-04-30',181*86400-3600))

    def test_missing_payload_or_unconfirmed_empty_is_rejected(self):
        raw=self.fixture('pfinztal-online-source.html')
        _,_,cid,window=imp.request_data(raw,date(2026,11,12))
        for bad in ({}, {'cals':{cid:{'sc':{'focus_start_date_range':window['unixStart'],'focus_end_date_range':window['unixEnd']},'json':[],'html':'<p>Login required</p>'}}}):
            with self.assertRaises(ValueError):imp.parse_response(json.dumps(bad),cid,window)

    def test_truncated_records_or_ignored_date_range_are_rejected(self):
        _,_,cid,window=imp.request_data(self.fixture('pfinztal-online-source.html'),date(2026,11,12))
        payload=json.loads(self.fixture('pfinztal-online-response.json'))
        payload['cals'][cid]['json'].pop()
        with self.assertRaisesRegex(ValueError,'Terminzahlen'):imp.parse_response(json.dumps(payload),cid,window)
        payload=json.loads(self.fixture('pfinztal-online-response.json'))
        payload['cals'][cid]['sc']['focus_end_date_range']='1'
        with self.assertRaisesRegex(ValueError,'Zeitfenster'):imp.parse_response(json.dumps(payload),cid,window)

    def test_wrong_category_is_not_mislabeled_as_hdb(self):
        with self.assertRaisesRegex(ValueError,'Quellenfilter'):
            imp.fetch_records(lambda url:self.fixture('pfinztal-online-source.html'),
                'pfinztal-haus-begegnungen',post=lambda *args:self.fixture('pfinztal-online-response.json'),today=date(2026,11,12))

    def test_cancelled_schema_is_preserved_and_missing_time_is_not_midnight(self):
        _,_,cid,window=imp.request_data(self.fixture('pfinztal-online-source.html'),date(2026,11,12))
        payload=json.loads(self.fixture('pfinztal-online-response.json'))
        data=payload['cals'][cid]
        data['html']=data['html'].replace('EventScheduled','EventCancelled').replace('15:00 - 18:00(GMT+01:00)','Uhrzeit folgt')
        records=imp.parse_response(json.dumps(payload),cid,window)
        self.assertTrue(all(r['status']=='cancelled' for r in records))
        self.assertIsNone(records[0]['startTime']);self.assertIsNone(records[0]['endTime'])
        self.assertFalse(records[0]['allDay'])

    def test_fixture_excludes_eventon_internal_metadata(self):
        raw=self.fixture('pfinztal-online-response.json').decode()
        self.assertNotIn('event_pmv',raw);self.assertNotIn('_wpnonce',raw)


if __name__=='__main__':unittest.main()
