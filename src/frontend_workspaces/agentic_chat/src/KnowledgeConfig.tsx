import React, { useState, useEffect } from "react";
import { X, Save, Plus, Trash2, Upload } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./ConfigModal.css";

interface KnowledgeSource {
  id: string;
  name: string;
  type: "file" | "url" | "database";
  path?: string;
  url?: string;
  enabled: boolean;
}

interface KnowledgeConfigData {
  sources: KnowledgeSource[];
  embeddingModel: string;
  chunkSize: number;
  chunkOverlap: number;
}

interface KnowledgeConfigProps {
  onClose: () => void;
}

export default function KnowledgeConfig({ onClose }: KnowledgeConfigProps) {
  const [config, setConfig] = useState<KnowledgeConfigData>({
    sources: [],
    embeddingModel: "text-embedding-3-small",
    chunkSize: 1000,
    chunkOverlap: 200,
  });
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await apiFetch('/api/config/knowledge');
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
      const response = await apiFetch('/api/config/knowledge', {
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

  const addSource = () => {
    const newSource: KnowledgeSource = {
      id: Date.now().toString(),
      name: "New Source",
      type: "file",
      enabled: true,
    };
    setConfig({
      ...config,
      sources: [...config.sources, newSource],
    });
  };

  const updateSource = (id: string, updates: Partial<KnowledgeSource>) => {
    setConfig({
      ...config,
      sources: config.sources.map(source =>
        source.id === id ? { ...source, ...updates } : source
      ),
    });
  };

  const removeSource = (id: string) => {
    setConfig({
      ...config,
      sources: config.sources.filter(source => source.id !== id),
    });
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Knowledge Configuration</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-content">
          <div className="config-card">
            <h3>Embedding Settings</h3>
            <div className="config-form">
              <div className="form-group">
                <label>Embedding Model</label>
                <select
                  value={config.embeddingModel}
                  onChange={(e) => setConfig({ ...config, embeddingModel: e.target.value })}
                >
                  <option value="text-embedding-3-small">text-embedding-3-small</option>
                  <option value="text-embedding-3-large">text-embedding-3-large</option>
                  <option value="text-embedding-ada-002">text-embedding-ada-002</option>
                </select>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Chunk Size</label>
                  <input
                    type="number"
                    value={config.chunkSize}
                    onChange={(e) => setConfig({ ...config, chunkSize: parseInt(e.target.value) })}
                    min="100"
                    max="4000"
                  />
                </div>

                <div className="form-group">
                  <label>Chunk Overlap</label>
                  <input
                    type="number"
                    value={config.chunkOverlap}
                    onChange={(e) => setConfig({ ...config, chunkOverlap: parseInt(e.target.value) })}
                    min="0"
                    max="1000"
                  />
                </div>
              </div>
            </div>
          </div>

          <div className="config-card">
            <div className="section-header">
              <h3>Knowledge Sources</h3>
              <button className="add-btn" onClick={addSource}>
                <Plus size={16} />
                Add Source
              </button>
            </div>

            <div className="sources-list">
              {config.sources.map((source) => (
                <div key={source.id} className="source-item">
                  <div className="source-header">
                    <input
                      type="checkbox"
                      checked={source.enabled}
                      onChange={(e) => updateSource(source.id, { enabled: e.target.checked })}
                    />
                    <input
                      type="text"
                      value={source.name}
                      onChange={(e) => updateSource(source.id, { name: e.target.value })}
                      className="source-name"
                    />
                    <select
                      value={source.type}
                      onChange={(e) => updateSource(source.id, { type: e.target.value as any })}
                    >
                      <option value="file">File</option>
                      <option value="url">URL</option>
                      <option value="database">Database</option>
                    </select>
                    <button
                      className="delete-btn"
                      onClick={() => removeSource(source.id)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>

                  <div className="source-details">
                    {source.type === "file" && (
                      <div className="form-group">
                        <input
                          type="text"
                          value={source.path || ""}
                          onChange={(e) => updateSource(source.id, { path: e.target.value })}
                          placeholder="File path..."
                        />
                      </div>
                    )}
                    {source.type === "url" && (
                      <div className="form-group">
                        <input
                          type="text"
                          value={source.url || ""}
                          onChange={(e) => updateSource(source.id, { url: e.target.value })}
                          placeholder="https://..."
                        />
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {config.sources.length === 0 && (
              <div className="empty-state">
                <p>No knowledge sources configured. Click "Add Source" to get started.</p>
              </div>
            )}
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


