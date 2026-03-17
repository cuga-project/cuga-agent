import React, { useState, useEffect } from "react";
import { X, Save } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./ConfigModal.css";

interface ModelConfigData {
  provider: string;
  model: string;
  temperature: number;
  maxTokens: number;
  topP: number;
  apiKey?: string;
}

interface ModelConfigProps {
  onClose: () => void;
}

export default function ModelConfig({ onClose }: ModelConfigProps) {
  const [config, setConfig] = useState<ModelConfigData>({
    provider: "watsonx",
    model: "openai/gpt-oss-120b",
    temperature: 0.7,
    maxTokens: 4096,
    topP: 1.0,
  });
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await apiFetch('/api/config/model');
      if (response.ok) {
        const data = await response.json();
        setConfig(data);
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };

  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await apiFetch('/api/config/model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
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
    }
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Model Configuration</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-content">
          <div className="config-card">
            <h3>Language Model Settings</h3>
            <div className="config-form">
              <div className="form-group">
                <label>Provider</label>
                <select
                  value={config.provider}
                  onChange={(e) => setConfig({ ...config, provider: e.target.value })}
                >
                  <option value="anthropic">Anthropic</option>
                  <option value="openai">OpenAI</option>
                  <option value="azure">Azure OpenAI</option>
                  <option value="watsonx">IBM watsonx</option>
                  <option value="ollama">Ollama</option>
                </select>
              </div>

              <div className="form-group">
                <label>Model</label>
                <input
                  type="text"
                  value={config.model}
                  onChange={(e) => setConfig({ ...config, model: e.target.value })}
                  placeholder="e.g., claude-3-5-sonnet-20241022"
                />
              </div>

              <div className="form-group">
                <label>Temperature: {config.temperature}</label>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={config.temperature}
                  onChange={(e) => setConfig({ ...config, temperature: parseFloat(e.target.value) })}
                />
                <small>Controls randomness: 0 = focused, 2 = creative</small>
              </div>

              <div className="form-group">
                <label>Max Tokens</label>
                <input
                  type="number"
                  value={config.maxTokens}
                  onChange={(e) => setConfig({ ...config, maxTokens: parseInt(e.target.value) })}
                  min="1"
                  max="200000"
                />
              </div>

              <div className="form-group">
                <label>Top P: {config.topP}</label>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.01"
                  value={config.topP}
                  onChange={(e) => setConfig({ ...config, topP: parseFloat(e.target.value) })}
                />
                <small>Nucleus sampling threshold</small>
              </div>

              {config.provider !== "ollama" && (
                <div className="form-group">
                  <label>API Key</label>
                  <input
                    type="password"
                    value={config.apiKey || ""}
                    onChange={(e) => setConfig({ ...config, apiKey: e.target.value })}
                    placeholder="Enter API key..."
                  />
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="config-modal-footer">
          <button className="cancel-btn" onClick={onClose}>
            Cancel
          </button>
          <button 
            className={`save-btn ${saveStatus}`}
            onClick={saveConfig}
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


