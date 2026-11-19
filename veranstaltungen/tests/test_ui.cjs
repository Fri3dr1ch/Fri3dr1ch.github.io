// Optional: npm install --no-save jsdom@26.1.0
// These DOM checks do not verify native browser layout or native dialog behavior.
const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const liveHtml=fs.readFileSync(path.join(__dirname,'../dist/index.html'),'utf8');
// Historical UI scenarios stay reproducible when sources remove past events.
// The current production data is separately rendered in the smoke check below.
const scenario=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui-scenarios-2026-11.json'),'utf8'));
const html=liveHtml.replace(/(<script id="event-data" type="application\/json">)[\s\S]*?(<\/script><!-- \/event-data -->)/,
 (_,open,close)=>open+JSON.stringify(scenario).replace(/</g,'\\u003c')+close);
const dialogSupport=w=>{
 w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
 w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
};
const errors=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e));
const dom=new JSDOM(html,{runScripts:'dangerously',url:'https://calendar.example/',virtualConsole,beforeParse(w){
 dialogSupport(w);
 const NativeDate=w.Date,fixed=NativeDate.parse('2026-11-13T12:00:00Z');
 w.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};
}});
const {document:d,Event}=dom.window,$=id=>d.getElementById(id);
let checks=0;
const test=(name,fn)=>{fn();checks++;console.log('OK',name);};
const inputs=id=>[...$(id).querySelectorAll('input[data-filter-option]')];
const selectMany=(id,values)=>{for(const input of inputs(id))input.checked=values.includes(input.value);$(id).dispatchEvent(new Event('change',{bubbles:true}));};
const set=(id,value)=>{if(id==='category'||id==='source-filter'){selectMany(id,inputs(id).filter(i=>!value||i.value===value||(value==='groetzingen'&&i.value.startsWith('groetzingen'))).map(i=>i.value));}else{$(id).value=value;$(id).dispatchEvent(new Event(id==='search'?'input':'change'));}};
const dataset=JSON.parse($('event-data').textContent);
const firstEvent=()=>d.querySelector('#agenda-events [data-event]');
test('current embedded production data loads and renders independently of fixtures',()=>{
 const liveErrors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>liveErrors.push(e));
 const live=new JSDOM(liveHtml,{runScripts:'dangerously',url:'https://calendar.example/',virtualConsole:vc,beforeParse:dialogSupport});
 try{
  const doc=live.window.document,embedded=JSON.parse(doc.getElementById('event-data').textContent);
  assert.equal(liveErrors.length,0);
  assert.equal(doc.getElementById('source-count').textContent,`${embedded.events.length} Termine · ${embedded.sources.length} Quellen`);
  assert.ok(doc.querySelectorAll('.day').length>=28);
 }finally{live.window.close();}
});
test('new publisher domains open the exact published detail and provenance links',()=>{
 const domains=['naturtreff-groetzingen.de','www.vhs-karlsruhe-land.de','www.hagsfeld.de','www.stutensee.de','www.bergdoerfer.info','www.karlsbad.de','www.koenigsbach-stein.de'];
 const sample=structuredClone(scenario);
 sample.events=domains.map((host,i)=>{
  const e={...scenario.events[0],id:'new-source-'+i,title:'Termin '+i,startDate:'2026-11-19',endDate:'2026-11-19',url:'https://'+host+'/termine/'+i,longRunning:false,openingOccurrences:null};
  e.sources=[{sourceId:e.sourceId,sourceName:e.sourceName,url:e.url}];return e;
 });
 const page=liveHtml.replace(/(<script id="event-data" type="application\/json">)[\s\S]*?(<\/script><!-- \/event-data -->)/,
  (_,a,b)=>a+JSON.stringify(sample).replace(/</g,'\\u003c')+b);
 const demo=new JSDOM(page,{runScripts:'dangerously',url:'https://calendar.example/',beforeParse(w){
  dialogSupport(w);
  const NativeDate=w.Date,fixed=NativeDate.parse('2026-11-19T12:00:00Z');
  w.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};
 }});
 try{
  const doc=demo.window.document;
  doc.querySelector('[data-day="2026-11-19"]').click();
  for(const e of sample.events){
   doc.querySelector('#agenda-events [data-event="'+e.id+'"]').click();
   assert.equal(doc.querySelector('#detail-original').href,e.url);
   assert.equal(doc.querySelector('#detail-source-links a').href,e.url);
   doc.querySelector('#close-dialog').click();
  }
 }finally{demo.window.close();}
});
test('all sources load and calendar renders',()=>{
 assert.equal(errors.length,0);assert.equal($('source-count').textContent,`${dataset.events.length} Termine · ${dataset.sources.length} Quellen`);
 assert.equal(inputs('source-filter').length,dataset.sources.length);assert.ok(inputs('source-filter').every(i=>i.checked));assert.ok(d.querySelectorAll('.day').length>=28);
 assert.doesNotMatch(d.body.textContent,/Privater Entwurf|private Vorschau/);
});
test('combined Grötzingen filter includes merged RSS/PDF events',()=>{
 set('source-filter','groetzingen');
 for(const node of d.querySelectorAll('[data-event]')){
  const e=dataset.events.find(e=>e.id===node.dataset.event);
  assert.ok(e.sources.some(s=>s.sourceId.startsWith('groetzingen')));
 }
 const button=d.querySelector('[data-day="2026-11-19"]');assert.ok(button);button.click();
 assert.ok([...d.querySelectorAll('#agenda-events [data-event]')].some(n=>n.textContent.includes('Naturparadies Rheinauen')));
});
test('source-specific and category filters combine',()=>{
 set('source-filter','groetzingen-jahresprogramm');set('category','musik');
 for(const node of d.querySelectorAll('[data-event]')){
  const e=dataset.events.find(e=>e.id===node.dataset.event);
  assert.equal(e.categoryKey,'musik');assert.ok(e.sources.some(s=>s.sourceId==='groetzingen-jahresprogramm'));
 }
});
test('new Grötzingen-only PDF event opens its source and hides missing ICS',()=>{
 set('category','');set('source-filter','groetzingen');set('search','Grötzingen glüht');
 d.querySelector('[data-day="2026-11-28"]').click();firstEvent().click();
 assert.match($('detail-title').textContent,/Grötzingen glüht/);
 assert.ok($('detail-original').href.endsWith('.pdf#page=1'));
 assert.equal($('detail-ical').hidden,true);assert.equal($('detail-source-links').children.length,2);
 $('close-dialog').click();
});
test('conflicting times are visible with both provenance links',()=>{
 set('search','Klavierfest');d.querySelector('[data-day="2026-11-21"]').click();firstEvent().click();
 assert.match($('detail-note').textContent,/18:00/);assert.match($('detail-note').textContent,/19:00/);
 assert.equal($('detail-note').hidden,false);assert.equal($('detail-source-links').children.length,2);
 assert.equal($('detail-ical').hidden,false);$('close-dialog').click();
});
test('obsolete undated candidates and past month hints are no longer shown',()=>{
 set('search','');set('category','');set('source-filter','groetzingen-jahresprogramm');
 assert.equal($('undated-section').hidden,true);assert.equal($('undated-list').children.length,0);
 assert.doesNotMatch($('undated-list').textContent,/Brennballturnier|Gleiszellen/);
});
test('half-year review date and source scope are displayed correctly',()=>{
 set('source-filter','');
 assert.match($('review-reminder').textContent,/15. Januar 2027/);
 assert.match($('coverage-note').textContent,/Stand Juli/);assert.match($('coverage-note').textContent,/Stand September/);
 assert.match($('source-details').textContent,new RegExp(dataset.importStats.deduplicatedRecords+' mehrfach'));
});
test('Durlach filter includes merged events and working original links',()=>{
 set('source-filter','durlach');set('search','Lesecafé');
 d.querySelector('[data-day="2026-11-18"]').click();firstEvent().click();
 assert.equal($('detail-source-links').children.length,2);
 assert.ok($('detail-source-links').querySelector('a[href^="https://www.durlacher.de/"]'));
 $('close-dialog').click();set('search','Historischer Stammtisch');d.querySelector('[data-day="2026-11-26"]').click();
 assert.match(firstEvent().textContent,/Abgesagt/);firstEvent().click();
 assert.equal($('detail-original').hidden,false);assert.equal(new URL($('detail-original').href).hostname,'www.durlacher.de');
 assert.equal($('detail-ical').hidden,true);assert.match($('detail-note').textContent,/abgesagt/);$('close-dialog').click();
});
test('new source filter combines with categories and uses Baden source URLs',()=>{
 set('source-filter','weingarten');set('search','KVV');d.querySelector('[data-day="2026-11-12"]').click();
 firstEvent().click();assert.equal(new URL($('detail-original').href).hostname,'www.weingarten-baden.de');
 assert.equal($('detail-original').hidden,false);assert.match($('detail-time').textContent,/10:00–11:00/);$('close-dialog').click();
 set('category','musik');set('search','');
 for(const node of d.querySelectorAll('[data-event]')){
  const e=dataset.events.find(e=>e.id===node.dataset.event);
  assert.equal(e.categoryKey,'musik');assert.ok(e.sources.some(s=>s.sourceId==='weingarten'));
 }
 set('category','');
});
test('checked two-day market shows both daily opening hours',()=>{
 set('source-filter','durlach');set('search','HWK');d.querySelector('[data-day="2026-11-14"]').click();
 assert.equal(d.querySelectorAll('#agenda-events [data-event]').length,1);
 assert.match(firstEvent().querySelector('.event-time').textContent,/08:00/);firstEvent().click();
 assert.match($('detail-time').textContent,/Jeweils 08:00–18:00/);assert.equal($('detail-source-links').children.length,3);
 $('close-dialog').click();
});
test('all added source filters retain merged provenance and correct scope',()=>{
 set('search','');set('category','');
 for(const sourceId of ['pfinztal','pfinztal-vereine','remchingen','kulturhalle-remchingen','eki-groetzingen','walzbachtal','pfinztal-haus-begegnungen']){
  set('source-filter',sourceId);
  for(const node of d.querySelectorAll('[data-event]')){
   const e=dataset.events.find(e=>e.id===node.dataset.event);
   assert.ok(e.sources.some(s=>s.sourceId===sourceId));
  }
  assert.match($('coverage-note').textContent,new RegExp(dataset.sources.find(s=>s.id===sourceId).name));
 }
 assert.match($('coverage-note').textContent,/Keine veröffentlichten Termine/);
 assert.equal(d.querySelectorAll('#agenda-events [data-event]').length,0);
 assert.doesNotMatch($('source-details').textContent,/undefined/);
});
test('organizer correction and new original domains work in details',()=>{
 set('source-filter','eki-groetzingen');set('search','Volkstrauertag');d.querySelector('[data-day="2026-11-15"]').click();firstEvent().click();
 assert.match($('detail-time').textContent,/11:30/);assert.match($('detail-note').textContent,/11:00/);
 assert.equal(new URL($('detail-original').href).hostname,'eki-groetzingen.de');assert.equal($('detail-original').hidden,false);$('close-dialog').click();
 set('source-filter','pfinztal');set('search','Seniorennachmittag');d.querySelector('[data-day="2026-11-12"]').click();firstEvent().click();
 assert.equal(new URL($('detail-original').href).hostname,'pfinztal.de');assert.equal($('detail-original').hidden,false);$('close-dialog').click();
 set('source-filter','remchingen');set('search','Repair');d.querySelector('[data-day="2026-11-20"]').click();firstEvent().click();
 assert.equal(new URL($('detail-original').href).hostname,'www.remchingen.de');assert.equal($('detail-original').hidden,false);$('close-dialog').click();
 set('source-filter','kulturhalle-remchingen');set('search','Sing A Song');d.querySelector('[data-day="2026-11-20"]').click();firstEvent().click();
 assert.equal(new URL($('detail-original').href).hostname,'www.kulturhalle-remchingen.de');assert.equal($('detail-original').hidden,false);
 assert.match($('detail-note').textContent,/ausverkauft/);$('close-dialog').click();
});
test('Pfinztal ambiguous rows have their own attribution and PDF page links',()=>{
 set('search','');set('source-filter','pfinztal-vereine');
 assert.equal($('undated-list').children.length,4);assert.match($('undated-list').textContent,/Pfinztal/);
 assert.match($('undated-list').textContent,/15. November 2026 oder 22. November 2026/);
 assert.match($('undated-list').textContent,/Termin offen · Kalenderausgabe 2026/);
 assert.doesNotMatch($('undated-list').textContent,/28.06.2025|Zirkusprojekt|Unbenannter Eintrag/);
 assert.equal($('undated-list').querySelectorAll('a[href*="pfinztal.de/"][href*="#page="]').length,4);
});
test('list view, search reset, and month navigation still work',()=>{
 set('source-filter','');set('search','zzzzzz-no-matching-event');
 d.querySelector('.empty-reset').click();assert.ok(inputs('source-filter').every(i=>i.checked));assert.ok(inputs('category').every(i=>i.checked));
 $('list-view-button').click();assert.equal($('list-view').hidden,false);assert.equal($('month-view').hidden,true);
 assert.ok($('list-view').querySelectorAll('.event-row').length>0);
 $('month-view-button').click();const before=$('month-title').textContent;$('next-month').click();$('previous-month').click();assert.equal($('month-title').textContent,before);
});
test('Berghausen postponement and original source remain visible',()=>{
 set('source-filter','naturfreunde-berghausen');set('search','Baumpflanz');
 d.querySelector('[data-day="2026-11-21"]').click();
 assert.match(firstEvent().textContent,/Verschoben/);firstEvent().click();
 assert.match($('detail-note').textContent,/neuer Termin.*nicht bekannt/i);
 assert.equal(new URL($('detail-original').href).hostname,'www.naturfreunde-berghausen.de');
 assert.equal($('detail-original').hidden,false);$('close-dialog').click();
});
test('Söllingen opening is one ongoing offer with real dates and feed link',()=>{
 set('source-filter','naturfreunde-soellingen');set('search','Hausöffnung');
 assert.equal(d.querySelectorAll('#ongoing-grid [data-event]').length,1);
 assert.equal(d.querySelectorAll('#agenda-events [data-event]').length,0);
 const card=d.querySelector('#ongoing-grid [data-event]');assert.match(card.textContent,/Sonntags 12:00–18:00/);card.click();
 assert.match($('detail-openings').textContent,/15.11./);assert.equal($('detail-openings').hidden,false);
 assert.equal(new URL($('detail-ical').href).hostname,'calendar.google.com');assert.equal($('detail-ical').hidden,false);
 $('close-dialog').click();
 for(let i=0;i<9;i++)$('next-month').click();
 assert.match($('month-title').textContent,/August 2027/);assert.equal($('ongoing-count').textContent,'(0)');
 for(let i=0;i<9;i++)$('previous-month').click();
});
test('reviewed Grötzingen event and monthly review date are accessible',()=>{
 set('source-filter','naturfreunde-groetzingen');set('search','Arbeitseinsatz');
 d.querySelector('[data-day="2026-11-14"]').click();firstEvent().click();
 assert.match($('detail-time').textContent,/10:00/);assert.match($('detail-original').href,/1a_Nov-Dez26.jpg/);
 assert.equal($('detail-original').hidden,false);assert.equal($('detail-openings').hidden,true);$('close-dialog').click();
 assert.match($('review-reminder').textContent,/1. Dezember 2026/);
 assert.equal(dataset.events.filter(e=>e.title.includes('Eintopf')&&e.startDate==='2026-11-15').length,0);
});
test('BauSteinWelten remains an unchanged ongoing exhibition',()=>{
 set('source-filter','karlsruhe');set('search','BauSteinWelten');
 assert.equal(d.querySelectorAll('#ongoing-grid [data-event]').length,1);
 const exhibit=dataset.events.find(e=>e.title==='BauSteinWelten');assert.equal(exhibit.endDate,'2026-11-29');assert.equal(exhibit.longRunning,true);
});
test('source HTML does not create executable event markup',()=>{
 assert.equal(d.querySelectorAll('script').length,2);assert.equal(d.querySelectorAll('[onclick],[onerror]').length,0);assert.equal(errors.length,0);
});
test('Walzbachtal includes Jöhlingen and exposes its original calendar link',()=>{
 set('search','');set('category','');set('source-filter','walzbachtal');d.querySelector('[data-day="2026-11-15"]').click();firstEvent().click();
 assert.match($('detail-title').textContent,/Gemeinschaftskonzert/);assert.match($('detail-location').textContent,/Jöhlingen/);
 assert.equal(new URL($('detail-original').href).hostname,'www.walzbachtal.de');assert.equal($('detail-original').hidden,false);assert.match($('detail-time').textContent,/17:00–19:00/);$('close-dialog').click();
});
test('generic venues in different towns remain distinguishable on the same day',()=>{
 set('search','Lokalschau');set('category','');selectMany('source-filter',['groetzingen-jahresprogramm','pfinztal-vereine']);d.querySelector('[data-day="2026-11-15"]').click();
 const rows=[...d.querySelectorAll('#agenda-events [data-event]')];assert.equal(rows.length,2);
 assert.ok(rows.some(r=>r.querySelector('.event-meta').textContent.includes('Hasenheim · Grötzingen')));
 assert.ok(rows.some(r=>r.querySelector('.event-meta').textContent.includes('Hasenheim · Pfinztal-Wöschbach')));
});
test('performances with different source start times stay distinct and show their times',()=>{
 set('source-filter','karlsruhe');set('category','');set('search','Elternabend');$('more-ongoing').hidden||$('more-ongoing').click();
 const cards=[...d.querySelectorAll('#ongoing-grid [data-event]')];assert.equal(cards.length,2);
 assert.ok(cards.some(c=>c.textContent.includes('14:30')));assert.ok(cards.some(c=>c.textContent.includes('19:00')));
 cards[0].click();assert.match($('detail-time').textContent,/Beginn laut Quelle/);$('close-dialog').click();set('search','');
});
test('multiple sources and categories form unions within each filter and intersect across filters',()=>{
 set('search','');selectMany('source-filter',['durlach','eki-groetzingen']);selectMany('category',['musik','stadtleben']);$('list-view-button').click();
 const shown=[...d.querySelectorAll('#list-view [data-event]')].map(n=>dataset.events.find(e=>e.id===n.dataset.event));
 const expected=dataset.events.filter(e=>!e.longRunning&&e.startDate<='2026-11-30'&&e.endDate>='2026-11-01'&&['musik','stadtleben'].includes(e.categoryKey)&&e.sources.some(s=>['durlach','eki-groetzingen'].includes(s.sourceId)));
 assert.deepEqual(new Set(shown.map(e=>e.id)),new Set(expected.map(e=>e.id)));assert.ok(shown.length>0);
 assert.equal($('source-summary').textContent,'2 Quellen ausgewählt');assert.equal($('category-summary').textContent,'2 Kategorien ausgewählt');
 $('month-view-button').click();
});
test('no checked sources produces no events and reset restores all choices',()=>{
 set('category','');selectMany('source-filter',[]);
 assert.equal(d.querySelectorAll('#agenda-events [data-event],#ongoing-grid [data-event]').length,0);assert.equal($('undated-section').hidden,true);
 assert.match($('coverage-note').textContent,/Keine Datenquellen ausgewählt/);
 d.querySelector('.empty-reset').click();assert.ok(inputs('source-filter').every(i=>i.checked));assert.ok(inputs('category').every(i=>i.checked));
 selectMany('category',[]);assert.equal(d.querySelectorAll('#agenda-events [data-event],#ongoing-grid [data-event]').length,0);d.querySelector('.empty-reset').click();
});
test('all/none buttons and Grötzingen group keep checkbox state synchronized',()=>{
 $('source-options').querySelector('[data-selection="none"]').click();assert.equal(inputs('source-filter').filter(i=>i.checked).length,0);
 const group=$('source-filter').querySelector('[data-source-group]');group.click();assert.equal(inputs('source-filter').filter(i=>i.checked).length,2);assert.equal(group.checked,true);
 const one=inputs('source-filter').find(i=>i.value==='groetzingen-jahresprogramm');one.click();assert.equal(group.indeterminate,true);
 $('source-options').querySelector('[data-selection="all"]').click();assert.ok(inputs('source-filter').every(i=>i.checked));assert.equal(group.indeterminate,false);
 $('category-filter').querySelector('[data-selection="none"]').click();assert.equal(inputs('category').filter(i=>i.checked).length,0);
 $('category-filter').querySelector('[data-selection="all"]').click();assert.ok(inputs('category').every(i=>i.checked));
});
test('a merged event is not duplicated when two of its sources are selected',()=>{
 set('search','HWK');selectMany('source-filter',['karlsruhe','durlach']);set('category','');d.querySelector('[data-day="2026-11-14"]').click();
 assert.equal(d.querySelectorAll('#agenda-events [data-event]').length,1);
});
test('filter panel closes with Escape and retains selected options',()=>{
 const panel=$('source-options');panel.open=true;const choice=inputs('source-filter').find(i=>i.value==='karlsruhe');choice.focus();
 choice.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 assert.equal(panel.open,false);assert.equal(d.activeElement,$('source-summary'));assert.equal(choice.checked,true);
});
test('uncertain periods expire after their final day in Berlin and retain unknown dates without a bound',()=>{
 function loadAt(when,open=false){
  const data=JSON.parse(JSON.stringify(scenario)),original=data.undatedEvents.find(e=>e.monthHint);
  data.undatedEvents.push({...original,id:'future-month-hint',title:'Ausflug im Dezember',dateCandidates:[],monthHint:'2026-12',dateText:'Dezember'});
  if(open)data.undatedEvents.push({...original,id:'unbounded-undated',title:'Termin ohne Zeitraum',dateCandidates:[],monthHint:null,dateText:'noch offen',sources:original.sources.map(({edition,...source})=>source)});
  const documentHtml=liveHtml.replace(/(<script id="event-data" type="application\/json">)[\s\S]*?(<\/script><!-- \/event-data -->)/,(_,a,b)=>a+JSON.stringify(data).replace(/</g,'\\u003c')+b);
  return new JSDOM(documentHtml,{runScripts:'dangerously',url:'file:///calendar/index.html',beforeParse(w){
   dialogSupport(w);const Native=w.Date,fixed=Native.parse(when);w.Date=class extends Native{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};
  }});
 }
 const onLast=loadAt('2026-11-22T12:00:00Z');try{
  const list=onLast.window.document.getElementById('undated-list');assert.match(list.textContent,/Herbstkonzert/);assert.match(list.textContent,/Zeitraum: Dezember 2026 · genauer Tag offen/);
 }finally{onLast.window.close();}
 const afterLast=loadAt('2026-11-23T12:00:00Z');try{
  assert.doesNotMatch(afterLast.window.document.getElementById('undated-list').textContent,/Herbstkonzert|Schlachtfest/);
 }finally{afterLast.window.close();}
 const yearEnd=loadAt('2026-12-31T12:00:00Z');try{assert.match(yearEnd.window.document.getElementById('undated-list').textContent,/Blutspende|Ausflug im Dezember/);}finally{yearEnd.window.close();}
 const nextYear=loadAt('2026-12-31T23:30:00Z');try{assert.equal(nextYear.window.document.getElementById('undated-section').hidden,true);}finally{nextYear.window.close();}
 const unknown=loadAt('2026-12-31T23:30:00Z',true);try{assert.equal(unknown.window.document.getElementById('undated-list').children.length,1);assert.match(unknown.window.document.getElementById('undated-list').textContent,/Termin ohne Zeitraum/);}finally{unknown.window.close();}
});
dom.window.close();console.log(`${checks} DOM checks passed; native browser layout remains unverified.`);
