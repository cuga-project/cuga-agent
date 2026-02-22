import React, { useState, useEffect } from "react";
import { X, Plus, Trash2, Save, Upload, Download } from "lucide-react";
import yaml from "js-yaml";
import "./ConfigModal.css";

interface Service {
  url: string;
  description: string;
}

interface McpServer {
  command?: string;
  args?: string[];
  url?: string;
  transport?: string;
  description?: string;
  env?: Record<string, string>;
}

interface App {
  name: string;
  description: string;
  url: string;
  type: string;
}

interface Tool {
  name: string;
  description: string;
}

interface ToolsConfigData {
  services?: Record<string, Service>[];
  mcpServers?: Record<string, McpServer>;
  apps?: App[];
  appTools?: Record<string, Tool[]>;
}

interface ToolsConfigProps {
  onClose: () => void;
}

export default function ToolsConfig({ onClose }: ToolsConfigProps) {
  const [configData, setConfigData] = useState<ToolsConfigData>({
    services: [],
    mcpServers: {},
    apps: [],
    appTools: {}
  });
  const [activeTab, setActiveTab] = useState<"apps" | "services" | "mcpServers">("apps");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      // Load tools config (includes services and mcpServers)
      const configResponse = await fetch('/api/config/tools');
      let configData: ToolsConfigData = {
        services: [],
        mcpServers: {},
        apps: [],
        appTools: {}
      };

      if (configResponse.ok) {
        const toolsConfig = await configResponse.json();
        configData = { 
          ...configData, 
          services: toolsConfig.services || [],
          mcpServers: toolsConfig.mcpServers || {}
        };
        console.log('Loaded tools config:', toolsConfig);
      }

      // Load available apps
      const appsResponse = await fetch('/api/apps');
      if (appsResponse.ok) {
        const appsData = await appsResponse.json();
        const apps: App[] = appsData.apps || [];
        configData.apps = apps;

        // Load tools for each app
        const appTools: Record<string, Tool[]> = {};
        for (const app of apps) {
          try {
            const toolsResponse = await fetch(`/api/apps/${app.name}/tools`);
            if (toolsResponse.ok) {
              const toolsData = await toolsResponse.json();
              appTools[app.name] = toolsData.tools || [];
            }
          } catch (error) {
            console.warn(`Failed to load tools for app ${app.name}:`, error);
            appTools[app.name] = [];
          }
        }
        configData.appTools = appTools;
      }

      console.log('Final config data:', configData);
      setConfigData(configData);
    } catch (error) {
      console.error("Error loading config:", error);
    } finally {
      setLoading(false);
    }
  };

  const saveConfig = async () => {
    setSaveStatus("saving");
    try {
      const response = await fetch('/api/config/tools', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(configData),
      });
      
      if (response.ok) {
        setSaveStatus("success");
        setTimeout(() => setSaveStatus("idle"), 2000);
      } else {
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (error) {
      console.error("Error saving config:", error);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  const addMcpServer = () => {
    const serverName = prompt("Enter MCP server name:");
    if (serverName && !configData.mcpServers?.[serverName]) {
      setConfigData({
        ...configData,
        mcpServers: {
          ...configData.mcpServers,
          [serverName]: {
            command: "uv",
            args: [],
            transport: "stdio",
            description: "",
            env: {}
          }
        }
      });
    }
  };

  const removeMcpServer = (serverName: string) => {
    const { [serverName]: removed, ...rest } = configData.mcpServers || {};
    setConfigData({
      ...configData,
      mcpServers: rest
    });
  };

  const updateMcpServer = (serverName: string, field: string, value: any) => {
    setConfigData({
      ...configData,
      mcpServers: {
        ...configData.mcpServers,
        [serverName]: {
          ...configData.mcpServers?.[serverName],
          [field]: value
        }
      }
    });
  };

  const addArg = (serverName: string) => {
    const currentServer = configData.mcpServers?.[serverName];
    const newArgs = [...(currentServer?.args || []), ""];
    updateMcpServer(serverName, "args", newArgs);
  };

  const updateArg = (serverName: string, index: number, value: string) => {
    const currentServer = configData.mcpServers?.[serverName];
    const newArgs = [...(currentServer?.args || [])];
    newArgs[index] = value;
    updateMcpServer(serverName, "args", newArgs);
  };

  const removeArg = (serverName: string, index: number) => {
    const currentServer = configData.mcpServers?.[serverName];
    const newArgs = (currentServer?.args || []).filter((_, i) => i !== index);
    updateMcpServer(serverName, "args", newArgs);
  };

  const addEnvVar = (serverName: string) => {
    const key = prompt("Enter environment variable name:");
    if (key) {
      const currentServer = configData.mcpServers?.[serverName];
      updateMcpServer(serverName, "env", {
        ...(currentServer?.env || {}),
        [key]: ""
      });
    }
  };

  const updateEnvVar = (serverName: string, key: string, value: string) => {
    const currentServer = configData.mcpServers?.[serverName];
    updateMcpServer(serverName, "env", {
      ...(currentServer?.env || {}),
      [key]: value
    });
  };

  const removeEnvVar = (serverName: string, key: string) => {
    const currentServer = configData.mcpServers?.[serverName];
    const { [key]: removed, ...rest } = currentServer?.env || {};
    updateMcpServer(serverName, "env", rest);
  };

  const exportConfig = () => {
    const yamlStr = yaml.dump(configData);
    const blob = new Blob([yamlStr], { type: 'text/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'mcp_servers_config.yaml';
    a.click();
    URL.revokeObjectURL(url);
  };

  const importConfig = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.yaml,.yml';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (file) {
        const text = await file.text();
        try {
          const data = yaml.load(text) as ToolsConfigData;
          setConfigData(data);
        } catch (error) {
          alert('Failed to parse YAML file');
        }
      }
    };
    input.click();
  };

  return (
    <div className="config-modal-overlay" onClick={onClose}>
      <div className="config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="config-modal-header">
          <h2>Tools Configuration</h2>
          <button className="config-modal-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="config-modal-tabs">
          <button
            className={`config-tab ${activeTab === "apps" ? "active" : ""}`}
            onClick={() => setActiveTab("apps")}
          >
            Apps & Tools
          </button>
          <button
            className={`config-tab ${activeTab === "mcpServers" ? "active" : ""}`}
            onClick={() => setActiveTab("mcpServers")}
          >
            MCP Servers
          </button>
          <button
            className={`config-tab ${activeTab === "services" ? "active" : ""}`}
            onClick={() => setActiveTab("services")}
          >
            Services
          </button>
        </div>

        <div className="config-modal-toolbar">
          <button className="toolbar-btn" onClick={importConfig} disabled title="Import disabled">
            <Upload size={14} />
            Import YAML
          </button>
          <button className="toolbar-btn" onClick={exportConfig} disabled title="Export disabled">
            <Download size={14} />
            Export YAML
          </button>
        </div>

        <div className="config-modal-content">
          {activeTab === "apps" && (
            <div className="apps-section">
              <div className="section-header">
                <h3>Available Apps & Tools</h3>
                {loading && <span className="loading-text">Loading...</span>}
              </div>

              {(configData.apps || []).length === 0 && !loading ? (
                <div className="empty-state">
                  <p>No apps available. Make sure the registry service is running.</p>
                </div>
              ) : (
                <div className="apps-grid">
                  {(configData.apps || []).map((app) => (
                    <div key={app.name} className="app-card">
                      <div className="app-header">
                        <h4>{app.name}</h4>
                        <span className={`app-type ${app.type}`}>{app.type}</span>
                      </div>
                      <p className="app-description">{app.description || "No description available"}</p>
                      {app.url && (
                        <p className="app-url">{app.url}</p>
                      )}

                      <div className="app-tools">
                        <h5>Available Tools ({(configData.appTools?.[app.name] || []).length})</h5>
                        {(configData.appTools?.[app.name] || []).length === 0 ? (
                          <p className="no-tools">No tools available</p>
                        ) : (
                          <div className="tools-list">
                            {(configData.appTools?.[app.name] || []).map((tool, index) => (
                              <div key={index} className="tool-item">
                                <span className="tool-name">{tool.name}</span>
                                <span className="tool-description">{tool.description || "No description"}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === "mcpServers" && (
            <div className="mcp-servers-section">
              <div className="section-header">
                <h3>MCP Servers</h3>
                <button className="add-btn" onClick={addMcpServer} disabled title="Add server disabled">
                  <Plus size={16} />
                  Add Server
                </button>
              </div>

              {Object.entries(configData.mcpServers || {}).map(([serverName, server]) => (
                <div key={serverName} className="config-card">
                  <div className="config-card-header">
                    <h4>{serverName}</h4>
                    <button
                      className="delete-btn"
                      onClick={() => removeMcpServer(serverName)}
                      disabled
                      title="Delete disabled"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>

                  <div className="config-form">
                    <div className="form-group">
                      <label>Description</label>
                      <textarea
                        value={server.description || ""}
                        onChange={(e) => updateMcpServer(serverName, "description", e.target.value)}
                        rows={2}
                        placeholder="Server description..."
                        disabled
                      />
                    </div>

                    <div className="form-row">
                      <div className="form-group">
                        <label>Command</label>
                        <input
                          type="text"
                          value={server.command || ""}
                          onChange={(e) => updateMcpServer(serverName, "command", e.target.value)}
                          placeholder="e.g., uv, python, node"
                          disabled
                        />
                      </div>

                      <div className="form-group">
                        <label>Transport</label>
                        <select
                          value={server.transport || "stdio"}
                          onChange={(e) => updateMcpServer(serverName, "transport", e.target.value)}
                          disabled
                        >
                          <option value="stdio">stdio</option>
                          <option value="sse">sse</option>
                          <option value="http">http</option>
                        </select>
                      </div>
                    </div>

                    {server.transport !== "sse" && server.transport !== "http" && (
                      <div className="form-group">
                        <div className="form-group-header">
                          <label>Arguments</label>
                          <button className="add-small-btn" onClick={() => addArg(serverName)} disabled title="Add argument disabled">
                            <Plus size={12} />
                            Add Arg
                          </button>
                        </div>
                        <div className="args-list">
                          {(server.args || []).map((arg, index) => (
                            <div key={index} className="arg-item">
                              <input
                                type="text"
                                value={arg}
                                onChange={(e) => updateArg(serverName, index, e.target.value)}
                                placeholder="Argument"
                                disabled
                              />
                              <button
                                className="remove-btn"
                                onClick={() => removeArg(serverName, index)}
                                disabled
                                title="Remove disabled"
                              >
                                <X size={14} />
                              </button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {(server.transport === "sse" || server.transport === "http") && (
                      <div className="form-group">
                        <label>URL</label>
                        <input
                          type="text"
                          value={server.url || ""}
                          onChange={(e) => updateMcpServer(serverName, "url", e.target.value)}
                          placeholder={server.transport === "http" ? "https://example.com/mcp" : "http://localhost:8000/sse"}
                          disabled
                        />
                      </div>
                    )}

                    <div className="form-group">
                      <div className="form-group-header">
                        <label>Environment Variables</label>
                        <button className="add-small-btn" onClick={() => addEnvVar(serverName)} disabled title="Add environment variable disabled">
                          <Plus size={12} />
                          Add Env
                        </button>
                      </div>
                      <div className="env-list">
                        {Object.entries(server.env || {}).map(([key, value]) => (
                          <div key={key} className="env-item">
                            <span className="env-key">{key}</span>
                            <input
                              type="text"
                              value={value}
                              onChange={(e) => updateEnvVar(serverName, key, e.target.value)}
                              placeholder="Value"
                              disabled
                            />
                            <button
                              className="remove-btn"
                              onClick={() => removeEnvVar(serverName, key)}
                              disabled
                              title="Remove disabled"
                            >
                              <X size={14} />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              ))}

              {Object.keys(configData.mcpServers || {}).length === 0 && (
                <div className="empty-state">
                  <p>No MCP servers configured. Click "Add Server" to get started.</p>
                </div>
              )}
            </div>
          )}

          {activeTab === "services" && (
            <div className="services-section">
              <div className="section-header">
                <h3>OpenAPI Services</h3>
                {loading && <span className="loading-text">Loading...</span>}
              </div>

              {(configData.services || []).length === 0 && !loading ? (
                <div className="empty-state">
                  <p>No services configured. Services are defined in the YAML configuration file.</p>
                </div>
              ) : (
                <div className="services-list">
                  {(configData.services || []).map((serviceObj, index) => {
                    // Each service is an object with one key (the service name)
                    const serviceName = Object.keys(serviceObj)[0];
                    const service = serviceObj[serviceName];
                    
                    return (
                      <div key={index} className="config-card">
                        <div className="config-card-header">
                          <h4>{serviceName}</h4>
                          <span className="service-badge">OpenAPI</span>
                        </div>
                        
                        <div className="config-form">
                          <div className="form-group">
                            <label>Description</label>
                            <p className="service-description">{service.description || "No description available"}</p>
                          </div>
                          
                          <div className="form-group">
                            <label>OpenAPI URL</label>
                            <p className="service-url">{service.url}</p>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="config-modal-footer">
          <button className="cancel-btn" onClick={onClose}>
            Close
          </button>
          <button 
            className={`save-btn ${saveStatus}`}
            onClick={saveConfig}
            disabled
            title="Save disabled - read-only mode"
          >
            <Save size={16} />
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
}


