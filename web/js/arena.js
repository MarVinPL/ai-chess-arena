let arenaModels=[];let tournamentBoardActive=false;const START_FEN='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';const $=id=>document.getElementById(id);const pieces={K:'♔',Q:'♕',R:'♖',B:'♗',N:'♘',P:'♙',k:'♚',q:'♛',r:'♜',b:'♝',n:'♞',p:'♟'};
function materialAdvantage(fen){if(!fen)return {white:0,black:0};const values={p:1,n:3,b:3,r:5,q:9};let white=0,black=0;for(const piece of (fen.split(' ')[0]||'')){const value=values[piece.toLowerCase()]||0;if(piece>='A'&&piece<='Z')white+=value;else if(piece>='a'&&piece<='z')black+=value;}const balance=white-black;return {white:balance,black:-balance};}
function pieceInventory(fen,color){const counts={k:0,q:0,r:0,b:0,n:0,p:0};const board=(fen||'').split(' ')[0]||'';for(const piece of board){const key=piece.toLowerCase();if(Object.prototype.hasOwnProperty.call(counts,key)&&((color==='white'&&piece===piece.toUpperCase())||(color==='black'&&piece===piece.toLowerCase())))counts[key]++;}const values={q:9,r:5,b:3,n:3,p:1};const material=Object.entries(values).reduce((sum,[key,value])=>sum+counts[key]*value,0);const figures=counts.q+counts.r+counts.b+counts.n;const pawns=counts.p;const total=Object.values(counts).reduce((sum,count)=>sum+count,0);return {counts,figures,pawns,total,material};}
function renderPieceInventory(fen){const white=pieceInventory(fen,'white'),black=pieceInventory(fen,'black');const symbols={white:{q:'♕',r:'♖',b:'♗',n:'♘',p:'♙',k:'♔'},black:{q:'♛',r:'♜',b:'♝',n:'♞',p:'♟',k:'♚'}};for(const [color,data] of Object.entries({white,black})){const counts=data.counts;$(color+'-piece-types').textContent=['q','r','b','n','p'].map(key=>symbols[color][key]+counts[key]).join(' · ')+' · '+symbols[color].k+(counts.k?'✓':'—');$(color+'-piece-summary').textContent=data.figures+' figur · '+data.pawns+' pionków · '+data.total+' bierek · wartość materialna: '+data.material;const opponent=color==='white'?black:white;$(color+'-captured').textContent='Zbity materiał '+(color==='white'?'czarnych':'białych')+' (bilans względem startu): '+Math.max(0,39-opponent.material);}}
function renderLastMove(moves=[]){const last=Array.isArray(moves)?moves[moves.length-1]:null;const text=last&&/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(last.uci||'')?'Ostatni ruch: '+(last.san||last.uci)+' · '+last.uci:'Ostatni ruch: brak';$('last-move-note').textContent=text;}
function formatMaterialScore(score){return score>0?'+'+score:String(score);}
function draw(fen,moves=[]){if(!fen)return;const rows=fen.split(' ')[0].split('/');let html='';rows.forEach((row,r)=>{let files=[];for(const ch of row){if(/[1-8]/.test(ch))for(let i=0;i<+ch;i++)files.push('');else files.push(ch)}files.forEach((p,c)=>{html+=`<div class="sq ${(r+c)%2?'dark':'light'}" data-file="${String.fromCharCode(97+c)}" data-rank="${8-r}" aria-label="${String.fromCharCode(97+c)}${8-r}" data-piece-color="${p?(p===p.toUpperCase()?'white':'black'):'empty'}"><span class="piece">${pieces[p]||''}</span></div>`})});const last={white:[...moves].reverse().find(m=>m.color==='white'||(m.ply&&m.ply%2===1)),black:[...moves].reverse().find(m=>m.color==='black'||(m.ply&&m.ply%2===0))};const square=s=>{if(!/^[a-h][1-8]$/.test(s||''))return null;return {x:(s.charCodeAt(0)-97)*100+50,y:(8-Number(s[1]))*100+50}};let arrows='';for(const [color,move] of Object.entries(last)){if(!move||!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move.uci||''))continue;const from=square(move.uci.slice(0,2)),to=square(move.uci.slice(2,4));const x1=from.x,y1=from.y,x2=to.x,y2=to.y;const dx=x2-x1,dy=y2-y1,len=Math.hypot(dx,dy)||1;const ex=x2-dx/len*17,ey=y2-dy/len*17;const stroke=color==='white'?'#35d2c0':'#f2b65e';const marker=color==='white'?'arrow-white':'arrow-black';arrows+=`<path d="M ${x1} ${y1} L ${ex} ${ey}" stroke="${stroke}" stroke-width="9" stroke-linecap="round" opacity=".95" marker-end="url(#${marker})"/>`;}const marks=['white','black'].map(color=>{const m=last[color];if(!m||!/^[a-h][1-8][a-h][1-8]/.test(m.uci||''))return '';return [m.uci.slice(0,2),m.uci.slice(2,4)].map(s=>{const q=square(s);const col=s.charCodeAt(0)-97,row=8-Number(s[1]);return `<rect x="${col*100+3}" y="${row*100+3}" width="94" height="94" rx="7" fill="none" stroke="${color==='white'?'#35d2c0':'#f2b65e'}" stroke-width="5" opacity=".85"/>`}).join('')}).join('');const overlay=`<svg class="board-arrows" viewBox="0 0 800 800" aria-hidden="true"><defs><marker id="arrow-white" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#35d2c0" stroke="#173b3a" stroke-width=".6"/></marker><marker id="arrow-black" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#f2b65e"/></marker></defs>${marks}${arrows}</svg>`;$('board').innerHTML=html+overlay}
async function post(path){const r=await fetch(path,{method:'POST'});return r.json()}async function refresh(){try{const s=await (await fetch('/api/state')).json();if(!tournamentBoardActive){const previous=Array.isArray(s.moves)?s.moves.map((m,i)=>({...m,ply:i+1})):[];draw(s.fen,previous);renderLastMove(previous);}if(!tournamentBoardActive){renderPieceInventory(s.fen||START_FEN);$("white").textContent=(s.active_model||"Brak modelu")+" · białe";$("black").textContent=(s.active_model||"Brak modelu")+" · czarne";}$('online').textContent=s.llm_online?'Model lokalny online':'Model offline';$('online').style.color=s.llm_online?'#a9d5b5':'#ff9b9b';$('count').textContent=s.moves.length;$('avg').textContent=s.avg_move_seconds==null?'—':s.avg_move_seconds+' s';$('illegal').textContent=s.illegal_attempts;$('ram').textContent=s.memory_mb.MemAvailable? s.memory_mb.MemAvailable+' MB':'—';$('moves').textContent=s.moves.length?s.moves.map((m,i)=>`${i+1}. ${m.san}  (${m.uci}, ${m.seconds}s)`).join('\n'):'Brak ruchów.';$('status').textContent=s.game_over?`Koniec: ${s.result} · ${s.termination}`:s.running?'Partia trwa…':`Ruch: ${s.turn==='white'?'białych':'czarnych'}`;$('error').textContent=s.error||'';$('step').disabled=s.game_over||s.running||!s.llm_online;$('run').disabled=s.game_over||s.running||!s.llm_online;$('stop').disabled=!s.running;const reason=s.game_over?'Partia zakończona':s.running?'Partia już trwa':!s.llm_online?'Lokalny model jest offline':!s.active_model?'Brak gotowego modelu':null;for(const id of ['step','run']){$(id).title=$(id).disabled?(reason||'Ruch jest chwilowo niedostępny'):'';}$('stop').title=$('stop').disabled?'Brak uruchomionej partii':'';$('controls-note').textContent=reason&&($('step').disabled||$('run').disabled)?'Sterowanie: '+reason:'';loadGames()}catch(e){$('error').textContent='Błąd połączenia z aplikacją: '+e}}
async function loadGames(){const g=await (await fetch('/api/games')).json();$('games').textContent=g.length?g.map(x=>{const label=x.adjudication_status==='adjudicated'?' · WYNIK ADJUDYKOWANY':x.adjudication_status==='unresolved'?' · FAILED / NIERozstrzygnięta':'';const reason=x.failure_reason?' · przyczyna: '+x.failure_reason:'';return `#${x.id} · ${x.white_model||x.white||'Białe'} vs ${x.black_model||x.black||'Czarne'} · ${x.started_at} · wynik ${x.result}${label}${reason} · ${x.move_count??x.moves} półruchów · avg ${x.avg_seconds}s · interwencje sędziego ${x.arbiter_interventions??0}`}).join('\\n'):'Brak zapisanych partii.'}
$('step').onclick=async()=>{ $('step').disabled=true;try{const r=await post('/api/step');if(r.error)$('error').textContent=r.error}catch(e){$('error').textContent=String(e)}await refresh()};$('run').onclick=async()=>{await post('/api/run');await refresh()};$('stop').onclick=async()=>{await post('/api/stop');await refresh()};$('reset').onclick=async()=>{await post('/api/reset');await refresh()};
async function refreshLive(){try{const r=await fetch('/api/live',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const s=await r.json();const hasPairing=Boolean(s.pairing_id&&s.white&&s.black);const terminalStage=['idle','completed','stopped','failed','error'].includes(s.stage);$('active-players').textContent=hasPairing?((terminalStage?'Ostatnia para: ':'')+s.white+' vs '+s.black):'Brak aktywnej partii';$('match-kind').textContent=hasPairing?((s.white===s.black?'Self-play — wyłączony z Elo':'Mecz dwóch różnych modeli')+(terminalStage?' · '+s.stage:'')):'—';const currentModel=arenaModels.find(m=>m.filename===s.model||m.name===s.model);$('generating-model').textContent=hasPairing&&!terminalStage&&s.model?(currentModel?currentModel.name:s.model):'—';if(s.pairing_id){tournamentBoardActive=true;draw(s.fen||START_FEN,Array.isArray(s.moves)?s.moves:[]);renderLastMove(s.moves);renderPieceInventory(s.fen||START_FEN);const material=materialAdvantage(s.fen||START_FEN);$('white').textContent=(s.white||'Białe')+' · białe ('+formatMaterialScore(material.white)+')';$('black').textContent=(s.black||'Czarne')+' · czarne ('+formatMaterialScore(material.black)+')'}else if(['idle','starting','completed','stopped','failed','error'].includes(s.stage)){tournamentBoardActive=false} $('live-stage').textContent=(s.message||'Oczekiwanie na turniej')+' · '+(s.stage||'idle');$('live-model').textContent=s.model||((s.white&&s.black)?s.white+' vs '+s.black:'—');$('live-color').textContent=({white:'białe',black:'czarne'}[s.color]||'—')+(s.pairing_id?' · para #'+s.pairing_id:'')+(s.question_no?' · Passkey '+s.question_no+'/3':'');$('live-time').textContent=s.stage==='generating'&&s.generation_started_at?Math.max(0,(Date.now()/1000-s.generation_started_at)).toFixed(1)+' s':(s.elapsed_seconds!=null?s.elapsed_seconds+' s':'—');$('live-move').textContent=s.proposed_move||((s.last_move&&s.last_move.san)?s.last_move.san+' ('+s.last_move.uci+')':'—');$('live-fen').textContent=s.fen||'Brak aktywnej pozycji.';$('live-legal').textContent=Array.isArray(s.legal_moves)?s.legal_moves.join(', '):'—';$('live-response').textContent=s.response||'Brak odpowiedzi.';const moves=Array.isArray(s.moves)?s.moves:[];if(moves.length)$('live-response').textContent+='\n\nRuchy: '+moves.map(m=>m.ply+'. '+m.model+': '+m.san+' ['+m.seconds+' s]').join(' · ');$('live-events').textContent=(s.events||[]).map(e=>e.at+' · '+e.message+(e.model?' · '+e.model:'')+(e.response?' · '+e.response:'')).join('\n')||'Brak zdarzeń.';}catch(e){$('live-stage').textContent='Błąd odczytu statusu: '+e}}
async function refreshModelPermissions(){
 const status=$('model-permissions-status'),host=$('model-permissions-list');
 try{
  const response=await fetch('/api/model-permissions',{cache:'no-store'});
  if(!response.ok)throw Error('HTTP '+response.status);
  const rows=await response.json();host.textContent='';
  status.textContent=rows.length+' żądań / decyzji w rejestrze';
  if(!rows.length){host.textContent='Brak żądań. Nowe modele nie mają automatycznie przyznanych uprawnień.';return}
  rows.forEach(item=>{
   const card=document.createElement('div');card.className='hf-queue-item';
   const title=document.createElement('strong');title.textContent='#'+item.id+' · model '+item.model_id+' · '+item.scope+' · '+item.status;card.appendChild(title);
   const details=document.createElement('small');details.textContent=item.repo_id+' · revision '+item.revision+' · SHA-256 '+item.artifact_sha256+' · zgłosił: '+item.requested_by;card.appendChild(details);
   const history=document.createElement('small');history.textContent='Historia: '+(item.history||[]).map(e=>e.event+' / '+e.actor+' / '+e.occurred_at+(e.reason?' / '+e.reason:'')).join(' → ');card.appendChild(history);
   const actions=document.createElement('div');actions.className='formrow';
   const addAction=(label,action)=>{
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=label;
    button.addEventListener('click',async()=>{
     const reason=action==='approve'?'':window.prompt('Uzasadnienie decyzji (min. 3 znaki):','');
     if(action!=='approve'&&(!reason||reason.trim().length<3))return;
     if(!window.confirm('Potwierdź decyzję '+action+' dla żądania #'+item.id+'.'))return;
     try{const res=await fetch('/api/model-permissions/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:item.id,action,confirm:true,reason})});const data=await res.json();if(!res.ok)throw Error(data.error||'Błąd decyzji');status.textContent='Zapisano decyzję dla żądania #'+item.id;await refreshModelPermissions()}catch(error){status.textContent='Nie udało się zapisać decyzji: '+error}
    });actions.appendChild(button);
   };
   if(item.status==='pending'){addAction('Zatwierdź','approve');addAction('Odrzuć','reject')}
   if(item.status==='approved')addAction('Wycofaj','revoke');
   card.appendChild(actions);host.appendChild(card);
  });
 }catch(error){status.textContent='Nie udało się odczytać rejestru: '+error}
}
$('model-permissions-refresh').addEventListener('click',refreshModelPermissions);
$('model-permission-request-form').addEventListener('submit',async event=>{
 event.preventDefault();
 const status=$('model-permissions-status');
 const payload={model_id:Number($('permission-model-id').value),repo_id:$('permission-repo-id').value.trim(),revision:$('permission-revision').value.trim(),artifact_sha256:$('permission-artifact-sha').value.trim(),scope:$('permission-scope').value,reason:$('permission-reason').value.trim(),confirm:$('permission-confirm').checked};
 try{const response=await fetch('/api/model-permissions/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error||'Błąd żądania');status.textContent='Utworzono oczekujące żądanie #'+data.id+' — nie jest ono jeszcze zgodą.';$('model-permission-request-form').reset();await refreshModelPermissions()}catch(error){status.textContent='Nie udało się złożyć żądania: '+error}
});
refreshModelPermissions();
refresh();loadLeague();refreshLive();setInterval(refresh,1200);setInterval(refreshLive,1000);setInterval(loadLeague,10000);
function tableRows(target,rows,headers){const host=$(target);host.textContent='';if(!rows.length){host.textContent='Brak danych.';return}const t=document.createElement('table');t.className='league-table';const head=document.createElement('tr');headers.forEach(h=>{const th=document.createElement('th');th.textContent=h;head.appendChild(th)});t.appendChild(head);rows.forEach(row=>{const tr=document.createElement('tr');row.forEach(v=>{const td=document.createElement('td');td.textContent=String(v);tr.appendChild(td)});t.appendChild(tr)});host.appendChild(t)}
let selectedTournamentId=null;
function renderTournamentRosterCounts(counts){
 const host=$('tournament-roster-counters');host.textContent='';
 if(!counts){host.textContent='Brak aktywnego turnieju';return}
 const eliminated=document.createElement('span');eliminated.append('Wyeliminowani: ');
 const eliminatedValue=document.createElement('strong');eliminatedValue.textContent=String(counts.eliminated??0);eliminated.appendChild(eliminatedValue);
 const remaining=document.createElement('span');remaining.append('Pozostali: ');
 const remainingValue=document.createElement('strong');remainingValue.textContent=String(counts.remaining??0);remaining.appendChild(remainingValue);
 host.append(eliminated,remaining);
}
async function loadTournamentView(id){
 selectedTournamentId=Number(id);
 try{
  const r=await fetch('/api/tournaments/'+selectedTournamentId+'/view',{cache:'no-store'});
  const d=await r.json();if(!r.ok)throw Error(d.error||r.status);
  const t=d.tournament;
  renderTournamentRosterCounts(d.roster_counts);
  $('tournament-detail-title').textContent='#'+t.id+' · '+t.name+' · '+t.status+' · '+t.format+(d.active_pairing?' · AKTYWNA PARA: '+d.active_pairing.white_name+' vs '+d.active_pairing.black_name:'');
  const liveMoves=Array.isArray(d.live?.moves)?d.live.moves:[];
  if(d.active_pairing&&liveMoves.length){$('tournament-detail-title').textContent+=' · ostatni ruch: '+(liveMoves[liveMoves.length-1].san||liveMoves[liveMoves.length-1].uci||'—')}
  const p=d.progress;
  $('tournament-progress').textContent='Liga: '+p.league.completed+'/'+p.league.planned+' · Puchar: '+p.draft.completed+'/'+p.draft.planned+' · Łącznie: '+p.total.completed+'/'+p.total.planned+' rozegranych (wyłącznie zapisane, poprawnie powiązane wyniki).';
  tableRows('tournament-standings',(d.standings||[]).map((x,i)=>[i+1,x.name,x.points,x.played,x.wins,x.draws,x.losses]),['#','Zawodnik','Pkt','Partie','W','R','P']);
  const fixtureRows=(d.fixtures||[]).map(x=>{
   const round=x.stage==='league'?'Liga · leg '+(x.leg||'—'):'Puchar · runda '+(x.round_no??'—');
   const result=x.saved_game_id?x.result:'—';
   const status=x.status==='running'?'AKTYWNA · running':x.status; const adjudication=x.adjudication_status==='adjudicated'?' · ADJUDYKOWANA':'';
   return [round,x.white_name+' vs '+x.black_name,status,result,x.saved_game_id?'partia #'+x.saved_game_id:'—'];
  });
  tableRows('tournament-fixtures',fixtureRows,['Faza','Para','Status','Wynik','Zapis']);
  const previewHost=$('tournament-game-previews');previewHost.textContent='';
  (d.fixtures||[]).filter(x=>x.game_url).forEach(x=>{const link=document.createElement('a');link.href=x.game_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Otwórz PGN partii #'+x.saved_game_id+' · '+x.white_name+' vs '+x.black_name;link.style.display='block';link.style.margin='5px 0';previewHost.appendChild(link)});
  const bracketHost=$('tournament-bracket');bracketHost.textContent='';
  if(d.bracket_status!=='available'){
   bracketHost.textContent=d.bracket_status==='awaiting_bracket_generation'?'Liga jest zakończona. Drabinka oczekuje na zapisane rozstawienia; podgląd nie generuje jej automatycznie.':'Drabinka pucharowa zostanie utworzona po zakończeniu fazy ligowej.';
  }else{
   (d.bracket||[]).forEach(round=>{
    const section=document.createElement('div');section.style.margin='8px 0';
    const title=document.createElement('strong');title.textContent='Runda '+round.round_no;section.appendChild(title);
    (round.matches||[]).forEach(m=>{const line=document.createElement('div');line.className='notice';line.textContent=(m.white_seed?'#'+m.white_seed+' ':'')+m.white_name+' vs '+(m.black_seed?'#'+m.black_seed+' ':'')+m.black_name+' · '+m.status+' · '+(m.result||'—');section.appendChild(line)});
    (round.byes||[]).forEach(b=>{const line=document.createElement('div');line.className='notice';line.textContent='Bye · seed '+b.seed+' · '+b.name;section.appendChild(line)});
    bracketHost.appendChild(section);
   });
  }
  const championHost=$('tournament-champion');championHost.textContent=d.champion?'Mistrz: '+d.champion.name:'Mistrz: jeszcze nierozstrzygnięty';
  const admission=d.late_admission||{};
  if(t.parent_tournament_id){const note=document.createElement('div');note.className='notice';note.textContent='Turniej eliminacyjny nowych zawodników dla turnieju #'+t.parent_tournament_id+'. Kryterium awansu: co najmniej jedno zwycięstwo; ranking: punkty, zwycięstwa, remisy, nazwa.';championHost.appendChild(note)}
  else {
   if(admission.initial_entrant_count!=null){
    const quota=document.createElement('div');quota.className='notice';quota.style.marginTop='8px';
    quota.textContent='Nowi zawodnicy: wykorzystano '+admission.used+'/'+admission.limit+' · pozostało '+admission.remaining+' (podstawa: '+admission.initial_entrant_count+' zawodników na starcie).';championHost.appendChild(quota);
   }
   (admission.batches||[]).forEach(batch=>{const line=document.createElement('div');line.className='notice';line.textContent='Eliminacje #'+batch.qualification_tournament_id+' · '+batch.status+' · turniej eliminacyjny: '+batch.qualification_status;championHost.appendChild(line)});
   if(t.status==='planned'&&Array.isArray(admission.candidates)&&admission.candidates.length){
    const section=document.createElement('div');section.className='notice';section.style.marginTop='8px';
    const label=document.createElement('label');label.textContent='Dodaj nowych zawodników bez limitu przed startem:';label.style.display='block';
    const select=document.createElement('select');select.multiple=true;select.size=Math.min(8,admission.candidates.length);select.style.width='100%';select.style.margin='7px 0';
    admission.candidates.forEach(candidate=>{const option=document.createElement('option');option.value=String(candidate.model_id);option.textContent=candidate.name+' · #'+candidate.model_id;select.appendChild(option)});
    const add=document.createElement('button');add.textContent='Dodaj wybranych do składu';add.onclick=async()=>{
     const model_ids=Array.from(select.selectedOptions).map(option=>Number(option.value));
     if(!model_ids.length){$('league-message').textContent='Wybierz co najmniej jednego zawodnika.';return}
     add.disabled=true;
     try{const response=await fetch('/api/tournaments/entries',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tournament_id:t.id,model_ids})});const result=await response.json();if(!response.ok)throw Error(result.error||response.status);$('league-message').textContent='Dodano '+result.added_count+' zawodników. Skład liczy teraz '+result.total_entrants+'.';await loadLeague()}
     catch(error){$('league-message').textContent='Nie udało się dodać zawodników: '+error;add.disabled=false}
    };
    section.append(label,select,add);championHost.appendChild(section);
   }
   if(t.status==='stopped'&&admission.remaining>0&&Array.isArray(admission.candidates)&&admission.candidates.length>=2){
    const section=document.createElement('div');section.className='notice';section.style.marginTop='8px';
    const label=document.createElement('label');label.textContent='Kandydaci do osobnej eliminacji (wybierz co najmniej 2):';label.style.display='block';
    const select=document.createElement('select');select.multiple=true;select.size=Math.min(8,admission.candidates.length);select.style.width='100%';select.style.margin='7px 0';
    admission.candidates.forEach(candidate=>{const option=document.createElement('option');option.value=String(candidate.model_id);option.textContent=candidate.name+' · #'+candidate.model_id;select.appendChild(option)});
    const start=document.createElement('button');start.textContent='Utwórz i uruchom eliminacje';start.onclick=async()=>{
     const model_ids=Array.from(select.selectedOptions).map(option=>Number(option.value));
     if(model_ids.length<2){$('league-message').textContent='Wybierz co najmniej dwóch kandydatów.';return}
     if(!confirm('Uruchomić osobny turniej eliminacyjny dla '+model_ids.length+' kandydatów? Turniej główny pozostanie zatrzymany.'))return;
     start.disabled=true;
     try{const created=await fetch('/api/tournaments/admissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({parent_tournament_id:t.id,model_ids})});const batch=await created.json();if(!created.ok)throw Error(batch.error||created.status);
      const launched=await fetch('/api/tournaments/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tournament_id:batch.qualification_tournament_id})});const run=await launched.json();if(!launched.ok)throw Error(run.error||launched.status);
      $('league-message').textContent='Uruchomiono eliminacje #'+batch.qualification_tournament_id+'. Po zakończeniu najlepsze modele z co najmniej jednym zwycięstwem zostaną automatycznie dopuszczone w ramach pozostałego limitu.';await loadLeague();
     }catch(error){$('league-message').textContent='Nie udało się uruchomić eliminacji: '+error;start.disabled=false}
    };
    section.append(label,select,start);championHost.appendChild(section);
   }
   if((admission.results||[]).length){const title=document.createElement('strong');title.textContent='Historia kwalifikacji nowych zawodników';championHost.appendChild(title);const list=document.createElement('div');list.className='notice';(admission.results||[]).forEach(item=>{const line=document.createElement('div');line.textContent='#'+item.rank+' '+item.name+' · '+item.points+' pkt · W '+item.wins+' · R '+item.draws+' · '+({qualified:'dopuszczony',not_qualified:'nie spełnił kryterium',capacity_exhausted:'brak miejsca w limicie'}[item.outcome]||item.outcome);list.appendChild(line)});championHost.appendChild(list)}
  }
 }catch(e){$('tournament-detail-title').textContent='Nie udało się odczytać turnieju: '+e}
}
function renderTournaments(rows){
 const host=$('tournament-list');
 host.classList.add('tournament-list');
 host.textContent='';
 if(!rows.length){host.textContent='Brak turniejów.';return}
 const statusLabels={planned:'Zaplanowany',running:'W trakcie',stopped:'Zatrzymany',failed:'Błąd',completed:'Zakończony'};
 const addField=(grid,label,value)=>{
  const field=document.createElement('div');field.className='tournament-card__field';
  const term=document.createElement('dt');term.textContent=label;
  const detail=document.createElement('dd');detail.textContent=String(value);
  field.append(term,detail);grid.appendChild(field);
 };
 rows.forEach(t=>{
  const card=document.createElement('article');card.className='tournament-card';
  const header=document.createElement('div');header.className='tournament-card__header';
  const title=document.createElement('h5');title.className='tournament-card__title';
  title.textContent=t.name||'Turniej bez nazwy';
  if(t.parent_tournament_id)title.textContent+=' · eliminacje dla #'+t.parent_tournament_id;
  const id=document.createElement('span');id.className='tournament-card__id';id.textContent='#'+t.id;
  title.appendChild(document.createTextNode(' '));title.appendChild(id);
  const status=document.createElement('span');status.className='tournament-status';
  status.dataset.status=String(t.status||'unknown');
  status.textContent=statusLabels[t.status]||('Status: '+(t.status||'nieznany'));
  header.append(title,status);card.appendChild(header);
  const fields=document.createElement('dl');fields.className='tournament-card__fields';
  const completed=Number(t.completed_games??0);
  const planned=Number(t.total_games_planned??t.games_planned??0);
  addField(fields,'Format',t.format||'—');
  addField(fields,'Uczestnicy',Number(t.players??0));
  addField(fields,'Postęp partii',completed+' / '+planned);
  addField(fields,'Awans do draftu',(t.qualification_percent||80)+'%');
  if(!t.parent_tournament_id&&t.initial_entrant_count!=null)addField(fields,'Limit nowych zawodników',Number(t.late_admission_used||0)+' / '+Number(t.late_admission_limit||0));
  if(t.status==='stopped'||t.status==='running'||t.resume_blocked)addField(fields,'Checkpoint',t.resume_blocked?'brak · wznowienie zablokowane — wymagany przegląd':(t.has_checkpoint?'zapisany · gotowy do wznowienia':'brak aktywnej partii do odtworzenia'));
  card.appendChild(fields);
  const actions=document.createElement('div');actions.className='tournament-card__actions';actions.setAttribute('aria-label','Akcje turnieju '+(t.name||t.id));
  const view=document.createElement('button');view.textContent='Tabela i drabinka';view.onclick=()=>loadTournamentView(t.id);actions.appendChild(view);
  const diagnostic=document.createElement('section');diagnostic.className='tournament-preflight';diagnostic.hidden=true;diagnostic.setAttribute('aria-live','polite');
  card.appendChild(diagnostic);
  const openRegistry=(target)=>{const el=$(target);if(el){el.scrollIntoView({behavior:'smooth',block:'start'});el.setAttribute('tabindex','-1');el.focus({preventScroll:true});}};
  const openModelAdmission=(modelId)=>{const target=$('model-admission-'+modelId);if(!target){openRegistry('model-admission-list');$('league-message').textContent='Model '+modelId+' nie ma osobnego wpisu na jawnej allowliście. Sprawdź manifest i zakres dopuszczenia.';return;}target.scrollIntoView({behavior:'smooth',block:'center'});target.focus({preventScroll:true});target.style.outline='3px solid #f1d99d';target.style.outlineOffset='3px';};
  const showPreflight=async()=>{
   diagnostic.hidden=false;diagnostic.dataset.ready='unknown';diagnostic.textContent='Sprawdzam warunki startu…';
   try{
    const r=await fetch('/api/tournaments/'+t.id+'/preflight',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);
    const entrants=d.entrants||[];const blocked=d.blocked||[];diagnostic.replaceChildren();diagnostic.dataset.ready=String(Boolean(d.ready));
    const heading=document.createElement('div');heading.className='tournament-preflight__title';heading.textContent=d.ready?'Gotowy do startu — wszystkie warunki spełnione':'Start zablokowany — '+blocked.length+' zawodników wymaga działania';diagnostic.appendChild(heading);
    const runtime=document.createElement('p');runtime.textContent='Runtime Llamafile: '+(d.runtime_available?'dostępny':'niedostępny')+'. Brak lokalnego GGUF nie blokuje startu — modele są pobierane na żądanie, jeśli ich manifest i dopuszczenie są poprawne.';diagnostic.appendChild(runtime);
    entrants.forEach(e=>{
     const row=document.createElement('div');row.className='tournament-preflight__entrant';
     const name=document.createElement('strong');name.textContent=(e.ready_to_start?'✓ ':'⚠ ')+e.name+' · model '+e.model_id;row.appendChild(name);
     const reasons=e.blockers||[];
     if(reasons.length){const list=document.createElement('ul');list.className='tournament-preflight__reasons';reasons.forEach(reason=>{const li=document.createElement('li');li.textContent=reason;list.appendChild(li)});row.appendChild(list)}
     else{const ok=document.createElement('p');ok.textContent='Brak blokad. '+(e.local_file_available?'Plik jest dostępny lokalnie.':(e.download_on_demand?'Model zostanie pobrany na żądanie.':'Model nie wymaga obecnie lokalnego pliku.'));row.appendChild(ok)}
     if(reasons.length){const controls=document.createElement('div');controls.className='tournament-preflight__actions';
      const admission=reasons.some(x=>/allowlist|dopuszc|manifest|rewizj|scope|repozytor/i.test(x));
      const license=reasons.some(x=>/licencj/i.test(x));
      if(admission){const b=document.createElement('button');b.type='button';b.textContent='Pokaż warunki dopuszczenia · model '+e.model_id;b.onclick=()=>openModelAdmission(e.model_id);controls.appendChild(b)}
      if(license){const b=document.createElement('button');b.type='button';b.textContent='Otwórz rejestr modeli i licencje';b.onclick=()=>openRegistry('model-list');controls.appendChild(b)}
      const reviewSelect=$('review-model-id');
      const reviewSupported=reviewSelect&&Array.from(reviewSelect.options).some(option=>option.value===String(e.model_id));
      if(reviewSupported){const b=document.createElement('button');b.type='button';b.textContent='Przygotuj propozycję do audytu · model '+e.model_id;b.onclick=()=>{const form=$('model-review-form');if(form){form.hidden=false;reviewSelect.value=String(e.model_id);$('model-review-status').textContent='Propozycja dotyczy dokładnej rewizji '+e.revision+'. Zapis trafia do audytu i nie zmienia allowlisty ani nie dopuszcza modelu.';openRegistry('model-review-form')}else openRegistry('model-admission-list')};controls.appendChild(b)}
      row.appendChild(controls);
     }
     diagnostic.appendChild(row);
    });
    const refresh=document.createElement('button');refresh.type='button';refresh.textContent='Ponów diagnostykę';refresh.onclick=showPreflight;diagnostic.appendChild(refresh);
    $('league-message').textContent='Diagnostyka turnieju #'+t.id+': '+entrants.length+' uczestników, '+blocked.length+' z blokadą.';
   }catch(e){diagnostic.textContent='Nie udało się sprawdzić gotowości: '+e;$('league-message').textContent=diagnostic.textContent}
  };
  if(['planned','stopped','failed'].includes(t.status)){
   const check=document.createElement('button');check.textContent='Dlaczego nie startuje?';check.onclick=showPreflight;actions.appendChild(check);
  }
  if(['planned','stopped','failed'].includes(t.status)&&!t.resume_blocked){
   const start=document.createElement('button');start.textContent=t.status==='planned'?'Uruchom turniej':'Wznów turniej';
   start.onclick=async()=>{
    try{const check=await fetch('/api/tournaments/'+t.id+'/preflight',{cache:'no-store'});const readiness=await check.json();if(!check.ok)throw Error(readiness.error||check.status);if(!readiness.ready){await showPreflight();diagnostic.scrollIntoView({behavior:'smooth',block:'nearest'});return;}
     if(!confirm('Wszystkie warunki preflight są spełnione. Uruchomić turniej? Modele będą ładowane pojedynczo, kolejno do RAM.'))return;
     const r=await fetch('/api/tournaments/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tournament_id:t.id})});const d=await r.json();if(!r.ok){await showPreflight();throw Error(d.error||r.status)}$('league-message').textContent='Start turnieju przyjęty; sprawdzam stan runnera…';await loadLeague();
    }catch(e){$('league-message').textContent='Nie uruchomiono turnieju: '+e}
   };actions.appendChild(start);
  }
  if(t.status==='running'){
   const stop=document.createElement('button');stop.className='stop';stop.textContent='Zatrzymaj turniej';
   stop.onclick=async()=>{
    if(!confirm('Bezpiecznie zatrzymać turniej? Ostatnia zweryfikowana pozycja zostanie zapisana i będzie można wznowić od tego miejsca.'))return;
    try{const r=await fetch('/api/tournaments/stop',{method:'POST'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent=d.status==='stop_requested'?'Żądanie zatrzymania przyjęte. Trwa zapis ostatniej zweryfikowanej pozycji.':'Turniej nie jest obecnie uruchomiony.';await loadLeague()}
    catch(e){$('league-message').textContent='Nie udało się zatrzymać: '+e}
   };actions.appendChild(stop);
  }
  card.appendChild(actions);host.appendChild(card);
  if(t.status==='planned')showPreflight();
 });
}
function renderModelStatus(models,storage){
 arenaModels=Array.isArray(models)?models:[];
 const active=arenaModels.filter(m=>Boolean(m.active));
 const files=new Set(((storage&&storage.managed_models)||[]).map(file=>file.filename).filter(Boolean));
 const ready=active.filter(m=>m.status==='downloaded'&&Number(m.managed_copy)===1&&Number(m.license_verified)===1&&m.filename&&m.sha256&&m.size_bytes&&files.has(m.filename));
 $('registry-count').textContent=String(active.length);
 $('ready-count').textContent=String(ready.length);
 $('model-status-note').textContent='Rejestr: '+active.length+' aktywnych wpisów. Lokalnie gotowe do uruchomienia: '+ready.length+'. Wpisy skatalogowane bez zweryfikowanej kopii w magazynie areny nie są liczone jako dostępne.';
}
const reviewOpen=$('model-review-open');
if(reviewOpen)reviewOpen.addEventListener('click',()=>{clearReviewPreview('');delete $('model-review-form').dataset.editSubmissionId;delete $('model-review-form').dataset.originalProposal;$('model-review-form').hidden=false;$('model-review-status').textContent='';});
const reviewCancel=$('model-review-cancel');
if(reviewCancel)reviewCancel.addEventListener('click',()=>{clearReviewPreview('');delete $('model-review-form').dataset.editSubmissionId;delete $('model-review-form').dataset.originalProposal;$('model-review-form').hidden=true;});
const reviewForm=$('model-review-form');
const reviewProposedStatus=$('review-proposed-status');
const reviewScopeInputs=reviewForm?[...reviewForm.querySelectorAll('input[name=review-scope-format]')]:[];
let pendingReviewPayload=null;
function clearReviewPreview(message='Dane zmieniono. Wygeneruj nowe podsumowanie przed zapisem.'){
 if(!pendingReviewPayload)return;
 pendingReviewPayload=null;
 reviewForm.querySelector('[data-review-preview]')?.remove();
 const submit=reviewForm.querySelector('button[type="submit"]');if(submit){submit.disabled=false;submit.textContent='Pokaż podsumowanie przed zapisem';}
 if(message)$('model-review-status').textContent=message;
}
function syncReviewScopeControls(){
 const blocking=['review_required','blocked'].includes(reviewProposedStatus?.value);
 reviewScopeInputs.forEach(input=>{input.disabled=blocking;if(blocking)input.checked=false;});
 const hint=$('review-scope-hint');
 if(hint)hint.textContent=blocking?'Dla statusów „wymaga przeglądu” i „zablokowany” zakres dopuszczenia musi pozostać pusty. Pola zakresu są wyłączone.':'Dla statusów „eligible” i „restricted” wybierz co najmniej jeden zakres: PURE_AI lub AI_ENGINE.';
}
if(reviewProposedStatus){reviewProposedStatus.addEventListener('change',syncReviewScopeControls);syncReviewScopeControls();}
if(reviewForm){
 reviewForm.querySelectorAll('input,select,textarea').forEach(field=>{field.addEventListener('input',()=>clearReviewPreview());field.addEventListener('change',()=>clearReviewPreview());});
 reviewForm.addEventListener('submit',event=>{
  event.preventDefault();
  const proposedStatus=reviewProposedStatus.value;
  const selectedScopes=reviewScopeInputs.filter(input=>input.checked).map(input=>input.value);
  if(['eligible','restricted'].includes(proposedStatus)&&!selectedScopes.length){$('model-review-status').textContent='Nie zapisano propozycji: dla statusu eligible/restricted wybierz co najmniej jeden zakres.';return;}
  if(['review_required','blocked'].includes(proposedStatus)&&selectedScopes.length){$('model-review-status').textContent='Nie zapisano propozycji: status blokujący nie może mieć zakresu dopuszczenia.';return;}
  const payload={model_id:Number($('review-model-id').value),license_id:$('review-license-id').value,license_review:$('review-license-status').value,license_notes:$('review-license-notes').value,proposed_status:proposedStatus,scope_formats:selectedScopes,restrictions:$('review-restrictions').value,obligations:$('review-obligations').value,block_reason:$('review-block-reason').value,technical_readiness:$('review-technical').value,technical_evidence:$('review-technical-evidence').value,technical_blockers:$('review-technical-blockers').value,revision_confirmed:$('review-revision-confirmed').checked,revision_notes:$('review-revision-notes').value};
  pendingReviewPayload=payload;
  const original=reviewForm.dataset.originalProposal?JSON.parse(reviewForm.dataset.originalProposal):{};
  const labels={model_id:'Model',license_id:'Licencja',license_review:'Ocena licencji',license_notes:'Dowody licencji',proposed_status:'Proponowany status',scope_formats:'Zakres',restrictions:'Ograniczenia',obligations:'Obowiązki',block_reason:'Uzasadnienie blokady',technical_readiness:'Gotowość techniczna',technical_evidence:'Dowody techniczne',technical_blockers:'Blokady techniczne',revision_confirmed:'Potwierdzenie rewizji',revision_notes:'Uwagi o rewizji'};
  const preview=document.createElement('section');preview.dataset.reviewPreview='1';preview.style.cssText='padding:12px;margin:10px 0;border:1px solid #52627a;border-radius:9px';
  const heading=document.createElement('strong');heading.textContent='Podsumowanie propozycji przed zapisem';preview.appendChild(heading);
  const intro=document.createElement('p');intro.textContent='Sprawdź wartości oraz różnice względem poprzedniej wersji. Sam podgląd niczego nie zapisuje ani nie dopuszcza modelu.';preview.appendChild(intro);
  const list=document.createElement('ul');Object.entries(labels).forEach(([key,label])=>{const before=original[key];const after=payload[key];if(reviewForm.dataset.originalProposal&&JSON.stringify(before)===JSON.stringify(after))return;const item=document.createElement('li');const name=document.createElement('strong');name.textContent=label+': ';item.appendChild(name);const value=document.createElement('span');const format=v=>Array.isArray(v)?(v.join(', ')||'—'):(typeof v==='boolean'?(v?'Tak':'Nie'):(String(v??'').trim()||'—'));value.textContent=reviewForm.dataset.originalProposal?'przed: '+format(before)+' → po: '+format(after):format(after);item.appendChild(value);list.appendChild(item);});preview.appendChild(list);
  const safety=document.createElement('p');safety.className='notice';safety.textContent='Zapis tworzy wyłącznie propozycję audytową. Nie zmienia allowlisty, nie potwierdza licencji i nie uruchamia modelu.';preview.appendChild(safety);
  const confirm=document.createElement('button');confirm.type='button';confirm.textContent='Potwierdź i zapisz propozycję';
  confirm.onclick=async()=>{if(!pendingReviewPayload)return;confirm.disabled=true;const submit=reviewForm.querySelector('button[type="submit"]');submit.disabled=true;try{const editingId=reviewForm.dataset.editSubmissionId;const response=await fetch(editingId?'/api/model-review-submissions/action':'/api/model-review-submissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(editingId?{submission_id:Number(editingId),action:'revise',proposal:pendingReviewPayload,reason:'Poprawiono w panelu'}:pendingReviewPayload)});const data=await response.json();if(!response.ok)throw Error(data.error||response.status);pendingReviewPayload=null;$('model-review-status').textContent=editingId?`Zaktualizowano propozycję #${editingId}; wróciła do kolejki przeglądu.`:`Zapisano propozycję #${data.submission_id} dla modelu ${data.model_id}. ${data.note}`;delete reviewForm.dataset.editSubmissionId;delete reviewForm.dataset.originalProposal;reviewForm.reset();syncReviewScopeControls();preview.remove();submit.disabled=false;submit.textContent='Pokaż podsumowanie przed zapisem';await loadModelReviewSubmissions();}catch(error){$('model-review-status').textContent='Nie zapisano propozycji: '+error;confirm.disabled=false;submit.disabled=false;}};
  const edit=document.createElement('button');edit.type='button';edit.className='secondary';edit.textContent='Wróć do edycji';edit.style.marginLeft='8px';edit.onclick=()=>clearReviewPreview('Wrócono do edycji. Po zmianach ponownie pokaż podsumowanie.');preview.append(confirm,edit);reviewForm.querySelector('[data-review-preview]')?.remove();reviewForm.appendChild(preview);const submit=reviewForm.querySelector('button[type="submit"]');submit.disabled=true;submit.textContent='Podsumowanie gotowe';$('model-review-status').textContent='Przejrzyj podsumowanie. Zapis wymaga osobnego potwierdzenia.';
 });
}
async function loadModelReviewSubmissions(){
 const host=$('model-review-queue');if(!host)return;host.textContent='Pobieram propozycje…';
 try{const r=await fetch('/api/model-review-submissions',{cache:'no-store'});const rows=await r.json();if(!r.ok)throw Error(rows.error||r.status);host.replaceChildren();
  if(!Array.isArray(rows)||!rows.length){host.textContent='Brak propozycji do przeglądu.';$('model-review-queue-status').textContent='0 zgłoszeń';return;}
  $('model-review-queue-status').textContent=rows.length+' zgłoszeń';
  rows.forEach(item=>{const card=document.createElement('article');card.style.cssText='padding:12px;margin:9px 0;border:1px solid #52627a;border-radius:9px;overflow-wrap:anywhere';
   const title=document.createElement('strong');title.textContent='Propozycja #'+item.id+' · model '+item.model_id+' · '+item.status;card.appendChild(title);
   const meta=document.createElement('p');meta.textContent='Zapisano: '+item.submitted_at+' · licencja: '+(item.proposal.license_id||'brak')+' ('+(item.proposal.license_review||'pending')+') · techniczna: '+(item.proposal.technical_readiness||'not_evaluated')+' · proponowany status: '+(item.proposal.proposed_status||'review_required');card.appendChild(meta);
   const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Pełna propozycja i dowody';details.appendChild(summary);const pre=document.createElement('pre');pre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px';pre.textContent=JSON.stringify(item.proposal,null,2);details.appendChild(pre);card.appendChild(details);
   if(item.history?.length){const hist=document.createElement('details');const hs=document.createElement('summary');hs.textContent='Historia decyzji ('+item.history.length+')';hist.appendChild(hs);const ul=document.createElement('ul');item.history.forEach(ev=>{const li=document.createElement('li');li.textContent=ev.event+' · '+ev.actor+' · '+ev.occurred_at+(ev.reason?' · '+ev.reason:'');ul.appendChild(li)});hist.appendChild(ul);card.appendChild(hist)}
   const actions=document.createElement('div');actions.className='formrow';actions.style.marginTop='10px';
   if(item.status==='pending_review'){
    const accept=document.createElement('button');accept.type='button';accept.textContent='Akceptuj do kolejnego etapu';accept.onclick=async()=>{if(!confirm('Przenieść propozycję #'+item.id+' do etapu accepted_into_pr? Allowlista pozostanie bez zmian.'))return;await submitReviewAction(item.id,'accept',{},'')};actions.appendChild(accept);
    const edit=document.createElement('button');edit.type='button';edit.className='secondary';edit.textContent='Popraw propozycję';edit.onclick=()=>{clearReviewPreview('');const p=item.proposal;$('model-review-form').hidden=false;$('review-model-id').value=String(item.model_id);$('review-license-id').value=p.license_id||'';$('review-license-status').value=p.license_review||'pending';$('review-license-notes').value=p.license_notes||'';$('review-proposed-status').value=p.proposed_status||'review_required';$('review-technical').value=p.technical_readiness||'not_evaluated';$('review-restrictions').value=p.restrictions||'';$('review-obligations').value=p.obligations||'';$('review-block-reason').value=p.block_reason||'';$('review-technical-evidence').value=p.technical_evidence||'';$('review-technical-blockers').value=p.technical_blockers||'';$('review-revision-confirmed').checked=!!p.revision_confirmed;$('review-revision-notes').value=p.revision_notes||'';reviewScopeInputs.forEach(x=>x.checked=(p.scope_formats||[]).includes(x.value));syncReviewScopeControls();reviewForm.dataset.editSubmissionId=String(item.id);reviewForm.dataset.originalProposal=JSON.stringify(p);$('model-review-status').textContent='Poprawiasz propozycję #'+item.id+'. Zobaczysz podsumowanie różnic przed zapisem.';reviewForm.scrollIntoView({behavior:'smooth',block:'start'})};actions.appendChild(edit);
    const reject=document.createElement('button');reject.type='button';reject.className='stop';reject.textContent='Odrzuć';reject.onclick=async()=>{const reason=prompt('Uzasadnienie odrzucenia propozycji #'+item.id+':');if(!reason||reason.trim().length<3)return;await submitReviewAction(item.id,'reject',{},reason)};actions.appendChild(reject);
   }
   card.appendChild(actions);host.appendChild(card);
  });
 }catch(e){host.textContent='Nie udało się pobrać kolejki: '+e;$('model-review-queue-status').textContent='Błąd odczytu'}
}
async function submitReviewAction(id,action,proposal={},reason=''){try{const r=await fetch('/api/model-review-submissions/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({submission_id:id,action,proposal,reason})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('model-review-queue-status').textContent='Propozycja #'+id+': '+d.status+'. '+d.note;await loadModelReviewSubmissions()}catch(e){$('model-review-queue-status').textContent='Nie zapisano decyzji: '+e}}
$('model-review-refresh')?.addEventListener('click',loadModelReviewSubmissions);loadModelReviewSubmissions();

let hfCandidateItems=[];
function renderHfCandidates(){
 const host=$('hf-candidate-list');if(!host)return;host.replaceChildren();
 const query=String($('hf-candidate-query')?.value||'').trim().toLowerCase();
 const statusFilter=String($('hf-candidate-status-filter')?.value||'all');
 const filtered=hfCandidateItems.filter(x=>{
  const matchesQuery=!query||[x.display_name,x.repo_id,x.declared_license_tag,x.catalog_id].some(v=>String(v||'').toLowerCase().includes(query));
  const inactive=!['active','not_checked'].includes(x.source_status);
  const matchesStatus=statusFilter==='all'||(statusFilter==='no_audit'&&!x.last_audit&&!inactive)||(statusFilter==='needs_review'&&!!x.last_audit&&!x.last_review&&!inactive)||(statusFilter==='review_in_progress'&&!!x.last_review)||(statusFilter==='inactive'&&inactive);
  return matchesQuery&&matchesStatus;
 });
 $('hf-candidate-count').textContent='Katalog: '+hfCandidateItems.length+' kandydatów · widoczne: '+Math.min(filtered.length,30)+' / '+filtered.length+'. Audyt uruchamiany osobno dla wybranego wpisu.';
 if(!filtered.length){host.textContent='Brak kandydatów pasujących do wyszukiwania.';return;}
 filtered.slice(0,30).forEach(item=>{
  const card=document.createElement('div');card.style.cssText='padding:9px;margin:7px 0;border:1px solid #34445c;border-radius:8px';
  const title=document.createElement('strong');title.textContent=item.display_name+' · '+item.repo_id;card.appendChild(title);
  const meta=document.createElement('div');meta.style.cssText='font-size:12px;color:#aebbd0';meta.textContent='Licencja deklarowana: '+(item.declared_license_tag||'brak')+' · rewizja: '+String(item.revision||item.audited_revision||'brak').slice(0,12)+' · status: '+(item.source_status||'—')+' · '+(item.last_audit?'audyt: '+item.last_audit.audited_at:'bez audytu');card.appendChild(meta);
  const downloadButton=document.createElement('button');downloadButton.type='button';downloadButton.className='secondary';downloadButton.textContent='Pliki GGUF · pobierz i zweryfikuj';downloadButton.style.marginTop='7px';downloadButton.onclick=()=>openHfDownloadPicker(item);card.appendChild(downloadButton);
  if(item.last_audit){const show=document.createElement('button');show.type='button';show.className='secondary';show.textContent='Pokaż zapisany audyt';show.style.marginTop='7px';show.onclick=async()=>{try{const url='/api/hf-candidates/audit?catalog_id='+encodeURIComponent(item.catalog_id)+'&revision='+encodeURIComponent(item.last_audit.revision);const r=await fetch(url,{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);renderHfAuditReport(d);}catch(e){$('hf-candidate-audit-result').textContent='Nie udało się odczytać raportu: '+e;}};card.appendChild(show);}
  if(item.last_review){const reviewMeta=document.createElement('div');reviewMeta.style.cssText='font-size:12px;color:#aebbd0;margin-top:4px';reviewMeta.textContent='Ręczny przegląd zapisany: '+item.last_review.reviewed_at+' · status: w toku (bez dopuszczenia)';card.appendChild(reviewMeta);}
  if(item.last_audit){const reviewButton=document.createElement('button');reviewButton.type='button';reviewButton.className='secondary';reviewButton.textContent='Otwórz weryfikację w panelu';reviewButton.style.marginTop='7px';reviewButton.onclick=async()=>{try{const url='/api/hf-candidates/audit?catalog_id='+encodeURIComponent(item.catalog_id)+'&revision='+encodeURIComponent(item.last_audit.revision);const r=await fetch(url,{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);renderHfAuditReport(d);}catch(e){$('hf-candidate-audit-result').textContent='Nie udało się otworzyć weryfikacji: '+e;}};card.appendChild(reviewButton);}
  const canAudit=['active','not_checked'].includes(item.source_status);
  const button=document.createElement('button');button.type='button';button.textContent=canAudit?'Wykonaj wstępny audyt':'Audyt niedostępny · źródło nieaktywne';button.disabled=!canAudit;button.style.marginTop='7px';button.onclick=async()=>{
   button.disabled=true;button.textContent='Analizuję publiczne metadane…';const output=$('hf-candidate-audit-result');output.textContent='Audyt '+item.repo_id+'…';
   try{const r=await fetch('/api/hf-candidates/audit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({catalog_id:item.catalog_id})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);renderHfAuditReport(d);item.last_audit={audited_at:d.audited_at,revision:d.pinned_revision};}
   catch(e){output.textContent='Audyt nie został ukończony: '+e;}finally{button.disabled=false;button.textContent='Wykonaj ponowny audyt';meta.textContent='Licencja deklarowana: '+(item.declared_license_tag||'brak')+' · rewizja: '+String(item.revision||item.audited_revision||'brak').slice(0,12)+' · '+(item.last_audit?'audyt: '+item.last_audit.audited_at:'bez audytu');}
  };card.appendChild(button);host.appendChild(card);
 });
}
function renderHfAuditReport(report){
 const host=$('hf-candidate-audit-result');host.replaceChildren();
 const title=document.createElement('strong');title.textContent='Wynik wstępnego audytu: '+report.repo_id;host.appendChild(title);
 const summary=document.createElement('p');summary.textContent='Wynik: public_metadata_only · rewizja zgodna: '+(report.revision_matches?'tak':'nie')+' · licencja deklarowana: '+(report.declared_license||'brak')+' · pliki licencji: '+((report.license_files||[]).join(', ')||'brak');host.appendChild(summary);
 const notes=document.createElement('ul');(report.sources||[]).forEach(source=>{const li=document.createElement('li');li.textContent=source;notes.appendChild(li)});host.appendChild(notes);
 const blockers=document.createElement('p');blockers.textContent='Ograniczenia: '+(report.limitations||[]).join(' ');host.appendChild(blockers);
 const provenance=document.createElement('details');const provenanceSummary=document.createElement('summary');provenanceSummary.textContent='Pochodzenie, pewność i decyzja wymagane dla każdego pola';provenance.appendChild(provenanceSummary);const provenancePre=document.createElement('pre');provenancePre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px';provenancePre.textContent=JSON.stringify(report.field_provenance||{},null,2);provenance.appendChild(provenancePre);host.appendChild(provenance);
 const link=document.createElement('a');link.href='https://huggingface.co/'+report.repo_id+'/tree/'+report.pinned_revision;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Otwórz przypiętą rewizję Hugging Face';host.appendChild(link);
 const f=report.suggested_form||{};
 if(report.arena_model_id){
  const fill=document.createElement('button');fill.type='button';fill.textContent='Wypełnij formularz propozycji tymi danymi';fill.style.display='block';fill.style.marginTop='9px';fill.onclick=()=>{
   const form=$('model-review-form');form.hidden=false;$('review-model-id').value=String(report.arena_model_id);
   $('review-license-id').value=f.license_id||'';$('review-license-status').value='pending';$('review-license-notes').value=f.license_notes||'';
   $('review-proposed-status').value='review_required';$('review-technical').value='not_evaluated';
   $('review-restrictions').value='';$('review-obligations').value='';$('review-block-reason').value=f.block_reason||'';
   $('review-technical-evidence').value=f.technical_evidence||'';$('review-technical-blockers').value=f.technical_blockers||'';
   $('review-revision-confirmed').checked=false;$('review-revision-notes').value=f.revision_notes||'';
   if(typeof syncReviewScopeControls==='function')syncReviewScopeControls();$('model-review-status').textContent='Pola wypełniono wstępną analizą. Sprawdź źródła i popraw dane; nic nie zapisano ani nie zatwierdzono.';form.scrollIntoView({behavior:'smooth',block:'start'});
  };host.appendChild(fill);
 }else{const note=document.createElement('p');note.textContent='To kandydat katalogowy bez aktywnego model_id w allowliście. Raport zapisano osobno; nie można wysłać go do formularza decyzji admission.';host.appendChild(note)}
 const evidence=document.createElement('details');const summaryEl=document.createElement('summary');summaryEl.textContent='Szczegóły techniczne i odczytane pliki tekstowe';evidence.appendChild(summaryEl);
 const pre=document.createElement('pre');pre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px';pre.textContent=JSON.stringify({metadata:report.metadata,field_provenance:report.field_provenance,license_file_texts:report.license_file_texts,readme_excerpt:report.readme_excerpt,suggested_form:f},null,2);evidence.appendChild(pre);host.appendChild(evidence);
 const reviewHost=document.createElement('section');reviewHost.style.cssText='margin-top:12px;padding:12px;border:1px solid #52627a;border-radius:9px';reviewHost.textContent='Ładuję formularz ręcznej weryfikacji…';host.appendChild(reviewHost);
 loadHfCandidateReview(report,reviewHost);
}
async function loadHfCandidateReview(report,host){
 const query='?catalog_id='+encodeURIComponent(report.catalog_id)+'&revision='+encodeURIComponent(report.pinned_revision);
 try{const response=await fetch('/api/hf-candidates/review'+query,{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||response.status);renderHfCandidateReviewForm(report,data,host);}
 catch(error){host.textContent='Nie udało się odczytać ręcznego przeglądu: '+error;}
}
function renderHfCandidateReviewForm(report,saved,host){
 host.replaceChildren();
 const heading=document.createElement('strong');heading.textContent='Ręczna weryfikacja kandydata · '+report.display_name;host.appendChild(heading);
 const warning=document.createElement('p');warning.className='notice';warning.textContent='Zapisujesz ustalenia i dowody dla dokładnej rewizji. Zapis nie zatwierdza licencji, nie dodaje modelu do allowlisty i nie dopuszcza go do turnieju.';host.appendChild(warning);
 const prior=saved&&saved.review?saved.review:{};
 const makeField=(labelText,value,tag='textarea')=>{const label=document.createElement('label');label.style.cssText='display:block;margin:8px 0';label.textContent=labelText;const field=document.createElement(tag);field.className='field';field.style.cssText='display:block;width:100%;margin-top:4px';if(tag==='textarea')field.rows=3;field.value=value||'';label.appendChild(field);host.appendChild(label);return field;};
 const makeSelect=(labelText,options,value)=>{const label=document.createElement('label');label.style.cssText='display:block;margin:8px 0';label.textContent=labelText;const select=document.createElement('select');select.className='field';select.style.cssText='display:block;width:100%;margin-top:4px';options.forEach(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;select.appendChild(o)});select.value=value||options[0][0];label.appendChild(select);host.appendChild(label);return select;};
 const licenseId=makeField('Identyfikator licencji',prior.license_id||report.declared_license||'','input');
 const licenseStatus=makeSelect('Ocena licencji',[['pending','Niezweryfikowana'],['declared_permissive','Zadeklarowana przez repozytorium — permissive'],['verified_permissive','Niezależnie zweryfikowana — permissive'],['verified_restricted','Zweryfikowana — z ograniczeniami'],['not_compatible','Niezgodna']],prior.license_status||'pending');
 const licenseEvidence=makeField('Źródła i uzasadnienie oceny licencji',prior.license_evidence||((report.sources||[]).join('\n')+'\n\nWstępna deklaracja katalogu nie stanowi niezależnego potwierdzenia licencji.'));licenseEvidence.placeholder='Link do licencji, treść warunków, zakres praw, obowiązki i uzasadnienie';
 const revisionConfirmed=document.createElement('input');revisionConfirmed.type='checkbox';revisionConfirmed.checked=prior.revision_confirmed===true;const revisionLabel=document.createElement('label');revisionLabel.style.cssText='display:block;margin:8px 0';revisionLabel.append(revisionConfirmed,document.createTextNode(' Potwierdzam ręcznie zgodność dokładnej rewizji SHA'));host.appendChild(revisionLabel);
 const revisionNotes=makeField('Dowód porównania rewizji',prior.revision_notes||('Przypięta rewizja: '+report.pinned_revision+'\nOdczytana rewizja: '+report.observed_revision));
 const technical=makeSelect('Ocena gotowości technicznej',[['not_evaluated','Nieoceniona'],['ready','Test techniczny pozytywny'],['blocked','Zablokowana']],prior.technical_readiness||'not_evaluated');
 const technicalEvidence=makeField('Dowody testów technicznych',prior.technical_evidence||'');technicalEvidence.placeholder='Wersja runtime, sprzęt, testy i wyniki — bez wpisywania przypuszczeń';
 const technicalBlockers=makeField('Blokady techniczne',prior.technical_blockers||((report.suggested_form||{}).technical_blockers||''));
 const reviewerNotes=makeField('Uwagi recenzenta',prior.reviewer_notes||'');
 const status=document.createElement('p');status.className='status';status.setAttribute('role','status');status.textContent=saved&&saved.reviewed_at?'Ostatni zapis: '+saved.reviewed_at+' · '+saved.reviewer+' · weryfikacja w toku':'Brak zapisanego ręcznego przeglądu.';host.appendChild(status);
 const preview=document.createElement('section');preview.className='panel';preview.style.cssText='display:none;margin:10px 0;padding:10px;border:1px solid #52627a;border-radius:8px';preview.setAttribute('aria-live','polite');host.appendChild(preview);
 let pendingPayload=null;
 const save=document.createElement('button');save.type='button';save.textContent='Pokaż podsumowanie zmian';
 const confirmSave=document.createElement('button');confirmSave.type='button';confirmSave.textContent='Potwierdź i zapisz ustalenia';confirmSave.disabled=true;confirmSave.style.display='none';confirmSave.style.marginLeft='8px';
 const fields=[['license_id','Identyfikator licencji'],['license_status','Ocena licencji'],['license_evidence','Źródła i uzasadnienie licencji'],['revision_confirmed','Potwierdzenie rewizji'],['revision_notes','Dowód porównania rewizji'],['technical_readiness','Gotowość techniczna'],['technical_evidence','Dowody testów technicznych'],['technical_blockers','Blokady techniczne'],['reviewer_notes','Uwagi recenzenta']];
 const currentPayload=()=>({catalog_id:report.catalog_id,revision:report.pinned_revision,license_id:licenseId.value,license_status:licenseStatus.value,license_evidence:licenseEvidence.value,revision_confirmed:revisionConfirmed.checked,revision_notes:revisionNotes.value,technical_readiness:technical.value,technical_evidence:technicalEvidence.value,technical_blockers:technicalBlockers.value,reviewer_notes:reviewerNotes.value});
 const invalidatePreview=()=>{if(!pendingPayload)return;pendingPayload=null;preview.style.display='none';preview.replaceChildren();confirmSave.disabled=true;confirmSave.style.display='none';status.textContent='Dane zmieniono po podglądzie. Wygeneruj nowe podsumowanie przed zapisem.';};
 [licenseId,licenseStatus,licenseEvidence,revisionConfirmed,revisionNotes,technical,technicalEvidence,technicalBlockers,reviewerNotes].forEach(field=>{field.addEventListener('input',invalidatePreview);field.addEventListener('change',invalidatePreview);});
 save.onclick=()=>{const payload=currentPayload();pendingPayload=payload;preview.replaceChildren();const heading=document.createElement('strong');heading.textContent='Podsumowanie zmian przed zapisem';preview.appendChild(heading);const intro=document.createElement('p');intro.textContent='Kandydat: '+report.repo_id+' · rewizja: '+report.pinned_revision+'. Sprawdź każdą zmianę. Zapis pozostaje wyłącznie notatką audytową.';preview.appendChild(intro);const list=document.createElement('ul');let changed=0;fields.forEach(([key,label])=>{const before=prior[key]===undefined?(key==='license_id'?report.declared_license||'':''):prior[key];const after=payload[key];if(JSON.stringify(before)===JSON.stringify(after))return;changed++;const item=document.createElement('li');const name=document.createElement('strong');name.textContent=label+': ';item.appendChild(name);const value=document.createElement('span');const format=v=>typeof v==='boolean'?(v?'Tak':'Nie'):(String(v||'').trim()||'—');value.textContent='przed: '+format(before)+' → po: '+format(after);item.appendChild(value);list.appendChild(item);});if(!changed){const item=document.createElement('li');item.textContent='Brak zmian względem ostatniego zapisu.';list.appendChild(item);}preview.appendChild(list);const safety=document.createElement('p');safety.className='notice';safety.textContent='To nie zatwierdza licencji, nie zmienia allowlisty i nie dopuszcza modelu do turnieju.';preview.appendChild(safety);preview.style.display='block';confirmSave.disabled=false;confirmSave.style.display='inline-block';status.textContent='Podgląd gotowy. Zapis nastąpi dopiero po osobnym potwierdzeniu.';};
 confirmSave.onclick=async()=>{if(!pendingPayload)return;confirmSave.disabled=true;save.disabled=true;status.textContent='Zapisuję potwierdzone ustalenia…';try{const r=await fetch('/api/hf-candidates/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pendingPayload)});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);pendingPayload=null;status.textContent=d.note+' Zapis: '+d.reviewed_at;await loadHfCandidateReview(report,host);loadHfCandidates();loadHfCandidateReviewQueue();}catch(error){status.textContent='Nie zapisano: '+error;confirmSave.disabled=false;}finally{save.disabled=false;}};host.append(save,confirmSave);
 if(saved&&Array.isArray(saved.history)&&saved.history.length){const history=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Historia zapisów ('+saved.history.length+')';history.appendChild(summary);const list=document.createElement('ol');saved.history.forEach(event=>{const li=document.createElement('li');li.textContent=event.occurred_at+' · '+event.actor+' · '+(event.review.license_status||'pending')+' · '+(event.review.technical_readiness||'not_evaluated');list.appendChild(li)});history.appendChild(list);host.appendChild(history);}
 const note=document.createElement('p');note.className='notice';note.textContent='Historia zapisów jest zachowywana dla każdej przypiętej rewizji. Zmiana rewizji wymaga nowego przeglądu.';host.appendChild(note);
}
async function loadHfCandidateReviewQueue(){
 const host=$('hf-review-queue');if(!host)return;host.textContent='Pobieram propozycje…';
 try{const r=await fetch('/api/hf-candidates/review-queue',{cache:'no-store'});const rows=await r.json();if(!r.ok)throw Error(rows.error||r.status);host.replaceChildren();
  if(!Array.isArray(rows)||!rows.length){host.textContent='Brak propozycji kandydatów katalogowych.';$('hf-review-queue-status').textContent='0 propozycji';return;}
  $('hf-review-queue-status').textContent=rows.length+' propozycji';
  rows.forEach(item=>{const card=document.createElement('article');card.style.cssText='padding:10px;margin:8px 0;border:1px solid #52627a;border-radius:8px;overflow-wrap:anywhere';
   const title=document.createElement('strong');title.textContent='#'+item.id+' · '+item.repo_id+' · '+item.status;card.appendChild(title);
   const meta=document.createElement('p');meta.textContent='Rewizja: '+item.revision+' · zgłoszono: '+item.submitted_at+' · licencja: '+(item.review.license_id||'brak')+' ('+(item.review.license_status||'pending')+') · techniczna: '+(item.review.technical_readiness||'not_evaluated');card.appendChild(meta);
   const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Dowody i pełna propozycja';details.appendChild(summary);const pre=document.createElement('pre');pre.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px';pre.textContent=JSON.stringify(item.review,null,2);details.appendChild(pre);card.appendChild(details);
   if(item.history?.length){const hist=document.createElement('details');const hs=document.createElement('summary');hs.textContent='Historia decyzji ('+item.history.length+')';hist.appendChild(hs);const ul=document.createElement('ul');item.history.forEach(ev=>{const li=document.createElement('li');li.textContent=ev.event+' · '+ev.actor+' · '+ev.occurred_at+(ev.reason?' · '+ev.reason:'');ul.appendChild(li)});hist.appendChild(ul);card.appendChild(hist)}
   if(item.status==='pending_review'){const actions=document.createElement('div');actions.className='formrow';const accept=document.createElement('button');accept.type='button';accept.textContent='Akceptuj do etapu PR';accept.onclick=async()=>{if(!confirm('Przenieść propozycję #'+item.id+' do kolejnego etapu PR? Aktualna rewizja zostanie sprawdzona ponownie. Nie nastąpi admission ani pobranie.'))return;await decideHfCandidateProposal(item.id,'accept','')};actions.appendChild(accept);const reject=document.createElement('button');reject.type='button';reject.className='stop';reject.textContent='Odrzuć';reject.onclick=async()=>{const reason=prompt('Uzasadnienie odrzucenia propozycji #'+item.id+':');if(!reason||reason.trim().length<3)return;await decideHfCandidateProposal(item.id,'reject',reason)};actions.appendChild(reject);card.appendChild(actions)}
   host.appendChild(card);
  });
 }catch(e){host.textContent='Nie udało się pobrać kolejki: '+e;$('hf-review-queue-status').textContent='Błąd odczytu'}
}
async function decideHfCandidateProposal(id,action,reason){try{const r=await fetch('/api/hf-candidates/review-action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proposal_id:id,action,reason})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('hf-review-queue-status').textContent='Propozycja #'+id+': '+d.status+'. '+d.note;await loadHfCandidateReviewQueue()}catch(e){$('hf-review-queue-status').textContent='Nie zapisano decyzji: '+e}}
$('hf-review-queue-refresh')?.addEventListener('click',loadHfCandidateReviewQueue);
loadHfCandidateReviewQueue();
async function loadHfCandidates(){
 const host=$('hf-candidate-list');if(!host)return;host.textContent='Odczytuję katalog…';
 try{const r=await fetch('/api/hf-candidates',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);hfCandidateItems=Array.isArray(d)?d:[];renderHfCandidates();}
 catch(e){host.textContent='Nie udało się odczytać katalogu kandydatów: '+e;}
}
$('hf-candidate-query')?.addEventListener('input',renderHfCandidates);
$('hf-candidate-status-filter')?.addEventListener('change',renderHfCandidates);
$('hf-candidate-refresh')?.addEventListener('click',loadHfCandidates);
let hfDownloadSelections=[];
let hfDownloadInventories={};
function renderHfDownloadSelection(host){
 const old=host.querySelector('[data-download-selection]');if(old)old.remove();
 const section=document.createElement('section');section.dataset.downloadSelection='1';section.style.cssText='margin-top:12px;padding:10px;border:1px solid #52627a;border-radius:8px';
 const heading=document.createElement('strong');heading.textContent='Wybrane pliki · '+hfDownloadSelections.length+'/3';section.appendChild(heading);
 const list=document.createElement('ul');hfDownloadSelections.forEach(x=>{const li=document.createElement('li');li.textContent=x.repo_id+' · '+x.name+' · '+(x.size_bytes/1024**3).toFixed(2)+' GiB';list.appendChild(li)});section.appendChild(list);
 const total=hfDownloadSelections.reduce((n,x)=>n+x.size_bytes,0);const summary=document.createElement('p');summary.className='notice';summary.textContent='Łącznie: '+(total/1024**3).toFixed(2)+' GiB · '+new Set(hfDownloadSelections.map(x=>x.catalog_id)).size+' kandydatów. Limit: 16 GiB i 3 pliki.';section.appendChild(summary);
 const consentLabel=document.createElement('label');const consent=document.createElement('input');consent.type='checkbox';consentLabel.append(consent,document.createTextNode(' Potwierdzam pobranie wszystkich wybranych plików do kwarantanny VPS.'));section.appendChild(consentLabel);
 const start=document.createElement('button');start.type='button';start.textContent='Pobierz i zweryfikuj wybrane pliki';start.disabled=!hfDownloadSelections.length;start.style.display='block';start.style.marginTop='8px';start.addEventListener('click',async()=>{if(!consent.checked||!hfDownloadSelections.length)return;start.disabled=true;const groups={};hfDownloadSelections.forEach(x=>{(groups[x.catalog_id]??={catalog_id:x.catalog_id,files:[]}).files.push({name:x.name,sha256:x.sha256,revision:x.revision});});try{const res=await fetch('/api/hf-candidates/download-start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidates:Object.values(groups),confirm_download:true})});const job=await res.json();if(!res.ok)throw Error(job.error||res.status);hfDownloadSelections=[];renderHfDownloadSelection(host);watchHfDownloadJob(job.id,host);}catch(e){summary.textContent='Nie rozpoczęto pobierania: '+e;start.disabled=false;}});section.appendChild(start);
 const clear=document.createElement('button');clear.type='button';clear.className='secondary';clear.textContent='Wyczyść wybór';clear.style.marginLeft='8px';clear.addEventListener('click',()=>{hfDownloadSelections=[];renderHfDownloadSelection(host);host.querySelectorAll('input[type=checkbox][data-hf-file]').forEach(x=>x.checked=false);});section.appendChild(clear);host.appendChild(section);
}
async function openHfDownloadPicker(item){
 const host=$('hf-download-workspace');host.replaceChildren();host.textContent='Pobieram indeks plików GGUF dla przypiętej rewizji…';
 try{
  const r=await fetch('/api/hf-candidates/download-files?catalog_id='+encodeURIComponent(item.catalog_id),{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);hfDownloadInventories[item.catalog_id]=d;
  host.replaceChildren();const title=document.createElement('strong');title.textContent='Wybór plików GGUF · '+d.repo_id;host.appendChild(title);
  const info=document.createElement('p');info.className='notice';info.textContent='Rewizja '+d.revision+' · wolne miejsce: '+(d.free_bytes/1024**3).toFixed(1)+' GiB. Możesz dodawać pliki z kilku repozytoriów do jednej partii. Maks. 3 pliki / 16 GiB.';host.appendChild(info);
  if(!d.files.length){const empty=document.createElement('p');empty.textContent='Nie znaleziono plików GGUF z wiarygodnym rozmiarem i SHA-256 LFS.';host.appendChild(empty);renderHfDownloadSelection(host);return;}
  d.files.forEach(file=>{const label=document.createElement('label');label.style.cssText='display:block;padding:8px;border-top:1px solid #34445c';const check=document.createElement('input');check.type='checkbox';check.dataset.hfFile='1';check.disabled=file.allowlisted!==true;check.title=check.disabled?'Brak jawnej zgody na ten dokładny plik i rewizję.':'';const exists=()=>hfDownloadSelections.some(x=>x.catalog_id===item.catalog_id&&x.name===file.name);check.checked=exists();check.addEventListener('change',()=>{if(check.checked){if(hfDownloadSelections.length>=3||new Set([...hfDownloadSelections.map(x=>x.catalog_id),item.catalog_id]).size>3){check.checked=false;return;}hfDownloadSelections.push({catalog_id:item.catalog_id,repo_id:d.repo_id,revision:file.revision,name:file.name,size_bytes:file.size_bytes,sha256:file.sha256});}else{hfDownloadSelections=hfDownloadSelections.filter(x=>!(x.catalog_id===item.catalog_id&&x.name===file.name));}renderHfDownloadSelection(host);});label.append(check,document.createTextNode(' '+file.name+' · '+(file.size_bytes/1024**3).toFixed(2)+' GiB · SHA-256 '+file.sha256.slice(0,12)+'…'+(file.allowlisted?' · dozwolony':' · blokada: brak jawnej zgody')));host.appendChild(label);});
  renderHfDownloadSelection(host);
 }catch(e){host.textContent='Nie udało się pobrać listy plików: '+e;}
}
function formatDownloadBytes(value){
 const bytes=Number(value)||0;
 if(bytes>=1024**3)return (bytes/1024**3).toFixed(2)+' GiB';
 if(bytes>=1024**2)return (bytes/1024**2).toFixed(1)+' MiB';
 return bytes.toLocaleString('pl-PL')+' B';
}
async function refreshHfDownloadStatus(){
 const host=$('hf-download-status-body');if(!host)return;
 try{
  const r=await fetch('/api/hf-candidates/download-jobs',{cache:'no-store'});const j=await r.json();
  if(r.status===404){host.textContent='Brak bieżącej lub ostatniej partii pobierania.';return;}
  if(!r.ok)throw Error(j.error||r.status);
  host.replaceChildren();
  const labels={queued:'W kolejce',downloading:'Pobieranie',completed:'Zakończono · pliki zweryfikowane',failed:'Błąd pobierania',cancelled:'Anulowano',interrupted:'Przerwane po restarcie'};
  const heading=document.createElement('strong');heading.textContent=(labels[j.status]||j.status)+' · '+(j.completed_files||0)+'/'+(j.total_files||0)+' plików';host.appendChild(heading);
  const downloaded=Number(j.downloaded_bytes)||0,total=Number(j.total_bytes)||0,percent=Number(j.progress_percent)||0;
  const progress=document.createElement('progress');progress.max=100;progress.value=percent;progress.style.cssText='display:block;width:100%;height:18px;margin:10px 0;accent-color:#35d2c0';progress.setAttribute('aria-label','Postęp pobierania '+percent+' procent');host.appendChild(progress);
  const summary=document.createElement('p');summary.className='notice';summary.textContent=formatDownloadBytes(downloaded)+' / '+formatDownloadBytes(total)+' · '+percent.toFixed(1)+'%';host.appendChild(summary);
  const current=document.createElement('p');current.className='notice';current.textContent='Aktualny plik: '+(j.current_file||'—');
  if(['queued','downloading','interrupted'].includes(j.status))current.textContent+=' · '+formatDownloadBytes(j.current_file_bytes)+' / '+formatDownloadBytes(j.current_file_total_bytes);
  host.appendChild(current);
  const message=document.createElement('p');message.className='notice';message.textContent=j.message||'';host.appendChild(message);
  if(Array.isArray(j.results)&&j.results.length){
   const list=document.createElement('ul');j.results.forEach(item=>{const li=document.createElement('li');li.textContent=(item.status==='verified_quarantined'?'SHA-256 OK · ':'')+item.name+' · '+formatDownloadBytes(item.size_bytes);list.appendChild(li)});host.appendChild(list);
  }
  if(j.status==='interrupted'){
   const resume=document.createElement('button');resume.type='button';resume.textContent='Wznów bezpiecznie';resume.onclick=async()=>{resume.disabled=true;try{const rr=await fetch('/api/hf-candidates/download-resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:j.id,confirm_resume:true})});const rd=await rr.json();if(!rr.ok)throw Error(rd.error||rr.status);message.textContent='Wznowiono zadanie.';refreshHfDownloadStatus();}catch(e){message.textContent='Nie udało się wznowić: '+e;resume.disabled=false;}};host.appendChild(resume);
  }
  if(j.active){
   const cancel=document.createElement('button');cancel.type='button';cancel.className='secondary';cancel.textContent='Anuluj pobieranie';cancel.onclick=async()=>{cancel.disabled=true;try{const cr=await fetch('/api/hf-candidates/download-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:j.id})});const cd=await cr.json();if(!cr.ok)throw Error(cd.error||cr.status);message.textContent='Wysłano żądanie anulowania…';}catch(e){message.textContent='Nie udało się anulować: '+e;cancel.disabled=false;}};host.appendChild(cancel);
  }
 }catch(e){host.textContent='Nie udało się odczytać stanu pobierania: '+e;}
}
function watchHfDownloadJob(id,host){
 const status=$('hf-download-status');if(status)status.scrollIntoView({behavior:'smooth',block:'center'});
 refreshHfDownloadStatus();
}
async function loadHfDownloadPlan(){
 const host=$('hf-download-plan-body');if(!host)return;
 try{const r=await fetch('/api/hf-candidates/download-plan',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);
  const summary=d.summary||{};$('hf-download-plan-summary').textContent=`Katalog: ${summary.catalog_candidates??'—'} · jawna zgoda: ${summary.approved_artifacts??0} · plan: ${summary.planned??0} · gotowe: ${summary.verified_quarantined??0} · blokady: ${summary.blocked??0}`;
  host.replaceChildren();(d.items||[]).forEach(item=>{const card=document.createElement('div');card.style.cssText='padding:8px;margin:6px 0;border:1px solid #34445c;border-radius:8px';const title=document.createElement('strong');title.textContent=(item.status==='planned'?'Zaplanowany':item.status==='verified_quarantined'?'Już pobrany · SHA-256 OK':item.status==='downloading'?'Pobieranie':'Zablokowany')+' · '+item.filename;card.appendChild(title);const meta=document.createElement('div');meta.className='notice';meta.textContent=item.repo_id+' · rewizja '+String(item.revision||'').slice(0,12)+' · '+(item.size_bytes?formatDownloadBytes(item.size_bytes):'rozmiar nieznany');card.appendChild(meta);const why=document.createElement('p');why.className='notice';why.textContent=item.reason||'';card.appendChild(why);if(item.status==='planned'){const start=document.createElement('button');start.type='button';start.textContent='Pobierz do kwarantanny';start.onclick=async()=>{if(!confirm('Rozpocząć pobieranie '+item.filename+' ('+formatDownloadBytes(item.size_bytes)+') do kwarantanny?'))return;start.disabled=true;try{const payload={candidates:[{catalog_id:item.catalog_id,files:[{name:item.filename,sha256:item.sha256,revision:item.revision}]}]};const rr=await fetch('/api/hf-candidates/download-start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,confirm_download:true})});const job=await rr.json();if(!rr.ok)throw Error(job.error||rr.status);refreshHfDownloadStatus();loadHfDownloadHistory();}catch(e){why.textContent='Nie rozpoczęto pobierania: '+e;start.disabled=false;}};card.appendChild(start);}host.appendChild(card)});
  if(!(d.items||[]).length)host.textContent='Brak zaplanowanych artefaktów.';
 }catch(e){host.textContent='Nie udało się odczytać planu: '+e;}
}
async function loadHfDownloadHistory(){
 const host=$('hf-download-history');if(!host)return;
 try{const r=await fetch('/api/hf-candidates/download-history',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);host.replaceChildren();const jobs=d.jobs||[];if(!jobs.length){host.textContent='Historia jest pusta.';return;}jobs.forEach(job=>{const row=document.createElement('div');row.style.cssText='padding:7px;border-top:1px solid #34445c';const title=document.createElement('strong');title.textContent=(job.status||'')+' · '+(job.created_at||'')+' · '+(job.completed_files||0)+'/'+(job.total_files||0)+' plików';row.appendChild(title);const detail=document.createElement('div');detail.className='notice';detail.textContent=(job.tasks||[]).map(t=>t.repo_id+'/'+t.file.name).join(' · ')+' · '+formatDownloadBytes(job.downloaded_bytes||0)+' / '+formatDownloadBytes(job.total_bytes||0);row.appendChild(detail);if(job.message){const msg=document.createElement('div');msg.className='notice';msg.textContent=job.message;row.appendChild(msg);}host.appendChild(row)});}catch(e){host.textContent='Nie udało się odczytać historii: '+e;}
}
$('hf-download-plan-refresh')?.addEventListener('click',async()=>{const b=$('hf-download-plan-refresh');b.disabled=true;$('hf-download-plan-summary').textContent='Analizuję zatwierdzone artefakty…';try{const r=await fetch('/api/hf-candidates/download-plan/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);await loadHfDownloadPlan();}catch(e){$('hf-download-plan-summary').textContent='Nie udało się przeliczyć: '+e;}finally{b.disabled=false;}});
setInterval(refreshHfDownloadStatus,2500);
setInterval(loadHfDownloadHistory,15000);setInterval(loadHfDownloadPlan,15000);
refreshHfDownloadStatus();loadHfDownloadPlan();loadHfDownloadHistory();
loadHfCandidates();

async function refreshHfQuarantineDashboard(){
 const set=(id,value)=>{const el=$(id);if(el)el.textContent=value;};
 const queue=$('hf-dash-queue'),history=$('hf-dash-history');
 try{
  const [cr,hr,pr,qr,br,rr]=await Promise.all([fetch('/api/hf-candidates',{cache:'no-store'}),fetch('/api/hf-candidates/download-history',{cache:'no-store'}),fetch('/api/hf-candidates/download-plan',{cache:'no-store'}),fetch('/api/hf-candidates/audit-queue',{cache:'no-store'}),fetch('/api/hf-candidates/hardware',{cache:'no-store'}),fetch('/api/hf-candidates/runtime-preflight',{cache:'no-store'})]);
  const candidates=await cr.json(),historyData=await hr.json(),plan=await pr.json(),auditQueue=await qr.json(),hardware=await br.json(),runtimePreflight=await rr.json();
  for(const [response,data] of [[cr,candidates],[hr,historyData],[pr,plan],[qr,auditQueue],[br,hardware],[rr,runtimePreflight]])if(!response.ok)throw Error(data.error||response.status);
  const rows=Array.isArray(candidates)?candidates:[],jobs=Array.isArray(historyData.jobs)?historyData.jobs:[],queueItems=Array.isArray(auditQueue.items)?auditQueue.items:[];
  const verified=new Set();const artifactIdentity=(repo,revision,filename)=>[repo||'',revision||'',filename||''].join('/');jobs.forEach(job=>(job.results||[]).forEach(result=>{if(result.status==='verified_quarantined')verified.add(artifactIdentity(result.repo_id,result.revision,result.name));}));(plan.items||[]).forEach(item=>{if(item.status==='verified_quarantined')verified.add(artifactIdentity(item.repo_id,item.revision,item.filename));});
  const audited=rows.filter(x=>x.last_audit).length;set('hf-dash-candidates',String(rows.length));set('hf-dash-audited',String(audited));set('hf-dash-quarantined',String(verified.size));set('hf-dash-running',String(auditQueue.running||0));
  set('hf-shortcut-count',rows.length+' kandydatów · '+audited+' audytów · '+verified.size+' plików · '+(auditQueue.running||0)+' audytów aktywnych');
  const current=queueItems.find(x=>x.status==='running');set('hf-dash-current',current?current.display_name+' · '+current.repo_id+' · audyt metadanych w toku':'Brak aktywnego audytu. '+(auditQueue.counts?.queued||0)+' oczekuje w kolejce.');
  set('hf-dash-progress',current?'Audyt metadanych: 1 zadanie wykonywane sekwencyjnie. Pobieranie wag i test runtime: wyłączone.':'Brak aktywnego audytu. Pobieranie wag i test runtime: wyłączone.');
  const fmt=v=>v==null?'niedostępne':v+' GiB';
  set('hf-dash-hardware','RAM: '+fmt(hardware.ram?.available_gib)+' dostępne / '+fmt(hardware.ram?.total_gib)+' łącznie. Swap: '+fmt(hardware.swap?.free_gib)+' wolne / '+fmt(hardware.swap?.total_gib)+' łącznie. VRAM: '+(hardware.vram?.status==='not_available'?'brak wykrytego urządzenia':hardware.vram?.status||'nieznane')+'. Dysk kwarantanny: '+fmt(hardware.disk?.free_gib)+' wolne / '+fmt(hardware.disk?.total_gib)+' łącznie.'+(hardware.disk?.error?' Błąd: '+hardware.disk.error:''));
  if(queue){queue.replaceChildren();if(!queueItems.length)queue.textContent='Kolejka jest pusta.';queueItems.slice(0,40).forEach(item=>{const row=document.createElement('div');row.className='hf-queue-item';const title=document.createElement('strong');title.textContent=item.status.toUpperCase()+' · '+item.display_name;row.appendChild(title);const meta=document.createElement('small');meta.textContent=item.repo_id+' · rewizja '+String(item.revision).slice(0,12)+' · dodano '+item.queued_at+(item.error?' · błąd: '+item.error:'')+(item.started_at?' · start '+item.started_at:'')+(item.finished_at?' · koniec '+item.finished_at:'');row.appendChild(meta);queue.appendChild(row);});}
  const runtimeHost=$('hf-dash-runtime-queue');if(runtimeHost){runtimeHost.replaceChildren();const items=runtimePreflight.items||[];if(!items.length)runtimeHost.textContent='Brak zweryfikowanych plików w bazie kwarantanny.';items.forEach(item=>{const row=document.createElement('div');row.className='hf-queue-item';const title=document.createElement('strong');title.textContent=(item.status==='ready_pending_approval'?'PREFLIGHT OK · GOTOWY DO ZATWIERDZENIA (NIE TEST MODELU)':'POMINIĘTY / ZABLOKOWANY')+' · '+item.filename;row.appendChild(title);const meta=document.createElement('small');meta.textContent=item.repo_id+' · '+(Number(item.size_bytes)/1024**3).toFixed(2)+' GiB · szacowane RAM '+(Number(item.estimated_ram_bytes)/1024**3).toFixed(2)+' GiB · dostępne RAM '+(item.available_ram_bytes==null?'nieznane':(Number(item.available_ram_bytes)/1024**3).toFixed(2)+' GiB');row.appendChild(meta);const why=document.createElement('small');why.textContent=item.reason;row.appendChild(why);runtimeHost.appendChild(row);});}
  if(history){history.replaceChildren();const audits=rows.filter(x=>x.last_audit).map(x=>({kind:'Audyt metadanych',name:x.repo_id,at:x.last_audit.audited_at||'data nieznana',result:x.last_audit.status||'raport zapisany'}));const downloads=jobs.map(x=>({kind:'Pobieranie',name:(x.tasks||[]).map(t=>(t.repo_id||'')+'/'+(t.file?.name||t.name||'plik')).join(', '),at:x.created_at||'',result:(x.status||'')+' · '+(x.completed_files||0)+'/'+(x.total_files||0)+' plików'}));const queueHistory=queueItems.filter(x=>['failed','completed'].includes(x.status)).map(x=>({kind:'Audyt '+x.status,name:x.repo_id,at:x.finished_at||x.queued_at,result:x.error||'raport zapisany'}));const items=[...audits,...downloads,...queueHistory].sort((a,b)=>String(b.at).localeCompare(String(a.at)));if(!items.length)history.textContent='Brak zapisanej historii.';items.slice(0,30).forEach(item=>{const row=document.createElement('div');row.className='hf-queue-item';const title=document.createElement('strong');title.textContent=item.kind+' · '+item.name;row.appendChild(title);const meta=document.createElement('small');meta.textContent=item.at+' · '+item.result;row.appendChild(meta);history.appendChild(row);});}
 }catch(error){set('hf-shortcut-count','Nie udało się odczytać stanu');if(queue)queue.textContent='Nie udało się odczytać kolejki: '+error;if(history)history.textContent='Nie udało się odczytać historii: '+error;}
}
$('hf-dashboard-refresh')?.addEventListener('click',refreshHfQuarantineDashboard);
setInterval(refreshHfQuarantineDashboard,15000);refreshHfQuarantineDashboard();

function renderModelAdmission(items){
 const host=$('model-admission-list');host.textContent='';
 if(!Array.isArray(items)||!items.length){host.textContent='Brak wpisów na jawnej allowliście.';return}
 items.forEach(item=>{
  const card=document.createElement('div');card.id='model-admission-'+item.model_id;card.dataset.modelId=String(item.model_id);card.tabIndex=-1;card.style.cssText='padding:10px;margin:8px 0;border:1px solid #34445c;border-radius:9px;scroll-margin:24px';
  const title=document.createElement('strong');title.textContent=`${item.model_id}. ${item.display_name} · ${item.repo_id}`;card.appendChild(title);
  const meta=document.createElement('div');meta.textContent=`Allowlista: ${item.status} · techniczna: ${item.technical_readiness} · zakres: ${(item.admission_scope||[]).join(', ')||'brak'}`;card.appendChild(meta);
  const decision=document.createElement('div');decision.textContent=item.decision?`Decyzja lokalna: ${item.decision.status} · ${item.decision.actor} · ${item.decision.decided_at}${item.decision.reason?' · '+item.decision.reason:''}`:'Decyzja lokalna: brak';card.appendChild(decision);
  if(item.blockers?.length){const why=document.createElement('div');why.style.cssText='color:#ffb6a8;font-size:12px;margin-top:6px';why.textContent='Zablokowane: '+item.blockers.join(' ');card.appendChild(why)}
  if(![6,9,10].includes(Number(item.model_id))){const reviewButton=document.createElement('button');reviewButton.type='button';reviewButton.className='secondary';reviewButton.textContent='Uzupełnij blokady / przygotuj propozycję';reviewButton.style.marginTop='8px';reviewButton.onclick=()=>{const form=$('model-review-form');form.hidden=false;$('review-model-id').value=String(item.model_id);$('model-review-status').textContent='Propozycja dotyczy dokładnej rewizji '+item.revision+'. Zapis nie zmieni allowlisty ani nie dopuści modelu.';$('review-block-reason').value=item.block_reason||'';$('review-technical-evidence').value='';$('review-technical-blockers').value=(item.technical_blockers||[]).join('\n');form.scrollIntoView({behavior:'smooth',block:'nearest'})};card.appendChild(reviewButton)}else if(Number(item.model_id)===6){const note=document.createElement('div');note.style.cssText='font-size:12px;color:#ffb6a8;margin-top:8px';note.textContent='Brak akcji dopuszczenia: warunki Bielik v3 zawierają wymóg wieku 18+.';card.appendChild(note)}
  const history=document.createElement('div');history.style.cssText='font-size:12px;color:#aebbd0;margin-top:6px';history.textContent=item.history?.length?'Historia: '+item.history.map(h=>`${h.event} · ${h.actor} · ${h.occurred_at}${h.reason?' · '+h.reason:''}`).join(' | '):'Historia decyzji: brak';card.appendChild(history);
  if(item.can_admit){const button=document.createElement('button');button.textContent='Dopuść model';button.style.marginTop='8px';button.onclick=async()=>{if(!confirm(`Dopuścić ${item.repo_id} do lokalnego rejestru? Nie oznacza to dopuszczenia do turniejów.`))return;button.disabled=true;try{const r=await fetch('/api/model-admission/admit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:item.model_id,confirm:true})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Zapisano lokalną decyzję dopuszczenia. Odświeżam rejestr…';await loadLeague()}catch(e){$('league-message').textContent='Nie zapisano decyzji: '+e;button.disabled=false}};card.appendChild(button)}
  if(item.can_revoke){const button=document.createElement('button');button.className='stop';button.textContent='Wycofaj dopuszczenie';button.style.margin='8px 0 0 8px';button.onclick=async()=>{const reason=prompt('Podaj uzasadnienie wycofania:');if(!reason||reason.trim().length<3)return;if(!confirm(`Wycofać lokalną decyzję dla ${item.repo_id}?`))return;button.disabled=true;try{const r=await fetch('/api/model-admission/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:item.model_id,confirm:true,reason})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Wycofano lokalną decyzję. Odświeżam rejestr…';await loadLeague()}catch(e){$('league-message').textContent='Nie wycofano decyzji: '+e;button.disabled=false}};card.appendChild(button)}
  host.appendChild(card)
 });
}
async function loadLeague(){try{const urls=['/api/league','/api/league/engine','/api/models','/api/tournaments','/api/storage','/api/model-admission'];const results=await Promise.allSettled(urls.map(async u=>{const r=await fetch(u,{cache:'no-store'});if(!r.ok)throw Error(u+' HTTP '+r.status);return r.json()}));const [pureResult,engineResult,modelsResult,tournamentsResult,storageResult,admissionResult]=results;const pure=pureResult.status==='fulfilled'?pureResult.value:[];const engine=engineResult.status==='fulfilled'?engineResult.value:[];const models=modelsResult.status==='fulfilled'?modelsResult.value:[];const tournaments=tournamentsResult.status==='fulfilled'&&Array.isArray(tournamentsResult.value)?tournamentsResult.value:[];const storage=storageResult.status==='fulfilled'?storageResult.value:null;if(admissionResult.status==='fulfilled')renderModelAdmission(admissionResult.value);else $('league-message').textContent='Nie udało się odświeżyć rejestru dopuszczeń: '+admissionResult.reason;renderTournaments(tournaments);if(tournamentsResult.status!=='fulfilled')$('tournament-list').textContent='Nie udało się odczytać turniejów: '+tournamentsResult.reason;renderModelStatus(models,storage);if(pureResult.status!=='fulfilled'||engineResult.status!=='fulfilled')$('league-message').textContent='Nie udało się odczytać części rankingu: '+[pureResult,engineResult].filter(r=>r.status!=='fulfilled').map(r=>String(r.reason)).join('; ');tableRows('pure-ranking',pure.map(x=>[x.name,x.elo,x.wins,x.draws,x.losses,x.games]),['Model','Elo','W','D','L','G']);tableRows('engine-ranking',engine.map(x=>[x.name,x.elo,x.wins,x.draws,x.losses,x.games]),['Model','Elo','W','D','L','G']);tableRows('model-list',models.map(x=>[x.name,((x.inference?.thinking?'thinking ON':'thinking OFF')+' · T '+(x.inference?.temperature??0)),((x.token_budget?.current_max_tokens??x.inference?.max_tokens??16)+' tok'+(x.token_budget?.pending_max_tokens!=null?' → '+x.token_budget.pending_max_tokens+' w następnym':'')+(x.token_budget?.last_reason?' · '+x.token_budget.last_reason:'')), x.format,'v'+(x.version_id||'—'),(x.filename||'brak manifestu')+(x.draft_filename?' + '+x.draft_filename:''),x.license_verified?(x.filename&&x.sha256&&x.size_bytes&&(!x.draft_filename||(x.draft_sha256&&x.draft_size_bytes))?'gotowy · pobierany na żądanie':'brak manifestu'):'oczekuje na licencję',x.license_verified?'licencja OK':'licencja niezatwierdzona']),['Zawodnik','Thinking / temperatura','Limit tokenów','Format','Wersja ID','Plik źródłowy','Dostępność','Licencja']);if(modelsResult.status!=='fulfilled')$('model-list').textContent='Nie udało się odczytać modeli: '+modelsResult.reason;models.filter(x=>!x.license_verified&&x.version_id&&x.source).forEach(x=>{const evidenceButton=document.createElement('button');evidenceButton.textContent='Pobierz dowody licencji: '+x.name;evidenceButton.style.margin='5px 5px 0 0';evidenceButton.onclick=async()=>{evidenceButton.disabled=true;try{const r=await fetch('/api/models/license-evidence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:x.version_id})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);const ev=d.evidence;const files=(ev.license_files||[]).join(' | ');$('league-message').textContent='Dowody z Hugging Face: licencja='+(ev.license||'brak deklaracji')+'; pliki licencji='+(files||'nie znaleziono')+'; model bazowy='+(ev.base_model||'brak')+'. Zapisano dowody i czas pobrania. To nie zatwierdza licencji — otwórz plik i sprawdź warunki ręcznie.';await loadLeague()}catch(e){$('league-message').textContent='Nie udało się pobrać dowodów licencji: '+e}finally{evidenceButton.disabled=false}};$('model-list').appendChild(evidenceButton);if((!x.license||x.license.toLowerCase()==='nieustalona')&&!(x.license_evidence&&(x.license_evidence.license_files||[]).length))return;const b=document.createElement('button');b.textContent='Potwierdź sprawdzenie licencji: '+x.name;b.style.margin='5px 5px 0 0';b.onclick=async()=>{const reviewedLicense=x.license||prompt('Otwórz plik licencji w repozytorium, sprawdź warunki i wpisz dokładny identyfikator (np. Apache-2.0):');if(!reviewedLicense)return;if(!confirm('Czy otworzyłeś dokument licencji i samodzielnie sprawdziłeś, że pozwala na użycie modelu w tym projekcie?'))return;try{const r=await fetch('/api/models/verify-license',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:x.version_id,confirm_review:true,license:reviewedLicense})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Licencja oznaczona jako sprawdzona. Model może być uwzględniony w nowym turnieju.';await loadLeague()}catch(e){$('league-message').textContent='Nie udało się zatwierdzić licencji: '+e}};$('model-list').appendChild(b)});if(!selectedTournamentId||!tournaments.some(t=>Number(t.id)===selectedTournamentId)){const preferred=tournaments.find(t=>t.status==='running')||tournaments[0];if(preferred)selectedTournamentId=Number(preferred.id)}if(selectedTournamentId)await loadTournamentView(selectedTournamentId);else renderTournamentRosterCounts(null);if(storage){$('storage-info').textContent='Wolne miejsce: '+(storage.free_bytes/1073741824).toFixed(1)+' GiB. Magazyn areny: '+storage.managed_models.length+' plików; wolne '+(storage.managed_free_bytes/1073741824).toFixed(1)+' GiB. Współdzielone modele nie są usuwane.';const host=$('managed-models');host.textContent='';const owned=models.filter(x=>(x.managed_copy&&x.filename)||(x.draft_managed_copy&&x.draft_filename));if(!owned.length)host.textContent='Brak kopii pobranych przez arenę.';owned.forEach(x=>{const row=document.createElement('div');row.style.margin='8px 0';const label=document.createElement('span');label.textContent=`${x.name} · wersja ${x.version_id} · ${x.filename||'brak target'}${x.draft_filename?' + '+x.draft_filename:''} `;const btn=document.createElement('button');btn.className='stop';btn.textContent='Usuń kopię areny';btn.onclick=async()=>{if(!confirm('Usunąć wyłącznie tę kopię pobraną przez arenę? Metadane i wyniki pozostaną.'))return;try{const r=await fetch('/api/models/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version_id:x.version_id,confirm_delete:true})});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Usunięto kopię areny; metadane zachowane.';await loadLeague()}catch(e){$('league-message').textContent='Nie usunięto pliku: '+e}};row.append(label,btn);host.appendChild(row)})}else{$('storage-info').textContent='Nie udało się odczytać stanu magazynu: '+storageResult.reason;$('managed-models').textContent='Stan magazynu niedostępny.'}}catch(e){renderTournamentRosterCounts(null);$('league-message').textContent='Błąd odczytu ligi: '+e}}

async function addModelVariant(m,f,draft=null,thinking=false){
 const label=(draft?'target + MTP draft':'target solo')+' · thinking '+(thinking?'ON':'OFF');
 const modelName=m.name+' ['+f.filename.split('/').pop()+(draft?' + '+draft.filename.split('/').pop():'')+' · thinking '+(thinking?'ON':'OFF')+']';
 const metadata={provider:'Hugging Face',repo_id:m.repo_id,filename:f.filename.split('/').pop(),source_path:f.filename,download_url:'https://huggingface.co/'+m.repo_id+'/resolve/main/'+f.filename.split('/').map(encodeURIComponent).join('/'),size_bytes:f.size_bytes,sha256:f.sha256,inference:{thinking,temperature:0,max_tokens:thinking?512:16}};
 if(draft)metadata.draft_model={filename:draft.filename.split('/').pop(),source_path:draft.filename,download_url:'https://huggingface.co/'+m.repo_id+'/resolve/main/'+draft.filename.split('/').map(encodeURIComponent).join('/'),size_bytes:draft.size_bytes,sha256:draft.sha256};
 const payload={name:modelName,format:'PURE_AI',source:m.source,license:m.license,license_verified:false,metadata};
 const rr=await fetch('/api/models/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 const d=await rr.json();if(!rr.ok)throw Error(d.error||rr.status);
 const v=(d.versions||[])[0];
 $('league-message').textContent='Dodano konfigurację '+label+' jako osobnego zawodnika: '+modelName+'. Wersja ID: '+(v?.id||'—')+'. Sprawdź kartę i licencję, a następnie zatwierdź ją na liście modeli. Pliki zostaną pobrane dopiero przed partią.';
 await loadLeague();
}
function renderModelSearchResults(data){const host=$('model-search-results');host.textContent='';if(!data.length){host.textContent='Brak wyników z plikami GGUF.';return}data.forEach(m=>{const card=document.createElement('div');card.style.cssText='padding:10px;margin:8px 0;border:1px solid #34445c;border-radius:9px';const title=document.createElement('strong');title.textContent=m.name;const info=document.createElement('div');info.textContent=`Licencja: ${m.license} · pobrania: ${Number(m.downloads||0).toLocaleString()} · polubienia: ${m.likes||0}`;const link=document.createElement('a');link.href=m.source;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Otwórz kartę modelu';link.style.cssText='display:inline-block;color:#bcd7ff;margin:6px 10px 6px 0';card.append(title,info,link);m.files.forEach(f=>{const line=document.createElement('div');line.style.cssText='font-size:12px;color:#c1ccdc;margin-top:8px';const size=f.size_bytes?`${(f.size_bytes/1073741824).toFixed(2)} GiB`:'rozmiar niepodany';line.textContent=`Target: ${f.filename} · ${size} · SHA-256 ${f.sha256?'dostępny':'brak'}`;const solo=document.createElement('button');solo.textContent='Dodaj target solo · thinking OFF';solo.disabled=!f.sha256||!f.size_bytes;solo.onclick=async()=>{if(!confirm(`Dodać ${f.filename} jako osobnego zawodnika bez draft? Plik nie zostanie pobrany teraz.`))return;solo.disabled=true;try{await addModelVariant(m,f,null)}catch(e){$('league-message').textContent='Nie udało się dodać target solo: '+e;solo.disabled=false}};line.appendChild(solo);const soloThinking=document.createElement('button');soloThinking.textContent='Dodaj target solo · thinking ON';soloThinking.style.marginLeft='8px';soloThinking.disabled=solo.disabled;soloThinking.onclick=async()=>{if(!confirm('Dodać tę samą konfigurację GGUF jako osobnego zawodnika z thinking ON?'))return;soloThinking.disabled=true;try{await addModelVariant(m,f,null,true)}catch(e){$('league-message').textContent='Nie udało się dodać target solo thinking ON: '+e;soloThinking.disabled=false}};line.appendChild(soloThinking);const drafts=f.draft_models||(f.draft_model?[f.draft_model]:[]);drafts.forEach(draft=>{const detail=document.createElement('div');detail.textContent=`MTP draft: ${draft.filename} · ${(draft.size_bytes/1073741824).toFixed(2)} GiB · SHA-256 ${draft.sha256?'dostępny':'brak'}`;line.appendChild(detail);const pair=document.createElement('button');pair.textContent='Dodaj target + draft · thinking OFF';pair.disabled=!draft.sha256||!draft.size_bytes||solo.disabled;pair.style.marginLeft='8px';pair.onclick=async()=>{if(!confirm(`Dodać konfigurację target + ${draft.filename} jako osobnego zawodnika? Oba pliki zostaną pobrane dopiero przed partią.`))return;pair.disabled=true;try{await addModelVariant(m,f,draft)}catch(e){$('league-message').textContent='Nie udało się dodać pary target + draft: '+e;pair.disabled=false}};line.appendChild(pair);const pairThinking=document.createElement('button');pairThinking.textContent='Dodaj target + draft · thinking ON';pairThinking.style.marginLeft='8px';pairThinking.disabled=pair.disabled;pairThinking.onclick=async()=>{if(!confirm('Dodać tę samą parę target + draft jako osobnego zawodnika z thinking ON?'))return;pairThinking.disabled=true;try{await addModelVariant(m,f,draft,true)}catch(e){$('league-message').textContent='Nie udało się dodać pary thinking ON: '+e;pairThinking.disabled=false}};line.appendChild(pairThinking)});card.appendChild(line)});host.appendChild(card)})}
async function findModels(random=false){const host=$('model-search-results');const q=$('model-search-query').value.trim();if(!random&&q.length<2){$('league-message').textContent='Wpisz co najmniej 2 znaki.';return}host.textContent=random?'Losowanie modelu z Hugging Face…':'Wyszukiwanie w Hugging Face…';$('model-search-button').disabled=true;$('model-random-button').disabled=true;try{const url=random?'/api/models/random':'/api/models/search?q='+encodeURIComponent(q);const r=await fetch(url,{cache:'no-store'});const data=await r.json();if(!r.ok||data.error)throw Error(data.error||r.status);renderModelSearchResults(data);if(random&&data.length)$('league-message').textContent='Wylosowano propozycję modelu. Sprawdź kartę i licencję przed dodaniem oraz pobraniem.'}catch(e){host.textContent='Błąd wyszukiwania: '+e}finally{$('model-search-button').disabled=false;$('model-random-button').disabled=false}}
$('model-thinking').onchange=()=>{$('model-max-tokens').value=$('model-thinking').value==='on'?'512':'16'};
$('model-search-button').onclick=()=>findModels(false);
$('model-random-button').onclick=()=>findModels(true);
$('model-search-query').addEventListener('keydown',e=>{if(e.key==='Enter')$('model-search-button').click()});

$('model-form').onsubmit=async e=>{e.preventDefault();const payload={name:$('model-name').value.trim()+' [thinking '+$('model-thinking').value.toUpperCase()+']',format:$('model-format').value,source:$('model-source').value.trim(),license:$('model-license').value.trim(),filename:$('model-file').value.trim()||null,license_verified:$('license-confirm').checked,metadata:{inference:{thinking:$('model-thinking').value==='on',temperature:Number($('model-temperature').value),max_tokens:Number($('model-max-tokens').value)}}};if(payload.license_verified&&(!payload.source||!payload.license)){$('league-message').textContent='Do potwierdzenia licencji podaj źródło i nazwę licencji.';return}try{const r=await fetch('/api/models/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Dodano zawodnika: '+d.name;$('model-form').reset();await loadLeague()}catch(err){$('league-message').textContent='Nie udało się dodać modelu: '+err}};
$('download-form').onsubmit=async e=>{e.preventDefault();if(!$('download-license').checked){$('league-message').textContent='Potwierdź licencję i źródło.';return}const payload={version_id:Number($('download-version').value),url:$('download-url').value.trim(),filename:$('download-file').value.trim(),sha256:$('download-sha').value.trim(),size_bytes:Number($('download-size').value),license_verified:true,confirm_download:true};if(!confirm('Pobrać wskazany model do prywatnego magazynu areny?'))return;$('league-message').textContent='Pobieranie w toku…';try{const r=await fetch('/api/models/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Pobrano i zweryfikowano '+d.filename+' · SHA-256 '+d.sha256;$('download-form').reset();await loadLeague()}catch(err){$('league-message').textContent='Pobieranie nie powiodło się: '+err}};
$('tournament-form').onsubmit=async e=>{e.preventDefault();const payload={name:$('tournament-name').value.trim(),format:$('tournament-format').value,qualification_percent:Number($('draft-qualification').value)};try{const r=await fetch('/api/tournaments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw Error(d.error||r.status);$('league-message').textContent='Zaplanowano '+d.name+': '+d.players+' zawodników, '+d.games_planned+' partii ligowych + '+(d.draft_games_planned||0)+' draftu (cel '+d.qualification_percent+'%, '+d.expected_qualifiers+' zawodników). Modele z zatwierdzoną licencją nie muszą mieć lokalnego pliku — Arena pobierze GGUF przed użyciem.';$('tournament-form').reset();await loadLeague()}catch(err){$('league-message').textContent='Nie udało się zaplanować turnieju: '+err}};
document.getElementById('logout').addEventListener('click',async()=>{try{await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'});}finally{window.location.replace('/login');}});
