import React, { useState, useEffect } from "react";
import { X, Save } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./ConfigModal.css";

interface AgentBehaviorConfigProps {
  onClose: () => void;
}

interface BehaviorSettings {
  useVisionOnUI: boolean;
  useSOMOnWeb: boolean;
  decompositionStrategy: "sequential" | "parallel" | "hierarchical" | "adaptive";
}

export default function AgentBehaviorConfig({ onClose }: AgentBehaviorConfigProps) {
  const [settings, setSettings] = useState<BehaviorSettings>({
    useVisionOnUI: true,
    useSOMOnWeb: true,
    decompositionStrategy: "adaptive"
  });

  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");

  useEffect(() => {
    fetchBehaviorSettings();
  }, []);

  const fetchBehaviorSettings = async () => {
    try {
      const response = await apiFetch("/api/agent/behavior");
      if (response.ok) {
        const data = await response.json();
        setSettings(data);
      }
    } catch (error) {
      console.error("Failed to fetch behavior settings:", error);
    }
  };

  const handleSave = async () => {
    setSaveStatus("saving");
    try {
      const response = await apiFetch("/api/agent/behavior", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings)
      });
      
      if (response.ok) {
        setSaveStatus("success");
        setTimeout(() => setSaveStatus("idle"), 2000);
      } else {
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (error) {
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
      console.error("Error saving behavior settings:", error);
    }
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Agent Behavior Configuration</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-content">
          <div className="config-card">
            <h3>UI Interaction Settings</h3>
            <div className="config-form">
              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={settings.useVisionOnUI}
                    onChange={(e) =>
                      setSettings({ ...settings, useVisionOnUI: e.target.checked })
                    }
                  />
                  <span>Use Vision on UI Interactions</span>
                </label>
                <small>
                  Enable vision capabilities for understanding and interacting with user interfaces.
                  When enabled, the agent will use visual models to interpret UI elements.
                </small>
              </div>
            </div>
          </div>

          <div className="config-card">
            <h3>Web Action Settings</h3>
            <div className="config-form">
              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={settings.useSOMOnWeb}
                    onChange={(e) =>
                      setSettings({ ...settings, useSOMOnWeb: e.target.checked })
                    }
                  />
                  <span>Use Set of Marks (SOM) on Web Actions</span>
                </label>
                <small>
                  Enable Set of Marks technique for web interactions. SOM helps the agent
                  identify and interact with specific elements on web pages more accurately.
                </small>
              </div>
            </div>
          </div>

          <div className="config-card">
            <h3>Task Decomposition</h3>
            <div className="config-form">
              <div className="form-group">
                <label>Decomposition Strategy</label>
                <select
                  value={settings.decompositionStrategy}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      decompositionStrategy: e.target.value as BehaviorSettings["decompositionStrategy"]
                    })
                  }
                  style={{ width: "100%", padding: "8px", fontSize: "14px" }}
                >
                  <option value="sequential">Sequential</option>
                  <option value="parallel">Parallel</option>
                  <option value="hierarchical">Hierarchical</option>
                  <option value="adaptive">Adaptive</option>
                </select>
                <small>
                  Choose how the agent breaks down complex tasks:
                  <br />
                  <strong>Sequential:</strong> Execute tasks one after another in order
                  <br />
                  <strong>Parallel:</strong> Execute independent tasks simultaneously
                  <br />
                  <strong>Hierarchical:</strong> Break down into subtasks with dependencies
                  <br />
                  <strong>Adaptive:</strong> Automatically choose the best strategy based on task complexity
                </small>
              </div>
            </div>
          </div>
        </div>

        <div className="config-modal-footer">
          <button className="cancel-btn" onClick={onClose}>
            Cancel
          </button>
          <button 
            className={`save-btn ${saveStatus}`}
            onClick={handleSave}
            disabled={saveStatus === "saving"}
          >
            <Save size={16} />
            {saveStatus === "idle" && "Save Changes"}
            {saveStatus === "saving" && "Saving..."}
            {saveStatus === "success" && "Saved!"}
            {saveStatus === "error" && "Error!"}
          </button>
        </div>
      </div>
    </div>
  );
}

