import React, { useState, useEffect } from "react";
import { X, Save, Plus, Trash2 } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./ConfigModal.css";

interface SavedTrajectory {
  id: string;
  name: string;
  description: string;
  parameters: Array<{
    name: string;
    type: "string" | "number" | "boolean" | "array" | "object";
    required: boolean;
    description?: string;
  }>;
  minInteractions: number;
  confidence: number;
  enabled: boolean;
  createdAt: number;
}

interface MemoryConfigData {
  enableMemory: boolean;
  disableMemory: boolean;
  memoryType: "short_term" | "long_term" | "both";
  contextWindow: number;
  maxMemoryItems: number;
  semanticSearch: boolean;
  autoSummarization: boolean;
  factStorage: boolean;
  learningFromFailures: boolean;
  blockedMemoryItems: string[];
  saveAndReuse: {
    enabled: boolean;
    autoGeneralize: boolean;
    minSuccessfulRuns: number;
    requireApproval: boolean;
    savedTrajectories: SavedTrajectory[];
  };
}

interface MemoryConfigProps {
  onClose: () => void;
}

export default function MemoryConfig({ onClose }: MemoryConfigProps) {
  const [config, setConfig] = useState<MemoryConfigData>({
    enableMemory: true,
    disableMemory: false,
    memoryType: "both",
    contextWindow: 4096,
    maxMemoryItems: 100,
    semanticSearch: true,
    autoSummarization: true,
    factStorage: false,
    learningFromFailures: false,
    blockedMemoryItems: [],
    saveAndReuse: {
      enabled: false,
      autoGeneralize: false,
      minSuccessfulRuns: 3,
      requireApproval: true,
      savedTrajectories: [],
    },
  });
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [newBlockedItem, setNewBlockedItem] = useState("");
  const [expandedTrajectory, setExpandedTrajectory] = useState<string | null>(null);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await apiFetch('/api/config/memory');
      if (response.ok) {
        const data = await response.json();
        setConfig({
          enableMemory: data.enableMemory ?? true,
          disableMemory: data.disableMemory ?? false,
          memoryType: data.memoryType ?? "both",
          contextWindow: data.contextWindow ?? 4096,
          maxMemoryItems: data.maxMemoryItems ?? 100,
          semanticSearch: data.semanticSearch ?? true,
          autoSummarization: data.autoSummarization ?? true,
          factStorage: data.factStorage ?? false,
          learningFromFailures: data.learningFromFailures ?? false,
          blockedMemoryItems: data.blockedMemoryItems ?? [],
          saveAndReuse: data.saveAndReuse ?? {
            enabled: false,
            autoGeneralize: false,
            minSuccessfulRuns: 3,
            requireApproval: true,
            savedTrajectories: [],
          },
        });
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };

  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await apiFetch('/api/config/memory', {
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

  const addBlockedItem = () => {
    if (newBlockedItem.trim() && !config.blockedMemoryItems.includes(newBlockedItem.trim())) {
      setConfig({
        ...config,
        blockedMemoryItems: [...config.blockedMemoryItems, newBlockedItem.trim()],
      });
      setNewBlockedItem("");
    }
  };

  const removeBlockedItem = (item: string) => {
    setConfig({
      ...config,
      blockedMemoryItems: config.blockedMemoryItems.filter(i => i !== item),
    });
  };

  const toggleTrajectory = (id: string) => {
    setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        savedTrajectories: config.saveAndReuse.savedTrajectories.map(t =>
          t.id === id ? { ...t, enabled: !t.enabled } : t
        ),
      },
    });
  };

  const deleteTrajectory = (id: string) => {
    setConfig({
      ...config,
      saveAndReuse: {
        ...config.saveAndReuse,
        savedTrajectories: config.saveAndReuse.savedTrajectories.filter(t => t.id !== id),
      },
    });
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Memory Configuration</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-content">
          <div className="config-card">
            <h3>Memory Settings</h3>
            <div className="config-form">
              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.disableMemory}
                    onChange={(e) => {
                      const disabled = e.target.checked;
                      setConfig({ 
                        ...config, 
                        disableMemory: disabled,
                        enableMemory: disabled ? false : config.enableMemory,
                      });
                    }}
                  />
                  <span>Disable Memory Completely</span>
                </label>
                <small>Turn off all memory functionality</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.enableMemory}
                    onChange={(e) => setConfig({ ...config, enableMemory: e.target.checked })}
                    disabled={config.disableMemory}
                  />
                  <span>Enable Memory System</span>
                </label>
                <small>Allow the agent to remember context across conversations</small>
              </div>

              <div className="form-group">
                <label>Max Memory Items</label>
                <input
                  type="number"
                  value={config.maxMemoryItems}
                  onChange={(e) => setConfig({ ...config, maxMemoryItems: parseInt(e.target.value) })}
                  min="10"
                  max="1000"
                  disabled={!config.enableMemory || config.disableMemory}
                />
                <small>Maximum number of memory items to store</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.semanticSearch}
                    onChange={(e) => setConfig({ ...config, semanticSearch: e.target.checked })}
                    disabled={!config.enableMemory || config.disableMemory}
                  />
                  <span>Enable Semantic Search</span>
                </label>
                <small>Use embeddings to find relevant memories</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.autoSummarization}
                    onChange={(e) => setConfig({ ...config, autoSummarization: e.target.checked })}
                    disabled={!config.enableMemory || config.disableMemory}
                  />
                  <span>Auto-summarization</span>
                </label>
                <small>Automatically summarize long conversations</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.factStorage}
                    onChange={(e) => setConfig({ ...config, factStorage: e.target.checked })}
                    disabled={!config.enableMemory || config.disableMemory}
                  />
                  <span>Enable Fact Storage and Retrieval</span>
                </label>
                <small>Store and retrieve important facts like IDs, key values, and persistent information</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.learningFromFailures}
                    onChange={(e) => setConfig({ ...config, learningFromFailures: e.target.checked })}
                    disabled={!config.enableMemory || config.disableMemory}
                  />
                  <span>Enable Learning from Failures and Tip Injection</span>
                </label>
                <small>Learn from past failures and analyze trajectories to improve future task performance</small>
              </div>

              <div className="form-group">
                <div className="form-group-header">
                  <label>Blocked Memory Items</label>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <input
                      type="text"
                      value={newBlockedItem}
                      onChange={(e) => setNewBlockedItem(e.target.value)}
                      onKeyPress={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addBlockedItem();
                        }
                      }}
                      placeholder="e.g., passwords, secrets"
                      disabled={config.disableMemory}
                      style={{ width: "200px", padding: "4px 8px", fontSize: "12px" }}
                    />
                    <button
                      className="add-small-btn"
                      onClick={addBlockedItem}
                      disabled={config.disableMemory || !newBlockedItem.trim()}
                    >
                      <Plus size={12} />
                      Add
                    </button>
                  </div>
                </div>
                {config.blockedMemoryItems.length === 0 ? (
                  <div className="policies-empty">
                    No blocked items. Add items that the agent should never remember.
                  </div>
                ) : (
                  <div className="policies-list">
                    {config.blockedMemoryItems.map((item, index) => (
                      <div key={index} className="policy-item">
                        <span style={{ flex: 1, padding: "8px", fontSize: "13px" }}>{item}</span>
                        <button
                          className="remove-btn"
                          onClick={() => removeBlockedItem(item)}
                          disabled={config.disableMemory}
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <small>Items the agent is not allowed to remember (e.g., sensitive information, passwords)</small>
              </div>
            </div>
          </div>

          <div className="config-card">
            <h3>Save & Reuse Trajectories</h3>
            <div className="config-form">
              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.saveAndReuse.enabled}
                    onChange={(e) => setConfig({
                      ...config,
                      saveAndReuse: { ...config.saveAndReuse, enabled: e.target.checked }
                    })}
                    disabled={config.disableMemory}
                  />
                  <span>Enable Save & Reuse</span>
                </label>
                <small>Allow agent to save successful task trajectories as reusable tools</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.saveAndReuse.autoGeneralize}
                    onChange={(e) => setConfig({
                      ...config,
                      saveAndReuse: { ...config.saveAndReuse, autoGeneralize: e.target.checked }
                    })}
                    disabled={!config.saveAndReuse.enabled || config.disableMemory}
                  />
                  <span>Auto-generalize Trajectories</span>
                </label>
                <small>Automatically identify patterns and create reusable tools from successful tasks</small>
              </div>

              <div className="form-group">
                <label>Min. Successful Runs Before Saving</label>
                <input
                  type="number"
                  value={config.saveAndReuse.minSuccessfulRuns}
                  onChange={(e) => setConfig({
                    ...config,
                    saveAndReuse: { ...config.saveAndReuse, minSuccessfulRuns: parseInt(e.target.value) }
                  })}
                  min="1"
                  max="10"
                  disabled={!config.saveAndReuse.enabled || config.disableMemory}
                />
                <small>Number of successful executions before suggesting trajectory as reusable tool</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.saveAndReuse.requireApproval}
                    onChange={(e) => setConfig({
                      ...config,
                      saveAndReuse: { ...config.saveAndReuse, requireApproval: e.target.checked }
                    })}
                    disabled={!config.saveAndReuse.enabled || config.disableMemory}
                  />
                  <span>Require User Approval</span>
                </label>
                <small>Ask for permission before saving new trajectories as tools</small>
              </div>

              <div className="form-group" style={{ marginTop: "24px" }}>
                <label style={{ fontSize: "14px", fontWeight: 600, marginBottom: "12px", display: "block" }}>
                  Saved Trajectories ({config.saveAndReuse.savedTrajectories.length})
                </label>
                {config.saveAndReuse.savedTrajectories.length === 0 ? (
                  <div className="policies-empty">
                    No saved trajectories yet. When enabled, successful task patterns will appear here.
                  </div>
                ) : (
                  <div className="policies-list" style={{ maxHeight: "400px", overflowY: "auto" }}>
                    {config.saveAndReuse.savedTrajectories.map((trajectory) => {
                      const isExpanded = expandedTrajectory === trajectory.id;
                      return (
                        <div key={trajectory.id} className="policy-item" style={{ flexDirection: "column", alignItems: "stretch" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px" }}>
                            <input
                              type="checkbox"
                              checked={trajectory.enabled}
                              onChange={() => toggleTrajectory(trajectory.id)}
                              disabled={!config.saveAndReuse.enabled || config.disableMemory}
                              style={{ cursor: "pointer" }}
                            />
                            <div 
                              style={{ flex: 1, cursor: "pointer" }}
                              onClick={() => setExpandedTrajectory(isExpanded ? null : trajectory.id)}
                            >
                              <div style={{ fontWeight: 600, fontSize: "13px", color: "#1e293b" }}>
                                {trajectory.name}
                              </div>
                              <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>
                                {trajectory.description}
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: "4px", alignItems: "center", fontSize: "11px", color: "#64748b" }}>
                              <span>Confidence: {trajectory.confidence}%</span>
                              <span>•</span>
                              <span>{trajectory.parameters.length} params</span>
                            </div>
                            <button
                              className="remove-btn"
                              onClick={() => deleteTrajectory(trajectory.id)}
                              disabled={!config.saveAndReuse.enabled || config.disableMemory}
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                          
                          {isExpanded && (
                            <div style={{ 
                              padding: "12px", 
                              background: "#f8fafc", 
                              borderTop: "1px solid #e5e7eb",
                              fontSize: "12px"
                            }}>
                              <div style={{ marginBottom: "12px" }}>
                                <strong style={{ color: "#475569", display: "block", marginBottom: "6px" }}>
                                  Parameters:
                                </strong>
                                {trajectory.parameters.length === 0 ? (
                                  <div style={{ color: "#94a3b8", fontStyle: "italic" }}>No parameters</div>
                                ) : (
                                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                                    {trajectory.parameters.map((param, idx) => (
                                      <div key={idx} style={{ 
                                        background: "white", 
                                        padding: "6px 8px", 
                                        borderRadius: "4px",
                                        border: "1px solid #e5e7eb"
                                      }}>
                                        <code style={{ 
                                          color: "#667eea", 
                                          fontWeight: 600,
                                          fontSize: "11px"
                                        }}>
                                          {param.name}
                                        </code>
                                        <span style={{ color: "#64748b" }}>: {param.type}</span>
                                        {param.required && (
                                          <span style={{ color: "#ef4444", marginLeft: "4px" }}>*</span>
                                        )}
                                        {param.description && (
                                          <div style={{ 
                                            color: "#64748b", 
                                            fontSize: "11px", 
                                            marginTop: "2px" 
                                          }}>
                                            {param.description}
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                              <div style={{ display: "flex", gap: "16px", fontSize: "11px", color: "#64748b" }}>
                                <span>Min interactions: {trajectory.minInteractions}</span>
                                <span>•</span>
                                <span>Created: {new Date(trajectory.createdAt).toLocaleDateString()}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
                <small style={{ display: "block", marginTop: "8px" }}>
                  Trajectories are automatically learned from successful task completions
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


