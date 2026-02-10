"use strict";
(self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] = self["webpackChunk_carbon_ai_chat_examples_web_components_basic"] || []).push([["main-agentic_chat_src_Polici"],{

/***/ "../agentic_chat/src/PoliciesConfig.tsx":
/*!**********************************************!*\
  !*** ../agentic_chat/src/PoliciesConfig.tsx ***!
  \**********************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony export */ __webpack_require__.d(__webpack_exports__, {
/* harmony export */   "default": function() { return /* binding */ PoliciesConfig; }
/* harmony export */ });
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _ConfigModal_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./ConfigModal.css */ "../agentic_chat/src/ConfigModal.css");
// eslint-disable-next-line @typescript-eslint/no-unused-vars



function MultiSelect({
  items,
  selectedValues,
  onChange,
  placeholder,
  disabled,
  allowWildcard
}) {
  const [isOpen, setIsOpen] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  const [searchTerm, setSearchTerm] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const dropdownRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);
  const filteredItems = items.filter(item => item.label.toLowerCase().includes(searchTerm.toLowerCase()) || item.description?.toLowerCase().includes(searchTerm.toLowerCase()));
  const hasWildcard = selectedValues.includes("*");
  const toggleItem = value => {
    if (value === "*") {
      onChange(hasWildcard ? [] : ["*"]);
    } else {
      if (hasWildcard) {
        onChange([value]);
      } else {
        const newValues = selectedValues.includes(value) ? selectedValues.filter(v => v !== value) : [...selectedValues, value];
        onChange(newValues);
      }
    }
  };
  const displayText = hasWildcard ? "All (*)" : selectedValues.length === 0 ? placeholder || "Select..." : `${selectedValues.length} selected`;
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    ref: dropdownRef,
    style: {
      position: "relative",
      width: "100%"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    onClick: () => !disabled && setIsOpen(!isOpen),
    style: {
      padding: "8px 12px",
      border: "1px solid #e5e7eb",
      borderRadius: "6px",
      cursor: disabled ? "not-allowed" : "pointer",
      backgroundColor: disabled ? "#f9fafb" : "#fff",
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    style: {
      color: selectedValues.length === 0 ? "#9ca3af" : "#111827"
    }
  }, displayText), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
    size: 16,
    style: {
      transform: isOpen ? "rotate(180deg)" : "none",
      transition: "transform 0.2s"
    }
  })), isOpen && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      position: "absolute",
      top: "100%",
      left: 0,
      right: 0,
      marginTop: "4px",
      backgroundColor: "#fff",
      border: "1px solid #e5e7eb",
      borderRadius: "6px",
      boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
      maxHeight: "300px",
      overflow: "hidden",
      zIndex: 1000,
      display: "flex",
      flexDirection: "column"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      padding: "8px",
      borderBottom: "1px solid #e5e7eb"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      position: "relative"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Search, {
    size: 16,
    style: {
      position: "absolute",
      left: "8px",
      top: "50%",
      transform: "translateY(-50%)",
      color: "#9ca3af"
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "text",
    value: searchTerm,
    onChange: e => setSearchTerm(e.target.value),
    placeholder: "Search...",
    style: {
      width: "100%",
      padding: "6px 6px 6px 32px",
      border: "1px solid #e5e7eb",
      borderRadius: "4px",
      fontSize: "13px"
    },
    onClick: e => e.stopPropagation()
  }))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      overflowY: "auto",
      maxHeight: "240px"
    }
  }, allowWildcard && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    onClick: () => toggleItem("*"),
    style: {
      padding: "8px 12px",
      cursor: "pointer",
      backgroundColor: hasWildcard ? "#eff6ff" : "transparent",
      borderBottom: "1px solid #f3f4f6",
      display: "flex",
      alignItems: "center",
      gap: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: hasWildcard,
    readOnly: true,
    style: {
      cursor: "pointer"
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: "13px"
    }
  }, "All (*)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "12px",
      color: "#6b7280"
    }
  }, "Select all items"))), filteredItems.map(item => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: item.value,
    onClick: () => toggleItem(item.value),
    style: {
      padding: "8px 12px",
      cursor: "pointer",
      backgroundColor: selectedValues.includes(item.value) ? "#eff6ff" : "transparent",
      borderBottom: "1px solid #f3f4f6",
      display: "flex",
      alignItems: "center",
      gap: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: selectedValues.includes(item.value),
    readOnly: true,
    style: {
      cursor: "pointer"
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      flex: 1
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontWeight: 500,
      fontSize: "13px"
    }
  }, item.label), item.description && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      fontSize: "12px",
      color: "#6b7280",
      marginTop: "2px"
    }
  }, item.description)))), filteredItems.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      padding: "16px",
      textAlign: "center",
      color: "#9ca3af",
      fontSize: "13px"
    }
  }, "No items found"))));
}
function TagInput({
  values,
  onChange,
  placeholder,
  disabled
}) {
  const [inputValue, setInputValue] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("");
  const inputRef = (0,react__WEBPACK_IMPORTED_MODULE_0__.useRef)(null);
  const addTag = tag => {
    const trimmed = tag.trim();
    if (trimmed && !values.includes(trimmed)) {
      onChange([...values, trimmed]);
    }
    setInputValue("");
  };
  const removeTag = index => {
    onChange(values.filter((_, i) => i !== index));
  };
  const handleKeyDown = e => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(inputValue);
    } else if (e.key === "Backspace" && !inputValue && values.length > 0) {
      removeTag(values.length - 1);
    }
  };
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    onClick: () => !disabled && inputRef.current?.focus(),
    style: {
      border: "1px solid #e5e7eb",
      borderRadius: "6px",
      padding: "6px",
      minHeight: "42px",
      display: "flex",
      flexWrap: "wrap",
      gap: "6px",
      alignItems: "center",
      cursor: disabled ? "not-allowed" : "text",
      backgroundColor: disabled ? "#f9fafb" : "#fff"
    }
  }, values.map((tag, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    style: {
      display: "flex",
      alignItems: "center",
      gap: "4px",
      padding: "4px 8px",
      backgroundColor: "#eff6ff",
      border: "1px solid #dbeafe",
      borderRadius: "4px",
      fontSize: "13px",
      color: "#1e40af"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, tag), !disabled && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: e => {
      e.stopPropagation();
      removeTag(index);
    },
    style: {
      background: "none",
      border: "none",
      cursor: "pointer",
      padding: "0",
      display: "flex",
      alignItems: "center",
      color: "#3b82f6",
      fontSize: "16px",
      lineHeight: "1"
    },
    title: "Remove"
  }, "\xD7"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    ref: inputRef,
    type: "text",
    value: inputValue,
    onChange: e => setInputValue(e.target.value),
    onKeyDown: handleKeyDown,
    onBlur: () => {
      if (inputValue.trim()) {
        addTag(inputValue);
      }
    },
    placeholder: values.length === 0 ? placeholder : "",
    disabled: disabled,
    style: {
      border: "none",
      outline: "none",
      flex: 1,
      minWidth: "120px",
      padding: "4px",
      fontSize: "13px",
      backgroundColor: "transparent"
    }
  }));
}
function PoliciesConfig({
  onClose
}) {
  const [config, setConfig] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)({
    enablePolicies: true,
    policies: []
  });
  const [activeTab, setActiveTab] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("intent_guard");
  const [expandedPolicy, setExpandedPolicy] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(null);
  const [saveStatus, setSaveStatus] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)("idle");
  const [isLoading, setIsLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(true);
  const [availableTools, setAvailableTools] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [availableApps, setAvailableApps] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)([]);
  const [toolsLoading, setToolsLoading] = (0,react__WEBPACK_IMPORTED_MODULE_0__.useState)(false);
  (0,react__WEBPACK_IMPORTED_MODULE_0__.useEffect)(() => {
    loadConfig();
    loadTools();
  }, []);
  const loadConfig = async () => {
    setIsLoading(true);
    try {
      console.log("[PoliciesConfig] Loading policies from server...");
      const response = await fetch("/api/config/policies");
      console.log("[PoliciesConfig] Response status:", response.status);
      if (response.ok) {
        const data = await response.json();
        console.log("[PoliciesConfig] Loaded policies:", data);

        // Normalize natural_language trigger values to always be arrays (for backward compatibility)
        const normalizedPolicies = (data.policies ?? []).map(policy => ({
          ...policy,
          triggers: policy.triggers.map(trigger => {
            if (trigger.type === "natural_language" && trigger.value !== undefined) {
              // Ensure value is always an array for natural_language triggers
              const normalizedValue = Array.isArray(trigger.value) ? trigger.value : typeof trigger.value === "string" ? [trigger.value] : [];
              return {
                ...trigger,
                value: normalizedValue
              };
            }
            return trigger;
          })
        }));
        setConfig({
          enablePolicies: data.enablePolicies ?? true,
          policies: normalizedPolicies
        });
      } else {
        const errorText = await response.text();
        console.error("[PoliciesConfig] Failed to load policies:", response.status, errorText);
      }
    } catch (error) {
      console.error("[PoliciesConfig] Error loading config:", error);
    } finally {
      setIsLoading(false);
    }
  };
  const loadTools = async () => {
    setToolsLoading(true);
    try {
      console.log("[PoliciesConfig] Loading tools from server...");
      const response = await fetch("/api/tools/list");
      if (response.ok) {
        const data = await response.json();
        console.log("[PoliciesConfig] Loaded tools:", data);
        setAvailableTools(data.tools || []);
        setAvailableApps(data.apps || []);
      } else {
        console.error("[PoliciesConfig] Failed to load tools:", response.status);
      }
    } catch (error) {
      console.error("[PoliciesConfig] Error loading tools:", error);
    } finally {
      setToolsLoading(false);
    }
  };
  const exportPolicies = () => {
    try {
      const dataStr = JSON.stringify(config, null, 2);
      const dataBlob = new Blob([dataStr], {
        type: "application/json"
      });
      const url = URL.createObjectURL(dataBlob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `policies-export-${new Date().toISOString().split("T")[0]}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      console.log("[PoliciesConfig] Exported policies:", config.policies.length);
    } catch (error) {
      console.error("[PoliciesConfig] Export error:", error);
      alert("Failed to export policies. Check console for details.");
    }
  };
  const importPolicies = event => {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
      try {
        const importedData = JSON.parse(e.target?.result);
        if (importedData.policies && Array.isArray(importedData.policies)) {
          // Normalize natural_language trigger values to always be arrays (for backward compatibility)
          const normalizedPolicies = importedData.policies.map(policy => ({
            ...policy,
            triggers: policy.triggers.map(trigger => {
              if (trigger.type === "natural_language" && trigger.value !== undefined) {
                // Ensure value is always an array for natural_language triggers
                const normalizedValue = Array.isArray(trigger.value) ? trigger.value : typeof trigger.value === "string" ? [trigger.value] : [];
                return {
                  ...trigger,
                  value: normalizedValue
                };
              }
              return trigger;
            })
          }));
          setConfig({
            enablePolicies: importedData.enablePolicies ?? config.enablePolicies,
            policies: normalizedPolicies
          });
          console.log("[PoliciesConfig] Imported policies:", normalizedPolicies.length);
          alert(`Successfully imported ${normalizedPolicies.length} policies!`);
        } else {
          alert('Invalid policies file format. Expected a JSON file with a "policies" array.');
        }
      } catch (error) {
        console.error("[PoliciesConfig] Import error:", error);
        alert("Failed to import policies. Please check the file format.");
      }
    };
    reader.readAsText(file);
    // Reset input so the same file can be imported again
    event.target.value = "";
  };
  const saveConfig = async () => {
    // Force blur on any focused input to ensure pending changes are saved
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }

    // Small delay to ensure blur event handlers complete
    await new Promise(resolve => setTimeout(resolve, 50));
    setSaveStatus("saving");
    try {
      // Normalize natural_language trigger values to always be arrays
      const normalizedConfig = {
        ...config,
        policies: config.policies.map(policy => ({
          ...policy,
          triggers: policy.triggers.map(trigger => {
            if (trigger.type === "natural_language" && trigger.value !== undefined) {
              // Ensure value is always an array for natural_language triggers
              const normalizedValue = Array.isArray(trigger.value) ? trigger.value : typeof trigger.value === "string" ? [trigger.value] : [];
              return {
                ...trigger,
                value: normalizedValue
              };
            }
            return trigger;
          })
        }))
      };
      console.log("[PoliciesConfig] Saving config:", normalizedConfig);
      console.log("[PoliciesConfig] Policies count:", normalizedConfig.policies.length);
      normalizedConfig.policies.forEach((policy, idx) => {
        console.log(`[PoliciesConfig] Policy ${idx}: ${policy.name}`);
        console.log(`[PoliciesConfig] Policy ${idx} triggers:`, policy.triggers);
        // Log keyword trigger operators specifically
        policy.triggers.forEach((trigger, triggerIdx) => {
          if (trigger.type === "keyword") {
            console.log(`[PoliciesConfig] Policy ${idx} trigger ${triggerIdx}: type=keyword, operator=${trigger.operator || "MISSING"}, keywords=${JSON.stringify(trigger.value)}`);
          } else if (trigger.type === "natural_language") {
            console.log(`[PoliciesConfig] Policy ${idx} trigger ${triggerIdx}: type=natural_language, values=${JSON.stringify(trigger.value)}`);
          }
        });
      });
      const response = await fetch("/api/config/policies", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(normalizedConfig)
      });
      console.log("[PoliciesConfig] Response status:", response.status);
      if (response.ok) {
        const result = await response.json();
        console.log("[PoliciesConfig] Save successful:", result);
        setSaveStatus("success");
        setTimeout(() => setSaveStatus("idle"), 2000);
      } else {
        const errorText = await response.text();
        console.error("[PoliciesConfig] Save failed:", response.status, errorText);
        setSaveStatus("error");
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (error) {
      console.error("[PoliciesConfig] Save error:", error);
      setSaveStatus("error");
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };
  const addIntentGuard = () => {
    const newPolicy = {
      id: `guard_${Date.now()}`,
      name: "New Intent Guard",
      description: "Blocks or modifies specific user intents",
      policy_type: "intent_guard",
      enabled: true,
      triggers: [{
        type: "keyword",
        value: [],
        target: "intent",
        case_sensitive: false,
        operator: "and"
      }],
      response: {
        response_type: "natural_language",
        content: "This action is not allowed."
      },
      allow_override: false,
      priority: 50
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy]
    });
  };
  const addPlaybook = () => {
    const newPolicy = {
      id: `playbook_${Date.now()}`,
      name: "New Playbook",
      description: "Step-by-step guidance for a task",
      policy_type: "playbook",
      enabled: true,
      triggers: [{
        type: "keyword",
        value: [],
        target: "intent",
        case_sensitive: false,
        operator: "and"
      }],
      markdown_content: "# Task Guide\n\n## Steps:\n\n1. First step\n2. Second step\n3. Third step",
      steps: [{
        step_number: 1,
        instruction: "First step",
        expected_outcome: "Step 1 complete",
        tools_allowed: []
      }],
      priority: 50
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy]
    });
  };
  const addToolGuide = () => {
    const newPolicy = {
      id: `tool_guide_${Date.now()}`,
      name: "New Tool Guide",
      description: "Add additional context to tool descriptions",
      policy_type: "tool_guide",
      enabled: true,
      triggers: [{
        type: "always"
      }],
      target_tools: ["*"],
      target_apps: undefined,
      guide_content: "## Additional Guidelines\n\n- Follow best practices\n- Consider security implications",
      prepend: false,
      priority: 50
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy]
    });
  };
  const addToolApproval = () => {
    const newPolicy = {
      id: `tool_approval_${Date.now()}`,
      name: "New Tool Approval",
      description: "Require approval before executing specific tools",
      policy_type: "tool_approval",
      enabled: true,
      triggers: [],
      // ToolApproval policies don't use triggers - they're checked after code generation
      required_tools: [],
      required_apps: undefined,
      approval_message: "This tool requires your approval before execution.",
      show_code_preview: true,
      auto_approve_after: undefined,
      priority: 50
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy]
    });
  };
  const addOutputFormatter = () => {
    const newPolicy = {
      id: `output_formatter_${Date.now()}`,
      name: "New Output Formatter",
      description: "Format the final AI message output",
      policy_type: "output_formatter",
      enabled: true,
      triggers: [{
        type: "keyword",
        value: [],
        target: "agent_response",
        case_sensitive: false,
        operator: "and"
      }],
      format_type: "markdown",
      format_config: "Format the response in a clear, structured way with proper headings and bullet points.",
      priority: 50
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy]
    });
  };
  const updatePolicy = (id, updates) => {
    setConfig({
      ...config,
      policies: config.policies.map(policy => policy.id === id ? {
        ...policy,
        ...updates
      } : policy)
    });
  };
  const removePolicy = id => {
    setConfig({
      ...config,
      policies: config.policies.filter(p => p.id !== id)
    });
  };
  const intentGuards = config.policies.filter(p => p.policy_type === "intent_guard");
  const playbooks = config.policies.filter(p => p.policy_type === "playbook");
  const ToolGuides = config.policies.filter(p => p.policy_type === "tool_guide");
  const toolApprovals = config.policies.filter(p => p.policy_type === "tool_approval");
  const outputFormatters = config.policies.filter(p => p.policy_type === "output_formatter");
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-overlay"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "12px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Shield, {
    size: 24,
    style: {
      color: "#4e00ec"
    }
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h2", null, "Policies Configuration")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "8px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    onClick: exportPolicies,
    disabled: config.policies.length === 0,
    style: {
      display: "flex",
      alignItems: "center",
      gap: "6px",
      padding: "6px 12px",
      backgroundColor: config.policies.length === 0 ? "#e5e7eb" : "#f3f4f6",
      border: "1px solid #d1d5db",
      borderRadius: "6px",
      cursor: config.policies.length === 0 ? "not-allowed" : "pointer",
      fontSize: "13px",
      fontWeight: 500,
      color: config.policies.length === 0 ? "#9ca3af" : "#374151"
    },
    title: "Export all policies as JSON"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Download, {
    size: 16
  }), "Export"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: "6px",
      padding: "6px 12px",
      backgroundColor: "#f3f4f6",
      border: "1px solid #d1d5db",
      borderRadius: "6px",
      cursor: "pointer",
      fontSize: "13px",
      fontWeight: 500,
      color: "#374151"
    },
    title: "Import policies from JSON"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Upload, {
    size: 16
  }), "Import", /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "file",
    accept: ".json",
    onChange: importPolicies,
    style: {
      display: "none"
    }
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "config-modal-close",
    onClick: onClose
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.X, {
    size: 20
  })))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-tabs"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "intent_guard" ? "active" : ""}`,
    onClick: () => setActiveTab("intent_guard")
  }, "Intent Guards (", intentGuards.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "playbook" ? "active" : ""}`,
    onClick: () => setActiveTab("playbook")
  }, "Playbooks (", playbooks.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "tool_guide" ? "active" : ""}`,
    onClick: () => setActiveTab("tool_guide")
  }, "Tool Guide (", ToolGuides.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "tool_approval" ? "active" : ""}`,
    onClick: () => setActiveTab("tool_approval")
  }, "Tool Approval (", toolApprovals.length, ")"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `config-tab ${activeTab === "output_formatter" ? "active" : ""}`,
    onClick: () => setActiveTab("output_formatter")
  }, "Output Formatter (", outputFormatters.length, ")")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-content"
  }, isLoading ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card",
    style: {
      textAlign: "center",
      padding: "40px"
    }
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "Loading policies...")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-card"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Global Settings"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-form"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "form-group"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
    className: "checkbox-label"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
    type: "checkbox",
    checked: config.enablePolicies,
    onChange: e => setConfig({
      ...config,
      enablePolicies: e.target.checked
    })
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Enable Policy System")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Master switch for all policy enforcement (", config.policies.length, " policies configured)")))), activeTab === "intent_guard" && renderIntentGuards(), activeTab === "playbook" && renderPlaybooks(), activeTab === "tool_guide" && renderToolGuides(), activeTab === "tool_approval" && renderToolApprovals(), activeTab === "output_formatter" && renderOutputFormatters())), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "config-modal-footer"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: "cancel-btn",
    onClick: onClose
  }, "Cancel"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
    className: `save-btn ${saveStatus}`,
    onClick: saveConfig,
    disabled: saveStatus === "saving"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Save, {
    size: 16
  }), saveStatus === "idle" && "Save Changes", saveStatus === "saving" && "Saving...", saveStatus === "success" && "Saved!", saveStatus === "error" && "Error!"))));
  function renderIntentGuards() {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "section-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Intent Guards"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "add-btn",
      onClick: addIntentGuard,
      disabled: !config.enablePolicies
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
      size: 16
    }), "Add Intent Guard")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "sources-list"
    }, intentGuards.map(policy => {
      const isExpanded = expandedPolicy === policy.id;
      const keywordTrigger = policy.triggers.find(t => t.type === "keyword");
      const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: policy.id,
        className: "agent-config-card"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-header"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-top"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.enabled,
        onChange: e => updatePolicy(policy.id, {
          enabled: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "text",
        value: policy.name,
        onChange: e => updatePolicy(policy.id, {
          name: e.target.value
        }),
        className: "agent-config-name",
        placeholder: "Policy Name",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "expand-btn",
        onClick: () => setExpandedPolicy(isExpanded ? null : policy.id)
      }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
        size: 16
      }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
        size: 16
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "delete-btn",
        onClick: () => removePolicy(policy.id),
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
        size: 16
      }))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-summary"
      }, keywords.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, keywords.length, " keyword", keywords.length !== 1 ? "s" : ""), policy.triggers.some(t => t.type === "natural_language") && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, "AI trigger"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, "Priority: ", policy.priority))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-details"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.description,
        onChange: e => updatePolicy(policy.id, {
          description: e.target.value
        }),
        placeholder: "What this policy does...",
        rows: 2,
        disabled: !config.enablePolicies
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Trigger Keywords (Optional)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(TagInput, {
        values: keywords,
        onChange: newKeywords => {
          const updatedTriggers = policy.triggers.filter(t => t.type !== "keyword");
          if (newKeywords.length > 0) {
            const existingKeywordTrigger = policy.triggers.find(t => t.type === "keyword");
            updatedTriggers.push({
              type: "keyword",
              value: newKeywords,
              target: "intent",
              case_sensitive: false,
              operator: existingKeywordTrigger?.operator || "and"
            });
          }
          updatePolicy(policy.id, {
            triggers: updatedTriggers
          });
        },
        placeholder: "Type keyword and press Enter or comma",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Type keywords and press Enter or comma to add. Click \xD7 to remove.")), keywords.length > 1 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Keyword Matching"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
        value: keywordTrigger?.operator || "and",
        onChange: e => {
          const operator = e.target.value;
          const updatedTriggers = policy.triggers.map(t => t.type === "keyword" ? {
            ...t,
            operator
          } : t);
          updatePolicy(policy.id, {
            triggers: updatedTriggers
          });
        },
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "and"
      }, "Match ALL keywords (AND)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "or"
      }, "Match ANY keyword (OR)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Choose whether all keywords or any keyword should trigger this policy")), (() => {
        const nlTrigger = policy.triggers.find(t => t.type === "natural_language");
        const nlTriggerValues = nlTrigger ? Array.isArray(nlTrigger.value) ? nlTrigger.value : nlTrigger.value ? [nlTrigger.value] : [] : [];
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "form-group"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Natural Language Triggers"), nlTrigger ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(TagInput, {
          values: nlTriggerValues,
          onChange: newValues => {
            const updatedTriggers = policy.triggers.map(t => t.type === "natural_language" ? {
              ...t,
              value: newValues
            } : t);
            updatePolicy(policy.id, {
              triggers: updatedTriggers
            });
          },
          placeholder: "Type natural language trigger and press Enter",
          disabled: !config.enablePolicies
        }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "form-group",
          style: {
            marginTop: "12px"
          }
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Similarity Threshold"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
          type: "range",
          min: "0.5",
          max: "1.0",
          step: "0.05",
          value: nlTrigger.threshold || 0.7,
          onChange: e => {
            const updatedTriggers = policy.triggers.map(t => t.type === "natural_language" ? {
              ...t,
              threshold: parseFloat(e.target.value)
            } : t);
            updatePolicy(policy.id, {
              triggers: updatedTriggers
            });
          },
          disabled: !config.enablePolicies
        }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Threshold: ", (nlTrigger.threshold || 0.7).toFixed(2), " (higher = more strict matching)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: () => {
            const updatedTriggers = policy.triggers.filter(t => t.type !== "natural_language");
            updatePolicy(policy.id, {
              triggers: updatedTriggers
            });
          },
          disabled: !config.enablePolicies,
          style: {
            marginTop: "8px",
            padding: "6px 12px",
            backgroundColor: "#ef4444",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: config.enablePolicies ? "pointer" : "not-allowed",
            fontSize: "12px"
          }
        }, "Remove Natural Language Trigger")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: () => {
            const newTrigger = {
              type: "natural_language",
              value: [],
              target: "intent",
              threshold: 0.7
            };
            updatePolicy(policy.id, {
              triggers: [...policy.triggers, newTrigger]
            });
          },
          disabled: !config.enablePolicies,
          style: {
            padding: "6px 12px",
            backgroundColor: "#3b82f6",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: config.enablePolicies ? "pointer" : "not-allowed",
            fontSize: "13px"
          }
        }, "+ Add Natural Language Trigger"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Type natural language triggers and press Enter to add. AI will match similar intents using semantic understanding."));
      })(), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Response Message"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.response.content,
        onChange: e => updatePolicy(policy.id, {
          response: {
            ...policy.response,
            content: e.target.value
          }
        }),
        placeholder: "This action is not allowed.",
        rows: 3,
        disabled: !config.enablePolicies
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-row"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Priority"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "number",
        value: policy.priority,
        onChange: e => updatePolicy(policy.id, {
          priority: parseInt(e.target.value)
        }),
        min: "0",
        max: "100",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Higher priority policies are checked first")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", {
        className: "checkbox-label"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.allow_override,
        onChange: e => updatePolicy(policy.id, {
          allow_override: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Allow Override")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "User can bypass this policy")))));
    })), intentGuards.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "empty-state"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No intent guards configured. Click \"Add Intent Guard\" to create one.")));
  }
  function renderPlaybooks() {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "section-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Playbooks"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "add-btn",
      onClick: addPlaybook,
      disabled: !config.enablePolicies
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
      size: 16
    }), "Add Playbook")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "sources-list"
    }, playbooks.map(policy => {
      const isExpanded = expandedPolicy === policy.id;
      const keywordTrigger = policy.triggers.find(t => t.type === "keyword");
      const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: policy.id,
        className: "agent-config-card"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-header"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-top"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.enabled,
        onChange: e => updatePolicy(policy.id, {
          enabled: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "text",
        value: policy.name,
        onChange: e => updatePolicy(policy.id, {
          name: e.target.value
        }),
        className: "agent-config-name",
        placeholder: "Playbook Name",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "expand-btn",
        onClick: () => setExpandedPolicy(isExpanded ? null : policy.id)
      }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
        size: 16
      }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
        size: 16
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "delete-btn",
        onClick: () => removePolicy(policy.id),
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
        size: 16
      }))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-summary"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.steps.length, " step", policy.steps.length !== 1 ? "s" : ""), policy.triggers.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.triggers[0].type === "natural_language" ? "AI trigger" : `${keywords.length} keyword${keywords.length !== 1 ? "s" : ""}`))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-details"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.description,
        onChange: e => updatePolicy(policy.id, {
          description: e.target.value
        }),
        placeholder: "What this playbook guides the user through...",
        rows: 2,
        disabled: !config.enablePolicies
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Trigger Type"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
        value: policy.triggers.length > 0 && policy.triggers[0].type === "natural_language" ? "natural_language" : "keyword",
        onChange: e => {
          const triggerType = e.target.value;
          if (triggerType === "natural_language") {
            updatePolicy(policy.id, {
              triggers: [{
                type: "natural_language",
                value: [],
                target: "intent",
                threshold: 0.7
              }]
            });
          } else {
            updatePolicy(policy.id, {
              triggers: [{
                type: "keyword",
                value: [],
                target: "intent",
                case_sensitive: false,
                operator: "and"
              }]
            });
          }
        },
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "keyword"
      }, "Keywords (Exact Match)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "natural_language"
      }, "Natural Language (AI Match)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Choose how this playbook should be triggered")), policy.triggers.length > 0 && policy.triggers[0].type === "keyword" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Trigger Keywords"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(TagInput, {
        values: keywords,
        onChange: newKeywords => {
          const newTriggers = policy.triggers.map(t => t.type === "keyword" ? {
            ...t,
            value: newKeywords
          } : t);
          updatePolicy(policy.id, {
            triggers: newTriggers
          });
        },
        placeholder: "Type keyword and press Enter or comma",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Type keywords and press Enter or comma to add. Click \xD7 to remove.")), keywords.length > 1 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Keyword Matching"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
        value: keywordTrigger?.operator || "and",
        onChange: e => {
          const operator = e.target.value;
          const newTriggers = policy.triggers.map(t => t.type === "keyword" ? {
            ...t,
            operator
          } : t);
          updatePolicy(policy.id, {
            triggers: newTriggers
          });
        },
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "and"
      }, "Match ALL keywords (AND)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "or"
      }, "Match ANY keyword (OR)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Choose whether all keywords or any keyword should trigger this playbook"))), policy.triggers.length > 0 && policy.triggers[0].type === "natural_language" && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Natural Language Triggers"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(TagInput, {
        values: Array.isArray(policy.triggers[0].value) ? policy.triggers[0].value : policy.triggers[0].value ? [policy.triggers[0].value] : [],
        onChange: newTriggers => {
          const updatedTriggers = policy.triggers.map((t, idx) => idx === 0 ? {
            ...t,
            value: newTriggers
          } : t);
          updatePolicy(policy.id, {
            triggers: updatedTriggers
          });
        },
        placeholder: "Type trigger and press Enter",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Type natural language triggers and press Enter to add. AI will match similar user requests.")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Similarity Threshold"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "range",
        min: "0.5",
        max: "1.0",
        step: "0.05",
        value: policy.triggers[0].threshold || 0.7,
        onChange: e => {
          const newTriggers = policy.triggers.map((t, idx) => idx === 0 ? {
            ...t,
            threshold: parseFloat(e.target.value)
          } : t);
          updatePolicy(policy.id, {
            triggers: newTriggers
          });
        },
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Threshold: ", (policy.triggers[0].threshold || 0.7).toFixed(2), " (higher = more strict matching)"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Markdown Content"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.markdown_content,
        onChange: e => updatePolicy(policy.id, {
          markdown_content: e.target.value
        }),
        placeholder: "# Task Guide ## Steps: 1. First step\n2. Second step",
        rows: 8,
        disabled: !config.enablePolicies,
        style: {
          fontFamily: "monospace",
          fontSize: "13px"
        }
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Markdown-formatted guidance that will be shown to the agent")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Priority"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "number",
        value: policy.priority,
        onChange: e => updatePolicy(policy.id, {
          priority: parseInt(e.target.value)
        }),
        min: "0",
        max: "100",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Higher priority playbooks are checked first"))));
    })), playbooks.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "empty-state"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No playbooks configured. Click \"Add Playbook\" to create one.")));
  }
  function renderToolGuides() {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "section-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Tool Guide Policies"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "add-btn",
      onClick: addToolGuide,
      disabled: !config.enablePolicies
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
      size: 16
    }), "Add Tool Guide")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "sources-list"
    }, ToolGuides.map(policy => {
      const isExpanded = expandedPolicy === policy.id;
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: policy.id,
        className: "agent-config-card"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-header"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-top"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.enabled,
        onChange: e => updatePolicy(policy.id, {
          enabled: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "text",
        value: policy.name,
        onChange: e => updatePolicy(policy.id, {
          name: e.target.value
        }),
        className: "agent-config-name",
        placeholder: "Policy Name",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "expand-btn",
        onClick: () => setExpandedPolicy(isExpanded ? null : policy.id)
      }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
        size: 16
      }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
        size: 16
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "delete-btn",
        onClick: () => removePolicy(policy.id),
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
        size: 16
      }))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-summary"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.target_tools.includes("*") ? "All tools" : `${policy.target_tools.length} tool(s)`), policy.target_apps && policy.target_apps.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.target_apps.length, " app(s)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, "Priority: ", policy.priority))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-details"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.description,
        onChange: e => updatePolicy(policy.id, {
          description: e.target.value
        }),
        rows: 2,
        disabled: !config.enablePolicies
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Target Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(MultiSelect, {
        items: availableTools.map(tool => ({
          value: tool.name,
          label: tool.name,
          description: `${tool.app} - ${tool.description.substring(0, 60)}${tool.description.length > 60 ? "..." : ""}`
        })),
        selectedValues: policy.target_tools,
        onChange: values => updatePolicy(policy.id, {
          target_tools: values
        }),
        placeholder: toolsLoading ? "Loading tools..." : "Select tools to enrich",
        disabled: !config.enablePolicies || toolsLoading,
        allowWildcard: true
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Select specific tools to enrich, or use * to enrich all tools")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Target Apps (optional)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(MultiSelect, {
        items: availableApps.map(app => ({
          value: app.name,
          label: app.name,
          description: `${app.type} - ${app.tool_count} tool(s)`
        })),
        selectedValues: policy.target_apps || [],
        onChange: values => updatePolicy(policy.id, {
          target_apps: values.length > 0 ? values : undefined
        }),
        placeholder: toolsLoading ? "Loading apps..." : "Select apps (optional)",
        disabled: !config.enablePolicies || toolsLoading,
        allowWildcard: false
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Optionally filter by app name")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Guide Content (Markdown)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.guide_content,
        onChange: e => updatePolicy(policy.id, {
          guide_content: e.target.value
        }),
        placeholder: "## Additional Guidelines - Follow best practices\n- Consider security",
        rows: 6,
        disabled: !config.enablePolicies,
        style: {
          fontFamily: "monospace",
          fontSize: "13px"
        }
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Markdown content to add to tool descriptions")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.prepend,
        onChange: e => updatePolicy(policy.id, {
          prepend: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), "Prepend content (add before existing description)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Priority"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "number",
        value: policy.priority,
        onChange: e => updatePolicy(policy.id, {
          priority: parseInt(e.target.value)
        }),
        min: "0",
        max: "100",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Higher priority guides are applied first"))));
    })), ToolGuides.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "empty-state"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No tool guide policies configured. Click \"Add Tool Guide\" to create one.")));
  }
  function renderToolApprovals() {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "section-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Tool Approval Policies"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "add-btn",
      onClick: addToolApproval,
      disabled: !config.enablePolicies
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
      size: 16
    }), "Add Tool Approval")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "policies-list"
    }, toolApprovals.map(policy => {
      const isExpanded = expandedPolicy === policy.id;
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: policy.id,
        className: "agent-config-card"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-header"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-top"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.enabled,
        onChange: e => updatePolicy(policy.id, {
          enabled: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "text",
        value: policy.name,
        onChange: e => updatePolicy(policy.id, {
          name: e.target.value
        }),
        className: "agent-config-name",
        placeholder: "Policy Name",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "expand-btn",
        onClick: () => setExpandedPolicy(isExpanded ? null : policy.id)
      }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
        size: 16
      }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
        size: 16
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "delete-btn",
        onClick: () => removePolicy(policy.id),
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
        size: 16
      }))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-summary"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.required_tools.length === 0 ? "No tools selected" : policy.required_tools.includes("*") ? "All tools" : `${policy.required_tools.length} tool(s)`), policy.required_apps && policy.required_apps.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.required_apps.length, " app(s)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, "Priority: ", policy.priority))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-details"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.description,
        onChange: e => updatePolicy(policy.id, {
          description: e.target.value
        }),
        rows: 2,
        disabled: !config.enablePolicies
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Required Tools"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(MultiSelect, {
        items: availableTools.map(tool => ({
          value: tool.name,
          label: tool.name,
          description: `${tool.app} - ${tool.description.substring(0, 60)}${tool.description.length > 60 ? "..." : ""}`
        })),
        selectedValues: policy.required_tools,
        onChange: values => updatePolicy(policy.id, {
          required_tools: values
        }),
        placeholder: toolsLoading ? "Loading tools..." : "Select tools requiring approval",
        disabled: !config.enablePolicies || toolsLoading,
        allowWildcard: true
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Tools that require approval before execution")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Required Apps (optional)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(MultiSelect, {
        items: availableApps.map(app => ({
          value: app.name,
          label: app.name,
          description: `${app.type} - ${app.tool_count} tool(s)`
        })),
        selectedValues: policy.required_apps || [],
        onChange: values => updatePolicy(policy.id, {
          required_apps: values.length > 0 ? values : undefined
        }),
        placeholder: toolsLoading ? "Loading apps..." : "Select apps (optional)",
        disabled: !config.enablePolicies || toolsLoading,
        allowWildcard: false
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Optionally require approval for all tools from specific apps")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Approval Message (optional)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.approval_message || "",
        onChange: e => updatePolicy(policy.id, {
          approval_message: e.target.value || undefined
        }),
        placeholder: "This tool requires your approval before execution.",
        rows: 3,
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Custom message shown when requesting approval")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.show_code_preview,
        onChange: e => updatePolicy(policy.id, {
          show_code_preview: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), "Show code preview in approval request")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Auto-approve after (seconds, optional)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "number",
        value: policy.auto_approve_after || "",
        onChange: e => {
          const value = e.target.value ? parseInt(e.target.value) : undefined;
          updatePolicy(policy.id, {
            auto_approve_after: value
          });
        },
        min: "1",
        placeholder: "Leave empty for no auto-approve",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Automatically approve after N seconds (leave empty to disable)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Priority"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "number",
        value: policy.priority,
        onChange: e => updatePolicy(policy.id, {
          priority: parseInt(e.target.value)
        }),
        min: "0",
        max: "100",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Higher priority approval policies are checked first"))));
    })), toolApprovals.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "empty-state"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No tool approval policies configured. Click \"Add Tool Approval\" to create one.")));
  }
  function renderOutputFormatters() {
    return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "config-card"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "section-header"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Output Formatter Policies"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
      className: "add-btn",
      onClick: addOutputFormatter,
      disabled: !config.enablePolicies
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Plus, {
      size: 16
    }), "Add Output Formatter")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "policies-list"
    }, outputFormatters.map(policy => {
      const isExpanded = expandedPolicy === policy.id;
      const keywordTrigger = policy.triggers.find(t => t.type === "keyword");
      const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];
      return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        key: policy.id,
        className: "agent-config-card"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-header"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-top"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "checkbox",
        checked: policy.enabled,
        onChange: e => updatePolicy(policy.id, {
          enabled: e.target.checked
        }),
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "text",
        value: policy.name,
        onChange: e => updatePolicy(policy.id, {
          name: e.target.value
        }),
        className: "agent-config-name",
        placeholder: "Policy Name",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "expand-btn",
        onClick: () => setExpandedPolicy(isExpanded ? null : policy.id)
      }, isExpanded ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronUp, {
        size: 16
      }) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.ChevronDown, {
        size: 16
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
        className: "delete-btn",
        onClick: () => removePolicy(policy.id),
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Trash2, {
        size: 16
      }))), !isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-summary"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, policy.format_type === "direct" ? "Direct" : policy.format_type === "markdown" ? "Markdown (LLM)" : "JSON (LLM)"), keywords.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, keywords.length, " keyword", keywords.length !== 1 ? "s" : ""), policy.triggers.some(t => t.type === "natural_language") && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, "AI trigger"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
        className: "agent-summary-item"
      }, "Priority: ", policy.priority))), isExpanded && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "agent-config-details"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Description"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.description,
        onChange: e => updatePolicy(policy.id, {
          description: e.target.value
        }),
        rows: 2,
        disabled: !config.enablePolicies
      })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Trigger Keywords (Optional)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(TagInput, {
        values: keywords,
        onChange: newKeywords => {
          const updatedTriggers = policy.triggers.filter(t => t.type !== "keyword");
          if (newKeywords.length > 0) {
            const existingKeywordTrigger = policy.triggers.find(t => t.type === "keyword");
            updatedTriggers.push({
              type: "keyword",
              value: newKeywords,
              target: "agent_response",
              case_sensitive: false,
              operator: existingKeywordTrigger?.operator || "and"
            });
          }
          updatePolicy(policy.id, {
            triggers: updatedTriggers
          });
        },
        placeholder: "Type keyword and press Enter or comma",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Keywords to match against the last AI message content. Leave empty to always format.")), keywords.length > 1 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Keyword Matching"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
        value: keywordTrigger?.operator || "and",
        onChange: e => {
          const operator = e.target.value;
          const updatedTriggers = policy.triggers.map(t => t.type === "keyword" ? {
            ...t,
            operator
          } : t);
          updatePolicy(policy.id, {
            triggers: updatedTriggers
          });
        },
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "and"
      }, "Match ALL keywords (AND)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "or"
      }, "Match ANY keyword (OR)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Choose whether all keywords or any keyword should trigger this formatter")), (() => {
        const nlTrigger = policy.triggers.find(t => t.type === "natural_language");
        const nlTriggerValues = nlTrigger ? Array.isArray(nlTrigger.value) ? nlTrigger.value : nlTrigger.value ? [nlTrigger.value] : [] : [];
        return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "form-group"
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Natural Language Triggers"), nlTrigger ? /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement((react__WEBPACK_IMPORTED_MODULE_0___default().Fragment), null, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(TagInput, {
          values: nlTriggerValues,
          onChange: newValues => {
            const updatedTriggers = policy.triggers.map(t => t.type === "natural_language" ? {
              ...t,
              value: newValues
            } : t);
            updatePolicy(policy.id, {
              triggers: updatedTriggers
            });
          },
          placeholder: "Type natural language trigger and press Enter",
          disabled: !config.enablePolicies
        }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
          className: "form-group",
          style: {
            marginTop: "12px"
          }
        }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Similarity Threshold"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
          type: "range",
          min: "0.5",
          max: "1.0",
          step: "0.05",
          value: nlTrigger.threshold || 0.7,
          onChange: e => {
            const updatedTriggers = policy.triggers.map(t => t.type === "natural_language" ? {
              ...t,
              threshold: parseFloat(e.target.value)
            } : t);
            updatePolicy(policy.id, {
              triggers: updatedTriggers
            });
          },
          disabled: !config.enablePolicies
        }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Threshold: ", (nlTrigger.threshold || 0.7).toFixed(2), " (higher = more strict matching)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: () => {
            const updatedTriggers = policy.triggers.filter(t => t.type !== "natural_language");
            updatePolicy(policy.id, {
              triggers: updatedTriggers
            });
          },
          disabled: !config.enablePolicies,
          style: {
            marginTop: "8px",
            padding: "6px 12px",
            backgroundColor: "#ef4444",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: config.enablePolicies ? "pointer" : "not-allowed",
            fontSize: "12px"
          }
        }, "Remove Natural Language Trigger")) : /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("button", {
          onClick: () => {
            const newTrigger = {
              type: "natural_language",
              value: [],
              target: "agent_response",
              threshold: 0.7
            };
            updatePolicy(policy.id, {
              triggers: [...policy.triggers, newTrigger]
            });
          },
          disabled: !config.enablePolicies,
          style: {
            padding: "6px 12px",
            backgroundColor: "#3b82f6",
            color: "white",
            border: "none",
            borderRadius: "4px",
            cursor: config.enablePolicies ? "pointer" : "not-allowed",
            fontSize: "13px"
          }
        }, "+ Add Natural Language Trigger"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Type natural language triggers and press Enter to add. AI will match similar responses using semantic understanding."));
      })(), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Format Type"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("select", {
        value: policy.format_type,
        onChange: e => updatePolicy(policy.id, {
          format_type: e.target.value
        }),
        disabled: !config.enablePolicies
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "direct"
      }, "Direct Answer (No LLM)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "markdown"
      }, "Markdown Instructions (LLM)"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("option", {
        value: "json_schema"
      }, "JSON Schema (LLM)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, policy.format_type === "direct" ? "Directly replace the response with the provided string (no LLM processing)" : policy.format_type === "markdown" ? "Use LLM to reformat the response according to markdown instructions" : "Use LLM to extract and format the response as JSON matching the schema")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, policy.format_type === "direct" ? "Direct Answer String" : policy.format_type === "markdown" ? "Formatting Instructions (Markdown)" : "JSON Schema"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("textarea", {
        value: policy.format_config,
        onChange: e => updatePolicy(policy.id, {
          format_config: e.target.value
        }),
        placeholder: policy.format_type === "direct" ? "You are not allowed to view this sensitive data" : policy.format_type === "markdown" ? "Format the response in a clear, structured way with proper headings and bullet points." : '{\n  "type": "object",\n  "properties": {\n    "summary": {"type": "string"},\n    "details": {"type": "array"}\n  }\n}',
        rows: policy.format_type === "json_schema" ? 12 : policy.format_type === "direct" ? 4 : 8,
        disabled: !config.enablePolicies,
        style: {
          fontFamily: policy.format_type === "direct" ? "inherit" : "monospace",
          fontSize: "13px"
        }
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, policy.format_type === "direct" ? "This exact string will replace the AI response when triggers match (no LLM processing)" : policy.format_type === "markdown" ? "Markdown instructions for how to format the AI response (processed by LLM)" : "JSON schema that the formatted response must match (processed by LLM)")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
        className: "form-group"
      }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("label", null, "Priority"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("input", {
        type: "number",
        value: policy.priority,
        onChange: e => updatePolicy(policy.id, {
          priority: parseInt(e.target.value)
        }),
        min: "0",
        max: "100",
        disabled: !config.enablePolicies
      }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("small", null, "Higher priority formatters are checked first"))));
    })), outputFormatters.length === 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
      className: "empty-state"
    }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, "No output formatter policies configured. Click \"Add Output Formatter\" to create one.")));
  }
}

/***/ }),

/***/ "../agentic_chat/src/PolicyBlockComponent.css":
/*!****************************************************!*\
  !*** ../agentic_chat/src/PolicyBlockComponent.css ***!
  \****************************************************/
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyBlockComponent_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./PolicyBlockComponent.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/PolicyBlockComponent.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyBlockComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyBlockComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyBlockComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyBlockComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/PolicyBlockComponent.tsx":
/*!****************************************************!*\
  !*** ../agentic_chat/src/PolicyBlockComponent.tsx ***!
  \****************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _PolicyBlockComponent_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./PolicyBlockComponent.css */ "../agentic_chat/src/PolicyBlockComponent.css");



const PolicyBlockComponent = ({
  data
}) => {
  const {
    content,
    metadata
  } = data;
  const confidencePercent = Math.round(metadata.policy_confidence * 100);
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-icon"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Shield, {
    size: 24
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Intent Blocked by Policy"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-block-badge"
  }, "Security Policy"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-message"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.AlertCircle, {
    size: 18,
    className: "message-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, content)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-block-details"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-detail-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-label"
  }, "Policy Name:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-value"
  }, metadata.policy_name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-detail-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-label"
  }, "Policy ID:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-value policy-id"
  }, metadata.policy_id)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-detail-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-label"
  }, "Match Confidence:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "confidence-bar-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "confidence-bar"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "confidence-bar-fill",
    style: {
      width: `${confidencePercent}%`
    }
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "confidence-value"
  }, confidencePercent, "%"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-reasoning-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "reasoning-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Info, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Reasoning")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", {
    className: "reasoning-text"
  }, metadata.policy_reasoning)))));
};
/* harmony default export */ __webpack_exports__["default"] = (PolicyBlockComponent);

/***/ }),

/***/ "../agentic_chat/src/PolicyPlaybookComponent.css":
/*!*******************************************************!*\
  !*** ../agentic_chat/src/PolicyPlaybookComponent.css ***!
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
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyPlaybookComponent_css__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__(/*! !!../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!./PolicyPlaybookComponent.css */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/PolicyPlaybookComponent.css");

      
      
      
      
      
      
      
      
      

var options = {};

options.styleTagTransform = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleTagTransform_js__WEBPACK_IMPORTED_MODULE_5___default());
options.setAttributes = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_setAttributesWithoutAttributes_js__WEBPACK_IMPORTED_MODULE_3___default());
options.insert = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertBySelector_js__WEBPACK_IMPORTED_MODULE_2___default().bind(null, "head");
options.domAPI = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_styleDomAPI_js__WEBPACK_IMPORTED_MODULE_1___default());
options.insertStyleElement = (_node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_insertStyleElement_js__WEBPACK_IMPORTED_MODULE_4___default());

var update = _node_modules_pnpm_style_loader_4_0_0_webpack_5_101_3_node_modules_style_loader_dist_runtime_injectStylesIntoStyleTag_js__WEBPACK_IMPORTED_MODULE_0___default()(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyPlaybookComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"], options);




       /* unused harmony default export */ var __WEBPACK_DEFAULT_EXPORT__ = (_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyPlaybookComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"] && _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyPlaybookComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals ? _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_cjs_js_PolicyPlaybookComponent_css__WEBPACK_IMPORTED_MODULE_6__["default"].locals : undefined);


/***/ }),

/***/ "../agentic_chat/src/PolicyPlaybookComponent.tsx":
/*!*******************************************************!*\
  !*** ../agentic_chat/src/PolicyPlaybookComponent.tsx ***!
  \*******************************************************/
/***/ (function(__unused_webpack_module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! react */ "../node_modules/.pnpm/react@18.3.1/node_modules/react/index.js");
/* harmony import */ var react__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(react__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var lucide_react__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! lucide-react */ "../node_modules/.pnpm/lucide-react@0.525.0_react@18.3.1/node_modules/lucide-react/dist/esm/lucide-react.js");
/* harmony import */ var _PolicyPlaybookComponent_css__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__(/*! ./PolicyPlaybookComponent.css */ "../agentic_chat/src/PolicyPlaybookComponent.css");



const PolicyPlaybookComponent = ({
  data
}) => {
  const {
    content,
    metadata
  } = data;
  const confidencePercent = Math.round(metadata.policy_confidence * 100);
  const steps = metadata.playbook_steps || [];
  return /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-icon"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.BookOpen, {
    size: 24
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-title"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("h3", null, "Playbook Activated"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-playbook-badge"
  }, "Guided Workflow"))), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-message"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Lightbulb, {
    size: 18,
    className: "message-icon"
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("p", null, content || "I'll guide you through this process step by step.")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-playbook-details"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-detail-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-label"
  }, "Playbook Name:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-value"
  }, metadata.policy_name)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-detail-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-label"
  }, "Policy ID:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-value policy-id"
  }, metadata.policy_id)), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "policy-detail-row"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "policy-detail-label"
  }, "Match Confidence:"), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "confidence-bar-container"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "confidence-bar"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "confidence-bar-fill",
    style: {
      width: `${confidencePercent}%`
    }
  })), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "confidence-value"
  }, confidencePercent, "%"))), steps.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "playbook-steps-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "steps-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Info, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Workflow Steps (", steps.length, ")")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "steps-list"
  }, steps.map((step, index) => /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    key: index,
    className: "step-item"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "step-number"
  }, step.step_number), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "step-content"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "step-instruction"
  }, step.instruction), step.expected_outcome && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "step-outcome"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "outcome-label"
  }, "Expected:"), " ", step.expected_outcome), step.tools_allowed && step.tools_allowed.length > 0 && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "step-tools"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", {
    className: "tools-label"
  }, "Tools:"), " ", step.tools_allowed.join(", "))))))), metadata.playbook_guidance && /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "playbook-guidance-section"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "guidance-header"
  }, /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement(lucide_react__WEBPACK_IMPORTED_MODULE_1__.Info, {
    size: 16
  }), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("span", null, "Guidance")), /*#__PURE__*/react__WEBPACK_IMPORTED_MODULE_0___default().createElement("div", {
    className: "guidance-text"
  }, metadata.playbook_guidance)))));
};
/* harmony default export */ __webpack_exports__["default"] = (PolicyPlaybookComponent);

/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/PolicyBlockComponent.css":
/*!***********************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/PolicyBlockComponent.css ***!
  \***********************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, ".policy-block-container {\n  background: linear-gradient(135deg, #fff5f5 0%, #ffe5e5 100%);\n  border: 2px solid #ff6b6b;\n  border-radius: 12px;\n  padding: 20px;\n  margin: 16px 0;\n  box-shadow: 0 4px 12px rgba(255, 107, 107, 0.15);\n  animation: slideIn 0.3s ease-out;\n}\n\n@keyframes slideIn {\n  from {\n    opacity: 0;\n    transform: translateY(-10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.policy-block-header {\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  margin-bottom: 20px;\n  padding-bottom: 16px;\n  border-bottom: 1px solid rgba(255, 107, 107, 0.2);\n}\n\n.policy-block-icon {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 48px;\n  height: 48px;\n  background: linear-gradient(135deg, #ff6b6b 0%, #ff5252 100%);\n  border-radius: 12px;\n  color: white;\n  flex-shrink: 0;\n  box-shadow: 0 4px 8px rgba(255, 107, 107, 0.3);\n}\n\n.policy-block-title {\n  flex: 1;\n}\n\n.policy-block-title h3 {\n  margin: 0 0 6px 0;\n  font-size: 18px;\n  font-weight: 600;\n  color: #c92a2a;\n}\n\n.policy-block-badge {\n  display: inline-block;\n  padding: 4px 12px;\n  background: #ff6b6b;\n  color: white;\n  border-radius: 12px;\n  font-size: 12px;\n  font-weight: 500;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.policy-block-content {\n  display: flex;\n  flex-direction: column;\n  gap: 20px;\n}\n\n.policy-block-message {\n  display: flex;\n  align-items: flex-start;\n  gap: 12px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border-left: 4px solid #ff6b6b;\n}\n\n.message-icon {\n  color: #ff6b6b;\n  flex-shrink: 0;\n  margin-top: 2px;\n}\n\n.policy-block-message p {\n  margin: 0;\n  color: #495057;\n  font-size: 15px;\n  line-height: 1.6;\n}\n\n.policy-block-details {\n  display: flex;\n  flex-direction: column;\n  gap: 14px;\n  padding: 16px;\n  background: rgba(255, 255, 255, 0.7);\n  border-radius: 8px;\n}\n\n.policy-detail-row {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  font-size: 14px;\n}\n\n.policy-detail-label {\n  font-weight: 600;\n  color: #868e96;\n  min-width: 140px;\n}\n\n.policy-detail-value {\n  color: #212529;\n  font-weight: 500;\n}\n\n.policy-id {\n  font-family: 'Monaco', 'Menlo', 'Courier New', monospace;\n  font-size: 13px;\n  padding: 4px 8px;\n  background: rgba(255, 107, 107, 0.1);\n  border-radius: 4px;\n}\n\n.confidence-bar-container {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  flex: 1;\n}\n\n.confidence-bar {\n  flex: 1;\n  height: 8px;\n  background: rgba(255, 107, 107, 0.2);\n  border-radius: 4px;\n  overflow: hidden;\n}\n\n.confidence-bar-fill {\n  height: 100%;\n  background: linear-gradient(90deg, #ff6b6b 0%, #ff5252 100%);\n  border-radius: 4px;\n  transition: width 0.6s ease-out;\n}\n\n.confidence-value {\n  font-weight: 600;\n  color: #ff6b6b;\n  min-width: 45px;\n  text-align: right;\n}\n\n.policy-reasoning-section {\n  margin-top: 8px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border: 1px solid rgba(255, 107, 107, 0.2);\n}\n\n.reasoning-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 12px;\n  color: #495057;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.reasoning-header svg {\n  color: #ff6b6b;\n}\n\n.reasoning-text {\n  margin: 0;\n  color: #495057;\n  font-size: 14px;\n  line-height: 1.6;\n  font-style: italic;\n}\n\n/* Dark mode support */\n@media (prefers-color-scheme: dark) {\n  .policy-block-container {\n    background: linear-gradient(135deg, #2d1515 0%, #3d1a1a 100%);\n    border-color: #ff6b6b;\n  }\n\n  .policy-block-message {\n    background: rgba(255, 255, 255, 0.05);\n  }\n\n  .policy-block-message p {\n    color: #e9ecef;\n  }\n\n  .policy-block-details {\n    background: rgba(255, 255, 255, 0.03);\n  }\n\n  .policy-detail-value {\n    color: #e9ecef;\n  }\n\n  .policy-reasoning-section {\n    background: rgba(255, 255, 255, 0.05);\n    border-color: rgba(255, 107, 107, 0.3);\n  }\n\n  .reasoning-text {\n    color: #ced4da;\n  }\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/PolicyBlockComponent.css"],"names":[],"mappings":"AAAA;EACE,6DAA6D;EAC7D,yBAAyB;EACzB,mBAAmB;EACnB,aAAa;EACb,cAAc;EACd,gDAAgD;EAChD,gCAAgC;AAClC;;AAEA;EACE;IACE,UAAU;IACV,4BAA4B;EAC9B;EACA;IACE,UAAU;IACV,wBAAwB;EAC1B;AACF;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,mBAAmB;EACnB,oBAAoB;EACpB,iDAAiD;AACnD;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,WAAW;EACX,YAAY;EACZ,6DAA6D;EAC7D,mBAAmB;EACnB,YAAY;EACZ,cAAc;EACd,8CAA8C;AAChD;;AAEA;EACE,OAAO;AACT;;AAEA;EACE,iBAAiB;EACjB,eAAe;EACf,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,qBAAqB;EACrB,iBAAiB;EACjB,mBAAmB;EACnB,YAAY;EACZ,mBAAmB;EACnB,eAAe;EACf,gBAAgB;EAChB,yBAAyB;EACzB,qBAAqB;AACvB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,uBAAuB;EACvB,SAAS;EACT,aAAa;EACb,iBAAiB;EACjB,kBAAkB;EAClB,8BAA8B;AAChC;;AAEA;EACE,cAAc;EACd,cAAc;EACd,eAAe;AACjB;;AAEA;EACE,SAAS;EACT,cAAc;EACd,eAAe;EACf,gBAAgB;AAClB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,SAAS;EACT,aAAa;EACb,oCAAoC;EACpC,kBAAkB;AACpB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,eAAe;AACjB;;AAEA;EACE,gBAAgB;EAChB,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,wDAAwD;EACxD,eAAe;EACf,gBAAgB;EAChB,oCAAoC;EACpC,kBAAkB;AACpB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,OAAO;AACT;;AAEA;EACE,OAAO;EACP,WAAW;EACX,oCAAoC;EACpC,kBAAkB;EAClB,gBAAgB;AAClB;;AAEA;EACE,YAAY;EACZ,4DAA4D;EAC5D,kBAAkB;EAClB,+BAA+B;AACjC;;AAEA;EACE,gBAAgB;EAChB,cAAc;EACd,eAAe;EACf,iBAAiB;AACnB;;AAEA;EACE,eAAe;EACf,aAAa;EACb,iBAAiB;EACjB,kBAAkB;EAClB,0CAA0C;AAC5C;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,mBAAmB;EACnB,cAAc;EACd,gBAAgB;EAChB,eAAe;AACjB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,SAAS;EACT,cAAc;EACd,eAAe;EACf,gBAAgB;EAChB,kBAAkB;AACpB;;AAEA,sBAAsB;AACtB;EACE;IACE,6DAA6D;IAC7D,qBAAqB;EACvB;;EAEA;IACE,qCAAqC;EACvC;;EAEA;IACE,cAAc;EAChB;;EAEA;IACE,qCAAqC;EACvC;;EAEA;IACE,cAAc;EAChB;;EAEA;IACE,qCAAqC;IACrC,sCAAsC;EACxC;;EAEA;IACE,cAAc;EAChB;AACF","sourcesContent":[".policy-block-container {\n  background: linear-gradient(135deg, #fff5f5 0%, #ffe5e5 100%);\n  border: 2px solid #ff6b6b;\n  border-radius: 12px;\n  padding: 20px;\n  margin: 16px 0;\n  box-shadow: 0 4px 12px rgba(255, 107, 107, 0.15);\n  animation: slideIn 0.3s ease-out;\n}\n\n@keyframes slideIn {\n  from {\n    opacity: 0;\n    transform: translateY(-10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.policy-block-header {\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  margin-bottom: 20px;\n  padding-bottom: 16px;\n  border-bottom: 1px solid rgba(255, 107, 107, 0.2);\n}\n\n.policy-block-icon {\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  width: 48px;\n  height: 48px;\n  background: linear-gradient(135deg, #ff6b6b 0%, #ff5252 100%);\n  border-radius: 12px;\n  color: white;\n  flex-shrink: 0;\n  box-shadow: 0 4px 8px rgba(255, 107, 107, 0.3);\n}\n\n.policy-block-title {\n  flex: 1;\n}\n\n.policy-block-title h3 {\n  margin: 0 0 6px 0;\n  font-size: 18px;\n  font-weight: 600;\n  color: #c92a2a;\n}\n\n.policy-block-badge {\n  display: inline-block;\n  padding: 4px 12px;\n  background: #ff6b6b;\n  color: white;\n  border-radius: 12px;\n  font-size: 12px;\n  font-weight: 500;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.policy-block-content {\n  display: flex;\n  flex-direction: column;\n  gap: 20px;\n}\n\n.policy-block-message {\n  display: flex;\n  align-items: flex-start;\n  gap: 12px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border-left: 4px solid #ff6b6b;\n}\n\n.message-icon {\n  color: #ff6b6b;\n  flex-shrink: 0;\n  margin-top: 2px;\n}\n\n.policy-block-message p {\n  margin: 0;\n  color: #495057;\n  font-size: 15px;\n  line-height: 1.6;\n}\n\n.policy-block-details {\n  display: flex;\n  flex-direction: column;\n  gap: 14px;\n  padding: 16px;\n  background: rgba(255, 255, 255, 0.7);\n  border-radius: 8px;\n}\n\n.policy-detail-row {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  font-size: 14px;\n}\n\n.policy-detail-label {\n  font-weight: 600;\n  color: #868e96;\n  min-width: 140px;\n}\n\n.policy-detail-value {\n  color: #212529;\n  font-weight: 500;\n}\n\n.policy-id {\n  font-family: 'Monaco', 'Menlo', 'Courier New', monospace;\n  font-size: 13px;\n  padding: 4px 8px;\n  background: rgba(255, 107, 107, 0.1);\n  border-radius: 4px;\n}\n\n.confidence-bar-container {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  flex: 1;\n}\n\n.confidence-bar {\n  flex: 1;\n  height: 8px;\n  background: rgba(255, 107, 107, 0.2);\n  border-radius: 4px;\n  overflow: hidden;\n}\n\n.confidence-bar-fill {\n  height: 100%;\n  background: linear-gradient(90deg, #ff6b6b 0%, #ff5252 100%);\n  border-radius: 4px;\n  transition: width 0.6s ease-out;\n}\n\n.confidence-value {\n  font-weight: 600;\n  color: #ff6b6b;\n  min-width: 45px;\n  text-align: right;\n}\n\n.policy-reasoning-section {\n  margin-top: 8px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border: 1px solid rgba(255, 107, 107, 0.2);\n}\n\n.reasoning-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 12px;\n  color: #495057;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.reasoning-header svg {\n  color: #ff6b6b;\n}\n\n.reasoning-text {\n  margin: 0;\n  color: #495057;\n  font-size: 14px;\n  line-height: 1.6;\n  font-style: italic;\n}\n\n/* Dark mode support */\n@media (prefers-color-scheme: dark) {\n  .policy-block-container {\n    background: linear-gradient(135deg, #2d1515 0%, #3d1a1a 100%);\n    border-color: #ff6b6b;\n  }\n\n  .policy-block-message {\n    background: rgba(255, 255, 255, 0.05);\n  }\n\n  .policy-block-message p {\n    color: #e9ecef;\n  }\n\n  .policy-block-details {\n    background: rgba(255, 255, 255, 0.03);\n  }\n\n  .policy-detail-value {\n    color: #e9ecef;\n  }\n\n  .policy-reasoning-section {\n    background: rgba(255, 255, 255, 0.05);\n    border-color: rgba(255, 107, 107, 0.3);\n  }\n\n  .reasoning-text {\n    color: #ced4da;\n  }\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ }),

/***/ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/PolicyPlaybookComponent.css":
/*!**************************************************************************************************************************************************!*\
  !*** ../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/cjs.js!../agentic_chat/src/PolicyPlaybookComponent.css ***!
  \**************************************************************************************************************************************************/
/***/ (function(module, __webpack_exports__, __webpack_require__) {

/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/sourceMaps.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0__);
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__(/*! ../../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js */ "../node_modules/.pnpm/css-loader@7.1.2_webpack@5.101.3/node_modules/css-loader/dist/runtime/api.js");
/* harmony import */ var _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default = /*#__PURE__*/__webpack_require__.n(_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1__);
// Imports


var ___CSS_LOADER_EXPORT___ = _node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_api_js__WEBPACK_IMPORTED_MODULE_1___default()((_node_modules_pnpm_css_loader_7_1_2_webpack_5_101_3_node_modules_css_loader_dist_runtime_sourceMaps_js__WEBPACK_IMPORTED_MODULE_0___default()));
// Module
___CSS_LOADER_EXPORT___.push([module.id, "/* Policy Playbook Component Styles */\n\n.policy-playbook-container {\n  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);\n  border: 2px solid #3b82f6;\n  border-radius: 12px;\n  padding: 20px;\n  margin: 16px 0;\n  box-shadow: 0 4px 6px rgba(59, 130, 246, 0.1);\n  animation: slideIn 0.4s ease-out;\n}\n\n@keyframes slideIn {\n  from {\n    opacity: 0;\n    transform: translateY(-10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.policy-playbook-header {\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  margin-bottom: 20px;\n  padding-bottom: 16px;\n  border-bottom: 2px solid rgba(59, 130, 246, 0.2);\n}\n\n.policy-playbook-icon {\n  width: 48px;\n  height: 48px;\n  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);\n  border-radius: 12px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  color: white;\n  box-shadow: 0 4px 6px rgba(59, 130, 246, 0.2);\n}\n\n.policy-playbook-title {\n  flex: 1;\n}\n\n.policy-playbook-title h3 {\n  margin: 0 0 6px 0;\n  font-size: 20px;\n  font-weight: 700;\n  color: #1e40af;\n}\n\n.policy-playbook-badge {\n  display: inline-block;\n  padding: 4px 12px;\n  background: rgba(59, 130, 246, 0.15);\n  color: #1e40af;\n  border-radius: 12px;\n  font-size: 12px;\n  font-weight: 600;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.policy-playbook-content {\n  display: flex;\n  flex-direction: column;\n  gap: 16px;\n}\n\n.policy-playbook-message {\n  display: flex;\n  align-items: flex-start;\n  gap: 12px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border-left: 4px solid #3b82f6;\n  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);\n}\n\n.policy-playbook-message .message-icon {\n  color: #3b82f6;\n  flex-shrink: 0;\n  margin-top: 2px;\n}\n\n.policy-playbook-message p {\n  margin: 0;\n  color: #1e293b;\n  font-size: 15px;\n  line-height: 1.6;\n  font-weight: 500;\n}\n\n.policy-playbook-details {\n  background: rgba(255, 255, 255, 0.7);\n  border-radius: 8px;\n  padding: 16px;\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n}\n\n.policy-detail-row {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  font-size: 14px;\n}\n\n.policy-detail-label {\n  font-weight: 600;\n  color: #64748b;\n  min-width: 140px;\n}\n\n.policy-detail-value {\n  color: #1e293b;\n  font-weight: 500;\n}\n\n.policy-id {\n  font-family: 'Monaco', 'Menlo', 'Courier New', monospace;\n  font-size: 13px;\n  padding: 4px 8px;\n  background: rgba(59, 130, 246, 0.1);\n  border-radius: 4px;\n}\n\n.confidence-bar-container {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  flex: 1;\n}\n\n.confidence-bar {\n  flex: 1;\n  height: 8px;\n  background: rgba(59, 130, 246, 0.2);\n  border-radius: 4px;\n  overflow: hidden;\n}\n\n.confidence-bar-fill {\n  height: 100%;\n  background: linear-gradient(90deg, #3b82f6 0%, #2563eb 100%);\n  border-radius: 4px;\n  transition: width 0.6s ease-out;\n}\n\n.confidence-value {\n  font-weight: 600;\n  color: #3b82f6;\n  min-width: 45px;\n  text-align: right;\n}\n\n.playbook-steps-section {\n  margin-top: 8px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border: 1px solid rgba(59, 130, 246, 0.2);\n}\n\n.steps-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 16px;\n  color: #1e40af;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.steps-header svg {\n  color: #3b82f6;\n}\n\n.steps-list {\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n}\n\n.step-item {\n  display: flex;\n  gap: 12px;\n  padding: 12px;\n  background: #f8fafc;\n  border-radius: 6px;\n  border-left: 3px solid #3b82f6;\n}\n\n.step-number {\n  width: 32px;\n  height: 32px;\n  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);\n  color: white;\n  border-radius: 50%;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  font-weight: 700;\n  font-size: 14px;\n  flex-shrink: 0;\n}\n\n.step-content {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 6px;\n}\n\n.step-instruction {\n  font-weight: 600;\n  color: #1e293b;\n  font-size: 14px;\n  line-height: 1.5;\n}\n\n.step-outcome {\n  font-size: 13px;\n  color: #64748b;\n  line-height: 1.5;\n}\n\n.outcome-label {\n  font-weight: 600;\n  color: #475569;\n}\n\n.step-tools {\n  font-size: 12px;\n  color: #64748b;\n  font-family: 'Monaco', 'Menlo', 'Courier New', monospace;\n}\n\n.tools-label {\n  font-weight: 600;\n  color: #475569;\n}\n\n.playbook-guidance-section {\n  margin-top: 8px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border: 1px solid rgba(59, 130, 246, 0.2);\n}\n\n.guidance-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 12px;\n  color: #1e40af;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.guidance-header svg {\n  color: #3b82f6;\n}\n\n.guidance-text {\n  color: #475569;\n  font-size: 14px;\n  line-height: 1.6;\n  white-space: pre-wrap;\n}\n\n/* Dark mode support */\n@media (prefers-color-scheme: dark) {\n  .policy-playbook-container {\n    background: linear-gradient(135deg, #1e3a5f 0%, #2d4a6f 100%);\n    border-color: #3b82f6;\n  }\n\n  .policy-playbook-title h3 {\n    color: #93c5fd;\n  }\n\n  .policy-playbook-badge {\n    background: rgba(59, 130, 246, 0.25);\n    color: #93c5fd;\n  }\n\n  .policy-playbook-message {\n    background: rgba(255, 255, 255, 0.05);\n  }\n\n  .policy-playbook-message p {\n    color: #e2e8f0;\n  }\n\n  .policy-playbook-details {\n    background: rgba(255, 255, 255, 0.03);\n  }\n\n  .policy-detail-value {\n    color: #e2e8f0;\n  }\n\n  .playbook-steps-section,\n  .playbook-guidance-section {\n    background: rgba(255, 255, 255, 0.05);\n    border-color: rgba(59, 130, 246, 0.3);\n  }\n\n  .step-item {\n    background: rgba(255, 255, 255, 0.05);\n  }\n\n  .step-instruction {\n    color: #e2e8f0;\n  }\n\n  .guidance-text {\n    color: #cbd5e1;\n  }\n}\n\n", "",{"version":3,"sources":["webpack://./../agentic_chat/src/PolicyPlaybookComponent.css"],"names":[],"mappings":"AAAA,qCAAqC;;AAErC;EACE,6DAA6D;EAC7D,yBAAyB;EACzB,mBAAmB;EACnB,aAAa;EACb,cAAc;EACd,6CAA6C;EAC7C,gCAAgC;AAClC;;AAEA;EACE;IACE,UAAU;IACV,4BAA4B;EAC9B;EACA;IACE,UAAU;IACV,wBAAwB;EAC1B;AACF;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,mBAAmB;EACnB,oBAAoB;EACpB,gDAAgD;AAClD;;AAEA;EACE,WAAW;EACX,YAAY;EACZ,6DAA6D;EAC7D,mBAAmB;EACnB,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,YAAY;EACZ,6CAA6C;AAC/C;;AAEA;EACE,OAAO;AACT;;AAEA;EACE,iBAAiB;EACjB,eAAe;EACf,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,qBAAqB;EACrB,iBAAiB;EACjB,oCAAoC;EACpC,cAAc;EACd,mBAAmB;EACnB,eAAe;EACf,gBAAgB;EAChB,yBAAyB;EACzB,qBAAqB;AACvB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,uBAAuB;EACvB,SAAS;EACT,aAAa;EACb,iBAAiB;EACjB,kBAAkB;EAClB,8BAA8B;EAC9B,yCAAyC;AAC3C;;AAEA;EACE,cAAc;EACd,cAAc;EACd,eAAe;AACjB;;AAEA;EACE,SAAS;EACT,cAAc;EACd,eAAe;EACf,gBAAgB;EAChB,gBAAgB;AAClB;;AAEA;EACE,oCAAoC;EACpC,kBAAkB;EAClB,aAAa;EACb,aAAa;EACb,sBAAsB;EACtB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,eAAe;AACjB;;AAEA;EACE,gBAAgB;EAChB,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,wDAAwD;EACxD,eAAe;EACf,gBAAgB;EAChB,mCAAmC;EACnC,kBAAkB;AACpB;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,SAAS;EACT,OAAO;AACT;;AAEA;EACE,OAAO;EACP,WAAW;EACX,mCAAmC;EACnC,kBAAkB;EAClB,gBAAgB;AAClB;;AAEA;EACE,YAAY;EACZ,4DAA4D;EAC5D,kBAAkB;EAClB,+BAA+B;AACjC;;AAEA;EACE,gBAAgB;EAChB,cAAc;EACd,eAAe;EACf,iBAAiB;AACnB;;AAEA;EACE,eAAe;EACf,aAAa;EACb,iBAAiB;EACjB,kBAAkB;EAClB,yCAAyC;AAC3C;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,mBAAmB;EACnB,cAAc;EACd,gBAAgB;EAChB,eAAe;AACjB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,aAAa;EACb,sBAAsB;EACtB,SAAS;AACX;;AAEA;EACE,aAAa;EACb,SAAS;EACT,aAAa;EACb,mBAAmB;EACnB,kBAAkB;EAClB,8BAA8B;AAChC;;AAEA;EACE,WAAW;EACX,YAAY;EACZ,6DAA6D;EAC7D,YAAY;EACZ,kBAAkB;EAClB,aAAa;EACb,mBAAmB;EACnB,uBAAuB;EACvB,gBAAgB;EAChB,eAAe;EACf,cAAc;AAChB;;AAEA;EACE,OAAO;EACP,aAAa;EACb,sBAAsB;EACtB,QAAQ;AACV;;AAEA;EACE,gBAAgB;EAChB,cAAc;EACd,eAAe;EACf,gBAAgB;AAClB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,gBAAgB;AAClB;;AAEA;EACE,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,eAAe;EACf,cAAc;EACd,wDAAwD;AAC1D;;AAEA;EACE,gBAAgB;EAChB,cAAc;AAChB;;AAEA;EACE,eAAe;EACf,aAAa;EACb,iBAAiB;EACjB,kBAAkB;EAClB,yCAAyC;AAC3C;;AAEA;EACE,aAAa;EACb,mBAAmB;EACnB,QAAQ;EACR,mBAAmB;EACnB,cAAc;EACd,gBAAgB;EAChB,eAAe;AACjB;;AAEA;EACE,cAAc;AAChB;;AAEA;EACE,cAAc;EACd,eAAe;EACf,gBAAgB;EAChB,qBAAqB;AACvB;;AAEA,sBAAsB;AACtB;EACE;IACE,6DAA6D;IAC7D,qBAAqB;EACvB;;EAEA;IACE,cAAc;EAChB;;EAEA;IACE,oCAAoC;IACpC,cAAc;EAChB;;EAEA;IACE,qCAAqC;EACvC;;EAEA;IACE,cAAc;EAChB;;EAEA;IACE,qCAAqC;EACvC;;EAEA;IACE,cAAc;EAChB;;EAEA;;IAEE,qCAAqC;IACrC,qCAAqC;EACvC;;EAEA;IACE,qCAAqC;EACvC;;EAEA;IACE,cAAc;EAChB;;EAEA;IACE,cAAc;EAChB;AACF","sourcesContent":["/* Policy Playbook Component Styles */\n\n.policy-playbook-container {\n  background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);\n  border: 2px solid #3b82f6;\n  border-radius: 12px;\n  padding: 20px;\n  margin: 16px 0;\n  box-shadow: 0 4px 6px rgba(59, 130, 246, 0.1);\n  animation: slideIn 0.4s ease-out;\n}\n\n@keyframes slideIn {\n  from {\n    opacity: 0;\n    transform: translateY(-10px);\n  }\n  to {\n    opacity: 1;\n    transform: translateY(0);\n  }\n}\n\n.policy-playbook-header {\n  display: flex;\n  align-items: center;\n  gap: 16px;\n  margin-bottom: 20px;\n  padding-bottom: 16px;\n  border-bottom: 2px solid rgba(59, 130, 246, 0.2);\n}\n\n.policy-playbook-icon {\n  width: 48px;\n  height: 48px;\n  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);\n  border-radius: 12px;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  color: white;\n  box-shadow: 0 4px 6px rgba(59, 130, 246, 0.2);\n}\n\n.policy-playbook-title {\n  flex: 1;\n}\n\n.policy-playbook-title h3 {\n  margin: 0 0 6px 0;\n  font-size: 20px;\n  font-weight: 700;\n  color: #1e40af;\n}\n\n.policy-playbook-badge {\n  display: inline-block;\n  padding: 4px 12px;\n  background: rgba(59, 130, 246, 0.15);\n  color: #1e40af;\n  border-radius: 12px;\n  font-size: 12px;\n  font-weight: 600;\n  text-transform: uppercase;\n  letter-spacing: 0.5px;\n}\n\n.policy-playbook-content {\n  display: flex;\n  flex-direction: column;\n  gap: 16px;\n}\n\n.policy-playbook-message {\n  display: flex;\n  align-items: flex-start;\n  gap: 12px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border-left: 4px solid #3b82f6;\n  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);\n}\n\n.policy-playbook-message .message-icon {\n  color: #3b82f6;\n  flex-shrink: 0;\n  margin-top: 2px;\n}\n\n.policy-playbook-message p {\n  margin: 0;\n  color: #1e293b;\n  font-size: 15px;\n  line-height: 1.6;\n  font-weight: 500;\n}\n\n.policy-playbook-details {\n  background: rgba(255, 255, 255, 0.7);\n  border-radius: 8px;\n  padding: 16px;\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n}\n\n.policy-detail-row {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  font-size: 14px;\n}\n\n.policy-detail-label {\n  font-weight: 600;\n  color: #64748b;\n  min-width: 140px;\n}\n\n.policy-detail-value {\n  color: #1e293b;\n  font-weight: 500;\n}\n\n.policy-id {\n  font-family: 'Monaco', 'Menlo', 'Courier New', monospace;\n  font-size: 13px;\n  padding: 4px 8px;\n  background: rgba(59, 130, 246, 0.1);\n  border-radius: 4px;\n}\n\n.confidence-bar-container {\n  display: flex;\n  align-items: center;\n  gap: 12px;\n  flex: 1;\n}\n\n.confidence-bar {\n  flex: 1;\n  height: 8px;\n  background: rgba(59, 130, 246, 0.2);\n  border-radius: 4px;\n  overflow: hidden;\n}\n\n.confidence-bar-fill {\n  height: 100%;\n  background: linear-gradient(90deg, #3b82f6 0%, #2563eb 100%);\n  border-radius: 4px;\n  transition: width 0.6s ease-out;\n}\n\n.confidence-value {\n  font-weight: 600;\n  color: #3b82f6;\n  min-width: 45px;\n  text-align: right;\n}\n\n.playbook-steps-section {\n  margin-top: 8px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border: 1px solid rgba(59, 130, 246, 0.2);\n}\n\n.steps-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 16px;\n  color: #1e40af;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.steps-header svg {\n  color: #3b82f6;\n}\n\n.steps-list {\n  display: flex;\n  flex-direction: column;\n  gap: 12px;\n}\n\n.step-item {\n  display: flex;\n  gap: 12px;\n  padding: 12px;\n  background: #f8fafc;\n  border-radius: 6px;\n  border-left: 3px solid #3b82f6;\n}\n\n.step-number {\n  width: 32px;\n  height: 32px;\n  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);\n  color: white;\n  border-radius: 50%;\n  display: flex;\n  align-items: center;\n  justify-content: center;\n  font-weight: 700;\n  font-size: 14px;\n  flex-shrink: 0;\n}\n\n.step-content {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  gap: 6px;\n}\n\n.step-instruction {\n  font-weight: 600;\n  color: #1e293b;\n  font-size: 14px;\n  line-height: 1.5;\n}\n\n.step-outcome {\n  font-size: 13px;\n  color: #64748b;\n  line-height: 1.5;\n}\n\n.outcome-label {\n  font-weight: 600;\n  color: #475569;\n}\n\n.step-tools {\n  font-size: 12px;\n  color: #64748b;\n  font-family: 'Monaco', 'Menlo', 'Courier New', monospace;\n}\n\n.tools-label {\n  font-weight: 600;\n  color: #475569;\n}\n\n.playbook-guidance-section {\n  margin-top: 8px;\n  padding: 16px;\n  background: white;\n  border-radius: 8px;\n  border: 1px solid rgba(59, 130, 246, 0.2);\n}\n\n.guidance-header {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  margin-bottom: 12px;\n  color: #1e40af;\n  font-weight: 600;\n  font-size: 14px;\n}\n\n.guidance-header svg {\n  color: #3b82f6;\n}\n\n.guidance-text {\n  color: #475569;\n  font-size: 14px;\n  line-height: 1.6;\n  white-space: pre-wrap;\n}\n\n/* Dark mode support */\n@media (prefers-color-scheme: dark) {\n  .policy-playbook-container {\n    background: linear-gradient(135deg, #1e3a5f 0%, #2d4a6f 100%);\n    border-color: #3b82f6;\n  }\n\n  .policy-playbook-title h3 {\n    color: #93c5fd;\n  }\n\n  .policy-playbook-badge {\n    background: rgba(59, 130, 246, 0.25);\n    color: #93c5fd;\n  }\n\n  .policy-playbook-message {\n    background: rgba(255, 255, 255, 0.05);\n  }\n\n  .policy-playbook-message p {\n    color: #e2e8f0;\n  }\n\n  .policy-playbook-details {\n    background: rgba(255, 255, 255, 0.03);\n  }\n\n  .policy-detail-value {\n    color: #e2e8f0;\n  }\n\n  .playbook-steps-section,\n  .playbook-guidance-section {\n    background: rgba(255, 255, 255, 0.05);\n    border-color: rgba(59, 130, 246, 0.3);\n  }\n\n  .step-item {\n    background: rgba(255, 255, 255, 0.05);\n  }\n\n  .step-instruction {\n    color: #e2e8f0;\n  }\n\n  .guidance-text {\n    color: #cbd5e1;\n  }\n}\n\n"],"sourceRoot":""}]);
// Exports
/* harmony default export */ __webpack_exports__["default"] = (___CSS_LOADER_EXPORT___);


/***/ })

}]);
//# sourceMappingURL=main-agentic_chat_src_Polici.a13b231a94c70746c80d.js.map