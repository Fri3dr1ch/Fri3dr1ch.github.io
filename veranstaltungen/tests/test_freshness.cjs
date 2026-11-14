// Check online revalidation and the standalone HTML fallback in actual DOM code.
const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const page=fs.readFileSync(path.join(__dirname,'../dist/index.html'),'utf8');
const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/ui-scenarios-2026-11.json'),'utf8'));
const copy=x=>JSON.parse(JSON.stringify(x));
const fresh=copy(fixture);fresh.generatedAt='2026-11-13T12:00:00+00:00';
for(const source of fresh.sources)source.lastSuccessAt='2026-11-13T12:00:00+00:00';
fresh.events.push({...copy(fresh.events[0]),id:'fresh-additional-event',title:'Neu aufgenommener Termin'});
const old=copy(fixture);old.generatedAt='2026-11-12T07:00:00+00:00';
for(const source of old.sources)source.lastSuccessAt='2026-11-12T07:00:00+00:00';
async function load(embedded,fetcher,url='https://calendar.example/veranstaltungen/'){
 let resolve;const ready=new Promise(r=>resolve=r),calls=[],errors=[];
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e));
 const html=page.replace(/(<script id="event-data" type="application\/json">)[\s\S]*?(<\/script><!-- \/event-data -->)/,
  (_,a,b)=>a+JSON.stringify(embedded).replace(/</g,'\\u003c')+b);
 const dom=new JSDOM(html,{runScripts:'dangerously',url,virtualConsole:vc,beforeParse(w){
  const Native=w.Date,fixed=Native.parse('2026-11-13T12:00:00Z');
  w.Date=class extends Native{constructor(...args){super(...(args.length?args:[fixed]));}static now(){return fixed;}};
  w.document.addEventListener('calendar-ready',resolve,{once:true});
  w.fetch=async(...args)=>{calls.push(args);return fetcher(...args);};
 }});
 let timeout;try{await Promise.race([ready,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('calendar-ready fehlt')),6000);})]);}
 catch(error){dom.window.close();throw error;}finally{clearTimeout(timeout);}
 return {dom,d:dom.window.document,calls,errors};
}
let checks=0;
async function test(name,run){await run();checks++;console.log('OK',name);}
(async()=>{
 await test('an old HTML page loads the newer JSON data before rendering',async()=>{
  const r=await load(old,async()=>({ok:true,json:async()=>fresh}));try{
   assert.equal(r.calls.length,1);assert.equal(r.calls[0][1].cache,'no-store');
   assert.equal(new URL(r.calls[0][0]).pathname,'/veranstaltungen/events.json');
   assert.equal(r.d.getElementById('source-count').textContent,`${fresh.events.length} Termine · ${fresh.sources.length} Quellen`);
   assert.doesNotMatch(r.d.getElementById('notice').textContent,/älter als 24/);
   assert.match(r.d.getElementById('data-timestamp').textContent,/13.11./);assert.equal(r.errors.length,0);
  }finally{r.dom.window.close();}
 });
 await test('a failed JSON request keeps the embedded data and names its real age',async()=>{
  const r=await load(old,async()=>{throw Error('offline');});try{
   assert.match(r.d.getElementById('notice').textContent,/gespeicherte Datenstand/);
   assert.match(r.d.getElementById('notice').textContent,/Letzter erfolgreicher Abruf am 12.11./);
   assert.equal(r.d.getElementById('source-count').textContent,`${old.events.length} Termine · ${old.sources.length} Quellen`);
  }finally{r.dom.window.close();}
 });
 await test('older remote data cannot overwrite a newer embedded snapshot',async()=>{
  const r=await load(fresh,async()=>({ok:true,json:async()=>old}));try{
   assert.equal(r.d.getElementById('source-count').textContent,`${fresh.events.length} Termine · ${fresh.sources.length} Quellen`);
  }finally{r.dom.window.close();}
 });
 await test('malformed remote data falls back to the valid embedded snapshot',async()=>{
  const r=await load(old,async()=>({ok:true,json:async()=>({events:[]})}));try{
   assert.match(r.d.getElementById('notice').textContent,/gespeicherte Datenstand/);assert.equal(r.errors.length,0);
  }finally{r.dom.window.close();}
 });
 await test('a downloaded HTML file opens without a network request',async()=>{
  const r=await load(old,async()=>{throw Error('must not fetch');},'file:///calendar/index.html');try{
   assert.equal(r.calls.length,0);assert.doesNotMatch(r.d.getElementById('notice').textContent,/Aktualisierung konnte nicht/);
  }finally{r.dom.window.close();}
 });
 await test('an unresponsive server times out and the offline calendar still opens',async()=>{
  const r=await load(old,(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted')))));try{
   assert.match(r.d.getElementById('notice').textContent,/gespeicherte Datenstand/);assert.equal(r.errors.length,0);
  }finally{r.dom.window.close();}
 });
 console.log(`${checks} freshness checks passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
