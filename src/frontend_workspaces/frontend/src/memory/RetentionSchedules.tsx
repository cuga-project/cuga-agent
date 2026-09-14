import React, { useEffect, useState } from "react";
import { changeSchedule, loadSchedules, previewSchedule, saveSchedule, type RetentionSchedule, type ScheduleSpec } from "./api";

const defaults: ScheduleSpec = {schedule: "0 2 * * *", timeZone: "Etc/UTC", concurrencyPolicy: "Forbid", startingDeadlineSeconds: null, suspend: true};
function localTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "long", timeZone}).format(new Date(value));
}
function describe(spec: ScheduleSpec) {
  const match = /^(\d+) (\d+) \* \* (\*|[0-6])$/.exec(spec.schedule);
  if (!match) return spec.schedule;
  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  return `${match[3] === "*" ? "Every day" : `Every ${days[Number(match[3])]}`} at ${match[2].padStart(2,"0")}:${match[1].padStart(2,"0")}`;
}
export function RetentionSchedules({policyId, enabled}: {policyId: string; enabled: boolean}) {
  const [items, setItems] = useState<RetentionSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<RetentionSchedule | "new" | null>(null);
  const [id, setId] = useState("");
  const [spec, setSpec] = useState<ScheduleSpec>(defaults);
  const [frequency, setFrequency] = useState("daily");
  const [time, setTime] = useState("02:00");
  const [day, setDay] = useState("0");
  const [preview, setPreview] = useState<string[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<RetentionSchedule | null>(null);
  const refresh = async () => setItems((await loadSchedules()).filter(item => item.definition.policy_id === policyId));
  useEffect(() => {let active = true; loadSchedules().then(rows => {if (active) setItems(rows.filter(item => item.definition.policy_id === policyId));}).catch(e => {if(active) setError(e.message);}).finally(() => {if(active) setLoading(false);});return () => {active=false};}, [policyId]);
  const perform = async (action: () => Promise<void>) => {setBusy(true);setError("");try {await action();} catch(e) {setError(e instanceof Error ? e.message : "Schedule operation failed. Refresh before retrying.");} finally {setBusy(false);}};
  const edit = (item: RetentionSchedule | "new") => {
    setEditing(item);setError("");setPreview([]);setDeleteTarget(null);
    const value = item === "new" ? {...defaults} : {...item.definition.spec};setSpec(value);setId(item === "new" ? "" : item.schedule_id);
    const match = /^(\d+) (\d+) \* \* (\*|[0-6])$/.exec(value.schedule);
    setFrequency(match ? match[3] === "*" ? "daily" : "weekly" : "custom");
    setTime(match ? `${match[2].padStart(2,"0")}:${match[1].padStart(2,"0")}` : "02:00");setDay(match && match[3] !== "*" ? match[3] : "0");
  };
  const effectiveSpec = () => {if (frequency === "custom") return spec;const [hours,minutes] = time.split(":");return {...spec,schedule:`${Number(minutes)} ${Number(hours)} * * ${frequency === "weekly" ? day : "*"}`};};
  return <section className="memory-schedules" aria-label="Retention schedules">
    <h3>Schedules</h3><p>Schedules are stored and executed by Evolve.</p>
    {error && <p role="alert" className="memory-schedules__error">{error}</p>}
    <button type="button" disabled={busy} onClick={() => void perform(async () => {await refresh();setEditing(null);setPreview([]);})}>Refresh schedules</button>
    {loading ? <p>Loading schedules…</p> : !items.length && <p>No schedules configured for this policy.</p>}
    {items.map(item => <div className="memory-schedules__card" key={item.schedule_id}>
      <strong>{item.schedule_id}</strong><span className="memory-schedules__state">{item.definition.spec.suspend ? "Stopped" : "Active"}</span>
      <p>{describe(item.definition.spec)} · {item.definition.spec.timeZone}</p>
      <p>{item.definition.spec.suspend ? "Start this schedule to enable future runs." : item.next_runs?.[0] ? `Next run: ${localTime(item.next_runs[0],item.definition.spec.timeZone)}` : "Next run unavailable"}</p>
      {item.definition.agent_id && <p>Agent scope: {item.definition.agent_id}. Saving here applies it to all users and agents in this service instance.</p>}
      {item.definition.dry_run && <p>This schedule is configured for dry runs. Saving here enables applied retention.</p>}
      <div className="memory-schedules__actions"><button disabled={busy} onClick={() => edit(item)}>Edit</button><button disabled={busy || !enabled && item.definition.spec.suspend} onClick={() => void perform(async () => {await changeSchedule(item,item.definition.spec.suspend ? "start" : "stop");await refresh();})}>{item.definition.spec.suspend ? "Start" : "Stop"}</button><button className="memory-schedules__danger" disabled={busy} onClick={() => setDeleteTarget(item)}>Delete</button></div>
    </div>)}
    {deleteTarget && <div className="memory-schedules__notice" role="alert"><p>Delete schedule “{deleteTarget.schedule_id}”? Its policy and run history will remain.</p><div className="memory-schedules__actions"><button disabled={busy} className="memory-schedules__danger" onClick={() => void perform(async () => {await changeSchedule(deleteTarget,"delete");setDeleteTarget(null);await refresh();})}>Delete schedule</button><button disabled={busy} onClick={() => setDeleteTarget(null)}>Cancel</button></div></div>}
    <button disabled={busy || !enabled} onClick={() => edit("new")}>Add schedule</button>
    {editing && <form className="memory-schedules__editor" onSubmit={event => {event.preventDefault();void perform(async () => {await saveSchedule(id,policyId,effectiveSpec(),editing === "new" ? 0 : editing.revision);setEditing(null);await refresh();});}} onChange={() => setPreview([])}>
      <h3>{editing === "new" ? "Add schedule" : "Edit schedule"}</h3>
      <fieldset disabled={busy}><div className="memory-schedules__fields">
        <label>Schedule ID<input required maxLength={128} pattern="[A-Za-z0-9_.:-]+" value={id} readOnly={editing !== "new"} onChange={e => setId(e.target.value)}/></label>
        <label>Frequency<select value={frequency} onChange={e => setFrequency(e.target.value)}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="custom">Custom cron</option></select></label>
        {frequency !== "custom" && <label>Time<input type="time" required value={time} onChange={e => setTime(e.target.value)}/></label>}
        {frequency === "weekly" && <label>Day<select value={day} onChange={e => setDay(e.target.value)}>{["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"].map((name,index) => <option key={name} value={index}>{name}</option>)}</select></label>}
        {frequency === "custom" && <label>Cron expression<input required value={spec.schedule} onChange={e => setSpec({...spec,schedule:e.target.value})}/></label>}
        <label>Time zone (IANA)<input required value={spec.timeZone} onChange={e => setSpec({...spec,timeZone:e.target.value})} placeholder="America/Los_Angeles"/></label>
      </div><details><summary>Advanced timing</summary><div className="memory-schedules__fields"><label>If a previous run is still active<select value={spec.concurrencyPolicy} onChange={e => setSpec({...spec,concurrencyPolicy:e.target.value as ScheduleSpec["concurrencyPolicy"]})}><option value="Forbid">Skip the new run</option><option value="Allow">Allow overlap</option><option value="Replace">Replace previous run</option></select></label><label>Start deadline (seconds, optional)<input type="number" min={0} step={1} value={spec.startingDeadlineSeconds ?? ""} onChange={e => setSpec({...spec,startingDeadlineSeconds:e.target.value === "" ? null : Number(e.target.value)})}/></label></div></details>
      <button type="button" onClick={() => void perform(async () => setPreview((await previewSchedule(effectiveSpec())).next_runs))}>Preview next runs</button>
      {preview.length > 0 && <div className="memory-schedules__notice"><strong>Next five matching times</strong><ul>{preview.map(value => <li key={value}>{localTime(value,spec.timeZone)}</li>)}</ul><p>Preview only. Nothing has been saved.{spec.suspend ? " This schedule is stopped." : ""}</p></div>}
      {editing === "new" && <p>New schedules are created stopped. Start the schedule when ready.</p>}
      <div className="memory-schedules__actions"><button className="memory-schedules__primary" type="submit">Save changes</button><button type="button" onClick={() => setEditing(null)}>Cancel</button></div></fieldset>
    </form>}
  </section>;
}
