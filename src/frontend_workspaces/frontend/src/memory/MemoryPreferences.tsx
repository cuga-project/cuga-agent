import React, { useEffect, useState } from "react";
import { Button, Column, Grid, InlineNotification, Toggle } from "@carbon/react";
import { loadMemoryPreferences, saveMemoryPreference, type MemoryPreferencesState } from "./api";

export function MemoryPreferences({ admin = false }: { admin?: boolean }) {
  const [preferences, setPreferences] = useState<MemoryPreferencesState | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const refresh = () => loadMemoryPreferences(admin).then((value) => {
      if (active) { setPreferences(value); setError(""); }
    }).catch(() => { if (active) setError("Memory settings could not be loaded."); });
    void refresh();
    window.addEventListener("focus", refresh);
    window.addEventListener("memory-preferences-changed", refresh);
    return () => { active = false; window.removeEventListener("focus", refresh); window.removeEventListener("memory-preferences-changed", refresh); };
  }, [admin]);

  const save = async (enabled: boolean | null) => {
    setSaving(true);
    setError("");
    try {
      setPreferences(await saveMemoryPreference(enabled, admin));
      window.dispatchEvent(new Event("memory-preferences-changed"));
    }
    catch (err) { setError(err instanceof Error ? err.message : "Memory settings could not be saved."); }
    finally { setSaving(false); }
  };

  return <Grid className="memory-preferences" fullWidth>
    <Column sm={4} md={8} lg={16}>
      <div className="memory-settings__row">
        <div>
          <Toggle
            id={admin ? "instance-memory-enabled" : "user-memory-enabled"}
            labelText={admin ? "Memory for this service" : "Memory for me"}
            labelA="Off" labelB="On"
            toggled={preferences ? (admin ? preferences.instance_enabled : preferences.user_enabled) : false}
            disabled={!preferences || saving || (!admin && !preferences.instance_enabled)}
            onToggle={(enabled) => void save(enabled)}
          />
          <p className="memory-settings__note">
            {admin ? "Allow agents to save and use memories for all users of this service."
              : preferences && !preferences.instance_enabled ? "Memory is disabled for this service. Your preference is saved."
              : "Allow agents to save and use memories for your conversations across this service."}
          </p>
          <p className="memory-settings__note">Turning memory off keeps existing memories available to review and delete. Retention continues.</p>
        </div>
        {admin && preferences && <div>
          <p className="memory-settings__note">
            {preferences.instance_override === null ? "Using operator default" : "Administrator override"}
            {` (operator default: ${preferences.operator_default ? "on" : "off"})`}
          </p>
          {preferences.instance_override !== null && <Button kind="ghost" size="sm" disabled={saving} onClick={() => void save(null)}>Use operator default</Button>}
        </div>}
      </div>
      {error && <InlineNotification kind="error" title={error} lowContrast hideCloseButton />}
    </Column>
  </Grid>;
}
