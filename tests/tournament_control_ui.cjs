const assert=require('node:assert/strict');
const vm=require('node:vm');
const source=require('node:fs').readFileSync(0,'utf8');
class El {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.classList={add(){}};this.disabled=false;this._text='';}
 set textContent(t){this._text=t;this.children=[];}
 get textContent(){return this._text;}
 append(...xs){this.children.push(...xs);}
 appendChild(x){this.append(x);return x;}
 setAttribute(){}
 contains(x){return this===x||this.children.some(c=>c.contains(x));}
 querySelectorAll(selector){const match=e=>selector==='.tournament-card'?e.className==='tournament-card':false;return this.children.flatMap(c=>[...(match(c)?[c]:[]),...c.querySelectorAll(selector)]);}
}
const host=new El('div'),message=new El('div');
const doc={activeElement:null,createElement:t=>new El(t),createTextNode:t=>{const e=new El('#text');e.textContent=t;return e;}};
const find=(root,fn)=>fn(root)?root:root.children.map(x=>find(x,fn)).find(Boolean);
const button=text=>find(host,x=>x.tag==='button'&&x.textContent===text);
const status=()=>find(host,x=>x.className==='tournament-status').textContent;
let rows=[],posts=0,accept=true,failRead=false,release=null;
const context=vm.createContext({document:doc,$:id=>id==='tournament-list'?host:message,
 confirm:()=>accept,refreshLive:async()=>{},loadTournamentView:async()=>{},
 fetch:async(url,options={})=>{
  if(url.endsWith('/preflight'))return {ok:true,json:async()=>({ready:true})};
  if(options.method==='POST'){posts++;return {ok:!failRead,status:500,json:async()=>failRead?{error:'test error'}:{status:url.endsWith('/stop')?'stop_requested':'starting'}};}
  if(release)return new Promise(resolve=>{release=()=>resolve({ok:true,json:async()=>[{id:7,status:'stopped'}]});});
  return {ok:!failRead,status:503,json:async()=>rows};
 }});
const start=source.indexOf('function tournamentCardProgress('),end=source.indexOf('function renderModelStatus(',start);
vm.runInContext(source.match(/const tournamentControl=\{[^\n]+/)[0]+'\n'+source.slice(start,end),context);
const run=code=>vm.runInContext(code,context);
async function update(state){rows=[{id:7,name:'Test',status:state}];await run('syncTournamentCards()');}
(async()=>{
 await update('planned'); assert.equal(status(),'Zaplanowany');
 const startBtn=button('Uruchom turniej'); doc.activeElement=startBtn;
 await startBtn.onclick(); assert.equal(posts,1);assert.equal(status(),'Uruchamianie…');assert(button('Uruchamianie…').disabled);
 await startBtn.onclick();assert.equal(posts,1);
 await update('running');assert.equal(status(),'W trakcie');assert(button('Zatrzymaj turniej'));
 const stop=button('Zatrzymaj turniej');doc.activeElement=stop;
 await stop.onclick();assert.equal(posts,2);assert.equal(status(),'Zatrzymywanie…');
 await update('running');assert.equal(status(),'Zatrzymywanie…');
 await update('stopped');assert.equal(status(),'Zatrzymany');assert(button('Wznów turniej'));
 accept=false;await button('Wznów turniej').onclick();assert.equal(posts,2);assert.equal(status(),'Zatrzymany');
 accept=true;failRead=true;await button('Wznów turniej').onclick();assert.equal(posts,3);assert.equal(status(),'Zatrzymany');assert.match(message.textContent,/test error/);
 failRead=false;await button('Wznów turniej').onclick();assert.equal(posts,4);assert.equal(status(),'Wznawianie…');
 failRead=true;await run('syncTournamentCards()');assert.equal(status(),'Wznawianie…');
 failRead=false;await update('running');assert.equal(status(),'W trakcie');
 // External status change must override the focused stale control.
 doc.activeElement=button('Zatrzymaj turniej');await update('stopped');assert(button('Wznów turniej'));
 console.log('START/STOP/RESUME, delayed status, duplicate click, cancellation, HTTP errors, focus: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
