import React, { useState } from "react";
import {
  Button,
  InlineNotification,
  Select,
  SelectItem,
  Tag,
  TextInput,
  Tile,
  Toggle,
} from "@carbon/react";
import { ArrowDown, ArrowUp, Bot, Chat, Close, Add, Microphone, Translate, VolumeUp } from "@carbon/icons-react";

/**
 * Processing — the stages a message passes through before and after the agent, per channel.
 *
 * A stage is one thing: a POST to a service, with a declared INTERFACE. There are four shapes, and a
 * service that implements one can be dropped into any channel's list. What the service does inside —
 * which model, which language, which voice — is its own business and never appears here.
 *
 * PREVIEW: local state with the shipped defaults. Saving would write the stage list; the four
 * interfaces below are what the events layer already calls today.
 */

type Shape = "audio_to_text" | "text_to_passages" | "text_to_text" | "text_to_audio";

const SHAPES: Record<Shape, { label: string; Icon: any; request: string; response: string; blurb: string }> = {
  audio_to_text: {
    label: "audio → text",
    Icon: Microphone,
    request: 'POST <url>\nmultipart: file=<the audio bytes>',
    response: '{ "text": "…" }',
    blurb: "Turns a voice note into the question. Runs only when the message carries audio.",
  },
  text_to_passages: {
    label: "text → passages",
    Icon: Bot,
    request: 'POST <url>\n{ "query": "…the question…" }',
    response: '{ "context": "[Page 29]: …" }',
    blurb: "Looks something up. What comes back is put in front of the agent with the question.",
  },
  text_to_text: {
    label: "text → text",
    Icon: Translate,
    request: 'POST <url>\n{ "text": "…" }',
    response: '{ "text": "…" }',
    blurb: "Rewrites what passes through: translate, tidy, redact, shorten. Works before or after the agent.",
  },
  text_to_audio: {
    label: "text → audio",
    Icon: VolumeUp,
    request: 'POST <url>\n{ "text": "…the answer…", "accept": ["audio/ogg; codecs=opus"] }',
    response: "the audio bytes, with a Content-Type",
    blurb: "Speaks the answer. Runs only for someone who spoke to us.",
  },
};

type Stage = {
  id: string;
  title: string;
  shape: Shape;
  url: string;
  on: boolean;
  example: { in: string; out: string; note?: string };
};

const CATALOGUE: Stage[] = [
  {
    id: "transcribe",
    title: "Hear the voice note",
    shape: "audio_to_text",
    url: "http://speech:8300/v1/speech-to-text",
    on: true,
    example: {
      in: "voice note · audio/ogg; codecs=opus · 7 s",
      out: '"बीज की गुनवत्ता की पहचान कैसे करें?"',
      note: "the service picked the model and the language — this layer only sent bytes",
    },
  },
  {
    id: "search",
    title: "Search the handbook",
    shape: "text_to_passages",
    url: "http://handbook:8770/v1/search",
    on: true,
    example: {
      in: '"बीज की गुनवत्ता की पहचान कैसे करें?"',
      out: '"[Page 29]: Characteristics of good seed\\n- Genetically pure\\n\\n[Page 29]: Seed types…"  (2,011 chars)',
      note: "the agent is then asked:  Context: <passages>  Question: <the question>",
    },
  },
  {
    id: "translate_in",
    title: "Translate the question",
    shape: "text_to_text",
    url: "http://translate:8400/v1/text",
    on: false,
    example: { in: '"बीज की गुणवत्ता की पहचान कैसे करें?"', out: '"How do I identify seed quality?"', note: "for an index that only works well in one language" },
  },
  {
    id: "redact",
    title: "Redact before sending",
    shape: "text_to_text",
    url: "http://redact:8500/v1/text",
    on: false,
    example: { in: '"…call the officer on 98765 43210."', out: '"…call the officer on ●●●●● ●●●●●."', note: "an after-stage: the answer is rewritten before it leaves" },
  },
  {
    id: "speak",
    title: "Speak the answer",
    shape: "text_to_audio",
    url: "http://speech:8300/v1/text-to-speech",
    on: true,
    example: {
      in: '"- **जीनिक शुद्धता** – नाभिकीय बीज 100 % शुद्ध… (p. 29)"',
      out: "audio/ogg; codecs=opus · 171 KB · 41 s of speech",
      note: "the service stripped the markdown and the (p. 29) before speaking",
    },
  },
];

const pick = (id: string) => CATALOGUE.find((s) => s.id === id)!;

const CHANNELS = [
  { name: "whatsapp", label: "WhatsApp", accepts: "text · voice notes" },
  { name: "telegram", label: "Telegram", accepts: "text" },
  { name: "slack", label: "Slack", accepts: "text" },
  { name: "discord", label: "Discord", accepts: "text" },
  { name: "web", label: "Web chat", accepts: "text" },
];

const DEFAULTS: Record<string, { before: string[]; after: string[] }> = {
  whatsapp: { before: ["transcribe", "search"], after: ["speak"] },
  telegram: { before: ["search"], after: [] },
  slack: { before: ["search"], after: [] },
  discord: { before: ["search"], after: [] },
  web: { before: ["search"], after: [] },
};

function StageCard({ id, idx, total, onMove, onDrop, onToggle }: {
  id: string; idx: number; total: number; onMove: (d: number) => void; onDrop: () => void; onToggle: () => void;
}) {
  const s = pick(id);
  const shape = SHAPES[s.shape];
  return (
    <div className={`stage${s.on ? "" : " stage--off"}`}>
      <div className="stage-head">
        <span className="stage-num">{idx + 1}</span>
        <span className="stage-title">{s.title}</span>
        <Tag type="purple" size="sm"><shape.Icon size={12} /> {shape.label}</Tag>
        <span className="stage-actions">
          <Toggle id={`on-${id}`} size="sm" toggled={s.on} onToggle={onToggle} labelA="" labelB="" hideLabel />
          <Button kind="ghost" size="sm" hasIconOnly iconDescription="Move up" renderIcon={ArrowUp} disabled={idx === 0} onClick={() => onMove(-1)} />
          <Button kind="ghost" size="sm" hasIconOnly iconDescription="Move down" renderIcon={ArrowDown} disabled={idx === total - 1} onClick={() => onMove(1)} />
          <Button kind="ghost" size="sm" hasIconOnly iconDescription="Remove" renderIcon={Close} onClick={onDrop} />
        </span>
      </div>
      <code className="stage-url">{s.url}</code>
      <div className="stage-example">
        <div className="stage-io"><span className="stage-io-label">in</span><code>{s.example.in}</code></div>
        <div className="stage-io"><span className="stage-io-label stage-io-label--out">out</span><code>{s.example.out}</code></div>
        {s.example.note && <div className="stage-note">{s.example.note}</div>}
      </div>
    </div>
  );
}

export function ProcessingTab() {
  const [cfg, setCfg] = useState(DEFAULTS);
  const [stages, setStages] = useState(CATALOGUE);
  const [channel, setChannel] = useState("whatsapp");
  const [adding, setAdding] = useState<"before" | "after" | null>(null);
  const [draft, setDraft] = useState({ title: "", shape: "text_to_text" as Shape, url: "" });

  const lists = cfg[channel];
  const move = (phase: "before" | "after", idx: number, d: number) => {
    const l = [...lists[phase]];
    [l[idx], l[idx + d]] = [l[idx + d], l[idx]];
    setCfg({ ...cfg, [channel]: { ...lists, [phase]: l } });
  };
  const drop = (phase: "before" | "after", idx: number) =>
    setCfg({ ...cfg, [channel]: { ...lists, [phase]: lists[phase].filter((_, i) => i !== idx) } });
  const add = (phase: "before" | "after", id: string) =>
    setCfg({ ...cfg, [channel]: { ...lists, [phase]: [...lists[phase], id] } });
  const toggle = (id: string) => setStages(stages.map((s) => (s.id === id ? { ...s, on: !s.on } : s)));

  const addCustom = (phase: "before" | "after") => {
    const id = draft.title.toLowerCase().replace(/\W+/g, "_") || `stage_${stages.length}`;
    const s: Stage = {
      id, title: draft.title || "New stage", shape: draft.shape, url: draft.url || "http://…",
      on: true, example: { in: "—", out: "—", note: "your service decides what happens between these two" },
    };
    CATALOGUE.push(s);
    setStages([...stages, s]);
    add(phase, id);
    setAdding(null);
    setDraft({ title: "", shape: "text_to_text", url: "" });
  };

  const Phase = ({ phase }: { phase: "before" | "after" }) => (
    <Tile className="proc-panel">
      <div className="proc-phase-head">
        <span className="proc-phase-title">{phase === "before" ? "Before the agent" : "After the agent"}</span>
        <Tag type={phase === "before" ? "blue" : "purple"} size="sm">{lists[phase].length} stages</Tag>
      </div>
      {lists[phase].length === 0 && <p className="studio-muted">Nothing — the message goes straight through.</p>}
      {lists[phase].map((id, i) => (
        <StageCard key={id} id={id} idx={i} total={lists[phase].length}
          onMove={(d) => move(phase, i, d)} onDrop={() => drop(phase, i)} onToggle={() => toggle(id)} />
      ))}
      {adding === phase ? (
        <div className="stage-new">
          <TextInput id={`t-${phase}`} labelText="What it does" size="sm" placeholder="Translate the question"
            value={draft.title} onChange={(e: any) => setDraft({ ...draft, title: e.target.value })} />
          <Select id={`s-${phase}`} labelText="Interface" size="sm" value={draft.shape}
            onChange={(e: any) => setDraft({ ...draft, shape: e.target.value })}>
            {(Object.keys(SHAPES) as Shape[])
              .filter((k) => (phase === "before" ? k !== "text_to_audio" : k !== "audio_to_text" && k !== "text_to_passages"))
              .map((k) => <SelectItem key={k} value={k} text={SHAPES[k].label} />)}
          </Select>
          <TextInput id={`u-${phase}`} labelText="Service URL" size="sm" placeholder="http://my-service:8000/v1/text"
            value={draft.url} onChange={(e: any) => setDraft({ ...draft, url: e.target.value })} />
          <div className="stage-new-actions">
            <Button size="sm" onClick={() => addCustom(phase)}>Add</Button>
            <Button size="sm" kind="ghost" onClick={() => setAdding(null)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="stage-add-row">
          <Select id={`add-${phase}`} labelText="" size="sm" value="" onChange={(e: any) => e.target.value && add(phase, e.target.value)}>
            <SelectItem value="" text="+ Add a stage…" />
            {stages.filter((s) => !lists[phase].includes(s.id) &&
              (phase === "before" ? s.shape !== "text_to_audio" : s.shape === "text_to_text" || s.shape === "text_to_audio"))
              .map((s) => <SelectItem key={s.id} value={s.id} text={`${s.title} · ${SHAPES[s.shape].label}`} />)}
          </Select>
          <Button size="sm" kind="ghost" renderIcon={Add} onClick={() => setAdding(phase)}>Point at my own service</Button>
        </div>
      )}
    </Tile>
  );

  return (
    <div className="proc-wrap">
      <InlineNotification kind="info" lowContrast hideCloseButton title="Preview"
        subtitle="Design review only — not wired to the events service yet. A stage is one POST to a service; the four interfaces on the right are what the layer already calls today." />

      <div className="proc-split">
        <div className="proc-rail">
          <div className="proc-rail-head">Channels</div>
          {CHANNELS.map((c) => (
            <button key={c.name} className={`proc-rail-item${c.name === channel ? " proc-rail-item--on" : ""}`} onClick={() => setChannel(c.name)}>
              <span className="proc-rail-name"><Chat size={16} /> {c.label}</span>
              <span className="proc-rail-sub">{cfg[c.name].before.length + cfg[c.name].after.length} stages · {c.accepts}</span>
            </button>
          ))}
          <div className="proc-rail-foot">Each channel has its own list. A stage that needs audio simply never runs on a channel that has none.</div>
        </div>

        <div className="proc-main">
          <Phase phase="before" />
          <Tile className="proc-agent">
            <div className="proc-agent-title"><Bot size={18} /> Ask the agent</div>
            <div className="studio-muted">roster <code>indic_farm_assistant</code> · supervisor → <code>farm_assistant</code></div>
          </Tile>
          <Phase phase="after" />
        </div>

        <div className="proc-side">
          <Tile className="proc-panel">
            <div className="proc-phase-title">The interface</div>
            <p className="studio-muted proc-panel-sub">
              Four shapes. Anything that answers one of them can be a stage — yours, a vendor's, another team's.
            </p>
            {(Object.keys(SHAPES) as Shape[]).map((k) => {
              const sh = SHAPES[k];
              return (
                <div key={k} className="shape">
                  <div className="shape-head"><sh.Icon size={14} /> {sh.label}</div>
                  <div className="shape-blurb">{sh.blurb}</div>
                  <pre className="shape-io">{sh.request}</pre>
                  <pre className="shape-io shape-io--out">→ {sh.response}</pre>
                </div>
              );
            })}
          </Tile>
        </div>
      </div>
    </div>
  );
}
