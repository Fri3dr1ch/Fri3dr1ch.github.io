// Exercise the bundled Leaflet library against the real calendar UI. No map
// tiles are fetched by jsdom; these are DOM/API checks, not a visual browser test.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.join(__dirname,'..'),template=fs.readFileSync(path.join(root,'dist/index.html'),'utf8');
const leaflet=fs.readFileSync(path.join(root,'dist/assets/leaflet/leaflet.js'),'utf8');
const seed=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui-scenarios-2026-11.json'),'utf8'));
const p1={id:'place-tollhaus',name:'Kulturzentrum Tollhaus',address:'Alter Schlachthof 35, Karlsruhe',latitude:49.009,longitude:8.43};
const p2={id:'place-jahnhalle',name:'Jahnhalle Jöhlingen',address:'Jahnstraße 34, Jöhlingen',latitude:49.0268,longitude:8.582};
const p3={id:'place-museum',name:'Naturkundemuseum',address:'Karlsruhe',latitude:49.006,longitude:8.40};
const p4={id:'place-opening',name:'Naturfreundehaus Pfinztalblick',address:'Pfinztal',latitude:48.99,longitude:8.56};
function event(id,title,day,place,more={}){const e={...seed.events[0],id,title,description:'',location:place?.name||'Ort offen',sourceId:'karlsruhe',sourceName:'Stadt Karlsruhe',url:'https://kalender.karlsruhe.de/db/termine/test',categoryKey:'musik',category:'Musik',startDate:day,endDate:day,startTime:'14:00',endTime:'16:00',timeKnown:true,allDay:false,longRunning:false,conflicts:[],status:'scheduled',venue:{key:'venue-'+id,status:place?'resolved':'missing',method:place?'automatic':null,place:place?structuredClone(place):null},...more};delete e.openingOccurrences;delete e.dailyTimes;e.sources=[{sourceId:e.sourceId,sourceName:e.sourceName,url:e.url}];return e;}
function snapshot(){const data=structuredClone(seed);data.events=[
 event('map-a','Erstes Konzert','2026-11-15',p1),
 event('map-b','Zweites Konzert','2026-11-15',p1,{startTime:'19:30',endTime:'21:00'}),
 event('map-c','Lesung in Jöhlingen','2026-11-16',p2,{sourceId:'walzbachtal',sourceName:'Walzbachtal',categoryKey:'literatur',category:'Literatur & Vorträge'}),
 event('map-missing','Ohne Veranstaltungsort','2026-11-15',null),
 event('map-unreviewed','Ungeprüfter Vorschlag','2026-11-15',p2,{venue:{key:'unreviewed',status:'review',place:p2}}),
 event('map-online','Online-Abend','2026-11-15',null,{venue:{key:'online',status:'online',place:null}}),
 event('map-museum','Laufende Ausstellung','2026-11-01',p3,{endDate:'2026-11-30',longRunning:true,startTime:null,endTime:null,timeKnown:false,categoryKey:'ausstellungen',category:'Kunst & Ausstellungen'}),
 event('map-opening','Hausöffnung','2026-11-01',p4,{endDate:'2026-11-30',longRunning:true,sourceId:'naturfreunde-soellingen',sourceName:'NaturFreunde Söllingen'}),
 event('map-invalid','Ungültige Koordinate','2026-11-15',{...p3,id:'invalid',latitude:999}),
 event('map-december','Dezemberkonzert','2026-12-01',p1)
 ];data.events.find(e=>e.id==='map-opening').openingOccurrences=[{startDate:'2026-11-22',startTime:'12:00',endTime:'18:00'}];data.undatedEvents=[];data.importStats.eventCount=data.events.length;return data;}
const tick=()=>new Promise(r=>setTimeout(r,20));
function open({mobile=false,library=true,data=snapshot()}={}){const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 let html=template.replace(/(<script id="event-data" type="application\/json">)[\s\S]*?(<\/script><!-- \/event-data -->)/,(_,a,b)=>a+JSON.stringify(data).replace(/</g,'\\u003c')+b);
 if(library)html=html.replace('</script><!-- /event-data -->',()=>'</script><!-- /event-data --><script>\n'+leaflet+'\n</script>');
 const dom=new JSDOM(html,{url:'https://calendar.example/veranstaltungen/',runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  const NativeDate=w.Date,fixed=NativeDate.parse('2026-11-15T12:00:00Z');w.Date=class extends NativeDate{constructor(...a){super(...(a.length?a:[fixed]));}static now(){return fixed;}};
  Object.defineProperty(w,'innerWidth',{value:mobile?390:1200,writable:true});w.matchMedia=query=>({matches:query==='(any-pointer:fine)'?!mobile:mobile});
  Object.defineProperty(w.HTMLElement.prototype,'clientWidth',{get(){return this.id==='map-canvas'?700:0;}});Object.defineProperty(w.HTMLElement.prototype,'clientHeight',{get(){return this.id==='map-canvas'?560:0;}});
 }});const w=dom.window,d=w.document,$=id=>d.getElementById(id);
 function set(id,value){$(id).value=value;$(id).dispatchEvent(new w.Event(id==='search'?'input':'change',{bubbles:true}));}
 function sources(ids){for(const input of $('source-filter').querySelectorAll('input[data-filter-option]'))input.checked=ids.includes(input.value);$('source-filter').dispatchEvent(new w.Event('change',{bubbles:true}));}
 return {dom,w,d,$,errors,set,sources,data};}
let count=0;async function test(name,fn){await fn();count++;console.log('OK '+name);}
(async()=>{
 await test('the map is an optional third view and does not start Leaflet on page load',async()=>{const c=open();try{assert.equal(c.$('map-view').hidden,true);assert.equal(c.d.querySelectorAll('.leaflet-container').length,0);c.$('map-view-button').click();await tick();assert.equal(c.$('map-view').hidden,false);assert.equal(c.$('month-view').hidden,true);assert.equal(c.$('map-view-button').getAttribute('aria-pressed'),'true');assert.equal(c.d.querySelectorAll('.leaflet-marker-icon').length,4);assert.match(c.$('map-summary').textContent,/5 Termine an 4 Orten/);assert.match(c.$('map-summary').textContent,/4 weitere Termine/);assert.equal(c.errors.length,0);assert.match(c.d.querySelector('.leaflet-tile').src,/^https:\/\/tile\.openstreetmap\.org\//);assert.ok(c.d.querySelector('.leaflet-control-attribution a[href="https://www.openstreetmap.org/copyright"]'));}finally{c.w.close();}});
 await test('one venue marker retains both performances and opens the existing source details',async()=>{const c=open();try{c.$('map-view-button').click();await tick();c.d.querySelector('[data-map-place="place-tollhaus"]').click();assert.equal(c.$('map-events').querySelectorAll('[data-event]').length,2);assert.match(c.$('map-events').textContent,/19:30/);c.$('map-events').querySelector('[data-event="map-b"]').click();assert.equal(c.$('detail-title').textContent,'Zweites Konzert');assert.equal(c.$('detail-original').hidden,false);c.$('close-dialog').click();c.$('map-back').click();assert.equal(c.$('map-places').hidden,false);}finally{c.w.close();}});
 await test('source unions, category choices and search apply to map points and venue event lists',async()=>{const c=open();try{c.$('map-view-button').click();await tick();c.sources(['walzbachtal']);await tick();assert.equal(c.d.querySelectorAll('.leaflet-marker-icon').length,1);assert.match(c.$('map-places').textContent,/Jöhlingen/);c.d.querySelector('.map-place-row').click();assert.equal(c.$('map-events').querySelector('[data-event]').dataset.event,'map-c');c.sources(['karlsruhe','walzbachtal']);for(const i of c.$('category').querySelectorAll('input[data-filter-option]'))i.checked=i.value==='literatur';c.$('category').dispatchEvent(new c.w.Event('change',{bubbles:true}));await tick();assert.match(c.$('map-summary').textContent,/1 Termin an 1 Orten/);c.set('search','nicht vorhanden');await tick();assert.equal(c.d.querySelectorAll('.leaflet-marker-icon').length,0);assert.match(c.$('map-places').textContent,/noch keine Orte/);}finally{c.w.close();}});
 await test('day mode honours explicit house-opening occurrences and date navigation',async()=>{const c=open();try{c.$('map-view-button').click();c.set('map-period','day');await tick();assert.equal(c.$('map-day-field').hidden,false);assert.equal(c.d.querySelectorAll('.leaflet-marker-icon').length,2);assert.doesNotMatch(c.$('map-places').textContent,/Pfinztalblick/);c.set('map-date','2026-11-22');await tick();assert.match(c.$('map-places').textContent,/Pfinztalblick/);c.set('map-date','2026-12-01');await tick();assert.match(c.$('month-title').textContent,/Dezember/);assert.equal(c.d.querySelectorAll('.leaflet-marker-icon').length,1);assert.equal(c.$('map-date').value,'2026-12-01');}finally{c.w.close();}});
 await test('the market checkbox hides markets in the month, list and map', async()=>{
  const data=snapshot();
  const place={...p1,id:'market-square',name:'Marktplatz'};
  data.events.push(event('map-market','Wochenmarkt','2026-11-15',place,{categoryKey:'maerkte',category:'Märkte'}));
  const c=open({data});
  try{
   const box=c.$('category').querySelector('input[value="maerkte"]');
   assert.equal(box.parentElement.textContent,'Märkte');
   assert.ok(c.$('agenda-events').querySelector('[data-event="map-market"]'));
   box.click();
   assert.equal(c.$('agenda-events').querySelector('[data-event="map-market"]'),null);
   assert.ok(c.$('agenda-events').querySelector('[data-event="map-a"]'));
   c.$('list-view-button').click();
   assert.equal(c.$('list-view').querySelector('[data-event="map-market"]'),null);
   c.$('map-view-button').click();await tick();
   assert.equal(c.$('map-places').querySelector('[data-map-place="market-square"]'),null);
   box.click();await tick();
   assert.ok(c.$('map-places').querySelector('[data-map-place="market-square"]'));
  }finally{c.w.close();}
 });
 await test('a fine pointer scroll zooms the map in and out without a click', async()=>{
  const c=open();
  try{
   c.w.innerWidth=760;
   let map;
   c.w.L.Map.addInitHook(function(){map=this;});
   c.$('map-view-button').click();await tick();
   const before=map.getZoom();
   for(const direction of [-1,1]){
    const wheel=new c.w.WheelEvent('wheel',{deltaY:120*direction,clientX:350,clientY:280,bubbles:true,cancelable:true});
    c.$('map-canvas').dispatchEvent(wheel);
    assert.equal(wheel.defaultPrevented,true);
    await new Promise(resolve=>setTimeout(resolve,65));
    if(direction<0)assert.ok(map.getZoom()>before);
    else assert.equal(map.getZoom(),before);
   }
  }finally{c.w.close();}
 });
 await test('missing, unreviewed and invalid coordinates never create pins or disappear from the calendar',async()=>{const c=open();try{c.$('map-view-button').click();await tick();assert.equal(c.d.querySelectorAll('.leaflet-marker-icon').length,4);c.$('list-view-button').click();assert.match(c.$('list-view').textContent,/Ohne Veranstaltungsort/);assert.match(c.$('list-view').textContent,/Ungeprüfter Vorschlag/);assert.match(c.$('list-view').textContent,/Ungültige Koordinate/);assert.equal(JSON.parse(c.$('event-data').textContent).events.length,10);}finally{c.w.close();}});
 await test('mobile map gestures require an explicit toggle while marker selection remains available',async()=>{const c=open({mobile:true});try{let map;c.w.L.Map.addInitHook(function(){map=this;});c.$('map-view-button').click();await tick();assert.equal(map.scrollWheelZoom.enabled(),false);assert.equal(c.$('map-canvas').classList.contains('map-interactive'),false);c.$('map-touch').click();assert.equal(map.scrollWheelZoom.enabled(),false);assert.equal(c.$('map-canvas').classList.contains('map-interactive'),true);assert.equal(c.$('map-touch').getAttribute('aria-pressed'),'true');c.$('map-touch').click();assert.equal(c.$('map-canvas').classList.contains('map-interactive'),false);c.d.querySelector('.map-place-row').click();assert.equal(c.$('map-events').hidden,false);}finally{c.w.close();}});
 await test('source HTML stays inert in map labels, popups and selected event text',async()=>{const data=snapshot();data.events[0].venue.place.name='<img src=x onerror="window.pwned=1">';data.events[0].title='</script><script>window.pwned=1</script>';const c=open({data});try{c.$('map-view-button').click();await tick();assert.equal(c.w.pwned,undefined);assert.equal(c.$('map-places').querySelectorAll('img,script').length,0);c.d.querySelector('[data-map-place="place-tollhaus"]').click();assert.equal(c.$('map-events').querySelectorAll('img,script').length,0);assert.match(c.$('map-location-title').textContent,/<img/);}finally{c.w.close();}});
 await test('a failed Leaflet download keeps the venue list and calendar usable',async()=>{const c=open({library:false});try{assert.equal(c.d.querySelector('script[src$="leaflet.js"]'),null);c.$('map-view-button').click();const script=c.d.querySelector('script[src$="leaflet.js"]');assert.ok(script);script.dispatchEvent(new c.w.Event('error'));await tick();assert.match(c.$('map-message').textContent,/gerade nicht verfügbar/);assert.equal(c.d.querySelectorAll('.map-place-row').length,4);c.d.querySelector('.map-place-row').click();assert.ok(c.$('map-events').querySelector('[data-event]'));c.$('month-view-button').click();assert.equal(c.$('month-view').hidden,false);assert.equal(c.errors.length,0);}finally{c.w.close();}});
 console.log(count+' map checks passed with bundled Leaflet; visual browser layout remains unverified.');
})().catch(e=>{console.error(e);process.exitCode=1;});
