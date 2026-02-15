import React, { useState, useEffect } from "react";
import { X, Save, Plus, Trash2, ChevronDown, ChevronUp } from "lucide-react";
import "./ConfigModal.css";

interface Tool {
  name: string;
  enabled: boolean;
}

interface App {
  name: string;
  description: string;
  url: string;
  type: string;
}

interface AppTool {
  name: string;
  description: string;
}

interface AssignedApp {
  appName: string;
  tools: { name: string; enabled: boolean }[];
}

type AgentSourceType = "direct" | "a2a" | "mcp";

interface AgentSourceConfig {
  type: AgentSourceType;
  url?: string;
  name?: string;
  envVars?: Record<string, string>;
  streamType?: "http" | "sse";
}

interface SubAgent {
  id: string;
  name: string;
  role: string;
  description: string;
  enabled: boolean;
  capabilities: string[];
  tools: Tool[];
  assignedApps: AssignedApp[];
  policies: string[];
  source?: AgentSourceConfig;
}

interface SubAgentsConfigData {
  mode: "supervisor" | "single";
  subAgents: SubAgent[];
  supervisorStrategy: "sequential" | "parallel" | "adaptive";
  availableTools: string[];
}

interface SubAgentsConfigProps {
  onClose: () => void;
}

export default function SubAgentsConfig({ onClose }: SubAgentsConfigProps) {
  const [config, setConfig] = useState<SubAgentsConfigData>({
    mode: "supervisor",
    subAgents: [],
    supervisorStrategy: "adaptive",
    availableTools: [],
  });
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  const [availableApps, setAvailableApps] = useState<App[]>([]);
  const [appToolsCache, setAppToolsCache] = useState<Record<string, AppTool[]>>({});
  const [loadingApps, setLoadingApps] = useState(false);
  const [showAddAgentModal, setShowAddAgentModal] = useState(false);
  const [newAgentSource, setNewAgentSource] = useState<AgentSourceType>("direct");
  const [newAgentUrl, setNewAgentUrl] = useState("");
  const [newAgentName, setNewAgentName] = useState("");
  const [newAgentEnvVars, setNewAgentEnvVars] = useState<Array<{ key: string; value: string }>>([]);
  const [newAgentStreamType, setNewAgentStreamType] = useState<"http" | "sse">("http");

  useEffect(() => {
    loadConfig();
    loadApps();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await fetch('/api/config/subagents');
      if (response.ok) {
        const data = await response.json();
        const updatedData = {
          ...data,
          subAgents: data.subAgents.map((agent: any) => ({
            ...agent,
            assignedApps: agent.assignedApps || [],
            source: agent.source || { type: "direct" },
          })),
        };
        setConfig(updatedData);
      }
    } catch (error) {
      console.error("Error loading config:", error);
    }
  };

  const loadApps = async () => {
    setLoadingApps(true);
    try {
      const response = await fetch('/api/apps');
      if (response.ok) {
        const data = await response.json();
        setAvailableApps(data.apps || []);
      }
    } catch (error) {
      console.error("Error loading apps:", error);
    } finally {
      setLoadingApps(false);
    }
  };

  const loadAppTools = async (appName: string) => {
    if (appToolsCache[appName]) {
      return appToolsCache[appName];
    }
    try {
      const response = await fetch(`/api/apps/${encodeURIComponent(appName)}/tools`);
      if (response.ok) {
        const data = await response.json();
        const tools = data.tools || [];
        setAppToolsCache((prev) => ({ ...prev, [appName]: tools }));
        return tools;
      }
    } catch (error) {
      console.error(`Error loading tools for app ${appName}:`, error);
    }
    return [];
  };

  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await fetch('/api/config/subagents', {
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

  const openAddAgentModal = () => {
    setNewAgentSource("direct");
    setNewAgentUrl("");
    setNewAgentName("");
    setNewAgentEnvVars([]);
    setNewAgentStreamType("http");
    setShowAddAgentModal(true);
  };

  const closeAddAgentModal = () => {
    setShowAddAgentModal(false);
  };

  const addEnvVar = () => {
    setNewAgentEnvVars([...newAgentEnvVars, { key: "", value: "" }]);
  };

  const updateEnvVar = (index: number, key: string, value: string) => {
    const newEnvVars = [...newAgentEnvVars];
    newEnvVars[index] = { key, value };
    setNewAgentEnvVars(newEnvVars);
  };

  const removeEnvVar = (index: number) => {
    setNewAgentEnvVars(newAgentEnvVars.filter((_, i) => i !== index));
  };

  const createAgent = () => {
    const sourceConfig: AgentSourceConfig = {
      type: newAgentSource,
    };

    if (newAgentSource === "a2a" || newAgentSource === "mcp") {
      if (newAgentSource === "a2a") {
        sourceConfig.url = newAgentUrl;
        sourceConfig.name = newAgentName;
      } else {
        sourceConfig.url = newAgentUrl;
        sourceConfig.streamType = newAgentStreamType;
      }
      
      const envVarsObj: Record<string, string> = {};
      newAgentEnvVars.forEach(env => {
        if (env.key.trim()) {
          envVarsObj[env.key.trim()] = env.value;
        }
      });
      if (Object.keys(envVarsObj).length > 0) {
        sourceConfig.envVars = envVarsObj;
      }
    }

    const newAgent: SubAgent = {
      id: Date.now().toString(),
      name: newAgentSource === "a2a" && newAgentName ? newAgentName : "New Agent",
      role: "Assistant",
      description: "",
      enabled: true,
      capabilities: [],
      tools: config.availableTools.map(tool => ({ name: tool, enabled: false })),
      assignedApps: [],
      policies: [],
      source: sourceConfig,
    };
    
    setConfig({
      ...config,
      subAgents: [...config.subAgents, newAgent],
    });
    
    closeAddAgentModal();
  };

  const assignApp = async (agentId: string, appName: string) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (!agent) return;

    if (agent.assignedApps.some(a => a.appName === appName)) {
      return;
    }

    const tools = await loadAppTools(appName);
    const newAssignedApp: AssignedApp = {
      appName,
      tools: tools.map(t => ({ name: t.name, enabled: true })),
    };

    updateAgent(agentId, {
      assignedApps: [...agent.assignedApps, newAssignedApp],
    });
  };

  const unassignApp = (agentId: string, appName: string) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      updateAgent(agentId, {
        assignedApps: agent.assignedApps.filter(a => a.appName !== appName),
      });
    }
  };

  const toggleAppTool = (agentId: string, appName: string, toolName: string) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newAssignedApps = agent.assignedApps.map(app =>
        app.appName === appName
          ? {
              ...app,
              tools: app.tools.map(t =>
                t.name === toolName ? { ...t, enabled: !t.enabled } : t
              ),
            }
          : app
      );
      updateAgent(agentId, { assignedApps: newAssignedApps });
    }
  };

  const addPolicy = (agentId: string) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      updateAgent(agentId, {
        policies: [...agent.policies, ""]
      });
    }
  };

  const updatePolicy = (agentId: string, index: number, value: string) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newPolicies = [...agent.policies];
      newPolicies[index] = value;
      updateAgent(agentId, { policies: newPolicies });
    }
  };

  const removePolicy = (agentId: string, index: number) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newPolicies = agent.policies.filter((_, i) => i !== index);
      updateAgent(agentId, { policies: newPolicies });
    }
  };

  const toggleTool = (agentId: string, toolName: string) => {
    const agent = config.subAgents.find(a => a.id === agentId);
    if (agent) {
      const newTools = agent.tools.map(t =>
        t.name === toolName ? { ...t, enabled: !t.enabled } : t
      );
      updateAgent(agentId, { tools: newTools });
    }
  };

  const updateAgent = (id: string, updates: Partial<SubAgent>) => {
    setConfig({
      ...config,
      subAgents: config.subAgents.map(agent =>
        agent.id === id ? { ...agent, ...updates } : agent
      ),
    });
  };

  const removeAgent = (id: string) => {
    setConfig({
      ...config,
      subAgents: config.subAgents.filter(agent => agent.id !== id),
    });
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Sub-Agents Configuration</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-content">
          {config.mode === "supervisor" && (
            <div className="config-card">
              <div className="section-header">
                <h3>Sub-Agents</h3>
                <button className="add-btn" onClick={openAddAgentModal}>
                  <Plus size={16} />
                  Add Agent
                </button>
              </div>

              <div className="sources-list">
                {config.subAgents.map((agent) => {
                  const isExpanded = expandedAgent === agent.id;
                  const enabledTools = agent.tools.filter(t => t.enabled).length;
                  const totalAppTools = agent.assignedApps.reduce(
                    (sum, app) => sum + app.tools.filter(t => t.enabled).length,
                    0
                  );
                  
                  return (
                    <div key={agent.id} className="agent-config-card">
                      <div className="agent-config-header">
                        <div className="agent-config-top">
                          <input
                            type="checkbox"
                            checked={agent.enabled}
                            onChange={(e) => updateAgent(agent.id, { enabled: e.target.checked })}
                          />
                          <input
                            type="text"
                            value={agent.name}
                            onChange={(e) => updateAgent(agent.id, { name: e.target.value })}
                            className="agent-config-name"
                            placeholder="Agent Name"
                          />
                          <input
                            type="text"
                            value={agent.role}
                            onChange={(e) => updateAgent(agent.id, { role: e.target.value })}
                            placeholder="Role"
                            style={{ width: "120px" }}
                          />
                          <button
                            className="expand-btn"
                            onClick={() => setExpandedAgent(isExpanded ? null : agent.id)}
                          >
                            {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                          </button>
                          <button
                            className="delete-btn"
                            onClick={() => removeAgent(agent.id)}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                        
                        {!isExpanded && (
                          <div className="agent-summary">
                            {agent.source && (
                              <span className="agent-summary-item" title={`Source: ${agent.source.type.toUpperCase()}${agent.source.url ? ` - ${agent.source.url}` : ''}`}>
                                {agent.source.type === "direct" ? "Direct" : agent.source.type === "a2a" ? "A2A" : "MCP"}
                              </span>
                            )}
                            <span className="agent-summary-item">
                              {agent.assignedApps.length} app{agent.assignedApps.length !== 1 ? 's' : ''}
                            </span>
                            <span className="agent-summary-item">
                              {totalAppTools + enabledTools} tool{(totalAppTools + enabledTools) !== 1 ? 's' : ''}
                            </span>
                            <span className="agent-summary-item">
                              {agent.policies.length} polic{agent.policies.length !== 1 ? 'ies' : 'y'}
                            </span>
                          </div>
                        )}
                      </div>

                      {isExpanded && (
                        <div className="agent-config-details">
                          {agent.source && (
                            <div className="form-group">
                              <label>Source Configuration</label>
                              <div className="source-info-card">
                                <div className="source-info-row">
                                  <strong>Type:</strong>
                                  <span>{agent.source.type === "direct" ? "Direct" : agent.source.type === "a2a" ? "A2A Protocol" : "MCP Server"}</span>
                                </div>
                                {agent.source.url && (
                                  <div className="source-info-row">
                                    <strong>URL:</strong>
                                    <span>{agent.source.url}</span>
                                  </div>
                                )}
                                {agent.source.name && (
                                  <div className="source-info-row">
                                    <strong>Name:</strong>
                                    <span>{agent.source.name}</span>
                                  </div>
                                )}
                                {agent.source.streamType && (
                                  <div className="source-info-row">
                                    <strong>Stream Type:</strong>
                                    <span>{agent.source.streamType.toUpperCase()}</span>
                                  </div>
                                )}
                                {agent.source.envVars && Object.keys(agent.source.envVars).length > 0 && (
                                  <div className="source-info-row">
                                    <strong>Environment Variables:</strong>
                                    <div className="env-vars-display">
                                      {Object.entries(agent.source.envVars).map(([key, value]) => (
                                        <div key={key} className="env-var-display-item">
                                          <code>{key}</code>
                                          <span>=</span>
                                          <code>{value}</code>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                          <div className="form-group">
                            <label>Description</label>
                            <textarea
                              value={agent.description}
                              onChange={(e) => updateAgent(agent.id, { description: e.target.value })}
                              placeholder="What this agent does..."
                              rows={2}
                            />
                          </div>

                          <div className="form-group">
                            <label>Capabilities</label>
                            <input
                              type="text"
                              value={agent.capabilities.join(", ")}
                              onChange={(e) => updateAgent(agent.id, { 
                                capabilities: e.target.value.split(",").map(c => c.trim()).filter(c => c)
                              })}
                              placeholder="research, code, planning, analysis"
                            />
                            <small>Comma-separated list of capabilities</small>
                          </div>

                          <div className="form-group">
                            <div className="form-group-header">
                              <label>Assigned Apps</label>
                              <select
                                value=""
                                onChange={(e) => {
                                  if (e.target.value) {
                                    assignApp(agent.id, e.target.value);
                                    e.target.value = "";
                                  }
                                }}
                                style={{ width: "200px", marginLeft: "auto" }}
                              >
                                <option value="">Select an app to assign...</option>
                                {availableApps
                                  .filter(app => !agent.assignedApps.some(a => a.appName === app.name))
                                  .map(app => (
                                    <option key={app.name} value={app.name}>
                                      {app.name}
                                    </option>
                                  ))}
                              </select>
                            </div>
                            {agent.assignedApps.length === 0 ? (
                              <div className="policies-empty">
                                No apps assigned. Select an app from the dropdown above.
                              </div>
                            ) : (
                              <div className="apps-list">
                                {agent.assignedApps.map((assignedApp) => {
                                  const app = availableApps.find(a => a.name === assignedApp.appName);
                                  const enabledCount = assignedApp.tools.filter(t => t.enabled).length;
                                  return (
                                    <div key={assignedApp.appName} className="app-config-section">
                                      <div className="app-config-header">
                                        <div>
                                          <strong>{assignedApp.appName}</strong>
                                          {app?.description && (
                                            <small style={{ display: "block", color: "#666", marginTop: "4px" }}>
                                              {app.description}
                                            </small>
                                          )}
                                        </div>
                                        <button
                                          className="remove-btn"
                                          onClick={() => unassignApp(agent.id, assignedApp.appName)}
                                          title="Remove app"
                                        >
                                          <X size={14} />
                                        </button>
                                      </div>
                                      <div className="app-tools-section">
                                        <div className="form-group-header" style={{ marginTop: "8px", marginBottom: "8px" }}>
                                          <label style={{ fontSize: "0.9em", margin: 0 }}>
                                            Tools ({enabledCount}/{assignedApp.tools.length} enabled)
                                          </label>
                                        </div>
                                        <div className="tools-grid">
                                          {assignedApp.tools.map((tool) => (
                                            <label key={tool.name} className="tool-checkbox-label">
                                              <input
                                                type="checkbox"
                                                checked={tool.enabled}
                                                onChange={() => toggleAppTool(agent.id, assignedApp.appName, tool.name)}
                                              />
                                              <span>{tool.name}</span>
                                            </label>
                                          ))}
                                        </div>
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            )}
                            <small>Assign apps and configure which tools from each app this agent can use</small>
                          </div>

                          <div className="form-group">
                            <div className="form-group-header">
                              <label>Legacy Tools</label>
                              <span className="tools-count-small">{enabledTools}/{agent.tools.length} enabled</span>
                            </div>
                            <div className="tools-grid">
                              {agent.tools.map((tool) => (
                                <label key={tool.name} className="tool-checkbox-label">
                                  <input
                                    type="checkbox"
                                    checked={tool.enabled}
                                    onChange={() => toggleTool(agent.id, tool.name)}
                                  />
                                  <span>{tool.name}</span>
                                </label>
                              ))}
                            </div>
                            <small>Legacy tool configuration (deprecated - use apps above)</small>
                          </div>

                          <div className="form-group">
                            <div className="form-group-header">
                              <label>Policies (Natural Language)</label>
                              <button 
                                className="add-small-btn" 
                                onClick={() => addPolicy(agent.id)}
                              >
                                <Plus size={12} />
                                Add Policy
                              </button>
                            </div>
                            <div className="policies-list">
                              {agent.policies.length === 0 ? (
                                <div className="policies-empty">
                                  No policies defined. Add policies to control agent behavior.
                                </div>
                              ) : (
                                agent.policies.map((policy, index) => (
                                  <div key={index} className="policy-item">
                                    <textarea
                                      value={policy}
                                      onChange={(e) => updatePolicy(agent.id, index, e.target.value)}
                                      placeholder="e.g., Always verify information from multiple sources before making decisions"
                                      rows={2}
                                    />
                                    <button
                                      className="remove-btn"
                                      onClick={() => removePolicy(agent.id, index)}
                                    >
                                      <X size={14} />
                                    </button>
                                  </div>
                                ))
                              )}
                            </div>
                            <small>Define behavior rules in plain English</small>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {config.subAgents.length === 0 && (
                <div className="empty-state">
                  <p>No sub-agents configured. Click "Add Agent" to create one.</p>
                </div>
              )}
            </div>
          )}
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

      {showAddAgentModal && (
        <div className="config-modal-overlay" onClick={closeAddAgentModal}>
          <div className="config-modal add-agent-modal" onClick={(e) => e.stopPropagation()}>
            <div className="config-modal-header">
              <h2>Add New Sub-Agent</h2>
              <button className="config-modal-close" onClick={closeAddAgentModal}>
                <X size={20} />
              </button>
            </div>

            <div className="config-modal-content">
              <div className="config-card">
                <h3>Agent Source</h3>
                <div className="config-form">
                  <div className="form-group">
                    <label>How to create this agent?</label>
                    <select
                      value={newAgentSource}
                      onChange={(e) => setNewAgentSource(e.target.value as AgentSourceType)}
                    >
                      <option value="direct">Direct (Local Agent)</option>
                      <option value="a2a">A2A Protocol</option>
                      <option value="mcp">MCP Server</option>
                    </select>
                    <small>
                      {newAgentSource === "direct" && "Create a local agent directly"}
                      {newAgentSource === "a2a" && "Connect via A2A protocol"}
                      {newAgentSource === "mcp" && "Connect to an MCP server via HTTP or SSE"}
                    </small>
                  </div>

                  {newAgentSource === "a2a" && (
                    <>
                      <div className="form-group">
                        <label>Agent Name</label>
                        <input
                          type="text"
                          value={newAgentName}
                          onChange={(e) => setNewAgentName(e.target.value)}
                          placeholder="e.g., research-agent"
                        />
                        <small>Name identifier for the A2A agent</small>
                      </div>
                      <div className="form-group">
                        <label>URL</label>
                        <input
                          type="text"
                          value={newAgentUrl}
                          onChange={(e) => setNewAgentUrl(e.target.value)}
                          placeholder="e.g., http://localhost:8080"
                        />
                        <small>A2A protocol endpoint URL</small>
                      </div>
                    </>
                  )}

                  {newAgentSource === "mcp" && (
                    <>
                      <div className="form-group">
                        <label>MCP Server URL</label>
                        <input
                          type="text"
                          value={newAgentUrl}
                          onChange={(e) => setNewAgentUrl(e.target.value)}
                          placeholder="e.g., http://localhost:8001"
                        />
                        <small>MCP server endpoint URL</small>
                      </div>
                      <div className="form-group">
                        <label>Stream Type</label>
                        <select
                          value={newAgentStreamType}
                          onChange={(e) => setNewAgentStreamType(e.target.value as "http" | "sse")}
                        >
                          <option value="http">HTTP (Streamable)</option>
                          <option value="sse">SSE (Server-Sent Events)</option>
                        </select>
                        <small>Communication protocol for MCP server</small>
                      </div>
                    </>
                  )}

                  {(newAgentSource === "a2a" || newAgentSource === "mcp") && (
                    <div className="form-group">
                      <div className="form-group-header">
                        <label>Environment Variables</label>
                        <button className="add-small-btn" onClick={addEnvVar}>
                          <Plus size={12} />
                          Add Variable
                        </button>
                      </div>
                      {newAgentEnvVars.length === 0 ? (
                        <div className="policies-empty">
                          No environment variables. Click "Add Variable" to add one.
                        </div>
                      ) : (
                        <div className="env-list">
                          {newAgentEnvVars.map((env, index) => (
                            <div key={index} className="env-item">
                              <input
                                type="text"
                                value={env.key}
                                onChange={(e) => updateEnvVar(index, e.target.value, env.value)}
                                placeholder="Variable name"
                                style={{ width: "200px" }}
                              />
                              <span>=</span>
                              <input
                                type="text"
                                value={env.value}
                                onChange={(e) => updateEnvVar(index, env.key, e.target.value)}
                                placeholder="Variable value"
                                style={{ flex: 1 }}
                              />
                              <button
                                className="remove-btn"
                                onClick={() => removeEnvVar(index)}
                              >
                                <X size={14} />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      <small>Environment variables to pass to the agent</small>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="config-modal-footer">
              <button className="cancel-btn" onClick={closeAddAgentModal}>
                Cancel
              </button>
              <button
                className="save-btn"
                onClick={createAgent}
                disabled={
                  (newAgentSource === "a2a" && (!newAgentUrl || !newAgentName)) ||
                  (newAgentSource === "mcp" && !newAgentUrl)
                }
              >
                Create Agent
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

