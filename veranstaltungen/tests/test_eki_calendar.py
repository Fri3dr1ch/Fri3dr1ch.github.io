import importlib.util
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
import unittest

from lxml import etree,html

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

    def paged(self):
        # The CMS actually publishes pagerPage_<id>, an active page and an
        # explicit result range. Keep distinct dated occurrences on each page.
        boxes=html.fromstring(self.fixture().decode('utf-8')).xpath('//div[contains(concat(" ",normalize-space(@class)," ")," v-listefe-item ")]')
        pages={}
        for page in range(1,4):
            start=(page-1)*3+1;end=min(page*3,len(boxes))
            pager=html.Element('div',{'class':'termine-pager'})
            info=etree.SubElement(pager,'div',{'class':'inner-content-pager-info'})
            info.text=f'Ergebnisse {start} - {end} von {len(boxes)}'
            for number in range(1,4):
                link=etree.SubElement(pager,'a',href=f'/terminkalender/?pagerPage_f1092ea5={number}#a-upos-f1092ea5')
                link.text=str(number)
                if page==number:link.set('aria-current','page')
            body=html.Element('main')
            for box in boxes[start-1:end]:body.append(deepcopy(box))
            body.append(pager)
            pages[page]=html.tostring(body,encoding='utf-8')
        return pages

    def fetch_pages(self,pages):
        seen=[]
        def fetch(url):
            seen.append(url)
            page=int(parse_qs(urlsplit(url).query).get('pagerPage_f1092ea5',['1'])[0])
            return pages[page]
        return fetch,seen

    def test_cms_pagination_fetches_all_pages_once_and_keeps_distinct_occurrences(self):
        fetch,seen=self.fetch_pages(self.paged())
        rows,metadata=eki.fetch_records(fetch)
        self.assertEqual({r['recordId'] for r in rows},{r['recordId'] for r in eki.parse_listing(self.fixture())})
        self.assertEqual({r['title'] for r in rows},{r['title'] for r in eki.parse_listing(self.fixture())})
        self.assertEqual(len(rows),7)
        self.assertEqual(metadata['pagesFetched'],3)
        self.assertEqual(len(seen),3)
        self.assertTrue(all('#' not in url for url in seen))
        self.assertEqual(metadata['coverageEnd'],'2026-12-07')

    def test_ignored_pages_changed_total_and_duplicate_records_are_rejected(self):
        pages=self.paged()
        for bad in (pages[1],pages[2].replace(b'von 7',b'von 8'),
                    pages[2].replace(b'id/1068065-',b'id/1068063-').replace(b'14.11.2026',b'12.11.2026')):
            with self.subTest(bad=bad[:30]):
                fetch,_=self.fetch_pages({**pages,2:bad})
                with self.assertRaises(ValueError):eki.fetch_records(fetch)

    def test_missing_page_or_failed_fetch_cannot_be_published_as_complete(self):
        pages=self.paged()
        for p,raw in pages.items():
            tree=html.fromstring(raw.decode('utf-8'))
            for link in tree.xpath('//a[contains(@href,"pagerPage_f1092ea5=2")]'):link.getparent().remove(link)
            pages[p]=html.tostring(tree,encoding='utf-8')
        fetch,_=self.fetch_pages(pages)
        with self.assertRaisesRegex(ValueError,'nicht alle Ergebnisseiten'):eki.fetch_records(fetch)
        def broken(url):
            if '?' in url:raise TimeoutError('offline')
            return self.paged()[1]
        with self.assertRaises(TimeoutError):eki.fetch_records(broken)

    def test_external_or_unrecognized_pagination_links_are_rejected_before_fetch(self):
        first=self.paged()[1]
        for target in ('https://example.org/terminkalender/?pagerPage_f1092ea5=2',
                       '/terminkalender/?page=2','/terminkalender/?pagerPage_f1092ea5=101',
                       '/terminkalender/?pagerPage_f1092ea5=2&amp;filter=other'):
            changed=first.replace(b'/terminkalender/?pagerPage_f1092ea5=2#a-upos-f1092ea5',target.encode())
            seen=[]
            def fetch(url):seen.append(url);return changed
            with self.assertRaises(ValueError):eki.fetch_records(fetch)
            self.assertEqual(seen,[eki.URL])

if __name__=='__main__':unittest.main()
