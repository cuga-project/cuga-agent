"""The Flows console — a self-contained HTML page served by the CUGA server (no build step, so it
can't break the pre-built Studio bundle). It lists the caller's flows (subscriptions), lets you
**pause / resume / delete** them (CUGA drives Activepieces internally — no AP console needed), and
renders a **rich, read-only view** of each flow: the CUGA Source→Agent→Sink model PLUS the live AP
flow steps (trigger → invoke → delivery). Talks only to the /api/events/* endpoints in app.py.

Served at:  GET /api/events/flows/console
"""

FLOWS_CONSOLE_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CUGA — Flows</title>
<style>
  :root{ --bg:#0f1420; --card:#171e2e; --line:#26304a; --ink:#e8edf7; --dim:#93a0bd; --accent:#5b8cff;
         --now:#8b5cf6; --cron:#0ea5e9; --poll:#14b8a6; --push:#f59e0b; --ok:#22c55e; --paused:#eab308; --bad:#ef4444; }
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
     font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:18px 26px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  header h1{font-size:19px;margin:0;font-weight:650} .sub{color:var(--dim);font-size:12.5px}
  .btn{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;
       padding:6px 11px;font-size:12.5px;cursor:pointer} .btn:hover{border-color:var(--accent)}
  .btn.p{background:#1c2740} .btn.danger:hover{border-color:var(--bad);color:#fecaca}
  .wrap{max-width:1040px;margin:0 auto;padding:22px 26px}
  .hint{background:#141b2b;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:18px;color:var(--dim)}
  .hint code{background:#0c1120;color:#cfe0ff;padding:1px 6px;border-radius:5px}
  .grid{display:grid;gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .badge{font-size:11px;font-weight:700;letter-spacing:.03em;padding:2px 8px;border-radius:999px;color:#0b0f18}
  .b-NOW{background:var(--now);color:#fff}.b-CRON{background:var(--cron)}.b-POLL{background:var(--poll)}.b-PUSH{background:var(--push)}
  .pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line)}
  .st-active{color:#bbf7d0;border-color:#14532d;background:#0c2a17}.st-paused{color:#fde68a;border-color:#713f12;background:#2a2109}
  .agent{font-weight:650} .flow{color:var(--dim);font-size:12.5px;margin-top:3px}
  .arrow{color:var(--dim);padding:0 4px} .spacer{flex:1}
  .muted{color:var(--dim)} .empty{color:var(--dim);text-align:center;padding:40px}
  /* modal */
  .mask{position:fixed;inset:0;background:rgba(3,7,18,.72);display:none;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto}
  .modal{background:var(--card);border:1px solid var(--line);border-radius:14px;max-width:760px;width:100%;padding:20px 22px}
  .pipe{display:flex;flex-direction:column;gap:0;margin:10px 0}
  .node{border:1px solid var(--line);border-radius:10px;padding:10px 13px;background:#0e1523}
  .node .k{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
  .node .v{font-weight:600;margin-top:2px} .node .d{color:var(--dim);font-size:12px;margin-top:2px}
  .conn{width:2px;height:16px;background:var(--line);margin:0 auto}
  .sec{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin:16px 0 6px}
  pre{background:#0c1120;border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;font-size:12px;color:#cfe0ff;max-height:220px}
</style></head><body>
<header>
  <h1>⚡ Flows</h1><span class="sub" id="scope"></span>
  <span class="spacer"></span>
  <button class="btn" onclick="load()">↻ Refresh</button>
</header>
<div class="wrap">
  <div class="hint">Create a flow from chat with <code>/automate &lt;what&gt;</code> — one command whose router picks
    push / cron / poll for you: <code>/automate summarize new emails</code> (push),
    <code>/automate the market brief every weekday 8am</code> (cron),
    <code>/automate check bitcoin every 5 min on a move</code> (poll) — or just ask in plain English.
    Manage them here: pause, resume, delete, or <b>view</b> the flow (CUGA drives Activepieces for you).</div>
  <div class="grid" id="list"><div class="empty">loading…</div></div>
</div>

<div class="mask" id="mask" onclick="if(event.target.id==='mask')close_()">
  <div class="modal" id="modal"></div>
</div>

<script>
const $ = s => document.querySelector(s);
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(path, method){ const r = await fetch(path, {method:method||'GET', headers:{'content-type':'application/json'}}); return r.json(); }

async function load(){
  const d = await api('/api/events/subscriptions');
  $('#scope').textContent = 'scope: ' + (d.scope||'');
  const subs = (d.subscriptions||[]).filter(s => s.mode !== 'NOW');
  const el = $('#list');
  if(!subs.length){ el.innerHTML = '<div class="empty">No standing flows yet. Try <code>/watch new emails and summarize them</code> in chat.</div>'; return; }
  el.innerHTML = subs.map(card).join('');
}
function card(s){
  const mode = s.mode||'?'; const paused = (s.status==='paused');
  const src = s.source_connector && s.source_connector!=='cron' ? s.source_connector : (mode==='CRON'?'schedule':(mode==='POLL'?'timer':'—'));
  const sink = (s.deliver_to&&s.deliver_to.length)? s.deliver_to.join(', ') : (mode==='PUSH'?'reply':'—');
  return `<div class="card">
    <div class="row">
      <span class="badge b-${mode}">${mode}</span>
      <span class="agent">${esc(s.target_agent)}</span>
      <span class="pill st-${paused?'paused':'active'}">${paused?'paused':'active'}</span>
      <span class="spacer"></span>
      <button class="btn" onclick="view('${s.id}')">View</button>
      <button class="btn p" onclick="toggle('${s.id}', ${paused})">${paused?'Resume':'Pause'}</button>
      <button class="btn danger" onclick="del('${s.id}')">Delete</button>
    </div>
    <div class="flow"><b>${esc(src)}</b> <span class="arrow">→</span> ${esc(s.target_agent)} <span class="arrow">→</span> <b>${esc(sink)}</b>
      &nbsp;·&nbsp; <span class="muted">${esc((s.prompt||'').slice(0,90))}</span></div>
    ${actionOf(s)? `<div class="flow"><span class="badge" style="background:#3b2f14;color:#f59e0b">ACTION</span> <b>${esc(actionOf(s))}</b></div>`:''}
    <div class="flow muted">flow: ${esc(s.flow_name||s.id)}${s.ap_flow_id?` &nbsp;·&nbsp; <b>flow id</b> <code style="user-select:all">${esc(s.ap_flow_id)}</code>`:' &nbsp;·&nbsp; (direct — no AP flow)'} &nbsp;·&nbsp; sub <code style="user-select:all">${esc(s.id)}</code></div>
  </div>`;
}
// The post-agent ACTION this flow runs. For a DIRECT trigger (slack/discord/telegram) it lives in
// config.action_plan (the executor, Option A) — there is no AP flow to walk, so this is the ONLY place
// it shows. For an AP-push flow the action is also visible as an AP step in the detail view.
function actionOf(s){
  const p = s && s.config && s.config.action_plan;
  if(!p) return '';
  if(p.steps && p.steps.length) return p.steps.map(x=>x.app+'/'+x.ap_action+' (executor)').join(' + ');
  if(p.branches && p.branches.length) return 'branched: '+p.branches.map(
      b=>(b.tag || (b.step&&(b.step.app+'/'+b.step.ap_action)) || '?')).join(' / ');
  return '';
}
async function toggle(id, paused){ await api('/api/events/subscriptions/'+id+'/'+(paused?'resume':'pause'),'POST'); load(); }
async function del(id){ if(!confirm('Delete this flow? This removes it from Activepieces too.')) return; await api('/api/events/subscriptions/'+id,'DELETE'); load(); }

async function view(id){
  $('#modal').innerHTML = '<div class="muted">loading flow…</div>'; $('#mask').style.display='flex';
  const d = await api('/api/events/subscriptions/'+id+'/flow');
  if(!d.ok){ $('#modal').innerHTML = '<div class="muted">flow not found</div>'; return; }
  const s = d.subscription||{}; const ap = d.ap_flow;
  const src = s.source_connector && s.source_connector!=='cron' ? s.source_connector : (s.mode==='CRON'?'schedule':(s.mode==='POLL'?'timer':'inbound'));
  const sink = (s.deliver_to&&s.deliver_to.length)? s.deliver_to.join(', ') : (s.mode==='PUSH'?'reply to source':'—');
  // CUGA model pipeline
  const act = actionOf(s);
  const cuga = [
    {k:'Trigger ('+s.mode+')', v:src, d: triggerDesc(s)},
    {k:'Agent', v:s.target_agent, d:(s.prompt||'')},
    ...(act? [{k:'Action', v:act, d:'runs after the agent — an executor flow (AP keeps the creds)'}]:[]),
    {k:'Deliver', v:sink, d:''}
  ];
  // AP flow steps (rich, from the live AP flow JSON)
  let apNodes = [];
  const ver = ap && ap.version;
  if(ver && ver.trigger){ apNodes = walkAp(ver.trigger); }
  $('#modal').innerHTML = `
    <div class="row"><span class="badge b-${s.mode}">${s.mode}</span>
      <span class="agent">${esc(s.target_agent)}</span>
      <span class="pill st-${s.status==='paused'?'paused':'active'}">${esc(s.status)}</span>
      <span class="spacer"></span><button class="btn" onclick="close_()">✕ Close</button></div>
    <div class="sec">CUGA model</div>
    <div class="pipe">${cuga.map((n,i)=>node(n)+(i<cuga.length-1?'<div class="conn"></div>':'')).join('')}</div>
    <div class="sec">Activepieces flow ${ap?'':'(offline — showing CUGA model only)'}</div>
    ${apNodes.length? '<div class="pipe">'+apNodes.map((n,i)=>node(n)+(i<apNodes.length-1?'<div class="conn"></div>':'')).join('')+'</div>'
                    : '<div class="muted">'+(ap?'no steps':'AP unreachable or flow deleted')+'</div>'}
    <div class="sec">Details</div>
    <pre>${esc(JSON.stringify({id:s.id, flow_name:s.flow_name, ap_flow_id:s.ap_flow_id, backend:s.backend, dedup_key:s.dedup_key, deliver_to:s.deliver_to}, null, 2))}</pre>`;
}
function triggerDesc(s){
  if(s.mode==='PUSH') return 'when a new item appears in '+(s.source_connector||'the source');
  if(s.mode==='CRON') return 'on a fixed schedule';
  if(s.mode==='POLL') return 'checks on an interval, acts on change';
  return '';
}
function node(n){ return `<div class="node"><div class="k">${esc(n.k)}</div><div class="v">${esc(n.v||'—')}</div>${n.d?`<div class="d">${esc(n.d)}</div>`:''}</div>`; }
function walkAp(trigger){
  const out = [];
  out.push({k:'AP trigger', v:(trigger.displayName||trigger.name||'trigger'), d:(trigger.settings&&trigger.settings.triggerName)||''});
  let step = trigger.nextAction;
  while(step){ out.push({k:'Step ('+(step.type||'').toLowerCase()+')', v:(step.displayName||step.name||'step'),
                         d:(step.settings&&(step.settings.actionName||step.settings.pieceName))||''}); step = step.nextAction; }
  return out;
}
function close_(){ $('#mask').style.display='none'; }
document.addEventListener('keydown', e=>{ if(e.key==='Escape') close_(); });
load();
</script></body></html>"""
