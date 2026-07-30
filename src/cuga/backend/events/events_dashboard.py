"""The Events Dashboard — a self-contained, LIVE control-plane page served by the CUGA server (no
build step, so it can't break the pre-built Studio bundle). It fetches the real /api/events/* APIs
and renders EVERYTHING at a glance, pretty and readable:

  • a summary strip (watchers by type · native vs AP · fires)
  • every WATCHER (subscription) — agent, trigger type, backend, cadence, next/last fire, fire count,
    status, with pause / resume / delete / run-now actions
  • every RUN — time, sub-agent, mode·backend, tools invoked, the agent's answer, ms, status —
    filterable by mode / backend / status
  • an inline DRY-RUN composer (preview any utterance, zero side effects)
  • channel status

Talks only to the /api/events/* endpoints in app.py. Auto-refreshes.
Served at:  GET /api/events/dashboard
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CUGA · Event Dashboard</title>
<style>
  :root{--bg:#0d0f15;--panel:#141824;--card:#181d2b;--raise:#1e2434;--ink:#eef1f7;--dim:#8b93a7;
        --faint:#5b6478;--line:#252c3d;--acc:#5b8cff;--native:#3ad29f;--ap:#b07bff;--poll:#22c9c0;
        --push:#ff7eb6;--warn:#ffab5e;--bad:#ff6b7a;}
  *{box-sizing:border-box;margin:0;}
  body{background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,"IBM Plex Sans",Segoe UI,sans-serif;
       max-width:1240px;margin:0 auto;padding:1.6rem 1.6rem 5rem;}
  a{color:var(--acc);text-decoration:none;}
  .top{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:.8rem;margin-bottom:.4rem;}
  h1{font-size:1.5rem;letter-spacing:-.01em;display:flex;align-items:center;gap:.55rem;}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--native);box-shadow:0 0 10px var(--native);}
  .sub{color:var(--dim);font-size:.86rem;}
  .live{width:7px;height:7px;border-radius:50%;background:var(--native);display:inline-block;animation:pulse 1.6s infinite;}
  @keyframes pulse{0%,100%{opacity:.4;}50%{opacity:1;box-shadow:0 0 8px var(--native);}}
  .strip{display:flex;gap:1.6rem;flex-wrap:wrap;margin:1rem 0 1.3rem;}
  .stat b{display:block;font-size:1.5rem;font-weight:700;font-variant-numeric:tabular-nums;}
  .stat span{color:var(--dim);font-size:.74rem;text-transform:uppercase;letter-spacing:.05em;}
  .stat .n{color:var(--native);} .stat .a{color:var(--ap);}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1.05rem 1.2rem;margin-bottom:1.1rem;}
  .card h2{font-size:.82rem;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);margin-bottom:.7rem;
           display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;} .card h2 .cnt{color:var(--faint);}
  .cols{display:grid;grid-template-columns:1.5fr 1fr;gap:1.1rem;align-items:start;}
  @media(max-width:920px){.cols{grid-template-columns:1fr;}}
  .compose{display:flex;gap:.6rem;} .compose input{flex:1;background:var(--bg);border:1px solid var(--line);
           border-radius:9px;padding:.6rem .8rem;color:var(--ink);font-size:.92rem;} .compose input:focus{outline:none;border-color:var(--acc);}
  .btn{background:var(--acc);color:#fff;border:none;border-radius:9px;padding:.55rem 1rem;font-weight:600;cursor:pointer;font-size:.86rem;}
  .btn.ghost{background:transparent;border:1px solid var(--line);color:var(--dim);padding:.3rem .55rem;font-weight:500;font-size:.78rem;}
  .btn.ghost:hover{color:var(--ink);border-color:var(--acc);}
  .verdict{margin-top:.7rem;font-size:.86rem;color:var(--dim);display:flex;gap:.45rem;flex-wrap:wrap;align-items:center;min-height:1.4rem;}
  .pill{display:inline-block;border-radius:99px;padding:.08em .55em;font-size:.72rem;border:1px solid var(--line);white-space:nowrap;}
  .pill.native{color:var(--native);border-color:var(--native);}.pill.ap{color:var(--ap);border-color:var(--ap);}
  .pill.poll{color:var(--poll);border-color:var(--poll);}.pill.push{color:var(--push);border-color:var(--push);}
  .pill.cron{color:var(--native);border-color:var(--native);}.pill.now{color:var(--faint);border-color:var(--line);}
  .pill.ok{color:var(--native);border-color:var(--native);}.pill.paused{color:var(--warn);border-color:var(--warn);}
  .pill.fail{color:var(--bad);border-color:var(--bad);}.pill.arm{color:var(--native);border-color:var(--native);}
  .pill.decline{color:var(--warn);border-color:var(--warn);}
  table{width:100%;border-collapse:collapse;font-size:.85rem;}
  th{text-align:left;color:var(--faint);font-weight:600;font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;
     padding:.4em .55em;border-bottom:1px solid var(--line);}
  td{padding:.55em .55em;border-bottom:1px solid var(--line);vertical-align:middle;}
  tr:last-child td{border-bottom:none;}
  .agent{font-weight:600;} .mono{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;color:var(--dim);}
  .next{color:var(--native);font-variant-numeric:tabular-nums;white-space:nowrap;}
  .acts{display:flex;gap:.3rem;justify-content:flex-end;}
  .feed .row{display:flex;gap:.6rem;padding:.55rem 0;border-bottom:1px solid var(--line);} .feed .row:last-child{border-bottom:none;}
  .feed .tick{width:6px;height:6px;border-radius:50%;margin-top:.45rem;flex:none;background:var(--native);}
  .feed .row.ap .tick{background:var(--ap);} .feed .row.fail .tick{background:var(--bad);}
  .feed .meta{font-size:.73rem;color:var(--faint);margin-bottom:.12rem;} .feed .meta b{color:var(--ink);}
  .feed .meta .tool{color:var(--poll);} .feed .ans{color:#d4d8e2;font-size:.84rem;white-space:pre-wrap;}
  .filters{display:flex;gap:.4rem;flex-wrap:wrap;margin-left:auto;}
  .chip{background:var(--raise);border:1px solid var(--line);color:var(--dim);border-radius:99px;padding:.2rem .6rem;font-size:.75rem;cursor:pointer;}
  .chip.on{color:var(--ink);border-color:var(--acc);background:rgba(91,140,255,.12);}
  .empty{color:var(--faint);font-size:.86rem;padding:1rem .2rem;}
  .chan{display:flex;gap:.6rem;flex-wrap:wrap;} .chan .c{display:flex;align-items:center;gap:.4rem;background:var(--raise);
        border:1px solid var(--line);border-radius:8px;padding:.35rem .65rem;font-size:.8rem;}
  .chan .c .g{width:7px;height:7px;border-radius:50%;background:var(--native);} .chan .c.off .g{background:var(--faint);}
  .foot{color:var(--faint);font-size:.78rem;margin-top:1.4rem;}
</style></head>
<body>
  <div class="top">
    <div><h1><span class="dot"></span> Event Dashboard</h1>
      <div class="sub">every watcher, run &amp; channel — live · <span id="scope"></span></div></div>
    <div class="sub"><span class="live"></span> auto-refresh <span id="clock"></span>
      · <a href="/api/events/flows/console">flows console</a> · <a href="/docs">API</a></div>
  </div>

  <div class="strip" id="strip"></div>

  <div class="card">
    <h2>▷ Arm a watch <span class="cnt">— preview first (dry-run, zero side effects)</span></h2>
    <div class="compose">
      <input id="utt" placeholder="e.g. every 5 minutes give me a fun fact  ·  watch bitcoin every 2 min  ·  when a new email arrives"/>
      <button class="btn" onclick="dryrun()">Preview</button>
    </div>
    <div class="verdict" id="verdict"><span style="color:var(--faint)">type an utterance and hit Preview</span></div>
  </div>

  <div class="cols">
    <div>
      <div class="card">
        <h2>◱ Watchers <span class="cnt" id="wcount"></span></h2>
        <div id="watchers"></div>
      </div>
    </div>
    <div>
      <div class="card">
        <h2><span class="live"></span> Runs <span class="cnt" id="rcount"></span>
          <span class="filters" id="rfilters"></span></h2>
        <div class="feed" id="runs"></div>
      </div>
      <div class="card"><h2>◈ Channels</h2><div class="chan" id="chan"></div></div>
    </div>
  </div>

  <div class="foot">CUGA Events · native scheduler + Activepieces · this page reads only the
    <code>/api/events/*</code> APIs.</div>

<script>
const $=s=>document.querySelector(s);
let RUNFILTER={mode:null,backend:null,status:null};
let GW=sessionStorage.getItem('gw')||'';

async function j(u,o){try{const r=await fetch(u,o);return await r.json();}catch(e){return null;}}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function ago(iso){if(!iso)return'';const t=new Date(iso).getTime();if(!t)return'';
  const s=Math.max(0,(Date.now()-t)/1000);
  if(s<60)return Math.floor(s)+'s ago';if(s<3600)return Math.floor(s/60)+'m ago';
  if(s<86400)return Math.floor(s/3600)+'h ago';return Math.floor(s/86400)+'d ago';}
function until(ep){if(!ep)return'';const s=ep-Date.now()/1000;if(s<=0)return'due';
  if(s<60)return'in '+Math.ceil(s)+'s';if(s<3600)return'in '+Math.ceil(s/60)+'m';
  if(s<86400)return'in '+Math.ceil(s/3600)+'h';return new Date(ep*1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function mp(m){return {CRON:'cron',POLL:'poll',PUSH:'push',NOW:'now'}[m]||'now';}

function renderStrip(sum,ap){
  const m=sum.by_mode||{};
  $('#strip').innerHTML=[
    ['n',sum.total,'watchers'],['n',(m.CRON||0),'crons'],['poll',(m.POLL||0),'polls'],
    ['push',(m.PUSH||0),'pushes'],['n',sum.native_no_ap,'native (no AP)'],['a',sum.ap_flows,'AP flows'],
    ['',sum.paused,'paused']
  ].map(([c,v,l])=>`<div class="stat"><b class="${c}">${v??0}</b><span>${l}</span></div>`).join('');
}

function renderWatchers(subs){
  $('#wcount').textContent='— '+subs.length;
  if(!subs.length){$('#watchers').innerHTML='<div class="empty">No watchers armed. Try the Preview box above, then say it in chat.</div>';return;}
  const rows=subs.map(s=>{
    const nf=s.next_fire?until(s.next_fire):'';
    const cad=s.interval_seconds?(s.interval_seconds%60===0?`${s.interval_seconds/60} min`:`${s.interval_seconds}s`)
             :(s.cron_expr?`cron ${esc(s.cron_expr)}`:(s.mode==='PUSH'?'on event':'—'));
    const src=s.mode==='PUSH'?`${esc(s.source_connector||'')} · ${esc(s.event||'')}`
             :(s.source_connector==='cron'||s.source_connector==='interval'?'the clock':esc(s.source_connector||''));
    let task=s.prompt||''; const _i=task.indexOf('report:\n'); if(_i>=0)task=task.slice(_i+8);
    task=task.split('\nThis is a POLL:')[0].replace(/^["“]|["”]$/g,'').trim()||src;
    const st=s.status==='paused'?'<span class="pill paused">paused</span>':'<span class="pill ok">active</span>';
    const bk=s.backend==='native'?'<span class="pill native">native</span>':'<span class="pill ap">AP</span>';
    const fired=s.fire_count?`<span class="mono" title="fires">×${s.fire_count}</span>`:'';
    const pr=s.status==='paused'
      ?`<button class="btn ghost" onclick="act('${s.id}','resume')">resume</button>`
      :`<button class="btn ghost" onclick="act('${s.id}','pause')">pause</button>`;
    const run=s.backend==='native'?`<button class="btn ghost" onclick="fire('${s.id}')">run</button>`:'';
    return `<tr>
      <td title="${esc(s.prompt||'')}"><div>${esc(task.slice(0,80))}</div><div class="mono">${esc(s.target_agent)} · ${esc(s.id)}</div></td>
      <td>${src}</td>
      <td><span class="pill ${mp(s.mode)}">${s.mode}</span> ${bk}</td>
      <td>${cad} ${fired}</td>
      <td><span class="next">${nf||st}</span>${nf?' · '+st:''}</td>
      <td class="acts">${run}${pr}<button class="btn ghost" onclick="act('${s.id}','delete')">✕</button></td>
    </tr>`;
  }).join('');
  $('#watchers').innerHTML=`<table><tr><th>Watching for</th><th>Source</th><th>Type</th><th>Cadence</th><th>Next / status</th><th></th></tr>${rows}</table>`;
}

function renderRuns(runs){
  $('#rcount').textContent='— '+runs.length;
  const f=RUNFILTER;
  const shown=runs.filter(r=>(!f.mode||r.mode===f.mode)&&(!f.backend||r.backend===f.backend)&&(!f.status||r.status===f.status));
  if(!shown.length){$('#runs').innerHTML='<div class="empty">No runs yet. Arm a watcher and fire it (or wait for a tick).</div>';return;}
  $('#runs').innerHTML=shown.slice(0,40).map(r=>{
    const cls=r.status==='FAILED'?'fail':(r.backend==='ap'?'ap':'');
    const tools=(r.tools&&r.tools.length)?` · <span class="tool">${r.tools.map(esc).join(', ')}</span>`:'';
    const ms=r.ms?` · ${(r.ms/1000).toFixed(1)}s`:'';
    const badge=r.status==='FAILED'?'<span class="pill fail">failed</span> ':'';
    return `<div class="row ${cls}"><span class="tick"></span><div>
      <div class="meta">${badge}<b>${esc(r.agent||'')}</b> · <span class="pill ${mp(r.mode)}" style="padding:0 .4em">${r.mode}</span> ${r.backend||''}${tools}${ms} · ${ago(r.started_at)}</div>
      <div class="ans">${esc((r.answer||r.utterance||'').slice(0,320))}</div></div></div>`;
  }).join('');
}

function renderFilters(runs){
  const modes=[...new Set(runs.map(r=>r.mode))];
  const backs=[...new Set(runs.map(r=>r.backend))];
  const chip=(k,v,lbl)=>`<span class="chip ${RUNFILTER[k]===v?'on':''}" onclick="setf('${k}','${v}')">${lbl}</span>`;
  $('#rfilters').innerHTML=`<span class="chip ${!RUNFILTER.mode&&!RUNFILTER.backend&&!RUNFILTER.status?'on':''}" onclick="setf('clear')">all</span>`
    +modes.map(m=>chip('mode',m,m)).join('')+backs.map(b=>chip('backend',b,b)).join('');
}
function setf(k,v){if(k==='clear'){RUNFILTER={mode:null,backend:null,status:null};}else{RUNFILTER[k]=RUNFILTER[k]===v?null:v;}load();}

function renderChan(st){
  const chans=(st&&st.channels)||['web','telegram','discord','slack'];
  $('#chan').innerHTML=chans.map(c=>{
    const name=typeof c==='string'?c:c.name; const on=typeof c==='string'?true:(c.status!=='off');
    return `<div class="c ${on?'':'off'}"><span class="g"></span> ${esc(name)}</div>`;
  }).join('')||'<span class="empty">—</span>';
}

async function dryrun(){
  const t=$('#utt').value.trim(); if(!t){return;}
  $('#verdict').innerHTML='<span style="color:var(--faint)">previewing…</span>';
  const d=await j('/api/events/dry-run?text='+encodeURIComponent(t));
  if(!d){$('#verdict').textContent='preview failed';return;}
  const pc=d.backend==='ap'?'ap':(d.mode==='POLL'?'poll':mp(d.mode));
  const wc=(d.would||'').startsWith('decline')?'decline':'arm';
  $('#verdict').innerHTML=`<span class="pill ${pc}">${d.mode}</span><span class="pill ${pc}">${d.backend}</span>`
    +`<span class="pill ${wc}">would ${esc(d.would)}</span>`
    +`<span style="color:var(--faint)">${esc(d.cadence_human||d.routing||'')}${d.next_fire_preview?' · next '+new Date(d.next_fire_preview).toLocaleTimeString():''}</span>`;
}

async function act(id,what){
  if(what==='delete'&&!confirm('Delete watcher '+id+'?'))return;
  const m=what==='delete'?'DELETE':'POST';
  const u='/api/events/subscriptions/'+id+(what==='delete'?'':'/'+what);
  await j(u,{method:m}); load();
}
async function fire(id){
  if(!GW){GW=prompt('GATEWAY_TOKEN (from .env) — needed to fire a run:')||'';sessionStorage.setItem('gw',GW);}
  const r=await j('/api/events/subscriptions/'+id+'/run',{method:'POST',headers:GW?{'X-Gateway-Token':GW}:{}});
  if(r&&r.ok){alert('Fired '+id+':\n\n'+((r.answer||'(see Runs)').slice(0,400)));}
  else{alert('Fire failed'+(r&&r.error?': '+r.error:''));}
  load();
}

async function load(){
  const [subs,runs,st]=await Promise.all([
    j('/api/events/subscriptions'), j('/api/events/runs?limit=60'), j('/api/events/status')]);
  if(subs){$('#scope').textContent=esc(subs.scope||''); renderStrip(subs.summary||{by_mode:{}}, st); renderWatchers(subs.subscriptions||[]);}
  if(runs){renderFilters(runs.runs||[]); renderRuns(runs.runs||[]);}
  renderChan(st);
  $('#clock').textContent=new Date().toLocaleTimeString();
}
$('#utt').addEventListener('keydown',e=>{if(e.key==='Enter')dryrun();});
load(); setInterval(load,10000);
</script>
</body></html>
"""
