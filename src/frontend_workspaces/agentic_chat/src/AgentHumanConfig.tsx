import React, { useState, useEffect } from "react";
import { X, Save, Plus, Trash2, UserCog, Zap, Users as UsersIcon, Shield } from "lucide-react";
import { apiFetch } from "../../frontend/src/api";
import "./ConfigModal.css";

interface HumanInterventionRule {
  id: string;
  condition: string;
  enabled: boolean;
}

interface AgentHumanConfigData {
  autonomyLevel: 1 | 2 | 3;
  humanInTheLoop: boolean;
  requireConfirmationFor: {
    approvalOfPlans: boolean;
    criticalActions: boolean;
    financialTransactions: boolean;
    dataModification: boolean;
    externalCommunication: boolean;
    longRunningTasks: boolean;
  };
  interventionRules: HumanInterventionRule[];
  clarificationThreshold: number;
  autoApproveSimpleTasks: boolean;
  escalationEnabled: boolean;
  adaptiveLearning: {
    enabled: boolean;
    startWithHighOversight: boolean;
    learningRate: number;
    confidenceThreshold: number;
    memoryBased: boolean;
    trackSuccessRate: boolean;
    minInteractionsBeforeLearning: number;
  };
}

interface AgentHumanConfigProps {
  onClose: () => void;
}

export default function AgentHumanConfig({ onClose }: AgentHumanConfigProps) {
  const [config, setConfig] = useState<AgentHumanConfigData>({
    autonomyLevel: 2,
    humanInTheLoop: true,
    requireConfirmationFor: {
      approvalOfPlans: true,
      criticalActions: true,
      financialTransactions: true,
      dataModification: false,
      externalCommunication: true,
      longRunningTasks: false,
    },
    interventionRules: [],
    clarificationThreshold: 70,
    autoApproveSimpleTasks: true,
    escalationEnabled: true,
    adaptiveLearning: {
      enabled: false,
      startWithHighOversight: true,
      learningRate: 50,
      confidenceThreshold: 85,
      memoryBased: true,
      trackSuccessRate: true,
      minInteractionsBeforeLearning: 10,
    },
  });
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [newRule, setNewRule] = useState("");

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await apiFetch('/api/config/agent-human');
      if (response.ok) {
        const data = await response.json();
        setConfig({
          autonomyLevel: (data.autonomyLevel as 1 | 2 | 3) ?? 2,
          humanInTheLoop: data.humanInTheLoop ?? true,
          requireConfirmationFor: data.requireConfirmationFor ?? {
            approvalOfPlans: true,
            criticalActions: true,
            financialTransactions: true,
            dataModification: false,
            externalCommunication: true,
            longRunningTasks: false,
          },
          interventionRules: data.interventionRules ?? [],
          clarificationThreshold: data.clarificationThreshold ?? 70,
          autoApproveSimpleTasks: data.autoApproveSimpleTasks ?? true,
          escalationEnabled: data.escalationEnabled ?? true,
          adaptiveLearning: data.adaptiveLearning ?? {
            enabled: false,
            startWithHighOversight: true,
            learningRate: 50,
            confidenceThreshold: 85,
            memoryBased: true,
            trackSuccessRate: true,
            minInteractionsBeforeLearning: 10,
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
      const response = await apiFetch('/api/config/agent-human', {
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

  const addRule = () => {
    if (newRule.trim()) {
      const rule: HumanInterventionRule = {
        id: Date.now().toString(),
        condition: newRule.trim(),
        enabled: true,
      };
      setConfig({
        ...config,
        interventionRules: [...config.interventionRules, rule],
      });
      setNewRule("");
    }
  };

  const removeRule = (id: string) => {
    setConfig({
      ...config,
      interventionRules: config.interventionRules.filter(r => r.id !== id),
    });
  };

  const toggleRule = (id: string) => {
    setConfig({
      ...config,
      interventionRules: config.interventionRules.map(r =>
        r.id === id ? { ...r, enabled: !r.enabled } : r
      ),
    });
  };

  const getAutonomyLabel = (level: 1 | 2 | 3) => {
    switch (level) {
      case 1: return "Safe - Always Asks";
      case 2: return "Balanced - Sometimes Asks";
      case 3: return "Risky - Rarely Asks";
      default: return "Balanced - Sometimes Asks";
    }
  };

  const getAutonomyColor = (level: 1 | 2 | 3) => {
    switch (level) {
      case 1: return "#10b981"; // Green for safest (always asks)
      case 2: return "#f59e0b"; // Orange for moderate
      case 3: return "#ef4444"; // Red for riskiest (rarely asks)
      default: return "#f59e0b";
    }
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Agent / Human Interaction</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-content">
          <div className="config-card">
            <h3>Autonomy Level</h3>
            <div className="config-form">
              <div className="form-group">
                <div className="autonomy-slider-container">
                  <div className="autonomy-icons">
                    <UsersIcon size={24} color={config.autonomyLevel === 1 ? getAutonomyColor(config.autonomyLevel) : "#cbd5e1"} />
                    <Zap size={24} color={config.autonomyLevel === 3 ? getAutonomyColor(config.autonomyLevel) : "#cbd5e1"} />
                  </div>
                  <div className="autonomy-label-display">
                    <span className="autonomy-value" style={{ color: getAutonomyColor(config.autonomyLevel) }}>
                      Level {config.autonomyLevel}
                    </span>
                    <span className="autonomy-description">
                      {getAutonomyLabel(config.autonomyLevel)}
                    </span>
                  </div>
                  <input
                    type="range"
                    min="1"
                    max="3"
                    step="1"
                    value={config.autonomyLevel}
                    onChange={(e) => setConfig({ ...config, autonomyLevel: parseInt(e.target.value) as 1 | 2 | 3 })}
                    className="autonomy-slider"
                    style={{
                      background: `linear-gradient(to right, ${getAutonomyColor(config.autonomyLevel)} 0%, ${getAutonomyColor(config.autonomyLevel)} ${(config.autonomyLevel - 1) * 50}%, #e5e7eb ${(config.autonomyLevel - 1) * 50}%, #e5e7eb 100%)`
                    }}
                  />
                  <div className="safety-indicator" style={{ 
                    display: "flex", 
                    justifyContent: "space-between", 
                    alignItems: "center",
                    marginTop: "8px",
                    marginBottom: "4px",
                    padding: "0 4px"
                  }}>
                    <div className="safety-text" style={{ 
                      display: "flex", 
                      alignItems: "center", 
                      gap: "4px",
                      color: "#10b981" 
                    }}>
                      <Shield size={14} />
                      <strong>Safe</strong>
                    </div>
                    <div className="safety-text" style={{ 
                      display: "flex", 
                      alignItems: "center", 
                      gap: "4px",
                      color: "#f59e0b" 
                    }}>
                      <strong>Caution</strong>
                    </div>
                    <div className="safety-text" style={{ 
                      display: "flex", 
                      alignItems: "center", 
                      gap: "4px",
                      color: "#ef4444" 
                    }}>
                      <strong>Risky</strong>
                    </div>
                  </div>
                  <div className="autonomy-markers">
                    <span>Level 1</span>
                    <span>Level 2</span>
                    <span>Level 3</span>
                  </div>
                </div>
                <small>Slide left for maximum safety (agent always asks) or right for higher risk but faster results</small>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.humanInTheLoop}
                    onChange={(e) => setConfig({ ...config, humanInTheLoop: e.target.checked })}
                  />
                  <span>Enable Human-in-the-Loop</span>
                </label>
                <small>Allow human oversight and intervention during agent execution</small>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={config.autoApproveSimpleTasks}
                      onChange={(e) => setConfig({ ...config, autoApproveSimpleTasks: e.target.checked })}
                      disabled={!config.humanInTheLoop}
                    />
                    <span>Auto-Approve Simple Tasks</span>
                  </label>
                  <small>Skip confirmation for low-risk operations</small>
                </div>
                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={config.escalationEnabled}
                      onChange={(e) => setConfig({ ...config, escalationEnabled: e.target.checked })}
                      disabled={!config.humanInTheLoop}
                    />
                    <span>Enable Escalation</span>
                  </label>
                  <small>Agent can escalate complex issues to human</small>
                </div>
              </div>
            </div>
          </div>

          <div className="config-card">
            <h3>Require Confirmation For</h3>
            <div className="config-form">
              <div className="confirmation-grid">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.requireConfirmationFor.approvalOfPlans}
                    onChange={(e) => setConfig({
                      ...config,
                      requireConfirmationFor: { ...config.requireConfirmationFor, approvalOfPlans: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <div>
                    <span>Approval of Plans</span>
                    <small>Agent must get approval before executing task plans</small>
                  </div>
                </label>

                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.requireConfirmationFor.criticalActions}
                    onChange={(e) => setConfig({
                      ...config,
                      requireConfirmationFor: { ...config.requireConfirmationFor, criticalActions: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <div>
                    <span>Critical Actions</span>
                    <small>Deletions, system changes, irreversible operations</small>
                  </div>
                </label>

                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.requireConfirmationFor.financialTransactions}
                    onChange={(e) => setConfig({
                      ...config,
                      requireConfirmationFor: { ...config.requireConfirmationFor, financialTransactions: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <div>
                    <span>Financial Transactions</span>
                    <small>Payments, purchases, billing operations</small>
                  </div>
                </label>

                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.requireConfirmationFor.dataModification}
                    onChange={(e) => setConfig({
                      ...config,
                      requireConfirmationFor: { ...config.requireConfirmationFor, dataModification: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <div>
                    <span>Data Modification</span>
                    <small>Editing, updating, or modifying existing data</small>
                  </div>
                </label>

                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.requireConfirmationFor.externalCommunication}
                    onChange={(e) => setConfig({
                      ...config,
                      requireConfirmationFor: { ...config.requireConfirmationFor, externalCommunication: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <div>
                    <span>External Communication</span>
                    <small>Sending emails, messages, or external API calls</small>
                  </div>
                </label>

                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.requireConfirmationFor.longRunningTasks}
                    onChange={(e) => setConfig({
                      ...config,
                      requireConfirmationFor: { ...config.requireConfirmationFor, longRunningTasks: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <div>
                    <span>Long-Running Tasks</span>
                    <small>Tasks estimated to take more than 5 minutes</small>
                  </div>
                </label>
              </div>
            </div>
          </div>

          <div className="config-card">
            <div className="section-header">
              <h3>Return to Human When...</h3>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <input
                  type="text"
                  value={newRule}
                  onChange={(e) => setNewRule(e.target.value)}
                  onKeyPress={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addRule();
                    }
                  }}
                  placeholder="e.g., encountering sensitive data"
                  disabled={!config.humanInTheLoop}
                  style={{ width: "300px", padding: "6px 10px", fontSize: "13px" }}
                />
                <button
                  className="add-small-btn"
                  onClick={addRule}
                  disabled={!config.humanInTheLoop || !newRule.trim()}
                >
                  <Plus size={12} />
                  Add Rule
                </button>
              </div>
            </div>

            {config.interventionRules.length === 0 ? (
              <div className="policies-empty">
                No intervention rules defined. Add conditions when the agent should return control to a human.
              </div>
            ) : (
              <div className="intervention-rules-list">
                {config.interventionRules.map((rule) => (
                  <div key={rule.id} className="intervention-rule-item">
                    <input
                      type="checkbox"
                      checked={rule.enabled}
                      onChange={() => toggleRule(rule.id)}
                      disabled={!config.humanInTheLoop}
                    />
                    <span className={`rule-text ${!rule.enabled ? 'disabled' : ''}`}>
                      {rule.condition}
                    </span>
                    <button
                      className="remove-btn"
                      onClick={() => removeRule(rule.id)}
                      disabled={!config.humanInTheLoop}
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <small>Define specific scenarios when the agent should pause and request human input</small>
          </div>

          <div className="config-card">
            <h3>Adaptive Learning</h3>
            <div className="config-form">
              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.adaptiveLearning.enabled}
                    onChange={(e) => setConfig({
                      ...config,
                      adaptiveLearning: { ...config.adaptiveLearning, enabled: e.target.checked }
                    })}
                    disabled={!config.humanInTheLoop}
                  />
                  <span>Enable Adaptive Learning</span>
                </label>
                <small>Agent learns from interactions and adjusts autonomy over time</small>
              </div>

              <div className="adaptive-learning-info">
                <p className="info-text">
                  With adaptive learning, the agent starts cautious and gradually becomes more autonomous 
                  as it learns from successful interactions and builds confidence through memory.
                </p>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={config.adaptiveLearning.startWithHighOversight}
                    onChange={(e) => setConfig({
                      ...config,
                      adaptiveLearning: { ...config.adaptiveLearning, startWithHighOversight: e.target.checked }
                    })}
                    disabled={!config.adaptiveLearning.enabled || !config.humanInTheLoop}
                  />
                  <span>Start with High Oversight</span>
                </label>
                <small>Agent asks frequently at first, then reduces questions as it learns</small>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={config.adaptiveLearning.memoryBased}
                      onChange={(e) => setConfig({
                        ...config,
                        adaptiveLearning: { ...config.adaptiveLearning, memoryBased: e.target.checked }
                      })}
                      disabled={!config.adaptiveLearning.enabled || !config.humanInTheLoop}
                    />
                    <span>Memory-Based Learning</span>
                  </label>
                  <small>Use past interactions from memory to inform decisions</small>
                </div>

                <div className="form-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={config.adaptiveLearning.trackSuccessRate}
                      onChange={(e) => setConfig({
                        ...config,
                        adaptiveLearning: { ...config.adaptiveLearning, trackSuccessRate: e.target.checked }
                      })}
                      disabled={!config.adaptiveLearning.enabled || !config.humanInTheLoop}
                    />
                    <span>Track Success Rate</span>
                  </label>
                  <small>Monitor and learn from successful vs. corrected actions</small>
                </div>
              </div>

              <div className="form-group">
                <label>Min. Interactions Before Learning</label>
                <input
                  type="number"
                  value={config.adaptiveLearning.minInteractionsBeforeLearning}
                  onChange={(e) => setConfig({
                    ...config,
                    adaptiveLearning: { ...config.adaptiveLearning, minInteractionsBeforeLearning: parseInt(e.target.value) }
                  })}
                  min="1"
                  max="100"
                  disabled={!config.adaptiveLearning.enabled || !config.humanInTheLoop}
                />
                <small>Number of interactions required before agent starts adapting</small>
              </div>

              <div className="learning-examples">
                <h4>How It Works:</h4>
                <ul className="learning-bullets">
                  <li>
                    <strong>First Time:</strong> Agent asks for confirmation on most actions
                  </li>
                  <li>
                    <strong>After Success:</strong> If action succeeds and you approve, confidence increases
                  </li>
                  <li>
                    <strong>Pattern Recognition:</strong> Agent learns from similar past situations in memory
                  </li>
                  <li>
                    <strong>Gradual Autonomy:</strong> Over time, agent stops asking for familiar tasks
                  </li>
                  <li>
                    <strong>Context Aware:</strong> Still asks for new or high-risk situations
                  </li>
                </ul>
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

