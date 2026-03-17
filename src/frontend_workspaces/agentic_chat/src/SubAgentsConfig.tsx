import React, { useState, useEffect } from "react";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  TextInput,
  TextArea,
  Select,
  SelectItem,
  Checkbox,
  Toggle,
  Tile,
  Tag,
  Stack,
  InlineNotification,
  IconButton
} from "@carbon/react";
import { 
  Add, 
  Save, 
  TrashCan, 
  ChevronDown, 
  ChevronUp, 
  Close 
} from "@carbon/icons-react";
import { apiFetch } from "../../frontend/src/api";

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
      const response = await apiFetch('/api/config/subagents');
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
      const response = await apiFetch('/api/apps');
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
      const response = await apiFetch(`/api/apps/${encodeURIComponent(appName)}/tools`);
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
      const response = await apiFetch('/api/config/subagents', {
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
    const sourceConfig: AgentSourceConfig = { type: newAgentSource };

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
    if (agent.assignedApps.some(a => a.appName === appName)) return;

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
    <>
      <ComposedModal open={true} onClose={onClose} size="lg">
        <ModalHeader title="Sub-Agents Configuration" buttonOnClick={onClose} />
        <ModalBody hasForm>
          <Stack gap={6}>
            {saveStatus === "success" && (
              <InlineNotification kind="success" title="Success" subtitle="Configuration saved successfully" />
            )}
            {saveStatus === "error" && (
              <InlineNotification kind="error" title="Error" subtitle="Failed to save configuration" />
            )}

            {config.mode === "supervisor" && (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h4 className="cds--type-heading-03">Sub-Agents</h4>
                  <Button renderIcon={Add} size="sm" onClick={openAddAgentModal}>
                    Add Agent
                  </Button>
                </div>

                <Stack gap={5}>
                  {config.subAgents.map((agent) => {
                    const isExpanded = expandedAgent === agent.id;
                    const enabledTools = agent.tools.filter(t => t.enabled).length;
                    const totalAppTools = agent.assignedApps.reduce(
                      (sum, app) => sum + app.tools.filter(t => t.enabled).length,
                      0
                    );

                    return (
                      <Tile key={agent.id}>
                        <Stack gap={5}>
                          {/* Header Row */}
                          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
                            <Toggle
                              id={`toggle-${agent.id}`}
                              size="sm"
                              labelA="Disabled"
                              labelB="Enabled"
                              toggled={agent.enabled}
                              onToggle={(checked) => updateAgent(agent.id, { enabled: checked })}
                            />
                            <div style={{ flex: 1, minWidth: '200px' }}>
                              <TextInput
                                id={`name-${agent.id}`}
                                labelText="Agent Name"
                                hideLabel
                                value={agent.name}
                                placeholder="Agent Name"
                                onChange={(e) => updateAgent(agent.id, { name: e.target.value })}
                              />
                            </div>
                            <div style={{ width: '150px' }}>
                              <TextInput
                                id={`role-${agent.id}`}
                                labelText="Role"
                                hideLabel
                                value={agent.role}
                                placeholder="Role"
                                onChange={(e) => updateAgent(agent.id, { role: e.target.value })}
                              />
                            </div>
                            <IconButton
                              kind="ghost"
                              label="Expand/Collapse"
                              onClick={() => setExpandedAgent(isExpanded ? null : agent.id)}
                            >
                              {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                            </IconButton>
                            <IconButton
                              kind="danger--ghost"
                              label="Delete Agent"
                              onClick={() => removeAgent(agent.id)}
                            >
                              <TrashCan size={16} />
                            </IconButton>
                          </div>

                          {/* Collapsed Summary */}
                          {!isExpanded && (
                            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                              {agent.source && (
                                <Tag type="blue">
                                  Source: {agent.source.type.toUpperCase()}
                                </Tag>
                              )}
                              <Tag type="cyan">{agent.assignedApps.length} App(s)</Tag>
                              <Tag type="teal">{totalAppTools + enabledTools} Tool(s)</Tag>
                              <Tag type="purple">{agent.policies.length} Polic(ies)</Tag>
                            </div>
                          )}

                          {/* Expanded Details */}
                          {isExpanded && (
                            <Stack gap={6} style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid #e0e0e0' }}>
                              {agent.source && (
                                <Tile className="cds--tile--light">
                                  <h5 className="cds--type-heading-01" style={{ marginBottom: '1rem' }}>Source Configuration</h5>
                                  <Stack gap={3}>
                                    <div><strong>Type:</strong> {agent.source.type.toUpperCase()}</div>
                                    {agent.source.url && <div><strong>URL:</strong> {agent.source.url}</div>}
                                    {agent.source.name && <div><strong>Name:</strong> {agent.source.name}</div>}
                                    {agent.source.streamType && <div><strong>Stream Type:</strong> {agent.source.streamType.toUpperCase()}</div>}
                                    {agent.source.envVars && Object.keys(agent.source.envVars).length > 0 && (
                                      <div>
                                        <strong>Environment Variables:</strong>
                                        <ul style={{ paddingLeft: '1rem', listStyleType: 'disc' }}>
                                          {Object.entries(agent.source.envVars).map(([key, value]) => (
                                            <li key={key}><code>{key}</code> = <code>{value}</code></li>
                                          ))}
                                        </ul>
                                      </div>
                                    )}
                                  </Stack>
                                </Tile>
                              )}

                              <TextArea
                                id={`desc-${agent.id}`}
                                labelText="Description"
                                placeholder="What this agent does..."
                                value={agent.description}
                                onChange={(e) => updateAgent(agent.id, { description: e.target.value })}
                                rows={2}
                              />

                              <TextInput
                                id={`cap-${agent.id}`}
                                labelText="Capabilities"
                                helperText="Comma-separated list of capabilities (e.g., research, code, analysis)"
                                value={agent.capabilities.join(", ")}
                                onChange={(e) => updateAgent(agent.id, { 
                                  capabilities: e.target.value.split(",").map(c => c.trim()).filter(c => c)
                                })}
                              />

                              {/* Apps Assignment */}
                              <Stack gap={4}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
                                  <h5 className="cds--label">Assigned Apps</h5>
                                  <Select
                                    id={`assign-app-${agent.id}`}
                                    labelText="Assign an app"
                                    hideLabel
                                    inline
                                    value=""
                                    onChange={(e) => {
                                      if (e.target.value) {
                                        assignApp(agent.id, e.target.value);
                                        e.target.value = "";
                                      }
                                    }}
                                  >
                                    <SelectItem value="" text="Select an app to assign..." />
                                    {availableApps
                                      .filter(app => !agent.assignedApps.some(a => a.appName === app.name))
                                      .map(app => (
                                        <SelectItem key={app.name} value={app.name} text={app.name} />
                                      ))}
                                  </Select>
                                </div>

                                {agent.assignedApps.length === 0 ? (
                                  <p className="cds--type-helper-text">No apps assigned. Select an app from the dropdown above.</p>
                                ) : (
                                  <Stack gap={4}>
                                    {agent.assignedApps.map((assignedApp) => {
                                      const app = availableApps.find(a => a.name === assignedApp.appName);
                                      const enabledCount = assignedApp.tools.filter(t => t.enabled).length;
                                      
                                      return (
                                        <Tile key={assignedApp.appName} className="cds--tile--light">
                                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
                                            <div>
                                              <strong>{assignedApp.appName}</strong>
                                              {app?.description && <p className="cds--type-helper-text">{app.description}</p>}
                                            </div>
                                            <IconButton kind="ghost" label="Remove App" size="sm" onClick={() => unassignApp(agent.id, assignedApp.appName)}>
                                              <Close size={16} />
                                            </IconButton>
                                          </div>
                                          
                                          <p className="cds--label" style={{ marginBottom: '0.5rem' }}>
                                            Tools ({enabledCount}/{assignedApp.tools.length} enabled)
                                          </p>
                                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
                                            {assignedApp.tools.map((tool) => (
                                              <Checkbox
                                                key={tool.name}
                                                id={`tool-${agent.id}-${assignedApp.appName}-${tool.name}`}
                                                labelText={tool.name}
                                                checked={tool.enabled}
                                                onChange={(_, { checked }) => toggleAppTool(agent.id, assignedApp.appName, tool.name)}
                                              />
                                            ))}
                                          </div>
                                        </Tile>
                                      );
                                    })}
                                  </Stack>
                                )}
                              </Stack>

                              {/* Legacy Tools */}
                              <Stack gap={3}>
                                <h5 className="cds--label">Legacy Tools ({enabledTools}/{agent.tools.length} enabled)</h5>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '1rem' }}>
                                  {agent.tools.map((tool) => (
                                    <Checkbox
                                      key={tool.name}
                                      id={`legacy-${agent.id}-${tool.name}`}
                                      labelText={tool.name}
                                      checked={tool.enabled}
                                      onChange={(_, { checked }) => toggleTool(agent.id, tool.name)}
                                    />
                                  ))}
                                </div>
                                <p className="cds--type-helper-text">Legacy tool configuration (deprecated - use apps above)</p>
                              </Stack>

                              {/* Policies */}
                              <Stack gap={4}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                  <h5 className="cds--label">Policies (Natural Language)</h5>
                                  <Button kind="ghost" size="sm" renderIcon={Add} onClick={() => addPolicy(agent.id)}>
                                    Add Policy
                                  </Button>
                                </div>
                                
                                {agent.policies.length === 0 ? (
                                  <p className="cds--type-helper-text">No policies defined. Add policies to control agent behavior.</p>
                                ) : (
                                  <Stack gap={3}>
                                    {agent.policies.map((policy, index) => (
                                      <div key={index} style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
                                        <TextArea
                                          id={`policy-${agent.id}-${index}`}
                                          labelText={`Policy ${index + 1}`}
                                          hideLabel
                                          value={policy}
                                          onChange={(e) => updatePolicy(agent.id, index, e.target.value)}
                                          placeholder="e.g., Always verify information from multiple sources before making decisions"
                                          rows={2}
                                          style={{ flex: 1 }}
                                        />
                                        <IconButton kind="danger--ghost" label="Remove Policy" onClick={() => removePolicy(agent.id, index)}>
                                          <TrashCan size={16} />
                                        </IconButton>
                                      </div>
                                    ))}
                                  </Stack>
                                )}
                              </Stack>
                            </Stack>
                          )}
                        </Stack>
                      </Tile>
                    );
                  })}
                </Stack>

                {config.subAgents.length === 0 && (
                  <div style={{ textAlign: 'center', padding: '3rem', backgroundColor: '#f4f4f4' }}>
                    <p className="cds--type-body-long-01">No sub-agents configured. Click "Add Agent" to create one.</p>
                  </div>
                )}
              </>
            )}
          </Stack>
        </ModalBody>
        <ModalFooter>
          <Button kind="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button 
            kind="primary" 
            onClick={saveConfig} 
            disabled={saveStatus === "saving"}
            renderIcon={Save}
          >
            {saveStatus === "idle" && "Save Changes"}
            {saveStatus === "saving" && "Saving..."}
            {saveStatus === "success" && "Saved!"}
            {saveStatus === "error" && "Error!"}
          </Button>
        </ModalFooter>
      </ComposedModal>

      {/* Add New Agent Modal */}
      {showAddAgentModal && (
        <ComposedModal open={true} onClose={closeAddAgentModal} size="sm">
          <ModalHeader title="Add New Sub-Agent" buttonOnClick={closeAddAgentModal} />
          <ModalBody hasForm>
            <Stack gap={5}>
              <h4 className="cds--type-heading-02">Agent Source</h4>
              
              <Select
                id="agent-source-select"
                labelText="How to create this agent?"
                helperText={
                  newAgentSource === "direct" ? "Create a local agent directly" :
                  newAgentSource === "a2a" ? "Connect via A2A protocol" :
                  "Connect to an MCP server via HTTP or SSE"
                }
                value={newAgentSource}
                onChange={(e) => setNewAgentSource(e.target.value as AgentSourceType)}
              >
                <SelectItem value="direct" text="Direct (Local Agent)" />
                <SelectItem value="a2a" text="A2A Protocol" />
                <SelectItem value="mcp" text="MCP Server" />
              </Select>

              {newAgentSource === "a2a" && (
                <>
                  <TextInput
                    id="a2a-agent-name"
                    labelText="Agent Name"
                    helperText="Name identifier for the A2A agent"
                    value={newAgentName}
                    onChange={(e) => setNewAgentName(e.target.value)}
                    placeholder="e.g., research-agent"
                  />
                  <TextInput
                    id="a2a-agent-url"
                    labelText="URL"
                    helperText="A2A protocol endpoint URL"
                    value={newAgentUrl}
                    onChange={(e) => setNewAgentUrl(e.target.value)}
                    placeholder="e.g., http://localhost:8080"
                  />
                </>
              )}

              {newAgentSource === "mcp" && (
                <>
                  <TextInput
                    id="mcp-agent-url"
                    labelText="MCP Server URL"
                    helperText="MCP server endpoint URL"
                    value={newAgentUrl}
                    onChange={(e) => setNewAgentUrl(e.target.value)}
                    placeholder="e.g., http://localhost:8001"
                  />
                  <Select
                    id="mcp-stream-type"
                    labelText="Stream Type"
                    helperText="Communication protocol for MCP server"
                    value={newAgentStreamType}
                    onChange={(e) => setNewAgentStreamType(e.target.value as "http" | "sse")}
                  >
                    <SelectItem value="http" text="HTTP (Streamable)" />
                    <SelectItem value="sse" text="SSE (Server-Sent Events)" />
                  </Select>
                </>
              )}

              {(newAgentSource === "a2a" || newAgentSource === "mcp") && (
                <Stack gap={3}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h5 className="cds--label">Environment Variables</h5>
                    <Button kind="ghost" size="sm" renderIcon={Add} onClick={addEnvVar}>
                      Add Variable
                    </Button>
                  </div>
                  
                  {newAgentEnvVars.length === 0 ? (
                    <p className="cds--type-helper-text">No environment variables. Click "Add Variable" to add one.</p>
                  ) : (
                    <Stack gap={3}>
                      {newAgentEnvVars.map((env, index) => (
                        <div key={index} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                          <TextInput
                            id={`env-key-${index}`}
                            labelText={`Variable ${index} key`}
                            hideLabel
                            value={env.key}
                            onChange={(e) => updateEnvVar(index, e.target.value, env.value)}
                            placeholder="Key"
                            style={{ flex: 1 }}
                          />
                          <span>=</span>
                          <TextInput
                            id={`env-val-${index}`}
                            labelText={`Variable ${index} value`}
                            hideLabel
                            value={env.value}
                            onChange={(e) => updateEnvVar(index, env.key, e.target.value)}
                            placeholder="Value"
                            style={{ flex: 2 }}
                          />
                          <IconButton kind="danger--ghost" label="Remove Variable" onClick={() => removeEnvVar(index)}>
                            <TrashCan size={16} />
                          </IconButton>
                        </div>
                      ))}
                    </Stack>
                  )}
                </Stack>
              )}
            </Stack>
          </ModalBody>
          <ModalFooter>
            <Button kind="secondary" onClick={closeAddAgentModal}>
              Cancel
            </Button>
            <Button
              kind="primary"
              onClick={createAgent}
              disabled={
                (newAgentSource === "a2a" && (!newAgentUrl || !newAgentName)) ||
                (newAgentSource === "mcp" && !newAgentUrl)
              }
            >
              Create Agent
            </Button>
          </ModalFooter>
        </ComposedModal>
      )}
    </>
  );
}