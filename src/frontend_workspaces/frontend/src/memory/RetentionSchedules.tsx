import React, { useEffect, useState } from "react";
import {
  Button,
  TextInput,
  Select,
  SelectItem,
  Accordion,
  AccordionItem,
  Grid,
  Column,
  UnorderedList,
  ListItem,
} from "@carbon/react";
import {
  changeSchedule,
  loadSchedules,
  previewSchedule,
  saveSchedule,
  type RetentionSchedule,
  type ScheduleSpec,
} from "./api";

function Fields({ children }: { children: React.ReactNode }) {
  return (
    <Grid fullWidth className="memory-schedules__fields">
      {React.Children.toArray(children).map((child, index) => (
        <Column key={index} sm={4} md={4} lg={8}>
          {child}
        </Column>
      ))}
    </Grid>
  );
}

const defaults: ScheduleSpec = {
  schedule: "0 2 * * *",
  timeZone: "Etc/UTC",
  concurrencyPolicy: "Forbid",
  startingDeadlineSeconds: null,
  suspend: true,
};
function localTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "long",
    timeZone,
  }).format(new Date(value));
}
function describe(spec: ScheduleSpec) {
  const match = /^(\d+) (\d+) \* \* (\*|[0-6])$/.exec(spec.schedule);
  if (!match) return spec.schedule;
  const days = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
  ];
  return `${match[3] === "*" ? "Every day" : `Every ${days[Number(match[3])]}`} at ${match[2].padStart(2, "0")}:${match[1].padStart(2, "0")}`;
}
export function RetentionSchedules({
  policyId,
  enabled,
  readOnly = false,
}: {
  policyId: string;
  enabled: boolean;
  readOnly?: boolean;
}) {
  const inputId = React.useId();
  const [items, setItems] = useState<RetentionSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<RetentionSchedule | "new" | null>(
    null,
  );
  const [id, setId] = useState("");
  const [spec, setSpec] = useState<ScheduleSpec>(defaults);
  const [frequency, setFrequency] = useState("daily");
  const [time, setTime] = useState("02:00");
  const [day, setDay] = useState("0");
  const [preview, setPreview] = useState<string[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<RetentionSchedule | null>(
    null,
  );
  const refresh = async () =>
    setItems(
      (await loadSchedules()).filter(
        (item) => item.definition.policy_id === policyId,
      ),
    );
  useEffect(() => {
    let active = true;
    loadSchedules()
      .then((rows) => {
        if (active)
          setItems(
            rows.filter((item) => item.definition.policy_id === policyId),
          );
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [policyId]);
  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Schedule operation failed. Refresh before retrying.",
      );
    } finally {
      setBusy(false);
    }
  };
  const edit = (item: RetentionSchedule | "new") => {
    setEditing(item);
    setError("");
    setPreview([]);
    setDeleteTarget(null);
    const value =
      item === "new" ? { ...defaults } : { ...item.definition.spec };
    setSpec(value);
    setId(item === "new" ? "" : item.schedule_id);
    const match = /^(\d+) (\d+) \* \* (\*|[0-6])$/.exec(value.schedule);
    setFrequency(match ? (match[3] === "*" ? "daily" : "weekly") : "custom");
    setTime(
      match
        ? `${match[2].padStart(2, "0")}:${match[1].padStart(2, "0")}`
        : "02:00",
    );
    setDay(match && match[3] !== "*" ? match[3] : "0");
  };
  const effectiveSpec = () => {
    if (frequency === "custom") return spec;
    const [hours, minutes] = time.split(":");
    return {
      ...spec,
      schedule: `${Number(minutes)} ${Number(hours)} * * ${frequency === "weekly" ? day : "*"}`,
    };
  };
  return (
    <section className="memory-schedules" aria-label="Retention schedules">
<div className="memory-settings__row"><div><h2>Schedules</h2><p>Schedules are stored and executed by Evolve.</p></div>      <Button size="md" disabled={readOnly || busy || !enabled} onClick={() => edit("new")}>
        Add schedule
      </Button>
</div>
      {error && (
        <p role="alert" className="memory-schedules__error">
          {error}
        </p>
      )}
      <Button
        kind="ghost"
        size="sm"
        type="button"
        disabled={busy}
        onClick={() =>
          void perform(async () => {
            await refresh();
            setEditing(null);
            setPreview([]);
          })
        }
      >
        Refresh schedules
      </Button>
      {loading ? (
        <p>Loading schedules…</p>
      ) : (
        !items.length && <p>No schedules configured for this policy.</p>
      )}
      {items.map((item) => (
        <div className="memory-schedules__card" key={item.schedule_id}>
          <strong>{item.schedule_id}</strong>
          <span className="memory-schedules__state">
            {item.definition.spec.suspend ? "Stopped" : "Active"}
          </span>
          <p>
            {describe(item.definition.spec)} · {item.definition.spec.timeZone}
          </p>
          <p>
            {item.definition.spec.suspend
              ? "Start this schedule to enable future runs."
              : item.next_runs?.[0]
                ? `Next run: ${localTime(item.next_runs[0], item.definition.spec.timeZone)}`
                : "Next run unavailable"}
          </p>
          {item.definition.agent_id && (
            <p>
              Agent scope: {item.definition.agent_id}. Saving here applies it to
              all users and agents in this service instance.
            </p>
          )}
          {item.definition.dry_run && (
            <p>
              This schedule is configured for dry runs. Saving here enables
              applied retention.
            </p>
          )}
          <div className="memory-schedules__actions">
            <Button
              kind="ghost"
              size="sm"
              disabled={readOnly || busy}
              onClick={() => edit(item)}
            >
              Edit
            </Button>
            <Button
              kind="ghost"
              size="sm"
              disabled={readOnly || busy || (!enabled && item.definition.spec.suspend)}
              onClick={() =>
                void perform(async () => {
                  await changeSchedule(
                    item,
                    item.definition.spec.suspend ? "start" : "stop",
                  );
                  await refresh();
                })
              }
            >
              {item.definition.spec.suspend ? "Start" : "Stop"}
            </Button>
            <Button
              size="sm"
              kind="danger--ghost"
              disabled={readOnly || busy}
              onClick={() => setDeleteTarget(item)}
            >
              Delete
            </Button>
          </div>
        </div>
      ))}
      {deleteTarget && (
        <div className="memory-schedules__notice" role="alert">
          <p>
            Delete schedule “{deleteTarget.schedule_id}”? Its policy and run
            history will remain.
          </p>
          <div className="memory-schedules__actions">
            <Button
              size="sm"
              disabled={readOnly || busy}
              kind="danger--ghost"
              onClick={() =>
                void perform(async () => {
                  await changeSchedule(deleteTarget, "delete");
                  setDeleteTarget(null);
                  await refresh();
                })
              }
            >
              Delete schedule
            </Button>
            <Button
              kind="ghost"
              size="sm"
              disabled={readOnly || busy}
              onClick={() => setDeleteTarget(null)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {editing && (
        <form
          className="memory-schedules__editor"
          onSubmit={(event) => {
            event.preventDefault();
            void perform(async () => {
              await saveSchedule(
                id,
                policyId,
                effectiveSpec(),
                editing === "new" ? 0 : editing.revision,
              );
              setEditing(null);
              await refresh();
            });
          }}
          onChange={() => setPreview([])}
        >
          <h3>{editing === "new" ? "Add schedule" : "Edit schedule"}</h3>
          <fieldset disabled={readOnly || busy}>
            <Fields>
              <TextInput
                id={`${inputId}-5`}
                labelText="Schedule ID"
                required
                maxLength={128}
                pattern="[A-Za-z0-9_.:-]+"
                value={id}
                readOnly={editing !== "new"}
                onChange={(e) => setId(e.target.value)}
              />
              <Select
                id={`${inputId}-1`}
                labelText="Frequency"
                value={frequency}
                onChange={(e) => setFrequency(e.target.value)}
              >
                <SelectItem value="daily" text="Daily" />
                <SelectItem value="weekly" text="Weekly" />
                <SelectItem value="custom" text="Custom cron" />
              </Select>
              {frequency !== "custom" && (
                <TextInput
                  id={`${inputId}-6`}
                  labelText="Time"
                  type="time"
                  required
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              )}
              {frequency === "weekly" && (
                <Select
                  id={`${inputId}-2`}
                  labelText="Day"
                  value={day}
                  onChange={(e) => setDay(e.target.value)}
                >
                  {[
                    "Sunday",
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                  ].map((name, index) => (
                    <SelectItem key={name} value={index} text={name} />
                  ))}
                </Select>
              )}
              {frequency === "custom" && (
                <TextInput
                  id={`${inputId}-7`}
                  labelText="Cron expression"
                  required
                  value={spec.schedule}
                  onChange={(e) =>
                    setSpec({ ...spec, schedule: e.target.value })
                  }
                />
              )}
              <TextInput
                id={`${inputId}-8`}
                labelText="Time zone (IANA)"
                required
                value={spec.timeZone}
                onChange={(e) => setSpec({ ...spec, timeZone: e.target.value })}
                placeholder="America/Los_Angeles"
              />
            </Fields>
            <Accordion>
              <AccordionItem title="Advanced timing">
                <Fields>
                  <Select
                    id={`${inputId}-3`}
                    labelText="If a previous run is still active"
                    value={spec.concurrencyPolicy}
                    onChange={(e) =>
                      setSpec({
                        ...spec,
                        concurrencyPolicy: e.target
                          .value as ScheduleSpec["concurrencyPolicy"],
                      })
                    }
                  >
                    <SelectItem value="Forbid" text="Skip the new run" />
                    <SelectItem value="Allow" text="Allow overlap" />
                    <SelectItem value="Replace" text="Replace previous run" />
                  </Select>
                  <TextInput
                    id={`${inputId}-9`}
                    labelText="Start deadline (seconds, optional)"
                    type="number"
                    min={0}
                    step={1}
                    value={spec.startingDeadlineSeconds ?? ""}
                    onChange={(e) =>
                      setSpec({
                        ...spec,
                        startingDeadlineSeconds:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </Fields>
              </AccordionItem>
            </Accordion>
            <Button
              kind="ghost"
              size="sm"
              type="button"
              onClick={() =>
                void perform(async () =>
                  setPreview(
                    (await previewSchedule(effectiveSpec())).next_runs,
                  ),
                )
              }
            >
              Preview next runs
            </Button>
            {preview.length > 0 && (
              <div className="memory-schedules__notice">
                <strong>Next five matching times</strong>
                <UnorderedList>
                  {preview.map((value) => (
                    <ListItem key={value}>
                      {localTime(value, spec.timeZone)}
                    </ListItem>
                  ))}
                </UnorderedList>
                <p>
                  Preview only. Nothing has been saved.
                  {spec.suspend ? " This schedule is stopped." : ""}
                </p>
              </div>
            )}
            {editing === "new" && (
              <p>
                New schedules are created stopped. Start the schedule when
                ready.
              </p>
            )}
            <div className="memory-schedules__actions">
              <Button kind="primary" type="submit">
                Save changes
              </Button>
              <Button
                kind="ghost"
                size="sm"
                type="button"
                onClick={() => setEditing(null)}
              >
                Cancel
              </Button>
            </div>
          </fieldset>
        </form>
      )}
    </section>
  );
}
