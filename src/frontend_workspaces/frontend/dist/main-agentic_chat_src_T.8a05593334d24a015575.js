"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["main-agentic_chat_src_T"],{

/***/ "../agentic_chat/src/ToolReview.tsx":
/*!******************************************!*\
  !*** ../agentic_chat/src/ToolReview.tsx ***!
  \******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ ToolCallFlowDisplay; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");


function ToolCallFlowDisplay({
  toolData
}) {
  const toolCallData = toolData;
  const getArgIcon = (key, value) => {
    if (typeof value === "number") return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Hash, {
      className: "w-3 h-3 text-blue-500"
    });
    if (typeof value === "string") return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Type, {
      className: "w-3 h-3 text-green-500"
    });
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Database, {
      className: "w-3 h-3 text-gray-500"
    });
  };
  const formatArgValue = value => {
    if (typeof value === "string") return `"${value}"`;
    return String(value);
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-4xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg shadow-md border p-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-3 mb-4"
  }, toolCallData.name != "run_new_flow" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Shield, {
    className: "w-5 h-5 text-emerald-600"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.CheckCircle, {
    className: "w-4 h-4 text-emerald-500"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", {
    className: "text-lg font-semibold text-gray-800"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "space-y-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-gradient-to-r from-blue-50 to-indigo-50 rounded-lg p-4 border border-blue-100"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Settings, {
    className: "w-4 h-4 text-blue-600"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm font-medium text-blue-800"
  }, "Flow Name")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "font-mono text-lg font-semibold text-blue-900 bg-white px-3 py-2 rounded border"
  }, toolCallData.name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-gradient-to-r from-green-50 to-emerald-50 rounded-lg p-4 border border-green-100"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Database, {
    className: "w-4 h-4 text-green-600"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm font-medium text-green-800"
  }, "Inputs")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "space-y-2"
  }, Object.entries(toolCallData.args).map(([key, value]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: key,
    className: "bg-white rounded border p-3 flex items-center gap-3"
  }, getArgIcon(key, value), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "font-mono text-sm font-semibold text-gray-700"
  }, key, ":"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "font-mono text-sm text-gray-900 bg-gray-50 px-2 py-1 rounded"
  }, formatArgValue(value)))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded"
  }, typeof value))))), toolCallData.name != "run_new_flow" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between pt-2 border-t border-gray-100"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.CheckCircle, {
    className: "w-4 h-4 text-emerald-500"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm text-gray-600"
  }, "Verified and trusted flow")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "flex items-center gap-2 px-3 py-1.5 text-sm text-blue-600 hover:text-blue-700 hover:bg-blue-50 rounded-md transition-colors duration-200 border border-blue-200 hover:border-blue-300",
    onClick: () => {
      try {
        window.open(`${API_BASE_URL}/flows/flow.html`, "_blank");
      } catch (error) {
        alert("Local server not running. Please start your development server.");
      }
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Flow explained"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ExternalLink, {
    className: "w-3 h-3"
  })))))));
}

/***/ }),

/***/ "../agentic_chat/src/ToolsConfig.tsx":
/*!*******************************************!*\
  !*** ../agentic_chat/src/ToolsConfig.tsx ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ ToolsConfig; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var js_yaml__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! js-yaml */ "../node_modules/.pnpm/js-yaml@4.1.1/node_modules/js-yaml/dist/js-yaml.mjs");
/* harmony import */ var _ConfigModal_css__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! ./ConfigModal.css */ "../agentic_chat/src/ConfigModal.css");




function ToolsConfig({
  onClose
}) {
  const [configData, setConfigData] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    services: [],
    mcpServers: {},
    apps: [],
    appTools: {}
  });
  const [activeTab, setActiveTab] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("apps");
  const [saveStatus, setSaveStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("idle");
  const [loading, setLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadConfig();
  }, []);
  const loadConfig = async () => {
    setLoading(true);
    try {
      // Load tools config (includes services and mcpServers)
      const configResponse = await fetch('/api/config/tools');
      let configData = {
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
        const apps = appsData.apps || [];
        configData.apps = apps;

        // Load tools for each app
        const appTools = {};
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
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(configData)
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
  const removeMcpServer = serverName => {
    const {
      [serverName]: removed,
      ...rest
    } = configData.mcpServers || {};
    setConfigData({
      ...configData,
      mcpServers: rest
    });
  };
  const updateMcpServer = (serverName, field, value) => {
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
  const addArg = serverName => {
    const currentServer = configData.mcpServers?.[serverName];
    const newArgs = [...(currentServer?.args || []), ""];
    updateMcpServer(serverName, "args", newArgs);
  };
  const updateArg = (serverName, index, value) => {
    const currentServer = configData.mcpServers?.[serverName];
    const newArgs = [...(currentServer?.args || [])];
    newArgs[index] = value;
    updateMcpServer(serverName, "args", newArgs);
  };
  const removeArg = (serverName, index) => {
    const currentServer = configData.mcpServers?.[serverName];
    const newArgs = (currentServer?.args || []).filter((_, i) => i !== index);
    updateMcpServer(serverName, "args", newArgs);
  };
  const addEnvVar = serverName => {
    const key = prompt("Enter environment variable name:");
    if (key) {
      const currentServer = configData.mcpServers?.[serverName];
      updateMcpServer(serverName, "env", {
        ...(currentServer?.env || {}),
        [key]: ""
      });
    }
  };
  const updateEnvVar = (serverName, key, value) => {
    const currentServer = configData.mcpServers?.[serverName];
    updateMcpServer(serverName, "env", {
      ...(currentServer?.env || {}),
      [key]: value
    });
  };
  const removeEnvVar = (serverName, key) => {
    const currentServer = configData.mcpServers?.[serverName];
    const {
      [key]: removed,
      ...rest
    } = currentServer?.env || {};
    updateMcpServer(serverName, "env", rest);
  };
  const exportConfig = () => {
    const yamlStr = js_yaml__WEBPACK_IMPORTED_MODULE_2__["default"].dump(configData);
    const blob = new Blob([yamlStr], {
      type: 'text/yaml'
    });
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
    input.onchange = async e => {
      const file = e.target.files?.[0];
      if (file) {
        const text = await file.text();
        try {
          const data = js_yaml__WEBPACK_IMPORTED_MODULE_2__["default"].load(text);
          setConfigData(data);
        } catch (error) {
          alert('Failed to parse YAML file');
        }
      }
    };
    input.click();
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-overlay",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Tools Configuration"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-tabs"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "apps" ? "active" : ""}`,
    onClick: () => setActiveTab("apps")
  }, "Apps & Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "mcpServers" ? "active" : ""}`,
    onClick: () => setActiveTab("mcpServers")
  }, "MCP Servers"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "services" ? "active" : ""}`,
    onClick: () => setActiveTab("services")
  }, "Services")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-toolbar"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "toolbar-btn",
    onClick: importConfig,
    disabled: true,
    title: "Import disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Upload, {
    size: 14
  }), "Import YAML"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "toolbar-btn",
    onClick: exportConfig,
    disabled: true,
    title: "Export disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Download, {
    size: 14
  }), "Export YAML")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, activeTab === "apps" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "apps-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Available Apps & Tools"), loading && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "loading-text"
  }, "Loading...")), (configData.apps || []).length === 0 && !loading ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No apps available. Make sure the registry service is running.")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "apps-grid"
  }, (configData.apps || []).map(app => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: app.name,
    className: "app-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "app-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", null, app.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `app-type ${app.type}`
  }, app.type)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "app-description"
  }, app.description || "No description available"), app.url && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "app-url"
  }, app.url), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "app-tools"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h5", null, "Available Tools (", (configData.appTools?.[app.name] || []).length, ")"), (configData.appTools?.[app.name] || []).length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "no-tools"
  }, "No tools available") : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "tools-list"
  }, (configData.appTools?.[app.name] || []).map((tool, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "tool-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-name"
  }, tool.name), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tool-description"
  }, tool.description || "No description"))))))))), activeTab === "mcpServers" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mcp-servers-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "MCP Servers"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-btn",
    onClick: addMcpServer,
    disabled: true,
    title: "Add server disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 16
  }), "Add Server")), Object.entries(configData.mcpServers || {}).map(([serverName, server]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: serverName,
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", null, serverName), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "delete-btn",
    onClick: () => removeMcpServer(serverName),
    disabled: true,
    title: "Delete disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
    size: 16
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
    value: server.description || "",
    onChange: e => updateMcpServer(serverName, "description", e.target.value),
    rows: 2,
    placeholder: "Server description...",
    disabled: true
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Command"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: server.command || "",
    onChange: e => updateMcpServer(serverName, "command", e.target.value),
    placeholder: "e.g., uv, python, node",
    disabled: true
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Transport"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    value: server.transport || "stdio",
    onChange: e => updateMcpServer(serverName, "transport", e.target.value),
    disabled: true
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "stdio"
  }, "stdio"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    value: "sse"
  }, "sse")))), server.transport !== "sse" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Arguments"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-small-btn",
    onClick: () => addArg(serverName),
    disabled: true,
    title: "Add argument disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 12
  }), "Add Arg")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "args-list"
  }, (server.args || []).map((arg, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "arg-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: arg,
    onChange: e => updateArg(serverName, index, e.target.value),
    placeholder: "Argument",
    disabled: true
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "remove-btn",
    onClick: () => removeArg(serverName, index),
    disabled: true,
    title: "Remove disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 14
  })))))), server.transport === "sse" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "URL"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: server.url || "",
    onChange: e => updateMcpServer(serverName, "url", e.target.value),
    placeholder: "http://localhost:8000/sse",
    disabled: true
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Environment Variables"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "add-small-btn",
    onClick: () => addEnvVar(serverName),
    disabled: true,
    title: "Add environment variable disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
    size: 12
  }), "Add Env")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "env-list"
  }, Object.entries(server.env || {}).map(([key, value]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: key,
    className: "env-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "env-key"
  }, key), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: value,
    onChange: e => updateEnvVar(serverName, key, e.target.value),
    placeholder: "Value",
    disabled: true
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "remove-btn",
    onClick: () => removeEnvVar(serverName, key),
    disabled: true,
    title: "Remove disabled"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 14
  }))))))))), Object.keys(configData.mcpServers || {}).length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No MCP servers configured. Click \"Add Server\" to get started."))), activeTab === "services" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "services-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "section-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "OpenAPI Services"), loading && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "loading-text"
  }, "Loading...")), (configData.services || []).length === 0 && !loading ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "empty-state"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No services configured. Services are defined in the YAML configuration file.")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "services-list"
  }, (configData.services || []).map((serviceObj, index) => {
    // Each service is an object with one key (the service name)
    const serviceName = Object.keys(serviceObj)[0];
    const service = serviceObj[serviceName];
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: index,
      className: "config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-card-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h4", null, serviceName), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "service-badge"
    }, "OpenAPI")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-form"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
      className: "service-description"
    }, service.description || "No description available")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "form-group"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "OpenAPI URL"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
      className: "service-url"
    }, service.url))));
  })))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "cancel-btn",
    onClick: onClose
  }, "Close"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `save-btn ${saveStatus}`,
    onClick: saveConfig,
    disabled: true,
    title: "Save disabled - read-only mode"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Save, {
    size: 16
  }), "Save Changes"))));
}

/***/ }),

/***/ "../agentic_chat/src/VariablePopup.css":
/*!*********************************************!*\
  !*** ../agentic_chat/src/VariablePopup.css ***!
  \*********************************************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablePopup_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./VariablePopup.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/VariablePopup.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablePopup_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablePopup_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablePopup_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablePopup_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/VariablePopup.tsx":
/*!*********************************************!*\
  !*** ../agentic_chat/src/VariablePopup.tsx ***!
  \*********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var marked__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! marked */ "../node_modules/.pnpm/marked@16.3.0/node_modules/marked/lib/marked.esm.js");
/* harmony import */ var _VariablePopup_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./VariablePopup.css */ "../agentic_chat/src/VariablePopup.css");



const VariablePopup = ({
  variable,
  onClose
}) => {
  const handleDownload = () => {
    // Check if variable is a dict type and try to download as JSON
    if (variable.type === 'dict') {
      try {
        // Attempt to parse the value_preview as JSON
        const jsonData = JSON.parse(variable.value_preview);
        const content = JSON.stringify(jsonData, null, 2);
        // Use octet-stream to force the browser to respect the .json extension
        const blob = new Blob([content], {
          type: "application/octet-stream"
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${variable.name}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        return;
      } catch (error) {
        // If JSON parsing fails, fall back to markdown
        console.warn('Failed to parse dict as JSON, falling back to markdown download:', error);
      }
    }

    // Default to markdown download
    const content = `# Variable: ${variable.name}\n\n**Type:** ${variable.type}\n\n${variable.description ? `**Description:** ${variable.description}\n\n` : ""}**Value:**\n\`\`\`\n${variable.value_preview}\n\`\`\``;
    const blob = new Blob([content], {
      type: "text/markdown"
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${variable.name}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
  const formattedContent = `## ${variable.name}\n\n**Type:** \`${variable.type}\`${variable.count_items ? ` (${variable.count_items} items)` : ""}\n\n${variable.description ? `**Description:** ${variable.description}\n\n` : ""}**Value:**\n\`\`\`\n${variable.value_preview}\n\`\`\``;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variable-popup-overlay",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variable-popup-content",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variable-popup-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Variable Details"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variable-popup-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "variable-popup-download-btn",
    onClick: handleDownload,
    title: variable.type === 'dict' ? "Download as JSON" : "Download as Markdown"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("svg", {
    width: "16",
    height: "16",
    viewBox: "0 0 16 16",
    fill: "currentColor"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("path", {
    d: "M8.5 1a.5.5 0 0 0-1 0v8.793L5.354 7.646a.5.5 0 1 0-.708.708l3 3a.5.5 0 0 0 .708 0l3-3a.5.5 0 0 0-.708-.708L8.5 9.793V1z"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("path", {
    d: "M3 13h10a1 1 0 0 0 1-1v-1.5a.5.5 0 0 0-1 0V12H3v-.5a.5.5 0 0 0-1 0V12a1 1 0 0 0 1 1z"
  })), "Download ", variable.type === 'dict' ? 'JSON' : 'MD'), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "variable-popup-close-btn",
    onClick: onClose
  }, "\xD7"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variable-popup-body",
    dangerouslySetInnerHTML: {
      __html: (0,marked__WEBPACK_IMPORTED_MODULE_1__.marked)(formattedContent)
    }
  })));
};
/* harmony default export */ __webpack_exports__["default"] = (VariablePopup);

/***/ }),

/***/ "../agentic_chat/src/VariablesSidebar.css":
/*!************************************************!*\
  !*** ../agentic_chat/src/VariablesSidebar.css ***!
  \************************************************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablesSidebar_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./VariablesSidebar.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/VariablesSidebar.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablesSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablesSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablesSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_VariablesSidebar_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/VariablesSidebar.tsx":
/*!************************************************!*\
  !*** ../agentic_chat/src/VariablesSidebar.tsx ***!
  \************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _VariablePopup__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ./VariablePopup */ "../agentic_chat/src/VariablePopup.tsx");
/* harmony import */ var _VariablesSidebar_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./VariablesSidebar.css */ "../agentic_chat/src/VariablesSidebar.css");



const VariablesSidebar = ({
  variables,
  memories = {},
  history = [],
  selectedAnswerId,
  onSelectAnswer,
  mode = "variables"
}) => {
  const [isExpanded, setIsExpanded] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(true);
  const [selectedVariable, setSelectedVariable] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const variableKeys = Object.keys(variables);
  const normalizeMemories = memoriesValue => {
    if (!memoriesValue || typeof memoriesValue !== "object" || Array.isArray(memoriesValue)) {
      return {};
    }
    return memoriesValue;
  };
  const extractMemoryFacts = memoriesValue => {
    if (!memoriesValue || typeof memoriesValue !== "object" || Array.isArray(memoriesValue)) {
      return [];
    }
    const facts = [];
    Object.entries(memoriesValue).forEach(([category, rawFacts]) => {
      if (!Array.isArray(rawFacts)) {
        return;
      }
      rawFacts.forEach(fact => {
        const content = typeof fact?.content === "string" ? fact.content : "";
        if (!content) {
          return;
        }
        facts.push({
          id: typeof fact?.id === "string" ? fact.id : undefined,
          category,
          content,
          key: typeof fact?.key === "string" ? fact.key : null,
          value: typeof fact?.value === "string" ? fact.value : null
        });
      });
    });
    return facts;
  };
  const currentMemories = normalizeMemories(memories);
  const memoryFacts = extractMemoryFacts(currentMemories);
  const countForActiveView = mode === "variables" ? variableKeys.length : memoryFacts.length;
  console.log("VariablesSidebar render - variableKeys:", variableKeys.length, "memoryFacts:", memoryFacts.length, "history:", history.length, "mode:", mode, "selectedAnswerId:", selectedAnswerId);
  if (variableKeys.length === 0 && memoryFacts.length === 0 && history.length === 0) {
    console.log('VariablesSidebar: No variables or history, not rendering');
    return null;
  }
  const formatTimestamp = timestamp => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    });
  };
  const getVariablesCountForHistoryItem = item => {
    return Object.keys(item.variables || {}).length;
  };
  const getMemoriesCountForHistoryItem = item => {
    const itemMemories = normalizeMemories(item.memories);
    const itemMemoryFacts = extractMemoryFacts(itemMemories);
    return itemMemoryFacts.length;
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `variables-sidebar ${isExpanded ? 'expanded' : 'collapsed'}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-sidebar-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "variables-sidebar-toggle",
    onClick: () => setIsExpanded(!isExpanded),
    title: isExpanded ? "Collapse variables panel" : "Expand variables panel"
  }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("polyline", {
    points: "15 18 9 12 15 6"
  })) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("polyline", {
    points: "9 18 15 12 9 6"
  }))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-sidebar-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("svg", {
    width: "18",
    height: "18",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, mode === "variables" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("path", {
    d: "M4 7h16M4 12h16M4 17h16"
  }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("path", {
    d: "M12 3v18M3 12h18"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, mode === "variables" ? "Variables" : "Memories"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "variables-count"
  }, countForActiveView)), history.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
    className: "variables-history-select",
    value: selectedAnswerId || '',
    onChange: e => onSelectAnswer && onSelectAnswer(e.target.value),
    onClick: e => e.stopPropagation(),
    title: "Select which conversation turn to view"
  }, history.map(item => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
    key: item.id,
    value: item.id
  }, item.title, " - ", mode === "variables" ? getVariablesCountForHistoryItem(item) : getMemoriesCountForHistoryItem(item), " ", mode === "variables" ? "variable" : "memory", (mode === "variables" ? getVariablesCountForHistoryItem(item) : getMemoriesCountForHistoryItem(item)) !== 1 ? "s" : "", " (", formatTimestamp(item.timestamp), ")"))))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-sidebar-content"
  }, history.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-history-info"
  }, "Viewing: ", history.find(h => h.id === selectedAnswerId)?.title || 'Latest turn', /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "history-count"
  }, history.length, " turns total")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "variables-list"
  }, mode === "variables" && variableKeys.length === 0 && history.length > 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "no-variables-message"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No variables in current turn."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "Select a previous turn from the dropdown above to view its variables.")) : mode === "memories" && memoryFacts.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "no-variables-message"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No memories found in current turn."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "Ask a few questions so CUGA can store memory facts and show them here.")) : mode === "variables" ? variableKeys.map(varName => {
    const variable = variables[varName];
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: varName,
      className: "variable-item",
      onClick: () => setSelectedVariable({
        name: varName,
        ...variable
      })
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-item-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", {
      className: "variable-name"
    }, varName), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "variable-type"
    }, variable.type)), variable.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-description"
    }, variable.description), variable.count_items !== undefined && variable.count_items > 1 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-meta"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "variable-count"
    }, variable.count_items, " items")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-preview"
    }, variable.value_preview ? variable.value_preview.substring(0, 80) + (variable.value_preview.length > 80 ? "..." : "") : ""));
  }) : memoryFacts.map((memory, index) => {
    const popupValue = JSON.stringify(memory, null, 2);
    const memoryName = memory.key || memory.id || `memory_${index + 1}`;
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: `${memory.category}-${memory.id || index}`,
      className: "variable-item",
      onClick: () => setSelectedVariable({
        name: memoryName,
        type: "memory",
        description: `${memory.category.replace(/_/g, " ")} memory fact`,
        value_preview: popupValue,
        count_items: 1
      })
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-item-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", {
      className: "variable-name"
    }, memoryName), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "variable-type memory-type"
    }, memory.category)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-description"
    }, memory.content), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "variable-preview"
    }, popupValue.substring(0, 80) + (popupValue.length > 80 ? "..." : "")));
  })))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "variables-sidebar-floating-toggle",
    onClick: () => setIsExpanded(true),
    title: "Show sidebar panel"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("svg", {
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("polyline", {
    points: "9 18 15 12 9 6"
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "variables-floating-count"
  }, countForActiveView)), selectedVariable && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(_VariablePopup__WEBPACK_IMPORTED_MODULE_1__["default"], {
    variable: selectedVariable,
    onClose: () => setSelectedVariable(null)
  }));
};
/* harmony default export */ __webpack_exports__["default"] = (VariablesSidebar);

/***/ }),

/***/ "../agentic_chat/src/WorkspacePanel.css":
/*!**********************************************!*\
  !*** ../agentic_chat/src/WorkspacePanel.css ***!
  \**********************************************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WorkspacePanel_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./WorkspacePanel.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/WorkspacePanel.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WorkspacePanel_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WorkspacePanel_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WorkspacePanel_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WorkspacePanel_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/WorkspacePanel.tsx":
/*!**********************************************!*\
  !*** ../agentic_chat/src/WorkspacePanel.tsx ***!
  \**********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   WorkspacePanel: function() { return /* binding */ WorkspacePanel; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _WorkspacePanel_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./WorkspacePanel.css */ "../agentic_chat/src/WorkspacePanel.css");



function WorkspacePanel({
  isOpen,
  onToggle,
  highlightedFile
}) {
  const [fileTree, setFileTree] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [expandedFolders, setExpandedFolders] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(new Set());
  const [selectedFile, setSelectedFile] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [loading, setLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [error, setError] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [deleteConfirmation, setDeleteConfirmation] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [isDragOver, setIsDragOver] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const loadWorkspaceTree = (0,react__WEBPACK_IMPORTED_MODULE_0__.useCallback)(async () => {
    try {
      setError(null);
      const {
        workspaceService
      } = await __webpack_require__.e(/*! import() */ "agentic_chat_src_workspaceService_ts").then(__webpack_require__.bind(__webpack_require__, /*! ./workspaceService */ "../agentic_chat/src/workspaceService.ts"));
      const data = await workspaceService.getWorkspaceTree();
      setFileTree(data.tree || []);
    } catch (err) {
      console.error("Error loading workspace:", err);
      setError("Error loading workspace");
    }
  }, []);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    if (isOpen) {
      loadWorkspaceTree();
      // Set up polling for live updates
      const interval = setInterval(loadWorkspaceTree, 15000);
      return () => clearInterval(interval);
    }
  }, [isOpen, loadWorkspaceTree]);
  const toggleFolder = path => {
    const newExpanded = new Set(expandedFolders);
    if (newExpanded.has(path)) {
      newExpanded.delete(path);
    } else {
      newExpanded.add(path);
    }
    setExpandedFolders(newExpanded);
  };
  const handleFileClick = async file => {
    if (file.type === "directory") {
      toggleFolder(file.path);
      return;
    }

    // Check if it's a text or markdown file
    const textExtensions = ['.txt', '.md', '.json', '.yaml', '.yml', '.log', '.csv', '.html', '.css', '.js', '.ts', '.py'];
    const isTextFile = textExtensions.some(ext => file.name.toLowerCase().endsWith(ext));
    if (!isTextFile) {
      alert("Only text and markdown files can be previewed");
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`/api/workspace/file?path=${encodeURIComponent(file.path)}`);
      if (response.ok) {
        const data = await response.json();
        setSelectedFile({
          path: file.path,
          content: data.content,
          name: file.name
        });
      } else {
        alert("Failed to load file");
      }
    } catch (err) {
      console.error("Error loading file:", err);
      alert("Error loading file");
    } finally {
      setLoading(false);
    }
  };
  const handleDownload = async (filePath, fileName) => {
    try {
      const response = await fetch(`/api/workspace/download?path=${encodeURIComponent(filePath)}`);
      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      } else {
        alert("Failed to download file");
      }
    } catch (err) {
      console.error("Error downloading file:", err);
      alert("Error downloading file");
    }
  };
  const handleDeleteClick = file => {
    // Disabled: Delete functionality is not available
    // setDeleteConfirmation({ file, isOpen: true });
  };
  const handleDeleteConfirm = async () => {
    if (!deleteConfirmation) return;
    const {
      file
    } = deleteConfirmation;
    setLoading(true);
    try {
      const response = await fetch(`/api/workspace/file?path=${encodeURIComponent(file.path)}`, {
        method: 'DELETE'
      });
      if (response.ok) {
        // Refresh the workspace tree after successful deletion
        await loadWorkspaceTree();
        // Close any open file viewer if the deleted file was being viewed
        if (selectedFile && selectedFile.path === file.path) {
          setSelectedFile(null);
        }
      } else {
        alert("Failed to delete file");
      }
    } catch (err) {
      console.error("Error deleting file:", err);
      alert("Error deleting file");
    } finally {
      setLoading(false);
      setDeleteConfirmation(null);
    }
  };
  const handleDeleteCancel = () => {
    setDeleteConfirmation(null);
  };

  // Drag and drop handlers - DISABLED
  const handleDragEnter = e => {
    e.preventDefault();
    e.stopPropagation();
    // Disabled: Upload functionality is not available
    // if (e.dataTransfer?.types.includes('Files')) {
    //   setIsDragOver(true);
    // }
  };
  const handleDragLeave = e => {
    e.preventDefault();
    e.stopPropagation();
    // Disabled: Upload functionality is not available
    // const rect = e.currentTarget.getBoundingClientRect();
    // const x = e.clientX;
    // const y = e.clientY;
    // if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
    //   setIsDragOver(false);
    // }
  };
  const handleDragOver = e => {
    e.preventDefault();
    e.stopPropagation();
  };
  const handleDrop = async e => {
    e.preventDefault();
    e.stopPropagation();
    // Disabled: Upload functionality is not available
    // setIsDragOver(false);
    // const files = Array.from(e.dataTransfer.files);
    // if (files.length > 0) {
    //   await handleFileUpload(files);
    // }
  };
  const handleFileUpload = async files => {
    setLoading(true);
    setError(null);
    try {
      const uploadPromises = files.map(async file => {
        const formData = new FormData();
        formData.append('file', file);

        // Upload to cuga_workspace directory
        const response = await fetch('/api/workspace/upload', {
          method: 'POST',
          body: formData
        });
        if (!response.ok) {
          throw new Error(`Failed to upload ${file.name}: ${response.statusText}`);
        }
        return await response.json();
      });
      await Promise.all(uploadPromises);

      // Refresh the workspace tree after successful uploads
      await loadWorkspaceTree();
    } catch (err) {
      console.error('Error uploading files:', err);
      setError(`Upload failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };
  const renderFileTree = (nodes, level = 0) => {
    return nodes.map(node => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      key: node.path,
      style: {
        marginLeft: `${level * 16}px`
      }
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: `file-tree-item ${node.type === "directory" ? "directory" : "file"} ${highlightedFile === node.path ? "highlighted" : ""}`,
      onClick: () => handleFileClick(node)
    }, node.type === "directory" ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, expandedFolders.has(node.path) ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
      size: 16,
      className: "folder-icon"
    }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronRight, {
      size: 16,
      className: "folder-icon"
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Folder, {
      size: 16,
      className: "item-icon"
    })) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "folder-icon-spacer"
    }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.File, {
      size: 16,
      className: "item-icon"
    })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
      className: "item-name"
    }, node.name), node.type === "file" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "file-actions"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "download-icon-btn",
      onClick: e => {
        e.stopPropagation();
        handleDownload(node.path, node.name);
      },
      title: "Download file"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Download, {
      size: 14
    })))), node.type === "directory" && expandedFolders.has(node.path) && node.children && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "folder-children"
    }, renderFileTree(node.children, level + 1))));
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `workspace-panel ${isOpen ? "open" : "closed"} ${isDragOver ? "drag-over" : ""}`,
    onDragEnter: handleDragEnter,
    onDragLeave: handleDragLeave,
    onDragOver: handleDragOver,
    onDrop: handleDrop
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-panel-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-panel-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Folder, {
    size: 18
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Workspace"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-info-tooltip-wrapper",
    onClick: e => e.stopPropagation(),
    onMouseEnter: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Info, {
    size: 16,
    className: "info-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-info-tooltip"
  }, "This is the CUGA workspace. Tag files directly from your working directory using ", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("code", null, "@")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-panel-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "workspace-refresh-btn",
    onClick: loadWorkspaceTree,
    title: "Refresh"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.RefreshCw, {
    size: 16
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "workspace-close-btn",
    onClick: onToggle,
    title: "Close"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronRight, {
    size: 18
  })))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-panel-content"
  }, error ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-error"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, error), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: loadWorkspaceTree
  }, "Retry")) : fileTree.length === 0 ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-empty"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Folder, {
    size: 48,
    className: "empty-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "Workspace is empty"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Files created by agents will appear here")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-tree"
  }, renderFileTree(fileTree)))), !isOpen && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "workspace-toggle-btn",
    onClick: onToggle,
    title: "Open Workspace"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Folder, {
    size: 18
  })), selectedFile && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-viewer-overlay",
    onClick: () => setSelectedFile(null)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-viewer-modal",
    onClick: e => e.stopPropagation()
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-viewer-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-viewer-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.FileText, {
    size: 18
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, selectedFile.name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-viewer-actions"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "file-viewer-btn",
    onClick: () => handleDownload(selectedFile.path, selectedFile.name)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Download, {
    size: 16
  }), "Download"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "file-viewer-close",
    onClick: () => setSelectedFile(null)
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 18
  })))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "file-viewer-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", null, selectedFile.content)))), loading && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-loading-overlay"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "workspace-spinner"
  })));
}

/***/ }),

/***/ "../agentic_chat/src/WriteableElementExample.css":
/*!*******************************************************!*\
  !*** ../agentic_chat/src/WriteableElementExample.css ***!
  \*******************************************************/
/***/ (function(__unused_webpack_module, __unused_webpack___webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/injectStylesIntoStyleTag.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleDomAPI.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertBySelector.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/setAttributesWithoutAttributes.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/insertStyleElement.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4__);
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__(/*! !../../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js */ "../node_modules/.pnpm/style-loader@4.0.0_webpack@5.101.3/node_modules/style-loader/dist/runtime/styleTagTransform.js");
/* harmony import */ var _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WriteableElementExample_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./WriteableElementExample.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/WriteableElementExample.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WriteableElementExample_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WriteableElementExample_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WriteableElementExample_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_WriteableElementExample_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/action_agent.tsx":
/*!********************************************!*\
  !*** ../agentic_chat/src/action_agent.tsx ***!
  \********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ AgentThoughtsComponent; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function AgentThoughtsComponent({
  agentData
}) {
  const [showFullThoughts, setShowFullThoughts] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data for demonstration

  // Use props if provided, otherwise use sample data
  const {
    thoughts,
    next_agent,
    instruction
  } = agentData;
  function getAgentColor(agentName) {
    const colors = {
      ActionAgent: "bg-blue-100 text-blue-800 border-blue-300",
      ValidationAgent: "bg-green-100 text-green-800 border-green-300",
      NavigationAgent: "bg-purple-100 text-purple-800 border-purple-300",
      AnalysisAgent: "bg-yellow-100 text-yellow-800 border-yellow-300",
      TestAgent: "bg-orange-100 text-orange-800 border-orange-300"
    };
    return colors[agentName] || "bg-gray-100 text-gray-800 border-gray-300";
  }
  function getAgentIcon(agentName) {
    const icons = {
      ActionAgent: "🎯",
      QaAgent: "🔍"
    };
    return icons[agentName] || "🤖";
  }
  function truncateThoughts(thoughtsArray, maxLength = 120) {
    const firstThought = thoughtsArray[0] || "";
    if (firstThought.length <= maxLength) return firstThought;
    return firstThought.substring(0, maxLength) + "...";
  }
  function truncateInstruction(instruction, maxLength = 80) {
    if (instruction.length <= maxLength) return instruction;
    return instruction.substring(0, maxLength) + "...";
  }
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-3xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-base"
  }, "\uD83E\uDD16"), "Agent Workflow"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-2 py-1 rounded text-xs bg-indigo-100 text-indigo-700"
  }, "Processing")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3 p-2 bg-gray-50 rounded border"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, getAgentIcon(next_agent)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-600"
  }, "Next:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-2 py-1 rounded text-xs font-medium ${getAgentColor(next_agent)}`
  }, next_agent))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3 p-2 bg-blue-50 rounded border border-blue-200"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-start gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDCCB"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex-1"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-600 mb-1"
  }, "Current Instruction"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-700 leading-relaxed"
  }, truncateInstruction(instruction, 100))))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "border-t border-gray-100 pt-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-400"
  }, "\uD83D\uDCAD"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "Analysis (", thoughts.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullThoughts(!showFullThoughts),
    className: "text-xs text-gray-400 hover:text-gray-600"
  }, showFullThoughts ? "▲" : "▼"))), !showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-400 italic mt-1"
  }, truncateThoughts(thoughts, 80)), showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 space-y-1"
  }, thoughts.map((thought, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "flex items-start gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-300 mt-0.5 font-mono"
  }, index + 1, "."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-500 leading-relaxed"
  }, thought))))))));
}

/***/ }),

/***/ "../agentic_chat/src/action_status_component.tsx":
/*!*******************************************************!*\
  !*** ../agentic_chat/src/action_status_component.tsx ***!
  \*******************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ ActionStatusDashboard; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function ActionStatusDashboard({
  actionData
}) {
  const [showFullThoughts, setShowFullThoughts] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data - you can replace this with props

  const {
    thoughts,
    action,
    action_input_shortlisting_agent,
    action_input_coder_agent,
    action_input_conclude_task
  } = actionData;
  function truncateText(text, maxLength = 80) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
  }
  function getThoughtsSummary() {
    if (thoughts.length === 0) return "No thoughts recorded";
    const firstThought = truncateText(thoughts[0], 100);
    return firstThought;
  }
  function getActionIcon(actionType) {
    switch (actionType) {
      case "CoderAgent":
        return "👨‍💻";
      case "ShortlistingAgent":
        return "📋";
      case "conclude_task":
        return "🎯";
      default:
        return "⚡";
    }
  }
  function getActionColor(actionType) {
    switch (actionType) {
      case "CoderAgent":
        return "bg-purple-100 text-purple-800 border-purple-200";
      case "ShortlistingAgent":
        return "bg-blue-100 text-blue-800 border-blue-200";
      case "conclude_task":
        return "bg-green-100 text-green-800 border-green-200";
      default:
        return "bg-gray-100 text-gray-800 border-gray-200";
    }
  }

  // Determine which action is active
  const activeAction = action;
  const activeActionInput = action_input_coder_agent || action_input_shortlisting_agent || action_input_conclude_task;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-3xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700"
  }, "Active Action"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: `px-2 py-1 rounded text-xs font-medium ${getActionColor(activeAction)}`
  }, getActionIcon(activeAction), " ", activeAction)), activeActionInput && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `mb-3 p-2 rounded border ${getActionColor(activeAction)}`
  }, action_input_coder_agent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDC68\u200D\uD83D\uDCBB"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs font-medium text-purple-700"
  }, "Coder Agent Task")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-purple-600 leading-relaxed mb-2"
  }, action_input_coder_agent.task_description), action_input_coder_agent.context_variables_from_history && action_input_coder_agent.context_variables_from_history.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-purple-600"
  }, "Context:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex flex-wrap gap-1 mt-1"
  }, action_input_coder_agent.context_variables_from_history.slice(0, 3).map((variable, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    key: index,
    className: "px-1.5 py-0.5 bg-purple-50 text-purple-600 rounded text-xs"
  }, variable)), action_input_coder_agent.context_variables_from_history.length > 3 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-purple-500"
  }, "+", action_input_coder_agent.context_variables_from_history.length - 3, " more"))), action_input_coder_agent.relevant_apis && action_input_coder_agent.relevant_apis.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-purple-600"
  }, "APIs:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex flex-wrap gap-1 mt-1"
  }, action_input_coder_agent.relevant_apis.slice(0, 2).map((api, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    key: index,
    className: "px-1.5 py-0.5 bg-purple-50 text-purple-600 rounded text-xs"
  }, api.api_name)), action_input_coder_agent.relevant_apis.length > 2 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-purple-500"
  }, "+", action_input_coder_agent.relevant_apis.length - 2, " more")))), action_input_shortlisting_agent && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDCCB"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs font-medium text-blue-700"
  }, "Shortlisting Agent Task")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-blue-600 leading-relaxed"
  }, action_input_shortlisting_agent.task_description)), action_input_conclude_task && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2 mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83C\uDFAF"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs font-medium text-green-700"
  }, "Task Conclusion")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-green-600 leading-relaxed"
  }, action_input_conclude_task.final_response))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "grid grid-cols-3 gap-2 mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `p-2 rounded text-center text-xs ${action_input_coder_agent ? "bg-purple-100 text-purple-700" : "bg-gray-50 text-gray-400"}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm mb-1"
  }, "\uD83D\uDC68\u200D\uD83D\uDCBB"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "font-medium"
  }, "Coder"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs"
  }, action_input_coder_agent ? "Active" : "Inactive")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `p-2 rounded text-center text-xs ${action_input_shortlisting_agent ? "bg-blue-100 text-blue-700" : "bg-gray-50 text-gray-400"}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm mb-1"
  }, "\uD83D\uDCCB"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "font-medium"
  }, "Shortlister"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs"
  }, action_input_shortlisting_agent ? "Active" : "Inactive")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: `p-2 rounded text-center text-xs ${action_input_conclude_task ? "bg-green-100 text-green-700" : "bg-gray-50 text-gray-400"}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-sm mb-1"
  }, "\uD83C\uDFAF"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "font-medium"
  }, "Conclude"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs"
  }, action_input_conclude_task ? "Active" : "Inactive"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "border-t border-gray-100 pt-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-400"
  }, "\uD83D\uDCAD"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-500"
  }, "Analysis (", thoughts.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullThoughts(!showFullThoughts),
    className: "text-xs text-gray-400 hover:text-gray-600"
  }, showFullThoughts ? "▲" : "▼"))), !showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-400 italic mt-1"
  }, getThoughtsSummary()), showFullThoughts && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mt-2 space-y-1"
  }, thoughts.map((thought, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "flex items-start gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-300 mt-0.5 font-mono"
  }, index + 1, "."), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-xs text-gray-500 leading-relaxed"
  }, thought))))))));
}

/***/ }),

/***/ "../agentic_chat/src/app_analyzer_component.tsx":
/*!******************************************************!*\
  !*** ../agentic_chat/src/app_analyzer_component.tsx ***!
  \******************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ AppAnalyzerComponent; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);

function AppAnalyzerComponent({
  appData
}) {
  const [showAllApps, setShowAllApps] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data - you can replace this with props

  function getAppIcon(appName) {
    switch (appName.toLowerCase()) {
      case "gmail":
        return "📧";
      case "phone":
        return "📱";
      case "venmo":
        return "💰";
      case "calendar":
        return "📅";
      case "drive":
        return "📁";
      case "sheets":
        return "📊";
      case "slack":
        return "💬";
      case "spotify":
        return "🎵";
      case "uber":
        return "🚗";
      case "weather":
        return "🌤️";
      default:
        return "🔧";
    }
  }
  function getAppColor(appName) {
    switch (appName.toLowerCase()) {
      case "gmail":
        return "bg-red-100 text-red-700";
      case "phone":
        return "bg-blue-100 text-blue-700";
      case "venmo":
        return "bg-green-100 text-green-700";
      case "calendar":
        return "bg-purple-100 text-purple-700";
      case "drive":
        return "bg-yellow-100 text-yellow-700";
      default:
        return "bg-gray-100 text-gray-700";
    }
  }
  const displayedApps = showAllApps ? appData : appData.slice(0, 4);
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-4xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDD0D"), "App Analysis"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-2 py-1 rounded text-xs bg-blue-100 text-blue-700"
  }, appData.length, " apps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex flex-wrap gap-1.5 mb-3"
  }, displayedApps.map((app, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: `flex items-center gap-1.5 px-2 py-1 rounded ${getAppColor(app.name)}`
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, getAppIcon(app.name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs font-medium capitalize"
  }, app.name)))), appData.length > 4 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowAllApps(!showAllApps),
    className: "text-xs text-blue-600 hover:text-blue-800"
  }, showAllApps ? "▲ Less" : `▼ +${appData.length - 4} more`)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-gray-500"
  }, "\u2705 Ready to use ", appData.length, " integrated services"))));
}

/***/ }),

/***/ "../agentic_chat/src/coder_agent_output.tsx":
/*!**************************************************!*\
  !*** ../agentic_chat/src/coder_agent_output.tsx ***!
  \**************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ CoderAgentOutput; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var react_markdown__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! react-markdown */ "../node_modules/.pnpm/react-markdown@10.1.0_@types+react@18.3.24_react@18.3.1/node_modules/react-markdown/index.js");


function CoderAgentOutput({
  coderData
}) {
  const [showFullCode, setShowFullCode] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [showFullOutput, setShowFullOutput] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Handle both old format (summary) and new format (execution_output)
  const {
    code = "",
    summary,
    execution_output,
    variables
  } = coderData;
  const output = execution_output || summary || "";
  function getCodeSnippet(fullCode, maxLines = 4) {
    if (!fullCode) return "";
    const lines = fullCode.split("\n");
    if (lines.length <= maxLines) return fullCode;
    return lines.slice(0, maxLines).join("\n") + "\n...";
  }
  function truncateOutput(text, maxLength = 400) {
    if (!text) return "";
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + "...";
  }
  const codeLines = code ? code.split("\n").length : 0;
  const outputLength = output ? output.length : 0;
  const hasVariables = variables && Object.keys(variables).length > 0;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-3xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg border border-gray-200 p-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", {
    className: "text-sm font-medium text-gray-700 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-sm"
  }, "\uD83D\uDCBB"), "Coder Agent"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "px-2 py-1 rounded text-xs bg-purple-100 text-purple-700"
  }, output ? "Complete" : "In Progress")), code && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-600"
  }, "Code (", codeLines, " lines)"), codeLines > 4 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullCode(!showFullCode),
    className: "text-xs text-purple-600 hover:text-purple-800"
  }, showFullCode ? "▲ Less" : "▼ More")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-gray-900 rounded p-3",
    style: {
      overflowX: "auto"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("pre", {
    className: "text-green-400 text-xs font-mono leading-relaxed"
  }, showFullCode ? code : getCodeSnippet(code)))), output && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex items-center justify-between mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-600"
  }, "Execution Output (", outputLength, " chars)"), outputLength > 400 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setShowFullOutput(!showFullOutput),
    className: "text-xs text-green-600 hover:text-green-800"
  }, showFullOutput ? "▲ Less" : "▼ More")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-green-50 rounded p-3 border border-green-200",
    style: {
      overflowY: "auto",
      maxHeight: showFullOutput ? "none" : "300px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-green-800 leading-relaxed"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(react_markdown__WEBPACK_IMPORTED_MODULE_1__["default"], null, showFullOutput ? output : truncateOutput(output))))), hasVariables && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-3"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs text-gray-600"
  }, "Variables Created (", Object.keys(variables).length, ")")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-blue-50 rounded p-3 border border-blue-200"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-xs text-blue-800 space-y-1"
  }, Object.entries(variables).slice(0, 3).map(([key, value]) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: key,
    className: "font-mono"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "font-semibold"
  }, key), ": ", JSON.stringify(value).substring(0, 60), JSON.stringify(value).length > 60 ? '...' : '')), Object.keys(variables).length > 3 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "text-gray-500 italic"
  }, "+ ", Object.keys(variables).length - 3, " more...")))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex gap-3 text-xs text-gray-500"
  }, code && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "\uD83D\uDCCA ", codeLines, " lines"), output && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "\uD83D\uDCDD ", outputLength, " chars"), hasVariables && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "\uD83D\uDD22 ", Object.keys(variables).length, " vars"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "\uD83C\uDFAF ", output ? "Complete" : "In Progress")))));
}

/***/ }),

/***/ "../agentic_chat/src/constants.ts":
/*!****************************************!*\
  !*** ../agentic_chat/src/constants.ts ***!
  \****************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   API_BASE_URL: function() { return /* binding */ API_BASE_URL; },
/* harmony export */   RESPONSE_USER_PROFILE: function() { return /* binding */ RESPONSE_USER_PROFILE; }
/* harmony export */ });
/* unused harmony export getApiBaseUrl */
/* harmony import */ var _carbon_ai_chat__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! @carbon/ai-chat */ "../node_modules/.pnpm/@carbon+ai-chat@0.5.1_@carbon+icon-helpers@10.65.0_@carbon+icons@11.66.0_@carbon+react@_02e49597529a0dc24a12569b39403b32/node_modules/@carbon/ai-chat/dist/es/aiChatEntry.js");


// Declare process for webpack environment variable injection

const RESPONSE_USER_PROFILE = {
  id: "ai-chatbot-user",
  userName: "CUGA",
  fullName: "CUGA Agent",
  displayName: "CUGA",
  accountName: "CUGA Agent",
  replyToId: "ai-chatbot-user",
  userType: _carbon_ai_chat__WEBPACK_IMPORTED_MODULE_0__.UserType.BOT
};

// Get the base URL for the backend API
// In production (HF Spaces), use the current origin
// In development, use localhost with port 7860 (default port)
const getApiBaseUrl = () => {
  // If running in Hugging Face Spaces or production, use current origin
  if (typeof window !== 'undefined') {
    const hostname = window.location.hostname;

    // Check if we're on HF Spaces or not localhost
    if (hostname !== 'localhost' && hostname !== '127.0.0.1') {
      return window.location.origin;
    }
  }

  // Default to localhost:7860 for local development
  // This can be overridden by setting REACT_APP_API_URL environment variable
  // Note: In browser, process.env is injected by webpack at build time
  if (typeof process !== 'undefined' && process?.env?.REACT_APP_API_URL) {
    return process.env.REACT_APP_API_URL;
  }
  return 'http://localhost:7860';
};
const API_BASE_URL = getApiBaseUrl();

/***/ }),

/***/ "../agentic_chat/src/exampleUtterances.ts":
/*!************************************************!*\
  !*** ../agentic_chat/src/exampleUtterances.ts ***!
  \************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   exampleUtterances: function() { return /* binding */ exampleUtterances; }
/* harmony export */ });
const exampleUtterances = [{
  text: "From the list of emails in the file contacts.txt, please filter those who exist in the CRM application. For the filtered contacts, retrieve their name and their associated account name, and calculate their account's revenue percentile across all accounts. Finally, draft a an email based on email_template.md template summarizing the result and show it to me",
  reason: "Multi-step workflow: file reading, API filtering, data analysis, and content generation"
}, {
  text: "from contacts.txt show me which users belong to the crm system",
  reason: "Iterative task execution with dynamic followup planning"
}, {
  text: "./cuga_workspace/cuga_playbook.md",
  reason: "Driving agent behavior from playbooks: learn how CUGA uses tools and variables in this demo"
}, {
  text: "What is CUGA?",
  reason: "CUGA answers questions about itself from documentation"
}];

/***/ }),

/***/ "../agentic_chat/src/floating/stop_button.tsx":
/*!****************************************************!*\
  !*** ../agentic_chat/src/floating/stop_button.tsx ***!
  \****************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   StopButton: function() { return /* binding */ StopButton; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _StreamManager__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../StreamManager */ "../agentic_chat/src/StreamManager.tsx");
/* harmony import */ var _WriteableElementExample_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ../WriteableElementExample.css */ "../agentic_chat/src/WriteableElementExample.css");
// StopButton.tsx



const StopButton = ({
  location = "sidebar"
}) => {
  const [isStreaming, setIsStreaming] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(() => _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.getIsStreaming());
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    const unsubscribe = _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.subscribe(setIsStreaming);
    return unsubscribe;
  }, []);
  const handleStop = async () => {
    await _StreamManager__WEBPACK_IMPORTED_MODULE_1__.streamStateManager.stopStream();
    if (typeof window !== "undefined" && window.aiSystemInterface) {
      try {
        window.aiSystemInterface.stopProcessing?.();
        window.aiSystemInterface.setProcessingComplete?.(true);
      } catch (e) {
        // noop
      }
    }
  };
  if (!isStreaming) {
    return null;
  }
  const isInline = location === "inline";
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "floating-controls-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: handleStop,
    className: isInline ? "stop-button-inline" : "stop-button-floating",
    style: {
      color: isInline ? "white" : "black",
      border: isInline ? "none" : "#c6c6c6 solid 1px",
      backgroundColor: isInline ? "#ef4444" : "white",
      marginLeft: "auto",
      marginRight: "auto",
      opacity: isInline ? "1" : "0.6",
      fontWeight: "500",
      borderRadius: isInline ? "8px" : "4px",
      marginBottom: isInline ? "0" : "6px",
      padding: isInline ? "8px 12px" : "8px 16px",
      cursor: "pointer",
      fontSize: isInline ? "13px" : "14px",
      display: "flex",
      alignItems: "center",
      gap: "6px",
      transition: "all 0.2s ease",
      flexShrink: 0
    },
    onMouseOver: e => {
      if (isInline) {
        e.currentTarget.style.backgroundColor = "#dc2626";
        e.currentTarget.style.transform = "scale(1.05)";
      } else {
        e.currentTarget.style.backgroundColor = "black";
        e.currentTarget.style.color = "white";
        e.currentTarget.style.opacity = "1";
      }
    },
    onMouseOut: e => {
      if (isInline) {
        e.currentTarget.style.backgroundColor = "#ef4444";
        e.currentTarget.style.transform = "scale(1)";
      } else {
        e.currentTarget.style.backgroundColor = "";
        e.currentTarget.style.color = "black";
        e.currentTarget.style.opacity = "0.6";
      }
    }
  }, isInline ? "Stop" : "Stop Processing"));
};

/***/ }),

/***/ "../agentic_chat/src/generic_component.tsx":
/*!*************************************************!*\
  !*** ../agentic_chat/src/generic_component.tsx ***!
  \*************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ SingleExpandableContent; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var react_markdown__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! react-markdown */ "../node_modules/.pnpm/react-markdown@10.1.0_@types+react@18.3.24_react@18.3.1/node_modules/react-markdown/index.js");


function SingleExpandableContent({
  title,
  content,
  maxLength = 600
}) {
  const [isExpanded, setIsExpanded] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);

  // Sample data for demonstration
  const sampleTitle = title;
  const sampleContent = content;
  const shouldTruncate = sampleContent.length > maxLength;
  const displayContent = isExpanded || !shouldTruncate ? sampleContent : sampleContent.substring(0, maxLength) + "...";
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "p-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "max-w-4xl mx-auto"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "bg-white rounded-lg shadow-md border p-6"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-4"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", {
    className: "text-xl font-bold text-gray-800 flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-2xl"
  }, "\uD83D\uDCC4"), sampleTitle)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "mb-4",
    style: {
      overflowY: "scroll"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "text-gray-700 leading-relaxed text-sm"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(react_markdown__WEBPACK_IMPORTED_MODULE_1__["default"], null, displayContent))), shouldTruncate && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "flex justify-center"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: () => setIsExpanded(!isExpanded),
    className: "px-4 py-2 bg-blue-100 hover:bg-blue-200 text-blue-800 rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, isExpanded ? "Show less" : "Read more"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "text-xs"
  }, isExpanded ? "▲" : "▼"))))));
}

/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/VariablePopup.css":
/*!****************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/VariablePopup.css ***!
  \****************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".variable-popup-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  z-index: 10000;\n  animation: fadeIn 0.2s ease-in-out;\n}\n\n@keyframes fadeIn {\n  from {\n    opacity: 0;\n  }\n  to {\n    opacity: 1;\n  }\n}\n\n.variable-popup-content {\n  background: white;\n  width: 100%;\n  height: 100vh;\n  display: flex;\n  flex-direction: column;\n  animation: slideUp 0.3s ease-out;\n}\n\n@keyframes slideUp {\n  from {\n    transform: translateY(20px);\n    opacity: 0;\n  }\n  to {\n    transform: translateY(0);\n    opacity: 1;\n  }\n}\n\n.variable-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 20px 24px;\n  border-bottom: 1px solid #e5e7eb;\n}\n\n.variable-popup-header h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n  color: #1e293b;\n}\n\n.variable-popup-actions {\n  display: flex;\n  gap: 8px;\n  align-items: center;\n}\n\n.variable-popup-download-btn {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 8px 14px;\n  background: #4e00ec;\n  color: white;\n  border: none;\n  border-radius: 6px;\n  font-size: 13px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.variable-popup-download-btn:hover {\n  background: #3d00b8;\n  transform: translateY(-1px);\n  box-shadow: 0 4px 12px rgba(78, 0, 236, 0.3);\n}\n\n.variable-popup-download-btn:active {\n  transform: translateY(0);\n}\n\n.variable-popup-close-btn {\n  width: 32px;\n  height: 32px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  background: transparent;\n  border: none;\n  font-size: 28px;\n  color: #64748b;\n  cursor: pointer;\n  border-radius: 6px;\n  transition: all 0.2s;\n  line-height: 1;\n}\n\n.variable-popup-close-btn:hover {\n  background: #f1f5f9;\n  color: #1e293b;\n}\n\n.variable-popup-body {\n  padding: 24px;\n  overflow-y: auto;\n  flex: 1;\n}\n\n.variable-popup-body h2 {\n  margin: 0 0 16px 0;\n  font-size: 16px;\n  font-weight: 600;\n  color: #1e293b;\n}\n\n.variable-popup-body p {\n  margin: 8px 0;\n  color: #475569;\n  line-height: 1.6;\n}\n\n.variable-popup-body strong {\n  color: #1e293b;\n  font-weight: 600;\n}\n\n.variable-popup-body code {\n  background: #f1f5f9;\n  padding: 2px 6px;\n  border-radius: 4px;\n  font-size: 13px;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  color: #4e00ec;\n}\n\n.variable-popup-body pre {\n  background: #f8fafc;\n  border: 1px solid #e2e8f0;\n  border-radius: 8px;\n  padding: 16px;\n  overflow-x: auto;\n  margin: 12px 0;\n}\n\n.variable-popup-body pre code {\n  background: transparent;\n  padding: 0;\n  color: #334155;\n  font-size: 13px;\n  line-height: 1.5;\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/VariablePopup.css"],"names":[],"mappings":"AAAA;EACE,eAAe;EACf,MAAM;EACN,OAAO;EACP,QAAQ;EACR,SAAS;EACT,8BAA8B;EAC9B,cAAc;EACd,kCAAkC;AACpC;;AAEA;EACE;IACE,UAAU;EACZ;EACA;IACE,UAAU;EACZ;AACF;;AAEA;EACE,iBAAiB;EACjB,WAAW;EACX,aAAa;EACb,aAAa;EACb,sBAAsB;EACtB,gCAAgC;AAClC;;AAEA;EACE;IACE,2BAA2B;IAC3B,UAAU;EACZ;EACA;IACE,wBAAwB;IACxB,UAAU;EACZ;AACF;;AAEA;EACE,aAAa;EACb,8BAA8B;EAC9B,mBAAmB;EACnB,kBAAkB;EAClB,gCAAgC;AAClC;;AAEA;EACE,SAAS;EACT,eAAe;EACf,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,QAAQ;EACR,mBAAmB;AACrB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,iBAAiB;EACjB,mBAAmB;EACnB,YAAY;EACZ,YAAY;EACZ,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,eAAe;EACf,oBAAoB;AACtB;;AAEA;EACE,mBAAmB;EACnB,2BAA2B;EAC3B,4CAA4C;AAC9C;;AAEA;EACE,wBAAwB;AAC1B;;AAEA;EACE,WAAW;EACX,YAAY;EACZ,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,uBAAuB;EACvB,YAAY;EACZ,eAAe;EACf,cAAc;EACd,eAAe;EACf,kBAAkB;EAClB,oBAAoB;EACpB,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,gBAAgB;EAChB,OAAO;AACT;;AAEA;EACE,kBAAkB;EAClB,eAAe;EACf,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,mBAAmB;EACnB,gBAAgB;EAChB,kBAAkB;EAClB,eAAe;EACf,wDAAwD;EACxD,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,aAAa;EACb,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,uBAAuB;EACvB,UAAU;EACV,cAAc;EACd,eAAe;EACf,gBAAgB;AAClB","sourcesContent":[".variable-popup-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  z-index: 10000;\n  animation: fadeIn 0.2s ease-in-out;\n}\n\n@keyframes fadeIn {\n  from {\n    opacity: 0;\n  }\n  to {\n    opacity: 1;\n  }\n}\n\n.variable-popup-content {\n  background: white;\n  width: 100%;\n  height: 100vh;\n  display: flex;\n  flex-direction: column;\n  animation: slideUp 0.3s ease-out;\n}\n\n@keyframes slideUp {\n  from {\n    transform: translateY(20px);\n    opacity: 0;\n  }\n  to {\n    transform: translateY(0);\n    opacity: 1;\n  }\n}\n\n.variable-popup-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 20px 24px;\n  border-bottom: 1px solid #e5e7eb;\n}\n\n.variable-popup-header h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n  color: #1e293b;\n}\n\n.variable-popup-actions {\n  display: flex;\n  gap: 8px;\n  align-items: center;\n}\n\n.variable-popup-download-btn {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 8px 14px;\n  background: #4e00ec;\n  color: white;\n  border: none;\n  border-radius: 6px;\n  font-size: 13px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.variable-popup-download-btn:hover {\n  background: #3d00b8;\n  transform: translateY(-1px);\n  box-shadow: 0 4px 12px rgba(78, 0, 236, 0.3);\n}\n\n.variable-popup-download-btn:active {\n  transform: translateY(0);\n}\n\n.variable-popup-close-btn {\n  width: 32px;\n  height: 32px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  background: transparent;\n  border: none;\n  font-size: 28px;\n  color: #64748b;\n  cursor: pointer;\n  border-radius: 6px;\n  transition: all 0.2s;\n  line-height: 1;\n}\n\n.variable-popup-close-btn:hover {\n  background: #f1f5f9;\n  color: #1e293b;\n}\n\n.variable-popup-body {\n  padding: 24px;\n  overflow-y: auto;\n  flex: 1;\n}\n\n.variable-popup-body h2 {\n  margin: 0 0 16px 0;\n  font-size: 16px;\n  font-weight: 600;\n  color: #1e293b;\n}\n\n.variable-popup-body p {\n  margin: 8px 0;\n  color: #475569;\n  line-height: 1.6;\n}\n\n.variable-popup-body strong {\n  color: #1e293b;\n  font-weight: 600;\n}\n\n.variable-popup-body code {\n  background: #f1f5f9;\n  padding: 2px 6px;\n  border-radius: 4px;\n  font-size: 13px;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  color: #4e00ec;\n}\n\n.variable-popup-body pre {\n  background: #f8fafc;\n  border: 1px solid #e2e8f0;\n  border-radius: 8px;\n  padding: 16px;\n  overflow-x: auto;\n  margin: 12px 0;\n}\n\n.variable-popup-body pre code {\n  background: transparent;\n  padding: 0;\n  color: #334155;\n  font-size: 13px;\n  line-height: 1.5;\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/VariablesSidebar.css":
/*!*******************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/VariablesSidebar.css ***!
  \*******************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, "/* Ensure sidebar is fixed from the very left edge */\n.variables-sidebar {\n  position: fixed !important;\n  left: 0 !important;\n  top: 0;\n  bottom: 0;\n  background: white;\n  border-right: 1px solid #e5e7eb;\n  z-index: 1000;\n  display: flex;\n  flex-direction: column;\n  transition: width 0.3s ease, transform 0.3s ease;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.05);\n  margin: 0;\n  padding: 0;\n}\n\n.variables-sidebar.expanded {\n  width: 320px;\n}\n\n.variables-sidebar.collapsed {\n  /* When collapsed, slide it completely out of view */\n  transform: translateX(-100%);\n}\n\n/* Responsive design */\n@media (max-width: 768px) {\n  .variables-sidebar.expanded {\n    width: 280px;\n  }\n}\n\n@media (max-width: 640px) {\n  .variables-sidebar.expanded {\n    width: 100%;\n    max-width: 300px;\n  }\n  \n  .variables-sidebar.collapsed {\n    transform: translateX(-100%);\n  }\n}\n\n.variables-sidebar-header {\n  display: flex;\n  align-items: center;\n  padding: 16px;\n  border-bottom: 1px solid #e5e7eb;\n  gap: 12px;\n  min-height: 64px;\n  flex-wrap: wrap;\n}\n\n.variables-sidebar-toggle {\n  width: 36px;\n  height: 36px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  background: transparent;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  cursor: pointer;\n  color: #64748b;\n  transition: all 0.2s;\n  flex-shrink: 0;\n}\n\n.variables-sidebar-toggle:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  color: #4e00ec;\n}\n\n.variables-sidebar.collapsed .variables-sidebar-toggle {\n  margin: 0 auto;\n}\n\n.variables-sidebar-title {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-size: 16px;\n  font-weight: 600;\n  color: #1e293b;\n  flex: 1;\n}\n\n.variables-sidebar-title svg {\n  color: #4e00ec;\n}\n\n.variables-count {\n  background: #4e00ec;\n  color: white;\n  font-size: 12px;\n  font-weight: 600;\n  padding: 2px 8px;\n  border-radius: 12px;\n  margin-left: auto;\n}\n\n.variables-history-select {\n  width: 100%;\n  padding: 6px 10px;\n  font-size: 12px;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  background: white;\n  color: #1e293b;\n  cursor: pointer;\n  transition: all 0.2s;\n  margin-top: 8px;\n}\n\n.variables-history-select:hover {\n  border-color: #cbd5e1;\n  background: #f8fafc;\n}\n\n.variables-history-select:focus {\n  outline: none;\n  border-color: #4e00ec;\n  box-shadow: 0 0 0 3px rgba(78, 0, 236, 0.1);\n}\n\n.variables-history-info {\n  padding: 10px 12px;\n  background: #f8fafc;\n  border-bottom: 1px solid #e5e7eb;\n  font-size: 12px;\n  color: #64748b;\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n}\n\n.history-count {\n  font-weight: 600;\n  color: #4e00ec;\n}\n\n.variables-sidebar-content {\n  flex: 1;\n  overflow-y: auto;\n  overflow-x: hidden;\n}\n\n.variables-list {\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n  padding: 12px;\n}\n\n.variable-item {\n  background: #f8fafc;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  padding: 12px;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.variable-item:hover {\n  background: #f1f5f9;\n  border-color: #cbd5e1;\n  transform: translateY(-1px);\n  box-shadow: 0 2px 8px rgba(78, 0, 236, 0.1);\n}\n\n.variable-item:active {\n  transform: translateY(0);\n}\n\n.variable-item-header {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 8px;\n  margin-bottom: 6px;\n}\n\n.variable-name {\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  font-size: 13px;\n  font-weight: 600;\n  color: #4e00ec;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5dbff;\n  flex: 1;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.variable-type {\n  font-size: 11px;\n  font-weight: 500;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n  flex-shrink: 0;\n}\n\n.memory-type {\n  text-transform: capitalize;\n}\n\n.variable-description {\n  font-size: 12px;\n  color: #64748b;\n  line-height: 1.4;\n  margin-bottom: 6px;\n  display: -webkit-box;\n  -webkit-line-clamp: 2;\n  -webkit-box-orient: vertical;\n  overflow: hidden;\n}\n\n.variable-meta {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 6px;\n}\n\n.variable-count {\n  font-size: 11px;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.variable-preview {\n  font-size: 12px;\n  color: #475569;\n  background: white;\n  padding: 6px 8px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n  line-height: 1.4;\n}\n\n/* Scrollbar styling */\n.variables-sidebar-content::-webkit-scrollbar {\n  width: 6px;\n}\n\n.variables-sidebar-content::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.variables-sidebar-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.variables-sidebar-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n/* Animation */\n@keyframes slideIn {\n  from {\n    transform: translateX(-100%);\n  }\n  to {\n    transform: translateX(0);\n  }\n}\n\n.variables-sidebar {\n  animation: slideIn 0.3s ease-out;\n}\n\n/* Floating toggle button when sidebar is collapsed */\n.variables-sidebar-floating-toggle {\n  position: fixed;\n  left: 0;\n  top: 50%;\n  transform: translateY(-50%);\n  width: 48px;\n  height: 64px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-left: none;\n  border-radius: 0 8px 8px 0;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);\n  cursor: pointer;\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  gap: 4px;\n  z-index: 999;\n  transition: all 0.2s;\n  color: #64748b;\n}\n\n.variables-sidebar-floating-toggle:hover {\n  background: #f8fafc;\n  color: #4e00ec;\n  box-shadow: 2px 0 12px rgba(78, 0, 236, 0.2);\n}\n\n.variables-floating-count {\n  font-size: 11px;\n  font-weight: 600;\n  background: #4e00ec;\n  color: white;\n  padding: 2px 6px;\n  border-radius: 10px;\n  min-width: 20px;\n  text-align: center;\n}\n\n.no-variables-message {\n  padding: 24px 16px;\n  text-align: center;\n  color: #64748b;\n  background: #f8fafc;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  margin: 12px;\n}\n\n.no-variables-message p {\n  margin: 0 0 8px 0;\n  font-size: 14px;\n}\n\n.no-variables-message p:last-child {\n  margin-bottom: 0;\n  font-size: 12px;\n  color: #94a3b8;\n}\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/VariablesSidebar.css"],"names":[],"mappings":"AAAA,oDAAoD;AACpD;EACE,0BAA0B;EAC1B,kBAAkB;EAClB,MAAM;EACN,SAAS;EACT,iBAAiB;EACjB,+BAA+B;EAC/B,aAAa;EACb,aAAa;EACb,sBAAsB;EACtB,gDAAgD;EAChD,yCAAyC;EACzC,SAAS;EACT,UAAU;AACZ;;AAEA;EACE,YAAY;AACd;;AAEA;EACE,oDAAoD;EACpD,4BAA4B;AAC9B;;AAEA,sBAAsB;AACtB;EACE;IACE,YAAY;EACd;AACF;;AAEA;EACE;IACE,WAAW;IACX,gBAAgB;EAClB;;EAEA;IACE,4BAA4B;EAC9B;AACF;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,aAAa;EACb,gCAAgC;EAChC,SAAS;EACT,gBAAgB;EAChB,eAAe;AACjB;;AAEA;EACE,WAAW;EACX,YAAY;EACZ,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,uBAAuB;EACvB,yBAAyB;EACzB,kBAAkB;EAClB,eAAe;EACf,cAAc;EACd,oBAAoB;EACpB,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,cAAc;AAChB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,OAAO;AACT;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,YAAY;EACZ,eAAe;EACf,gBAAgB;EAChB,gBAAgB;EAChB,mBAAmB;EACnB,iBAAiB;AACnB;;AAEA;EACE,WAAW;EACX,iBAAiB;EACjB,eAAe;EACf,yBAAyB;EACzB,kBAAkB;EAClB,iBAAiB;EACjB,cAAc;EACd,eAAe;EACf,oBAAoB;EACpB,eAAe;AACjB;;AAEA;EACE,qBAAqB;EACrB,mBAAmB;AACrB;;AAEA;EACE,aAAa;EACb,qBAAqB;EACrB,2CAA2C;AAC7C;;AAEA;EACE,kBAAkB;EAClB,mBAAmB;EACnB,gCAAgC;EAChC,eAAe;EACf,cAAc;EACd,aAAa;EACb,mBAAmB;EACnB,8BAA8B;AAChC;;AAEA;EACE,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,OAAO;EACP,gBAAgB;EAChB,kBAAkB;AACpB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,QAAQ;EACR,aAAa;AACf;;AAEA;EACE,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,aAAa;EACb,eAAe;EACf,oBAAoB;AACtB;;AAEA;EACE,mBAAmB;EACnB,qBAAqB;EACrB,2BAA2B;EAC3B,2CAA2C;AAC7C;;AAEA;EACE,wBAAwB;AAC1B;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,8BAA8B;EAC9B,QAAQ;EACR,kBAAkB;AACpB;;AAEA;EACE,wDAAwD;EACxD,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,iBAAiB;EACjB,gBAAgB;EAChB,kBAAkB;EAClB,yBAAyB;EACzB,OAAO;EACP,gBAAgB;EAChB,uBAAuB;EACvB,mBAAmB;AACrB;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,iBAAiB;EACjB,gBAAgB;EAChB,kBAAkB;EAClB,yBAAyB;EACzB,cAAc;AAChB;;AAEA;EACE,0BAA0B;AAC5B;;AAEA;EACE,eAAe;EACf,cAAc;EACd,gBAAgB;EAChB,kBAAkB;EAClB,oBAAoB;EACpB,qBAAqB;EACrB,4BAA4B;EAC5B,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,kBAAkB;AACpB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,iBAAiB;EACjB,gBAAgB;EAChB,kBAAkB;EAClB,yBAAyB;AAC3B;;AAEA;EACE,eAAe;EACf,cAAc;EACd,iBAAiB;EACjB,gBAAgB;EAChB,kBAAkB;EAClB,yBAAyB;EACzB,wDAAwD;EACxD,gBAAgB;EAChB,uBAAuB;EACvB,mBAAmB;EACnB,gBAAgB;AAClB;;AAEA,sBAAsB;AACtB;EACE,UAAU;AACZ;;AAEA;EACE,uBAAuB;AACzB;;AAEA;EACE,mBAAmB;EACnB,kBAAkB;AACpB;;AAEA;EACE,mBAAmB;AACrB;;AAEA,cAAc;AACd;EACE;IACE,4BAA4B;EAC9B;EACA;IACE,wBAAwB;EAC1B;AACF;;AAEA;EACE,gCAAgC;AAClC;;AAEA,qDAAqD;AACrD;EACE,eAAe;EACf,OAAO;EACP,QAAQ;EACR,2BAA2B;EAC3B,WAAW;EACX,YAAY;EACZ,iBAAiB;EACjB,yBAAyB;EACzB,iBAAiB;EACjB,0BAA0B;EAC1B,wCAAwC;EACxC,eAAe;EACf,aAAa;EACb,sBAAsB;EACtB,mBAAmB;EACnB,uBAAuB;EACvB,QAAQ;EACR,YAAY;EACZ,oBAAoB;EACpB,cAAc;AAChB;;AAEA;EACE,mBAAmB;EACnB,cAAc;EACd,4CAA4C;AAC9C;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,mBAAmB;EACnB,YAAY;EACZ,gBAAgB;EAChB,mBAAmB;EACnB,eAAe;EACf,kBAAkB;AACpB;;AAEA;EACE,kBAAkB;EAClB,kBAAkB;EAClB,cAAc;EACd,mBAAmB;EACnB,yBAAyB;EACzB,kBAAkB;EAClB,YAAY;AACd;;AAEA;EACE,iBAAiB;EACjB,eAAe;AACjB;;AAEA;EACE,gBAAgB;EAChB,eAAe;EACf,cAAc;AAChB","sourcesContent":["/* Ensure sidebar is fixed from the very left edge */\n.variables-sidebar {\n  position: fixed !important;\n  left: 0 !important;\n  top: 0;\n  bottom: 0;\n  background: white;\n  border-right: 1px solid #e5e7eb;\n  z-index: 1000;\n  display: flex;\n  flex-direction: column;\n  transition: width 0.3s ease, transform 0.3s ease;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.05);\n  margin: 0;\n  padding: 0;\n}\n\n.variables-sidebar.expanded {\n  width: 320px;\n}\n\n.variables-sidebar.collapsed {\n  /* When collapsed, slide it completely out of view */\n  transform: translateX(-100%);\n}\n\n/* Responsive design */\n@media (max-width: 768px) {\n  .variables-sidebar.expanded {\n    width: 280px;\n  }\n}\n\n@media (max-width: 640px) {\n  .variables-sidebar.expanded {\n    width: 100%;\n    max-width: 300px;\n  }\n  \n  .variables-sidebar.collapsed {\n    transform: translateX(-100%);\n  }\n}\n\n.variables-sidebar-header {\n  display: flex;\n  align-items: center;\n  padding: 16px;\n  border-bottom: 1px solid #e5e7eb;\n  gap: 12px;\n  min-height: 64px;\n  flex-wrap: wrap;\n}\n\n.variables-sidebar-toggle {\n  width: 36px;\n  height: 36px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  background: transparent;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  cursor: pointer;\n  color: #64748b;\n  transition: all 0.2s;\n  flex-shrink: 0;\n}\n\n.variables-sidebar-toggle:hover {\n  background: #f8fafc;\n  border-color: #cbd5e1;\n  color: #4e00ec;\n}\n\n.variables-sidebar.collapsed .variables-sidebar-toggle {\n  margin: 0 auto;\n}\n\n.variables-sidebar-title {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-size: 16px;\n  font-weight: 600;\n  color: #1e293b;\n  flex: 1;\n}\n\n.variables-sidebar-title svg {\n  color: #4e00ec;\n}\n\n.variables-count {\n  background: #4e00ec;\n  color: white;\n  font-size: 12px;\n  font-weight: 600;\n  padding: 2px 8px;\n  border-radius: 12px;\n  margin-left: auto;\n}\n\n.variables-history-select {\n  width: 100%;\n  padding: 6px 10px;\n  font-size: 12px;\n  border: 1px solid #e5e7eb;\n  border-radius: 6px;\n  background: white;\n  color: #1e293b;\n  cursor: pointer;\n  transition: all 0.2s;\n  margin-top: 8px;\n}\n\n.variables-history-select:hover {\n  border-color: #cbd5e1;\n  background: #f8fafc;\n}\n\n.variables-history-select:focus {\n  outline: none;\n  border-color: #4e00ec;\n  box-shadow: 0 0 0 3px rgba(78, 0, 236, 0.1);\n}\n\n.variables-history-info {\n  padding: 10px 12px;\n  background: #f8fafc;\n  border-bottom: 1px solid #e5e7eb;\n  font-size: 12px;\n  color: #64748b;\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n}\n\n.history-count {\n  font-weight: 600;\n  color: #4e00ec;\n}\n\n.variables-sidebar-content {\n  flex: 1;\n  overflow-y: auto;\n  overflow-x: hidden;\n}\n\n.variables-list {\n  display: flex;\n  flex-direction: column;\n  gap: 8px;\n  padding: 12px;\n}\n\n.variable-item {\n  background: #f8fafc;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  padding: 12px;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.variable-item:hover {\n  background: #f1f5f9;\n  border-color: #cbd5e1;\n  transform: translateY(-1px);\n  box-shadow: 0 2px 8px rgba(78, 0, 236, 0.1);\n}\n\n.variable-item:active {\n  transform: translateY(0);\n}\n\n.variable-item-header {\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 8px;\n  margin-bottom: 6px;\n}\n\n.variable-name {\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  font-size: 13px;\n  font-weight: 600;\n  color: #4e00ec;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5dbff;\n  flex: 1;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.variable-type {\n  font-size: 11px;\n  font-weight: 500;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n  flex-shrink: 0;\n}\n\n.memory-type {\n  text-transform: capitalize;\n}\n\n.variable-description {\n  font-size: 12px;\n  color: #64748b;\n  line-height: 1.4;\n  margin-bottom: 6px;\n  display: -webkit-box;\n  -webkit-line-clamp: 2;\n  -webkit-box-orient: vertical;\n  overflow: hidden;\n}\n\n.variable-meta {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 6px;\n}\n\n.variable-count {\n  font-size: 11px;\n  color: #64748b;\n  background: white;\n  padding: 2px 6px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n}\n\n.variable-preview {\n  font-size: 12px;\n  color: #475569;\n  background: white;\n  padding: 6px 8px;\n  border-radius: 4px;\n  border: 1px solid #e5e7eb;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n  line-height: 1.4;\n}\n\n/* Scrollbar styling */\n.variables-sidebar-content::-webkit-scrollbar {\n  width: 6px;\n}\n\n.variables-sidebar-content::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.variables-sidebar-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.variables-sidebar-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n/* Animation */\n@keyframes slideIn {\n  from {\n    transform: translateX(-100%);\n  }\n  to {\n    transform: translateX(0);\n  }\n}\n\n.variables-sidebar {\n  animation: slideIn 0.3s ease-out;\n}\n\n/* Floating toggle button when sidebar is collapsed */\n.variables-sidebar-floating-toggle {\n  position: fixed;\n  left: 0;\n  top: 50%;\n  transform: translateY(-50%);\n  width: 48px;\n  height: 64px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-left: none;\n  border-radius: 0 8px 8px 0;\n  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);\n  cursor: pointer;\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  gap: 4px;\n  z-index: 999;\n  transition: all 0.2s;\n  color: #64748b;\n}\n\n.variables-sidebar-floating-toggle:hover {\n  background: #f8fafc;\n  color: #4e00ec;\n  box-shadow: 2px 0 12px rgba(78, 0, 236, 0.2);\n}\n\n.variables-floating-count {\n  font-size: 11px;\n  font-weight: 600;\n  background: #4e00ec;\n  color: white;\n  padding: 2px 6px;\n  border-radius: 10px;\n  min-width: 20px;\n  text-align: center;\n}\n\n.no-variables-message {\n  padding: 24px 16px;\n  text-align: center;\n  color: #64748b;\n  background: #f8fafc;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  margin: 12px;\n}\n\n.no-variables-message p {\n  margin: 0 0 8px 0;\n  font-size: 14px;\n}\n\n.no-variables-message p:last-child {\n  margin-bottom: 0;\n  font-size: 12px;\n  color: #94a3b8;\n}\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/WorkspacePanel.css":
/*!*****************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/WorkspacePanel.css ***!
  \*****************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".workspace-panel {\n  position: fixed;\n  top: 48px;\n  right: 0;\n  bottom: 32px;\n  width: 300px;\n  background: white;\n  border-left: 1px solid #e5e7eb;\n  display: flex;\n  flex-direction: column;\n  z-index: 800;\n  transition: transform 0.3s ease;\n  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.05);\n}\n\n.workspace-panel.closed {\n  transform: translateX(100%);\n}\n\n.workspace-panel.open {\n  transform: translateX(0);\n}\n\n.workspace-panel-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 16px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n}\n\n.workspace-panel-title {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-size: 14px;\n  font-weight: 600;\n  color: #1f2937;\n  flex: 1;\n}\n\n.workspace-info-tooltip-wrapper {\n  position: relative;\n  display: flex;\n  align-items: center;\n  margin-left: 8px;\n  cursor: help;\n}\n\n.workspace-info-tooltip-wrapper .info-icon {\n  color: #94a3b8;\n  cursor: help;\n  transition: color 0.2s ease;\n  pointer-events: auto;\n}\n\n.workspace-info-tooltip-wrapper:hover .info-icon {\n  color: #667eea;\n}\n\n.workspace-info-tooltip {\n  position: fixed;\n  top: 60px;\n  right: 20px;\n  width: 320px;\n  max-width: calc(100vw - 40px);\n  padding: 14px 16px;\n  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);\n  border: 2px solid #e0e7ff;\n  border-radius: 12px;\n  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.25), 0 4px 12px rgba(0, 0, 0, 0.15);\n  font-size: 13px;\n  line-height: 1.6;\n  color: #334155;\n  opacity: 0;\n  visibility: hidden;\n  transition: opacity 0.3s ease, visibility 0.3s ease, transform 0.3s ease;\n  z-index: 9999;\n  pointer-events: none;\n  font-weight: 500;\n  transform-origin: top right;\n  white-space: normal;\n  word-wrap: break-word;\n  word-break: break-word;\n  overflow-wrap: break-word;\n  hyphens: auto;\n}\n\n.workspace-info-tooltip code {\n  background: #e0e7ff;\n  color: #4f46e5;\n  padding: 2px 6px;\n  border-radius: 4px;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  font-size: 12px;\n  font-weight: 600;\n  white-space: nowrap;\n}\n\n.workspace-info-tooltip-wrapper:hover .workspace-info-tooltip {\n  opacity: 1;\n  visibility: visible;\n  pointer-events: auto;\n  transform: translateY(2px);\n}\n\n.workspace-panel-actions {\n  display: flex;\n  gap: 4px;\n}\n\n.workspace-refresh-btn,\n.workspace-close-btn {\n  background: none;\n  border: none;\n  color: #6b7280;\n  cursor: pointer;\n  padding: 4px;\n  border-radius: 4px;\n  display: flex;\n  align-items: center;\n  transition: all 0.2s;\n}\n\n.workspace-refresh-btn:hover,\n.workspace-close-btn:hover {\n  background: #e5e7eb;\n  color: #1f2937;\n}\n\n.workspace-panel-content {\n  flex: 1;\n  overflow-y: auto;\n  padding: 12px;\n}\n\n.workspace-error {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  padding: 32px 16px;\n  text-align: center;\n  color: #ef4444;\n}\n\n.workspace-error button {\n  margin-top: 12px;\n  padding: 6px 16px;\n  background: #ef4444;\n  color: white;\n  border: none;\n  border-radius: 6px;\n  cursor: pointer;\n  font-size: 13px;\n}\n\n.workspace-error button:hover {\n  background: #dc2626;\n}\n\n.workspace-empty {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  padding: 48px 16px;\n  text-align: center;\n  color: #9ca3af;\n}\n\n.workspace-empty .empty-icon {\n  opacity: 0.3;\n  margin-bottom: 16px;\n}\n\n.workspace-empty p {\n  font-size: 14px;\n  font-weight: 500;\n  margin: 0 0 8px 0;\n  color: #6b7280;\n}\n\n.workspace-empty small {\n  font-size: 12px;\n  color: #9ca3af;\n}\n\n.file-tree {\n  font-size: 13px;\n}\n\n.file-tree-item {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 8px;\n  cursor: pointer;\n  border-radius: 4px;\n  transition: background 0.2s;\n  position: relative;\n  user-select: none;\n}\n\n.file-tree-item:hover {\n  background: #f3f4f6;\n}\n\n.file-tree-item.highlighted {\n  background: #dbeafe !important;\n  border: 1px solid #3b82f6;\n  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.3);\n}\n\n.file-tree-item.directory {\n  font-weight: 500;\n  color: #374151;\n}\n\n.file-tree-item.file {\n  color: #6b7280;\n}\n\n.folder-icon {\n  flex-shrink: 0;\n  color: #9ca3af;\n}\n\n.folder-icon-spacer {\n  width: 16px;\n  flex-shrink: 0;\n}\n\n.item-icon {\n  flex-shrink: 0;\n  color: #667eea;\n}\n\n.file-tree-item.file .item-icon {\n  color: #94a3b8;\n}\n\n.item-name {\n  flex: 1;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.download-icon-btn {\n  background: none;\n  border: none;\n  color: #9ca3af;\n  cursor: pointer;\n  padding: 2px;\n  border-radius: 3px;\n  display: flex;\n  align-items: center;\n  opacity: 0;\n  transition: all 0.2s;\n}\n\n.file-tree-item:hover .download-icon-btn {\n  opacity: 1;\n}\n\n.download-icon-btn:hover {\n  background: #e5e7eb;\n  color: #667eea;\n}\n\n.folder-children {\n  margin-top: 2px;\n}\n\n.workspace-toggle-btn {\n  position: fixed;\n  top: 60px;\n  right: 16px;\n  width: 40px;\n  height: 40px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  cursor: pointer;\n  z-index: 750;\n  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);\n  transition: all 0.2s;\n  color: #667eea;\n}\n\n.workspace-toggle-btn:hover {\n  background: #f9fafb;\n  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);\n  transform: translateY(-1px);\n}\n\n/* File Viewer Modal */\n.file-viewer-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1100;\n  animation: fadeIn 0.2s ease;\n}\n\n@keyframes fadeIn {\n  from {\n    opacity: 0;\n  }\n  to {\n    opacity: 1;\n  }\n}\n\n.file-viewer-modal {\n  background: white;\n  border-radius: 12px;\n  width: 90%;\n  max-width: 800px;\n  max-height: 80vh;\n  display: flex;\n  flex-direction: column;\n  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);\n  animation: slideUp 0.3s ease;\n}\n\n@keyframes slideUp {\n  from {\n    opacity: 0;\n    transform: translateY(20px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.file-viewer-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 16px 20px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 12px 12px 0 0;\n}\n\n.file-viewer-title {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  font-size: 15px;\n  font-weight: 600;\n  color: #1f2937;\n}\n\n.file-viewer-title svg {\n  color: #667eea;\n}\n\n.file-viewer-actions {\n  display: flex;\n  gap: 8px;\n}\n\n.file-viewer-btn {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 12px;\n  background: #667eea;\n  color: white;\n  border: none;\n  border-radius: 6px;\n  font-size: 13px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.file-viewer-btn:hover {\n  background: #5568d3;\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);\n}\n\n.file-viewer-close {\n  background: none;\n  border: none;\n  color: #6b7280;\n  cursor: pointer;\n  padding: 4px;\n  border-radius: 4px;\n  display: flex;\n  align-items: center;\n  transition: all 0.2s;\n}\n\n.file-viewer-close:hover {\n  background: #e5e7eb;\n  color: #1f2937;\n}\n\n.file-viewer-content {\n  flex: 1;\n  overflow: auto;\n  padding: 20px;\n  background: #fafafa;\n}\n\n.file-viewer-content pre {\n  margin: 0;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  font-size: 13px;\n  line-height: 1.6;\n  color: #1f2937;\n  white-space: pre-wrap;\n  word-wrap: break-word;\n}\n\n.workspace-loading-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(255, 255, 255, 0.8);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1050;\n}\n\n.workspace-spinner {\n  width: 40px;\n  height: 40px;\n  border: 4px solid #e5e7eb;\n  border-top-color: #667eea;\n  border-radius: 50%;\n  animation: spin 0.8s linear infinite;\n}\n\n@keyframes spin {\n  to {\n    transform: rotate(360deg);\n  }\n}\n\n.workspace-panel-content::-webkit-scrollbar {\n  width: 6px;\n}\n\n.workspace-panel-content::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.workspace-panel-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.workspace-panel-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n.file-viewer-content::-webkit-scrollbar {\n  width: 8px;\n  height: 8px;\n}\n\n.file-viewer-content::-webkit-scrollbar-track {\n  background: #f1f5f9;\n}\n\n.file-viewer-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 4px;\n}\n\n.file-viewer-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n/* Delete functionality styles */\n.file-actions {\n  display: flex;\n  gap: 4px;\n  opacity: 0;\n  transition: opacity 0.2s ease;\n}\n\n.file-tree-item:hover .file-actions {\n  opacity: 1;\n}\n\n.delete-icon-btn {\n  background: none;\n  border: none;\n  padding: 2px;\n  border-radius: 3px;\n  color: #6b7280;\n  cursor: pointer;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  transition: all 0.2s ease;\n}\n\n.delete-icon-btn:hover {\n  background: #fee2e2;\n  color: #dc2626;\n}\n\n.delete-confirmation-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1000;\n}\n\n.delete-confirmation-modal {\n  background: white;\n  border-radius: 8px;\n  padding: 0;\n  max-width: 400px;\n  width: 90%;\n  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);\n}\n\n.delete-confirmation-header {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  padding: 20px 20px 16px 20px;\n  border-bottom: 1px solid #e5e7eb;\n}\n\n.delete-confirmation-header h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n  color: #1f2937;\n}\n\n.delete-icon {\n  color: #dc2626;\n}\n\n.delete-confirmation-content {\n  padding: 16px 20px;\n}\n\n.delete-confirmation-content p {\n  margin: 0 0 8px 0;\n  color: #374151;\n  font-size: 14px;\n}\n\n.delete-warning {\n  color: #dc2626;\n  font-weight: 500;\n}\n\n.delete-confirmation-actions {\n  display: flex;\n  gap: 12px;\n  justify-content: flex-end;\n  padding: 16px 20px 20px 20px;\n  border-top: 1px solid #e5e7eb;\n}\n\n.delete-cancel-btn {\n  padding: 8px 16px;\n  border: 1px solid #d1d5db;\n  background: white;\n  color: #374151;\n  border-radius: 6px;\n  font-size: 14px;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.delete-cancel-btn:hover:not(:disabled) {\n  background: #f9fafb;\n  border-color: #9ca3af;\n}\n\n.delete-confirm-btn {\n  padding: 8px 16px;\n  border: none;\n  background: #dc2626;\n  color: white;\n  border-radius: 6px;\n  font-size: 14px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.delete-confirm-btn:hover:not(:disabled) {\n  background: #b91c1c;\n}\n\n.delete-cancel-btn:disabled,\n.delete-confirm-btn:disabled {\n  opacity: 0.6;\n  cursor: not-allowed;\n}\n\n/* Drag and drop styles */\n.workspace-panel.drag-over {\n  border-color: #667eea;\n  box-shadow: -2px 0 12px rgba(102, 126, 234, 0.3);\n}\n\n.workspace-drag-overlay {\n  position: absolute;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(102, 126, 234, 0.1);\n  border: 2px dashed #667eea;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1000;\n  pointer-events: none;\n}\n\n.workspace-drag-content {\n  text-align: center;\n  color: #667eea;\n}\n\n.workspace-drag-icon {\n  font-size: 48px;\n  margin-bottom: 12px;\n  animation: bounce 1s infinite;\n}\n\n.workspace-drag-text {\n  font-size: 16px;\n  font-weight: 600;\n  color: #667eea;\n}\n\n@keyframes bounce {\n  0%, 20%, 50%, 80%, 100% {\n    transform: translateY(0);\n  }\n  40% {\n    transform: translateY(-10px);\n  }\n  60% {\n    transform: translateY(-5px);\n  }\n}\n\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/WorkspacePanel.css"],"names":[],"mappings":"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","sourcesContent":[".workspace-panel {\n  position: fixed;\n  top: 48px;\n  right: 0;\n  bottom: 32px;\n  width: 300px;\n  background: white;\n  border-left: 1px solid #e5e7eb;\n  display: flex;\n  flex-direction: column;\n  z-index: 800;\n  transition: transform 0.3s ease;\n  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.05);\n}\n\n.workspace-panel.closed {\n  transform: translateX(100%);\n}\n\n.workspace-panel.open {\n  transform: translateX(0);\n}\n\n.workspace-panel-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 12px 16px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n}\n\n.workspace-panel-title {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-size: 14px;\n  font-weight: 600;\n  color: #1f2937;\n  flex: 1;\n}\n\n.workspace-info-tooltip-wrapper {\n  position: relative;\n  display: flex;\n  align-items: center;\n  margin-left: 8px;\n  cursor: help;\n}\n\n.workspace-info-tooltip-wrapper .info-icon {\n  color: #94a3b8;\n  cursor: help;\n  transition: color 0.2s ease;\n  pointer-events: auto;\n}\n\n.workspace-info-tooltip-wrapper:hover .info-icon {\n  color: #667eea;\n}\n\n.workspace-info-tooltip {\n  position: fixed;\n  top: 60px;\n  right: 20px;\n  width: 320px;\n  max-width: calc(100vw - 40px);\n  padding: 14px 16px;\n  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);\n  border: 2px solid #e0e7ff;\n  border-radius: 12px;\n  box-shadow: 0 8px 32px rgba(102, 126, 234, 0.25), 0 4px 12px rgba(0, 0, 0, 0.15);\n  font-size: 13px;\n  line-height: 1.6;\n  color: #334155;\n  opacity: 0;\n  visibility: hidden;\n  transition: opacity 0.3s ease, visibility 0.3s ease, transform 0.3s ease;\n  z-index: 9999;\n  pointer-events: none;\n  font-weight: 500;\n  transform-origin: top right;\n  white-space: normal;\n  word-wrap: break-word;\n  word-break: break-word;\n  overflow-wrap: break-word;\n  hyphens: auto;\n}\n\n.workspace-info-tooltip code {\n  background: #e0e7ff;\n  color: #4f46e5;\n  padding: 2px 6px;\n  border-radius: 4px;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  font-size: 12px;\n  font-weight: 600;\n  white-space: nowrap;\n}\n\n.workspace-info-tooltip-wrapper:hover .workspace-info-tooltip {\n  opacity: 1;\n  visibility: visible;\n  pointer-events: auto;\n  transform: translateY(2px);\n}\n\n.workspace-panel-actions {\n  display: flex;\n  gap: 4px;\n}\n\n.workspace-refresh-btn,\n.workspace-close-btn {\n  background: none;\n  border: none;\n  color: #6b7280;\n  cursor: pointer;\n  padding: 4px;\n  border-radius: 4px;\n  display: flex;\n  align-items: center;\n  transition: all 0.2s;\n}\n\n.workspace-refresh-btn:hover,\n.workspace-close-btn:hover {\n  background: #e5e7eb;\n  color: #1f2937;\n}\n\n.workspace-panel-content {\n  flex: 1;\n  overflow-y: auto;\n  padding: 12px;\n}\n\n.workspace-error {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  padding: 32px 16px;\n  text-align: center;\n  color: #ef4444;\n}\n\n.workspace-error button {\n  margin-top: 12px;\n  padding: 6px 16px;\n  background: #ef4444;\n  color: white;\n  border: none;\n  border-radius: 6px;\n  cursor: pointer;\n  font-size: 13px;\n}\n\n.workspace-error button:hover {\n  background: #dc2626;\n}\n\n.workspace-empty {\n  display: flex;\n  flex-direction: column;\n  align-items: center;\n  justify-content: center;\n  padding: 48px 16px;\n  text-align: center;\n  color: #9ca3af;\n}\n\n.workspace-empty .empty-icon {\n  opacity: 0.3;\n  margin-bottom: 16px;\n}\n\n.workspace-empty p {\n  font-size: 14px;\n  font-weight: 500;\n  margin: 0 0 8px 0;\n  color: #6b7280;\n}\n\n.workspace-empty small {\n  font-size: 12px;\n  color: #9ca3af;\n}\n\n.file-tree {\n  font-size: 13px;\n}\n\n.file-tree-item {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 8px;\n  cursor: pointer;\n  border-radius: 4px;\n  transition: background 0.2s;\n  position: relative;\n  user-select: none;\n}\n\n.file-tree-item:hover {\n  background: #f3f4f6;\n}\n\n.file-tree-item.highlighted {\n  background: #dbeafe !important;\n  border: 1px solid #3b82f6;\n  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.3);\n}\n\n.file-tree-item.directory {\n  font-weight: 500;\n  color: #374151;\n}\n\n.file-tree-item.file {\n  color: #6b7280;\n}\n\n.folder-icon {\n  flex-shrink: 0;\n  color: #9ca3af;\n}\n\n.folder-icon-spacer {\n  width: 16px;\n  flex-shrink: 0;\n}\n\n.item-icon {\n  flex-shrink: 0;\n  color: #667eea;\n}\n\n.file-tree-item.file .item-icon {\n  color: #94a3b8;\n}\n\n.item-name {\n  flex: 1;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  white-space: nowrap;\n}\n\n.download-icon-btn {\n  background: none;\n  border: none;\n  color: #9ca3af;\n  cursor: pointer;\n  padding: 2px;\n  border-radius: 3px;\n  display: flex;\n  align-items: center;\n  opacity: 0;\n  transition: all 0.2s;\n}\n\n.file-tree-item:hover .download-icon-btn {\n  opacity: 1;\n}\n\n.download-icon-btn:hover {\n  background: #e5e7eb;\n  color: #667eea;\n}\n\n.folder-children {\n  margin-top: 2px;\n}\n\n.workspace-toggle-btn {\n  position: fixed;\n  top: 60px;\n  right: 16px;\n  width: 40px;\n  height: 40px;\n  background: white;\n  border: 1px solid #e5e7eb;\n  border-radius: 8px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  cursor: pointer;\n  z-index: 750;\n  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);\n  transition: all 0.2s;\n  color: #667eea;\n}\n\n.workspace-toggle-btn:hover {\n  background: #f9fafb;\n  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);\n  transform: translateY(-1px);\n}\n\n/* File Viewer Modal */\n.file-viewer-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1100;\n  animation: fadeIn 0.2s ease;\n}\n\n@keyframes fadeIn {\n  from {\n    opacity: 0;\n  }\n  to {\n    opacity: 1;\n  }\n}\n\n.file-viewer-modal {\n  background: white;\n  border-radius: 12px;\n  width: 90%;\n  max-width: 800px;\n  max-height: 80vh;\n  display: flex;\n  flex-direction: column;\n  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);\n  animation: slideUp 0.3s ease;\n}\n\n@keyframes slideUp {\n  from {\n    opacity: 0;\n    transform: translateY(20px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.file-viewer-header {\n  display: flex;\n  justify-content: space-between;\n  align-items: center;\n  padding: 16px 20px;\n  border-bottom: 1px solid #e5e7eb;\n  background: #f9fafb;\n  border-radius: 12px 12px 0 0;\n}\n\n.file-viewer-title {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  font-size: 15px;\n  font-weight: 600;\n  color: #1f2937;\n}\n\n.file-viewer-title svg {\n  color: #667eea;\n}\n\n.file-viewer-actions {\n  display: flex;\n  gap: 8px;\n}\n\n.file-viewer-btn {\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 12px;\n  background: #667eea;\n  color: white;\n  border: none;\n  border-radius: 6px;\n  font-size: 13px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s;\n}\n\n.file-viewer-btn:hover {\n  background: #5568d3;\n  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);\n}\n\n.file-viewer-close {\n  background: none;\n  border: none;\n  color: #6b7280;\n  cursor: pointer;\n  padding: 4px;\n  border-radius: 4px;\n  display: flex;\n  align-items: center;\n  transition: all 0.2s;\n}\n\n.file-viewer-close:hover {\n  background: #e5e7eb;\n  color: #1f2937;\n}\n\n.file-viewer-content {\n  flex: 1;\n  overflow: auto;\n  padding: 20px;\n  background: #fafafa;\n}\n\n.file-viewer-content pre {\n  margin: 0;\n  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', monospace;\n  font-size: 13px;\n  line-height: 1.6;\n  color: #1f2937;\n  white-space: pre-wrap;\n  word-wrap: break-word;\n}\n\n.workspace-loading-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(255, 255, 255, 0.8);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1050;\n}\n\n.workspace-spinner {\n  width: 40px;\n  height: 40px;\n  border: 4px solid #e5e7eb;\n  border-top-color: #667eea;\n  border-radius: 50%;\n  animation: spin 0.8s linear infinite;\n}\n\n@keyframes spin {\n  to {\n    transform: rotate(360deg);\n  }\n}\n\n.workspace-panel-content::-webkit-scrollbar {\n  width: 6px;\n}\n\n.workspace-panel-content::-webkit-scrollbar-track {\n  background: transparent;\n}\n\n.workspace-panel-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 3px;\n}\n\n.workspace-panel-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n.file-viewer-content::-webkit-scrollbar {\n  width: 8px;\n  height: 8px;\n}\n\n.file-viewer-content::-webkit-scrollbar-track {\n  background: #f1f5f9;\n}\n\n.file-viewer-content::-webkit-scrollbar-thumb {\n  background: #cbd5e1;\n  border-radius: 4px;\n}\n\n.file-viewer-content::-webkit-scrollbar-thumb:hover {\n  background: #94a3b8;\n}\n\n/* Delete functionality styles */\n.file-actions {\n  display: flex;\n  gap: 4px;\n  opacity: 0;\n  transition: opacity 0.2s ease;\n}\n\n.file-tree-item:hover .file-actions {\n  opacity: 1;\n}\n\n.delete-icon-btn {\n  background: none;\n  border: none;\n  padding: 2px;\n  border-radius: 3px;\n  color: #6b7280;\n  cursor: pointer;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  transition: all 0.2s ease;\n}\n\n.delete-icon-btn:hover {\n  background: #fee2e2;\n  color: #dc2626;\n}\n\n.delete-confirmation-overlay {\n  position: fixed;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(0, 0, 0, 0.5);\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1000;\n}\n\n.delete-confirmation-modal {\n  background: white;\n  border-radius: 8px;\n  padding: 0;\n  max-width: 400px;\n  width: 90%;\n  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);\n}\n\n.delete-confirmation-header {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  padding: 20px 20px 16px 20px;\n  border-bottom: 1px solid #e5e7eb;\n}\n\n.delete-confirmation-header h3 {\n  margin: 0;\n  font-size: 18px;\n  font-weight: 600;\n  color: #1f2937;\n}\n\n.delete-icon {\n  color: #dc2626;\n}\n\n.delete-confirmation-content {\n  padding: 16px 20px;\n}\n\n.delete-confirmation-content p {\n  margin: 0 0 8px 0;\n  color: #374151;\n  font-size: 14px;\n}\n\n.delete-warning {\n  color: #dc2626;\n  font-weight: 500;\n}\n\n.delete-confirmation-actions {\n  display: flex;\n  gap: 12px;\n  justify-content: flex-end;\n  padding: 16px 20px 20px 20px;\n  border-top: 1px solid #e5e7eb;\n}\n\n.delete-cancel-btn {\n  padding: 8px 16px;\n  border: 1px solid #d1d5db;\n  background: white;\n  color: #374151;\n  border-radius: 6px;\n  font-size: 14px;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.delete-cancel-btn:hover:not(:disabled) {\n  background: #f9fafb;\n  border-color: #9ca3af;\n}\n\n.delete-confirm-btn {\n  padding: 8px 16px;\n  border: none;\n  background: #dc2626;\n  color: white;\n  border-radius: 6px;\n  font-size: 14px;\n  font-weight: 500;\n  cursor: pointer;\n  transition: all 0.2s ease;\n}\n\n.delete-confirm-btn:hover:not(:disabled) {\n  background: #b91c1c;\n}\n\n.delete-cancel-btn:disabled,\n.delete-confirm-btn:disabled {\n  opacity: 0.6;\n  cursor: not-allowed;\n}\n\n/* Drag and drop styles */\n.workspace-panel.drag-over {\n  border-color: #667eea;\n  box-shadow: -2px 0 12px rgba(102, 126, 234, 0.3);\n}\n\n.workspace-drag-overlay {\n  position: absolute;\n  top: 0;\n  left: 0;\n  right: 0;\n  bottom: 0;\n  background: rgba(102, 126, 234, 0.1);\n  border: 2px dashed #667eea;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  z-index: 1000;\n  pointer-events: none;\n}\n\n.workspace-drag-content {\n  text-align: center;\n  color: #667eea;\n}\n\n.workspace-drag-icon {\n  font-size: 48px;\n  margin-bottom: 12px;\n  animation: bounce 1s infinite;\n}\n\n.workspace-drag-text {\n  font-size: 16px;\n  font-weight: 600;\n  color: #667eea;\n}\n\n@keyframes bounce {\n  0%, 20%, 50%, 80%, 100% {\n    transform: translateY(0);\n  }\n  40% {\n    transform: translateY(-10px);\n  }\n  60% {\n    transform: translateY(-5px);\n  }\n}\n\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/WriteableElementExample.css":
/*!**************************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/WriteableElementExample.css ***!
  \**************************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".floating-toggle {\n  width: fit-content;\n  margin-bottom: 6px;\n  top: 20px;\n  right: 20px;\n  background: #e0f2fe;\n  border-radius: 20px;\n  border: 1px solid #b3e5fc;\n  cursor: pointer;\n  z-index: 1000;\n  transition: all 0.2s ease;\n  font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif;\n  user-select: none;\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 12px;\n  height: 32px;\n  box-sizing: border-box;\n}\n\n.floating-toggle:hover {\n  background: #b3e5fc;\n  transform: translateY(-1px);\n}\n\n.toggle-icon {\n  font-size: 14px;\n  line-height: 1;\n}\n\n.toggle-text {\n  font-size: 12px;\n  font-weight: 500;\n  color: #0277bd;\n  line-height: 1;\n}\n\n/* Mobile positioning */\n@media (max-width: 768px) {\n  .floating-toggle {\n    top: 15px;\n    right: 15px;\n    height: 30px;\n    padding: 5px 10px;\n  }\n\n  .toggle-icon {\n    font-size: 13px;\n  }\n\n  .toggle-text {\n    font-size: 11px;\n  }\n}\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/WriteableElementExample.css"],"names":[],"mappings":"AAAA;EACE,kBAAkB;EAClB,kBAAkB;EAClB,SAAS;EACT,WAAW;EACX,mBAAmB;EACnB,mBAAmB;EACnB,yBAAyB;EACzB,eAAe;EACf,aAAa;EACb,yBAAyB;EACzB,8EAA8E;EAC9E,iBAAiB;EACjB,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,iBAAiB;EACjB,YAAY;EACZ,sBAAsB;AACxB;;AAEA;EACE,mBAAmB;EACnB,2BAA2B;AAC7B;;AAEA;EACE,eAAe;EACf,cAAc;AAChB;;AAEA;EACE,eAAe;EACf,gBAAgB;EAChB,cAAc;EACd,cAAc;AAChB;;AAEA,uBAAuB;AACvB;EACE;IACE,SAAS;IACT,WAAW;IACX,YAAY;IACZ,iBAAiB;EACnB;;EAEA;IACE,eAAe;EACjB;;EAEA;IACE,eAAe;EACjB;AACF","sourcesContent":[".floating-toggle {\n  width: fit-content;\n  margin-bottom: 6px;\n  top: 20px;\n  right: 20px;\n  background: #e0f2fe;\n  border-radius: 20px;\n  border: 1px solid #b3e5fc;\n  cursor: pointer;\n  z-index: 1000;\n  transition: all 0.2s ease;\n  font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif;\n  user-select: none;\n  display: flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 12px;\n  height: 32px;\n  box-sizing: border-box;\n}\n\n.floating-toggle:hover {\n  background: #b3e5fc;\n  transform: translateY(-1px);\n}\n\n.toggle-icon {\n  font-size: 14px;\n  line-height: 1;\n}\n\n.toggle-text {\n  font-size: 12px;\n  font-weight: 500;\n  color: #0277bd;\n  line-height: 1;\n}\n\n/* Mobile positioning */\n@media (max-width: 768px) {\n  .floating-toggle {\n    top: 15px;\n    right: 15px;\n    height: 30px;\n    padding: 5px 10px;\n  }\n\n  .toggle-icon {\n    font-size: 13px;\n  }\n\n  .toggle-text {\n    font-size: 11px;\n  }\n}\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ })

}]);
//# sourceMappingURL=main-agentic_chat_src_T.8a05593334d24a015575.js.map