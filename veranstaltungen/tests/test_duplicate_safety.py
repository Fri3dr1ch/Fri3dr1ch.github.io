from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from calendar_sources import combine_local_sources,is_duplicate,reviewed_match_valid
from import_groetzingen import build_dataset


class DuplicateSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={e['id']:e for e in json.loads((ROOT/'tests/fixtures/duplicate-cases-2026-11.json').read_text())}
        cls.rules=json.loads((ROOT/'data/reviewed-overlaps.json').read_text())

    def pair(self):
        a=deepcopy(self.cases['karlsruhe-13adfd4c93b2aadc'])
        b=deepcopy(self.cases['durlach-12092-2026-11-19-1930'])
        for e in (a,b):e.update(title='Konzert',location='Bürgersaal, Rathaus Grötzingen',organizer='',endTime=None)
        return a,b

    def test_same_title_time_and_generic_venue_in_different_towns_remain_separate(self):
        a=self.cases['groetzingen-jahresprogramm-annual-2026-129']
        b=self.cases['pfinztal-vereine-pdf-2026-wöschbach-p08-r013-2026-11-15-1000']
        self.assertEqual(a['title'],b['title']);self.assertEqual(a['location'],b['location']);self.assertFalse(is_duplicate(a,b))
        x,y=self.pair();x.update(location='Gemeindehaus',region='Grötzingen');y.update(location='Gemeindehaus',region='Pfinztal-Wöschbach')
        self.assertFalse(is_duplicate(x,y))

    def test_punctuation_ampersand_and_reordered_qualified_venue_are_safe_matches(self):
        a,b=self.pair();a['title']='Musik & Literatur';b['title']='„Musik und Literatur …“';b['location']='Rathaus Grötzingen, Bürgersaal'
        self.assertTrue(is_duplicate(a,b))

    def test_missing_or_different_clocks_organizers_and_periods_do_not_merge(self):
        for updates in ({'startTime':None},{'startTime':'20:30'},{'endDate':'2026-11-20'},{'organizer':'Anderer Veranstalter'}):
            a,b=self.pair();a['organizer']='Verein A';b['organizer']='Verein A';b.update(updates)
            self.assertFalse(is_duplicate(a,b),updates)
        a,b=self.pair();a['startTime']=b['startTime']=None;self.assertFalse(is_duplicate(a,b))
        a,b=self.pair();a['endTime']='21:00';b['endTime']='22:00';self.assertFalse(is_duplicate(a,b))

    def test_distinct_performances_from_one_source_are_kept(self):
        for first,second in [('karlsruhe-9787f36ce89ce5d0','karlsruhe-02d7eb8ee2b57bba'),('karlsruhe-ec994a521dc09ce6','karlsruhe-ea46c6ea46b1bb36')]:
            a,b=self.cases[first],self.cases[second]
            self.assertNotEqual(a['startTime'],b['startTime']);self.assertFalse(is_duplicate(a,b))

    def test_ambiguous_multiple_candidates_are_not_arbitrarily_selected(self):
        a,incoming=self.pair();b=deepcopy(a);b['id']='another-source-event';b['sources'][0]['sourceId']='another-source'
        base={'events':[a,b],'sources':[],'undatedEvents':[],'importStats':{'inputRecords':2}}
        snapshot={'source':{'id':'durlach','name':'Durlach'},'events':[incoming]}
        result=combine_local_sources(base,[snapshot]);self.assertEqual(len(result['events']),3)

    def test_confirmed_pairs_merge_with_both_original_sources_and_stable_target_id(self):
        targets={'karlsruhe-13adfd4c93b2aadc','karlsruhe-5d63411234a1e4c2','karlsruhe-d659bb57ca404eb8','remchingen-3413-2027-10-21-2000'}
        for rule in self.rules:
            if rule['targetId'] not in targets:continue
            target=deepcopy(self.cases[rule['targetId']])
            incoming=deepcopy(next(e for e in self.cases.values() if any(s['sourceId']==rule['sourceId'] and s['recordId']==rule['recordId'] for s in e['sources'])))
            base={'events':[target],'sources':[],'undatedEvents':[],'importStats':{'inputRecords':1}}
            snapshot={'source':{'id':rule['sourceId'],'name':'Quelle'},'events':[incoming],'reviewedMatches':[rule]}
            merged=combine_local_sources(base,[snapshot])['events'];self.assertEqual(len(merged),1);self.assertEqual(merged[0]['id'],target['id'])
            self.assertEqual({s['url'] for s in merged[0]['sources']},{target['url'],incoming['url']})
            for field,value in [('title','Anderer Termin'),('location','Anderer Ort'),('startTime','23:00'),('startDate','2026-11-01')]:
                changed=deepcopy(incoming);changed[field]=value
                self.assertFalse(reviewed_match_valid(rule,target,changed))

    def test_checked_pdf_rss_pair_uses_date_time_title_and_location_guards(self):
        rss=deepcopy(self.cases['karlsruhe-155ac6eca1525f72']);pdf=json.loads((ROOT/'data/groetzingen/groetzingen-kultur-natur-2026.json').read_text())
        for changes,expected in (({},1),({'startTime':'19:00'},2),({'location':'Anderer Saal'},2),({'title':'Anderes Programm'},2)):
            record={**rss,**changes};record['sourceGuid']='ka fixture'
            base={'sources':[],'events':[record],'timezone':'Europe/Berlin'}
            result=build_dataset(base,[pdf],'2026-11-14T18:00:00Z',date(2026,11,14))
            cases=[e for e in result['events'] if e['id']==rss['id'] or any(s.get('recordId')=='nature-2026-003' for s in e['sources'])]
            self.assertEqual(len(cases),expected,changes)


if __name__=='__main__':unittest.main()
