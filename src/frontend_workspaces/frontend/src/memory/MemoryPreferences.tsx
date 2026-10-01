import React, { useEffect, useState } from "react";
import { Column, Grid, InlineNotification, Toggle } from "@carbon/react";
import { loadMemoryPreferences, saveMemoryPreference, saveEpisodicMemoryPreference, type MemoryPreferencesState } from "./api";

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

  const save = async (enabled: boolean, episodic = false) => {
    setSaving(true);
    setError("");
    try {
      setPreferences(await (episodic ? saveEpisodicMemoryPreference(enabled) : saveMemoryPreference(enabled, admin)));
      window.dispatchEvent(new Event("memory-preferences-changed"));
    }
    catch (err) { setError(err instanceof Error ? err.message : "Memory settings could not be saved."); }
    finally { setSaving(false); }
  };

  return <Grid className={`memory-preferences${admin ? " memory-preferences--admin" : ""}`} fullWidth>
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
            {admin ? "Enable memory features for this service."
              : preferences && !preferences.instance_enabled ? "Memory is disabled for this service. Your preference is saved."
              : "Allow agents to save and use memories for your conversations across this service."}
          </p>
          {!admin && <p className="memory-settings__note">This controls whether agents save and use your memories.</p>}
        </div>

      </div>
      {admin && <div className="memory-settings__row">
        <div>
          <Toggle
            id="instance-episodic-memory-enabled"
            labelText="Episodic memory"
            labelA="Off" labelB="On"
            toggled={preferences?.episodic_enabled ?? false}
            disabled={!preferences || saving || !preferences.instance_enabled}
            onToggle={(enabled) => void save(enabled, true)}
            aria-describedby="episodic-memory-description"
          />
          <p id="episodic-memory-description" className="memory-settings__note">
            Learn from past conversations to improve future responses. When off, memory only saves and uses facts about users.
          </p>
        </div>
      </div>}
      {error && <InlineNotification kind="error" title={error} lowContrast hideCloseButton />}
    </Column>
  </Grid>;
}
