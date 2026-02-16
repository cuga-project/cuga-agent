// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React, { useState, useEffect, useRef } from "react";
import { X, Save, Plus, Trash2, ChevronDown, ChevronUp, Shield, Search, Download, Upload } from "lucide-react";
import "./ConfigModal.css";

interface PolicyTrigger {
  type: "keyword" | "natural_language" | "app" | "always";
  value?: string | string[];
  target?: string;
  case_sensitive?: boolean;
  threshold?: number;
  operator?: "and" | "or";
}

interface IntentGuardPolicy {
  id: string;
  name: string;
  description: string;
  policy_type: "intent_guard";
  enabled: boolean;
  triggers: PolicyTrigger[];
  response: {
    response_type: "natural_language" | "json";
    content: string;
  };
  allow_override: boolean;
  priority: number;
}

interface PlaybookStep {
  step_number: number;
  instruction: string;
  expected_outcome: string;
  tools_allowed?: string[];
}

interface PlaybookPolicy {
  id: string;
  name: string;
  description: string;
  policy_type: "playbook";
  enabled: boolean;
  triggers: PolicyTrigger[];
  markdown_content: string;
  steps: PlaybookStep[];
  priority: number;
}

interface ToolGuidePolicy {
  id: string;
  name: string;
  description: string;
  policy_type: "tool_guide";
  enabled: boolean;
  triggers: PolicyTrigger[];
  target_tools: string[];
  target_apps?: string[];
  guide_content: string;
  prepend: boolean;
  priority: number;
}

interface ToolApprovalPolicy {
  id: string;
  name: string;
  description: string;
  policy_type: "tool_approval";
  enabled: boolean;
  triggers: PolicyTrigger[];
  required_tools: string[];
  required_apps?: string[];
  approval_message?: string;
  show_code_preview: boolean;
  auto_approve_after?: number;
  priority: number;
}

interface OutputFormatterPolicy {
  id: string;
  name: string;
  description: string;
  policy_type: "output_formatter";
  enabled: boolean;
  triggers: PolicyTrigger[];
  format_type: "markdown" | "json_schema" | "direct";
  format_config: string;
  priority: number;
}

type Policy = IntentGuardPolicy | PlaybookPolicy | ToolGuidePolicy | ToolApprovalPolicy | OutputFormatterPolicy;

interface PoliciesConfigData {
  enablePolicies: boolean;
  policies: Policy[];
}

interface PoliciesConfigProps {
  onClose: () => void;
  /** When true (e.g. Manage page), GET/POST use X-Use-Draft header so backend uses draft policy collection. */
  draftMode?: boolean;
}

interface ToolInfo {
  name: string;
  app: string;
  app_type: string;
  description: string;
}

interface AppInfo {
  name: string;
  type: string;
  tool_count: number;
}

interface MultiSelectProps {
  items: Array<{ value: string; label: string; description?: string }>;
  selectedValues: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  allowWildcard?: boolean;
}

function MultiSelect({ items, selectedValues, onChange, placeholder, disabled, allowWildcard }: MultiSelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const filteredItems = items.filter(
    (item) =>
      item.label.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.description?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const hasWildcard = selectedValues.includes("*");

  const toggleItem = (value: string) => {
    if (value === "*") {
      onChange(hasWildcard ? [] : ["*"]);
    } else {
      if (hasWildcard) {
        onChange([value]);
      } else {
        const newValues = selectedValues.includes(value)
          ? selectedValues.filter((v) => v !== value)
          : [...selectedValues, value];
        onChange(newValues);
      }
    }
  };

  const displayText = hasWildcard
    ? "All (*)"
    : selectedValues.length === 0
    ? placeholder || "Select..."
    : `${selectedValues.length} selected`;

  return (
    <div ref={dropdownRef} style={{ position: "relative", width: "100%" }}>
      <div
        onClick={() => !disabled && setIsOpen(!isOpen)}
        style={{
          padding: "8px 12px",
          border: "1px solid #e5e7eb",
          borderRadius: "6px",
          cursor: disabled ? "not-allowed" : "pointer",
          backgroundColor: disabled ? "#f9fafb" : "#fff",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ color: selectedValues.length === 0 ? "#9ca3af" : "#111827" }}>{displayText}</span>
        <ChevronDown
          size={16}
          style={{ transform: isOpen ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}
        />
      </div>

      {isOpen && (
        <div
          style={{
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
            flexDirection: "column",
          }}
        >
          <div style={{ padding: "8px", borderBottom: "1px solid #e5e7eb" }}>
            <div style={{ position: "relative" }}>
              <Search
                size={16}
                style={{
                  position: "absolute",
                  left: "8px",
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "#9ca3af",
                }}
              />
              <input
                type="text"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search..."
                style={{
                  width: "100%",
                  padding: "6px 6px 6px 32px",
                  border: "1px solid #e5e7eb",
                  borderRadius: "4px",
                  fontSize: "13px",
                }}
                onClick={(e) => e.stopPropagation()}
              />
            </div>
          </div>

          <div style={{ overflowY: "auto", maxHeight: "240px" }}>
            {allowWildcard && (
              <div
                onClick={() => toggleItem("*")}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  backgroundColor: hasWildcard ? "#eff6ff" : "transparent",
                  borderBottom: "1px solid #f3f4f6",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <input type="checkbox" checked={hasWildcard} readOnly style={{ cursor: "pointer" }} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: "13px" }}>All (*)</div>
                  <div style={{ fontSize: "12px", color: "#6b7280" }}>Select all items</div>
                </div>
              </div>
            )}

            {filteredItems.map((item) => (
              <div
                key={item.value}
                onClick={() => toggleItem(item.value)}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  backgroundColor: selectedValues.includes(item.value) ? "#eff6ff" : "transparent",
                  borderBottom: "1px solid #f3f4f6",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedValues.includes(item.value)}
                  readOnly
                  style={{ cursor: "pointer" }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, fontSize: "13px" }}>{item.label}</div>
                  {item.description && (
                    <div style={{ fontSize: "12px", color: "#6b7280", marginTop: "2px" }}>{item.description}</div>
                  )}
                </div>
              </div>
            ))}

            {filteredItems.length === 0 && (
              <div style={{ padding: "16px", textAlign: "center", color: "#9ca3af", fontSize: "13px" }}>
                No items found
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface TagInputProps {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}

function TagInput({ values, onChange, placeholder, disabled }: TagInputProps) {
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const addTag = (tag: string) => {
    const trimmed = tag.trim();
    if (trimmed && !values.includes(trimmed)) {
      onChange([...values, trimmed]);
    }
    setInputValue("");
  };

  const removeTag = (index: number) => {
    onChange(values.filter((_, i) => i !== index));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(inputValue);
    } else if (e.key === "Backspace" && !inputValue && values.length > 0) {
      removeTag(values.length - 1);
    }
  };

  return (
    <div
      onClick={() => !disabled && inputRef.current?.focus()}
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: "6px",
        padding: "6px",
        minHeight: "42px",
        display: "flex",
        flexWrap: "wrap",
        gap: "6px",
        alignItems: "center",
        cursor: disabled ? "not-allowed" : "text",
        backgroundColor: disabled ? "#f9fafb" : "#fff",
      }}
    >
      {values.map((tag, index) => (
        <div
          key={index}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "4px",
            padding: "4px 8px",
            backgroundColor: "#eff6ff",
            border: "1px solid #dbeafe",
            borderRadius: "4px",
            fontSize: "13px",
            color: "#1e40af",
          }}
        >
          <span>{tag}</span>
          {!disabled && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                removeTag(index);
              }}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: "0",
                display: "flex",
                alignItems: "center",
                color: "#3b82f6",
                fontSize: "16px",
                lineHeight: "1",
              }}
              title="Remove"
            >
              ×
            </button>
          )}
        </div>
      ))}
      <input
        ref={inputRef}
        type="text"
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => {
          if (inputValue.trim()) {
            addTag(inputValue);
          }
        }}
        placeholder={values.length === 0 ? placeholder : ""}
        disabled={disabled}
        style={{
          border: "none",
          outline: "none",
          flex: 1,
          minWidth: "120px",
          padding: "4px",
          fontSize: "13px",
          backgroundColor: "transparent",
        }}
      />
    </div>
  );
}

export default function PoliciesConfig({ onClose, draftMode = false }: PoliciesConfigProps) {
  const [config, setConfig] = useState<PoliciesConfigData>({
    enablePolicies: true,
    policies: [],
  });
  const [activeTab, setActiveTab] = useState<
    "intent_guard" | "playbook" | "tool_guide" | "tool_approval" | "output_formatter"
  >("intent_guard");
  const [expandedPolicy, setExpandedPolicy] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "success" | "error">("idle");
  const [isLoading, setIsLoading] = useState(true);
  const [availableTools, setAvailableTools] = useState<ToolInfo[]>([]);
  const [availableApps, setAvailableApps] = useState<AppInfo[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);

  useEffect(() => {
    loadConfig();
    loadTools();
  }, []);

  const loadConfig = async () => {
    setIsLoading(true);
    try {
      console.log("[PoliciesConfig] Loading policies from server...");
      const response = await fetch("/api/config/policies", {
        headers: draftMode ? { "X-Use-Draft": "true" } : {},
      });
      console.log("[PoliciesConfig] Response status:", response.status);

      if (response.ok) {
        const data = await response.json();
        console.log("[PoliciesConfig] Loaded policies:", data);

        // Normalize natural_language trigger values to always be arrays (for backward compatibility)
        const normalizedPolicies = (data.policies ?? []).map((policy: Policy) => ({
          ...policy,
          triggers: policy.triggers.map((trigger: PolicyTrigger) => {
            if (trigger.type === "natural_language" && trigger.value !== undefined) {
              // Ensure value is always an array for natural_language triggers
              const normalizedValue = Array.isArray(trigger.value)
                ? trigger.value
                : typeof trigger.value === "string"
                ? [trigger.value]
                : [];
              return { ...trigger, value: normalizedValue };
            }
            return trigger;
          }),
        }));

        setConfig({
          enablePolicies: data.enablePolicies ?? true,
          policies: normalizedPolicies,
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
      const dataBlob = new Blob([dataStr], { type: "application/json" });
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

  const importPolicies = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const importedData = JSON.parse(e.target?.result as string);
        if (importedData.policies && Array.isArray(importedData.policies)) {
          // Normalize natural_language trigger values to always be arrays (for backward compatibility)
          const normalizedPolicies = importedData.policies.map((policy: Policy) => ({
            ...policy,
            triggers: policy.triggers.map((trigger: PolicyTrigger) => {
              if (trigger.type === "natural_language" && trigger.value !== undefined) {
                // Ensure value is always an array for natural_language triggers
                const normalizedValue = Array.isArray(trigger.value)
                  ? trigger.value
                  : typeof trigger.value === "string"
                  ? [trigger.value]
                  : [];
                return { ...trigger, value: normalizedValue };
              }
              return trigger;
            }),
          }));

          setConfig({
            enablePolicies: importedData.enablePolicies ?? config.enablePolicies,
            policies: normalizedPolicies,
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
    await new Promise((resolve) => setTimeout(resolve, 50));

    setSaveStatus("saving");
    try {
      // Normalize natural_language trigger values to always be arrays
      const normalizedConfig = {
        ...config,
        policies: config.policies.map((policy) => ({
          ...policy,
          triggers: policy.triggers.map((trigger) => {
            if (trigger.type === "natural_language" && trigger.value !== undefined) {
              // Ensure value is always an array for natural_language triggers
              const normalizedValue = Array.isArray(trigger.value)
                ? trigger.value
                : typeof trigger.value === "string"
                ? [trigger.value]
                : [];
              return { ...trigger, value: normalizedValue };
            }
            return trigger;
          }),
        })),
      };

      console.log("[PoliciesConfig] Saving config:", normalizedConfig);
      console.log("[PoliciesConfig] Policies count:", normalizedConfig.policies.length);
      normalizedConfig.policies.forEach((policy, idx) => {
        console.log(`[PoliciesConfig] Policy ${idx}: ${policy.name}`);
        console.log(`[PoliciesConfig] Policy ${idx} triggers:`, policy.triggers);
        // Log keyword trigger operators specifically
        policy.triggers.forEach((trigger, triggerIdx) => {
          if (trigger.type === "keyword") {
            console.log(
              `[PoliciesConfig] Policy ${idx} trigger ${triggerIdx}: type=keyword, operator=${
                trigger.operator || "MISSING"
              }, keywords=${JSON.stringify(trigger.value)}`
            );
          } else if (trigger.type === "natural_language") {
            console.log(
              `[PoliciesConfig] Policy ${idx} trigger ${triggerIdx}: type=natural_language, values=${JSON.stringify(
                trigger.value
              )}`
            );
          }
        });
      });
      const response = await fetch("/api/config/policies", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(draftMode ? { "X-Use-Draft": "true" } : {}),
        },
        body: JSON.stringify(normalizedConfig),
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
    const newPolicy: IntentGuardPolicy = {
      id: `guard_${Date.now()}`,
      name: "New Intent Guard",
      description: "Blocks or modifies specific user intents",
      policy_type: "intent_guard",
      enabled: true,
      triggers: [
        {
          type: "keyword",
          value: [],
          target: "intent",
          case_sensitive: false,
          operator: "and",
        },
      ],
      response: {
        response_type: "natural_language",
        content: "This action is not allowed.",
      },
      allow_override: false,
      priority: 50,
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy],
    });
  };

  const addPlaybook = () => {
    const newPolicy: PlaybookPolicy = {
      id: `playbook_${Date.now()}`,
      name: "New Playbook",
      description: "Step-by-step guidance for a task",
      policy_type: "playbook",
      enabled: true,
      triggers: [
        {
          type: "keyword",
          value: [],
          target: "intent",
          case_sensitive: false,
          operator: "and",
        },
      ],
      markdown_content: "# Task Guide\n\n## Steps:\n\n1. First step\n2. Second step\n3. Third step",
      steps: [
        {
          step_number: 1,
          instruction: "First step",
          expected_outcome: "Step 1 complete",
          tools_allowed: [],
        },
      ],
      priority: 50,
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy],
    });
  };

  const addToolGuide = () => {
    const newPolicy: ToolGuidePolicy = {
      id: `tool_guide_${Date.now()}`,
      name: "New Tool Guide",
      description: "Add additional context to tool descriptions",
      policy_type: "tool_guide",
      enabled: true,
      triggers: [
        {
          type: "always",
        },
      ],
      target_tools: ["*"],
      target_apps: undefined,
      guide_content: "## Additional Guidelines\n\n- Follow best practices\n- Consider security implications",
      prepend: false,
      priority: 50,
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy],
    });
  };

  const addToolApproval = () => {
    const newPolicy: ToolApprovalPolicy = {
      id: `tool_approval_${Date.now()}`,
      name: "New Tool Approval",
      description: "Require approval before executing specific tools",
      policy_type: "tool_approval",
      enabled: true,
      triggers: [], // ToolApproval policies don't use triggers - they're checked after code generation
      required_tools: [],
      required_apps: undefined,
      approval_message: "This tool requires your approval before execution.",
      show_code_preview: true,
      auto_approve_after: undefined,
      priority: 50,
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy],
    });
  };

  const addOutputFormatter = () => {
    const newPolicy: OutputFormatterPolicy = {
      id: `output_formatter_${Date.now()}`,
      name: "New Output Formatter",
      description: "Format the final AI message output",
      policy_type: "output_formatter",
      enabled: true,
      triggers: [
        {
          type: "keyword",
          value: [],
          target: "agent_response",
          case_sensitive: false,
          operator: "and",
        },
      ],
      format_type: "markdown",
      format_config: "Format the response in a clear, structured way with proper headings and bullet points.",
      priority: 50,
    };
    setConfig({
      ...config,
      policies: [...config.policies, newPolicy],
    });
  };

  const updatePolicy = (id: string, updates: Partial<Policy>) => {
    setConfig({
      ...config,
      policies: config.policies.map((policy) => (policy.id === id ? ({ ...policy, ...updates } as Policy) : policy)),
    });
  };

  const removePolicy = (id: string) => {
    setConfig({
      ...config,
      policies: config.policies.filter((p) => p.id !== id),
    });
  };

  const intentGuards = config.policies.filter((p) => p.policy_type === "intent_guard") as IntentGuardPolicy[];
  const playbooks = config.policies.filter((p) => p.policy_type === "playbook") as PlaybookPolicy[];
  const ToolGuides = config.policies.filter((p) => p.policy_type === "tool_guide") as ToolGuidePolicy[];
  const toolApprovals = config.policies.filter((p) => p.policy_type === "tool_approval") as ToolApprovalPolicy[];
  const outputFormatters = config.policies.filter(
    (p) => p.policy_type === "output_formatter"
  ) as OutputFormatterPolicy[];

  return (
    <div className="config-modal-overlay">
      <div className="config-modal">
        <div className="config-modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <Shield size={24} style={{ color: "#4e00ec" }} />
            <h2>Policies Configuration</h2>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <button
              onClick={exportPolicies}
              disabled={config.policies.length === 0}
              style={{
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
                color: config.policies.length === 0 ? "#9ca3af" : "#374151",
              }}
              title="Export all policies as JSON"
            >
              <Download size={16} />
              Export
            </button>
            <label
              style={{
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
                color: "#374151",
              }}
              title="Import policies from JSON"
            >
              <Upload size={16} />
              Import
              <input type="file" accept=".json" onChange={importPolicies} style={{ display: "none" }} />
            </label>
            <button className="config-modal-close" onClick={onClose}>
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="config-modal-tabs">
          <button
            className={`config-tab ${activeTab === "intent_guard" ? "active" : ""}`}
            onClick={() => setActiveTab("intent_guard")}
          >
            Intent Guards ({intentGuards.length})
          </button>
          <button
            className={`config-tab ${activeTab === "playbook" ? "active" : ""}`}
            onClick={() => setActiveTab("playbook")}
          >
            Playbooks ({playbooks.length})
          </button>
          <button
            className={`config-tab ${activeTab === "tool_guide" ? "active" : ""}`}
            onClick={() => setActiveTab("tool_guide")}
          >
            Tool Guide ({ToolGuides.length})
          </button>
          <button
            className={`config-tab ${activeTab === "tool_approval" ? "active" : ""}`}
            onClick={() => setActiveTab("tool_approval")}
          >
            Tool Approval ({toolApprovals.length})
          </button>
          <button
            className={`config-tab ${activeTab === "output_formatter" ? "active" : ""}`}
            onClick={() => setActiveTab("output_formatter")}
          >
            Output Formatter ({outputFormatters.length})
          </button>
        </div>

        <div className="config-modal-content">
          {isLoading ? (
            <div className="config-card" style={{ textAlign: "center", padding: "40px" }}>
              <p>Loading policies...</p>
            </div>
          ) : (
            <>
              <div className="config-card">
                <h3>Global Settings</h3>
                <div className="config-form">
                  <div className="form-group">
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={config.enablePolicies}
                        onChange={(e) => setConfig({ ...config, enablePolicies: e.target.checked })}
                      />
                      <span>Enable Policy System</span>
                    </label>
                    <small>
                      Master switch for all policy enforcement ({config.policies.length} policies configured)
                    </small>
                  </div>
                </div>
              </div>

              {activeTab === "intent_guard" && renderIntentGuards()}
              {activeTab === "playbook" && renderPlaybooks()}
              {activeTab === "tool_guide" && renderToolGuides()}
              {activeTab === "tool_approval" && renderToolApprovals()}
              {activeTab === "output_formatter" && renderOutputFormatters()}
            </>
          )}
        </div>

        <div className="config-modal-footer">
          <button className="cancel-btn" onClick={onClose}>
            Cancel
          </button>
          <button className={`save-btn ${saveStatus}`} onClick={saveConfig} disabled={saveStatus === "saving"}>
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

  function renderIntentGuards() {
    return (
      <div className="config-card">
        <div className="section-header">
          <h3>Intent Guards</h3>
          <button className="add-btn" onClick={addIntentGuard} disabled={!config.enablePolicies}>
            <Plus size={16} />
            Add Intent Guard
          </button>
        </div>

        <div className="sources-list">
          {intentGuards.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            const keywordTrigger = policy.triggers.find((t) => t.type === "keyword");
            const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];

            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <input
                      type="text"
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      className="agent-config-name"
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <button className="expand-btn" onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}>
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                    <button
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  {!isExpanded && (
                    <div className="agent-summary">
                      {keywords.length > 0 && (
                        <span className="agent-summary-item">
                          {keywords.length} keyword{keywords.length !== 1 ? "s" : ""}
                        </span>
                      )}
                      {policy.triggers.some((t) => t.type === "natural_language") && (
                        <span className="agent-summary-item">AI trigger</span>
                      )}
                      <span className="agent-summary-item">Priority: {policy.priority}</span>
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div className="agent-config-details">
                    <div className="form-group">
                      <label>Description</label>
                      <textarea
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        placeholder="What this policy does..."
                        rows={2}
                        disabled={!config.enablePolicies}
                      />
                    </div>

                    <div className="form-group">
                      <label>Trigger Keywords (Optional)</label>
                      <TagInput
                        values={keywords}
                        onChange={(newKeywords) => {
                          const updatedTriggers = policy.triggers.filter((t) => t.type !== "keyword");
                          if (newKeywords.length > 0) {
                            const existingKeywordTrigger = policy.triggers.find((t) => t.type === "keyword");
                            updatedTriggers.push({
                              type: "keyword",
                              value: newKeywords,
                              target: "intent",
                              case_sensitive: false,
                              operator: existingKeywordTrigger?.operator || "and",
                            });
                          }
                          updatePolicy(policy.id, { triggers: updatedTriggers });
                        }}
                        placeholder="Type keyword and press Enter or comma"
                        disabled={!config.enablePolicies}
                      />
                      <small>Type keywords and press Enter or comma to add. Click × to remove.</small>
                    </div>

                    {keywords.length > 1 && (
                      <div className="form-group">
                        <label>Keyword Matching</label>
                        <select
                          value={keywordTrigger?.operator || "and"}
                          onChange={(e) => {
                            const operator = e.target.value as "and" | "or";
                            const updatedTriggers = policy.triggers.map((t) =>
                              t.type === "keyword" ? { ...t, operator } : t
                            );
                            updatePolicy(policy.id, { triggers: updatedTriggers });
                          }}
                          disabled={!config.enablePolicies}
                        >
                          <option value="and">Match ALL keywords (AND)</option>
                          <option value="or">Match ANY keyword (OR)</option>
                        </select>
                        <small>Choose whether all keywords or any keyword should trigger this policy</small>
                      </div>
                    )}

                    {(() => {
                      const nlTrigger = policy.triggers.find((t) => t.type === "natural_language");
                      const nlTriggerValues = nlTrigger
                        ? Array.isArray(nlTrigger.value)
                          ? nlTrigger.value
                          : nlTrigger.value
                          ? [nlTrigger.value]
                          : []
                        : [];

                      return (
                        <div className="form-group">
                          <label>Natural Language Triggers</label>
                          {nlTrigger ? (
                            <>
                              <TagInput
                                values={nlTriggerValues}
                                onChange={(newValues) => {
                                  const updatedTriggers = policy.triggers.map((t) =>
                                    t.type === "natural_language" ? { ...t, value: newValues } : t
                                  );
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                placeholder="Type natural language trigger and press Enter"
                                disabled={!config.enablePolicies}
                              />
                              <div className="form-group" style={{ marginTop: "12px" }}>
                                <label>Similarity Threshold</label>
                                <input
                                  type="range"
                                  min="0.5"
                                  max="1.0"
                                  step="0.05"
                                  value={nlTrigger.threshold || 0.7}
                                  onChange={(e) => {
                                    const updatedTriggers = policy.triggers.map((t) =>
                                      t.type === "natural_language"
                                        ? { ...t, threshold: parseFloat(e.target.value) }
                                        : t
                                    );
                                    updatePolicy(policy.id, { triggers: updatedTriggers });
                                  }}
                                  disabled={!config.enablePolicies}
                                />
                                <small>
                                  Threshold: {(nlTrigger.threshold || 0.7).toFixed(2)} (higher = more strict matching)
                                </small>
                              </div>
                              <button
                                onClick={() => {
                                  const updatedTriggers = policy.triggers.filter((t) => t.type !== "natural_language");
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                disabled={!config.enablePolicies}
                                style={{
                                  marginTop: "8px",
                                  padding: "6px 12px",
                                  backgroundColor: "#ef4444",
                                  color: "white",
                                  border: "none",
                                  borderRadius: "4px",
                                  cursor: config.enablePolicies ? "pointer" : "not-allowed",
                                  fontSize: "12px",
                                }}
                              >
                                Remove Natural Language Trigger
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={() => {
                                const newTrigger: PolicyTrigger = {
                                  type: "natural_language",
                                  value: [],
                                  target: "intent",
                                  threshold: 0.7,
                                };
                                updatePolicy(policy.id, { triggers: [...policy.triggers, newTrigger] });
                              }}
                              disabled={!config.enablePolicies}
                              style={{
                                padding: "6px 12px",
                                backgroundColor: "#3b82f6",
                                color: "white",
                                border: "none",
                                borderRadius: "4px",
                                cursor: config.enablePolicies ? "pointer" : "not-allowed",
                                fontSize: "13px",
                              }}
                            >
                              + Add Natural Language Trigger
                            </button>
                          )}
                          <small>
                            Type natural language triggers and press Enter to add. AI will match similar intents using
                            semantic understanding.
                          </small>
                        </div>
                      );
                    })()}

                    <div className="form-group">
                      <label>Response Message</label>
                      <textarea
                        value={policy.response.content}
                        onChange={(e) =>
                          updatePolicy(policy.id, {
                            response: { ...policy.response, content: e.target.value },
                          })
                        }
                        placeholder="This action is not allowed."
                        rows={3}
                        disabled={!config.enablePolicies}
                      />
                    </div>

                    <div className="form-row">
                      <div className="form-group">
                        <label>Priority</label>
                        <input
                          type="number"
                          value={policy.priority}
                          onChange={(e) => updatePolicy(policy.id, { priority: parseInt(e.target.value) })}
                          min="0"
                          max="100"
                          disabled={!config.enablePolicies}
                        />
                        <small>Higher priority policies are checked first</small>
                      </div>

                      <div className="form-group">
                        <label className="checkbox-label">
                          <input
                            type="checkbox"
                            checked={policy.allow_override}
                            onChange={(e) => updatePolicy(policy.id, { allow_override: e.target.checked })}
                            disabled={!config.enablePolicies}
                          />
                          <span>Allow Override</span>
                        </label>
                        <small>User can bypass this policy</small>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {intentGuards.length === 0 && (
          <div className="empty-state">
            <p>No intent guards configured. Click "Add Intent Guard" to create one.</p>
          </div>
        )}
      </div>
    );
  }

  function renderPlaybooks() {
    return (
      <div className="config-card">
        <div className="section-header">
          <h3>Playbooks</h3>
          <button className="add-btn" onClick={addPlaybook} disabled={!config.enablePolicies}>
            <Plus size={16} />
            Add Playbook
          </button>
        </div>

        <div className="sources-list">
          {playbooks.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            const keywordTrigger = policy.triggers.find((t) => t.type === "keyword");
            const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];

            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <input
                      type="text"
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      className="agent-config-name"
                      placeholder="Playbook Name"
                      disabled={!config.enablePolicies}
                    />
                    <button className="expand-btn" onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}>
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                    <button
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  {!isExpanded && (
                    <div className="agent-summary">
                      <span className="agent-summary-item">
                        {policy.steps.length} step{policy.steps.length !== 1 ? "s" : ""}
                      </span>
                      {policy.triggers.length > 0 && (
                        <span className="agent-summary-item">
                          {policy.triggers[0].type === "natural_language"
                            ? "AI trigger"
                            : `${keywords.length} keyword${keywords.length !== 1 ? "s" : ""}`}
                        </span>
                      )}
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div className="agent-config-details">
                    <div className="form-group">
                      <label>Description</label>
                      <textarea
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        placeholder="What this playbook guides the user through..."
                        rows={2}
                        disabled={!config.enablePolicies}
                      />
                    </div>

                    <div className="form-group">
                      <label>Trigger Type</label>
                      <select
                        value={
                          policy.triggers.length > 0 && policy.triggers[0].type === "natural_language"
                            ? "natural_language"
                            : "keyword"
                        }
                        onChange={(e) => {
                          const triggerType = e.target.value as "keyword" | "natural_language";
                          if (triggerType === "natural_language") {
                            updatePolicy(policy.id, {
                              triggers: [
                                {
                                  type: "natural_language",
                                  value: [],
                                  target: "intent",
                                  threshold: 0.7,
                                },
                              ],
                            });
                          } else {
                            updatePolicy(policy.id, {
                              triggers: [
                                {
                                  type: "keyword",
                                  value: [],
                                  target: "intent",
                                  case_sensitive: false,
                                  operator: "and",
                                },
                              ],
                            });
                          }
                        }}
                        disabled={!config.enablePolicies}
                      >
                        <option value="keyword">Keywords (Exact Match)</option>
                        <option value="natural_language">Natural Language (AI Match)</option>
                      </select>
                      <small>Choose how this playbook should be triggered</small>
                    </div>

                    {policy.triggers.length > 0 && policy.triggers[0].type === "keyword" && (
                      <>
                        <div className="form-group">
                          <label>Trigger Keywords</label>
                          <TagInput
                            values={keywords}
                            onChange={(newKeywords) => {
                              const newTriggers = policy.triggers.map((t) =>
                                t.type === "keyword" ? { ...t, value: newKeywords } : t
                              );
                              updatePolicy(policy.id, { triggers: newTriggers });
                            }}
                            placeholder="Type keyword and press Enter or comma"
                            disabled={!config.enablePolicies}
                          />
                          <small>Type keywords and press Enter or comma to add. Click × to remove.</small>
                        </div>

                        {keywords.length > 1 && (
                          <div className="form-group">
                            <label>Keyword Matching</label>
                            <select
                              value={keywordTrigger?.operator || "and"}
                              onChange={(e) => {
                                const operator = e.target.value as "and" | "or";
                                const newTriggers = policy.triggers.map((t) =>
                                  t.type === "keyword" ? { ...t, operator } : t
                                );
                                updatePolicy(policy.id, { triggers: newTriggers });
                              }}
                              disabled={!config.enablePolicies}
                            >
                              <option value="and">Match ALL keywords (AND)</option>
                              <option value="or">Match ANY keyword (OR)</option>
                            </select>
                            <small>Choose whether all keywords or any keyword should trigger this playbook</small>
                          </div>
                        )}
                      </>
                    )}

                    {policy.triggers.length > 0 && policy.triggers[0].type === "natural_language" && (
                      <>
                        <div className="form-group">
                          <label>Natural Language Triggers</label>
                          <TagInput
                            values={
                              Array.isArray(policy.triggers[0].value)
                                ? policy.triggers[0].value
                                : policy.triggers[0].value
                                ? [policy.triggers[0].value]
                                : []
                            }
                            onChange={(newTriggers) => {
                              const updatedTriggers = policy.triggers.map((t, idx) =>
                                idx === 0 ? { ...t, value: newTriggers } : t
                              );
                              updatePolicy(policy.id, { triggers: updatedTriggers });
                            }}
                            placeholder="Type trigger and press Enter"
                            disabled={!config.enablePolicies}
                          />
                          <small>
                            Type natural language triggers and press Enter to add. AI will match similar user requests.
                          </small>
                        </div>

                        <div className="form-group">
                          <label>Similarity Threshold</label>
                          <input
                            type="range"
                            min="0.5"
                            max="1.0"
                            step="0.05"
                            value={policy.triggers[0].threshold || 0.7}
                            onChange={(e) => {
                              const newTriggers = policy.triggers.map((t, idx) =>
                                idx === 0 ? { ...t, threshold: parseFloat(e.target.value) } : t
                              );
                              updatePolicy(policy.id, { triggers: newTriggers });
                            }}
                            disabled={!config.enablePolicies}
                          />
                          <small>
                            Threshold: {(policy.triggers[0].threshold || 0.7).toFixed(2)} (higher = more strict
                            matching)
                          </small>
                        </div>
                      </>
                    )}

                    <div className="form-group">
                      <label>Markdown Content</label>
                      <textarea
                        value={policy.markdown_content}
                        onChange={(e) => updatePolicy(policy.id, { markdown_content: e.target.value })}
                        placeholder="# Task Guide&#10;&#10;## Steps:&#10;&#10;1. First step&#10;2. Second step"
                        rows={8}
                        disabled={!config.enablePolicies}
                        style={{ fontFamily: "monospace", fontSize: "13px" }}
                      />
                      <small>Markdown-formatted guidance that will be shown to the agent</small>
                    </div>

                    <div className="form-group">
                      <label>Priority</label>
                      <input
                        type="number"
                        value={policy.priority}
                        onChange={(e) => updatePolicy(policy.id, { priority: parseInt(e.target.value) })}
                        min="0"
                        max="100"
                        disabled={!config.enablePolicies}
                      />
                      <small>Higher priority playbooks are checked first</small>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {playbooks.length === 0 && (
          <div className="empty-state">
            <p>No playbooks configured. Click "Add Playbook" to create one.</p>
          </div>
        )}
      </div>
    );
  }

  function renderToolGuides() {
    return (
      <div className="config-card">
        <div className="section-header">
          <h3>Tool Guide Policies</h3>
          <button className="add-btn" onClick={addToolGuide} disabled={!config.enablePolicies}>
            <Plus size={16} />
            Add Tool Guide
          </button>
        </div>

        <div className="sources-list">
          {ToolGuides.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <input
                      type="text"
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      className="agent-config-name"
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <button className="expand-btn" onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}>
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                    <button
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  {!isExpanded && (
                    <div className="agent-summary">
                      <span className="agent-summary-item">
                        {policy.target_tools.includes("*") ? "All tools" : `${policy.target_tools.length} tool(s)`}
                      </span>
                      {policy.target_apps && policy.target_apps.length > 0 && (
                        <span className="agent-summary-item">{policy.target_apps.length} app(s)</span>
                      )}
                      <span className="agent-summary-item">Priority: {policy.priority}</span>
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div className="agent-config-details">
                    <div className="form-group">
                      <label>Description</label>
                      <textarea
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        rows={2}
                        disabled={!config.enablePolicies}
                      />
                    </div>

                    <div className="form-group">
                      <label>Target Tools</label>
                      <MultiSelect
                        items={availableTools.map((tool) => ({
                          value: tool.name,
                          label: tool.name,
                          description: `${tool.app} - ${tool.description.substring(0, 60)}${
                            tool.description.length > 60 ? "..." : ""
                          }`,
                        }))}
                        selectedValues={policy.target_tools}
                        onChange={(values) => updatePolicy(policy.id, { target_tools: values })}
                        placeholder={toolsLoading ? "Loading tools..." : "Select tools to enrich"}
                        disabled={!config.enablePolicies || toolsLoading}
                        allowWildcard={true}
                      />
                      <small>Select specific tools to enrich, or use * to enrich all tools</small>
                    </div>

                    <div className="form-group">
                      <label>Target Apps (optional)</label>
                      <MultiSelect
                        items={availableApps.map((app) => ({
                          value: app.name,
                          label: app.name,
                          description: `${app.type} - ${app.tool_count} tool(s)`,
                        }))}
                        selectedValues={policy.target_apps || []}
                        onChange={(values) =>
                          updatePolicy(policy.id, { target_apps: values.length > 0 ? values : undefined })
                        }
                        placeholder={toolsLoading ? "Loading apps..." : "Select apps (optional)"}
                        disabled={!config.enablePolicies || toolsLoading}
                        allowWildcard={false}
                      />
                      <small>Optionally filter by app name</small>
                    </div>

                    <div className="form-group">
                      <label>Guide Content (Markdown)</label>
                      <textarea
                        value={policy.guide_content}
                        onChange={(e) => updatePolicy(policy.id, { guide_content: e.target.value })}
                        placeholder="## Additional Guidelines&#10;&#10;- Follow best practices&#10;- Consider security"
                        rows={6}
                        disabled={!config.enablePolicies}
                        style={{ fontFamily: "monospace", fontSize: "13px" }}
                      />
                      <small>Markdown content to add to tool descriptions</small>
                    </div>

                    <div className="form-group">
                      <label>
                        <input
                          type="checkbox"
                          checked={policy.prepend}
                          onChange={(e) => updatePolicy(policy.id, { prepend: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        Prepend content (add before existing description)
                      </label>
                    </div>

                    <div className="form-group">
                      <label>Priority</label>
                      <input
                        type="number"
                        value={policy.priority}
                        onChange={(e) => updatePolicy(policy.id, { priority: parseInt(e.target.value) })}
                        min="0"
                        max="100"
                        disabled={!config.enablePolicies}
                      />
                      <small>Higher priority guides are applied first</small>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {ToolGuides.length === 0 && (
          <div className="empty-state">
            <p>No tool guide policies configured. Click "Add Tool Guide" to create one.</p>
          </div>
        )}
      </div>
    );
  }

  function renderToolApprovals() {
    return (
      <div className="config-card">
        <div className="section-header">
          <h3>Tool Approval Policies</h3>
          <button className="add-btn" onClick={addToolApproval} disabled={!config.enablePolicies}>
            <Plus size={16} />
            Add Tool Approval
          </button>
        </div>

        <div className="policies-list">
          {toolApprovals.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <input
                      type="text"
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      className="agent-config-name"
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <button className="expand-btn" onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}>
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                    <button
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  {!isExpanded && (
                    <div className="agent-summary">
                      <span className="agent-summary-item">
                        {policy.required_tools.length === 0
                          ? "No tools selected"
                          : policy.required_tools.includes("*")
                          ? "All tools"
                          : `${policy.required_tools.length} tool(s)`}
                      </span>
                      {policy.required_apps && policy.required_apps.length > 0 && (
                        <span className="agent-summary-item">{policy.required_apps.length} app(s)</span>
                      )}
                      <span className="agent-summary-item">Priority: {policy.priority}</span>
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div className="agent-config-details">
                    <div className="form-group">
                      <label>Description</label>
                      <textarea
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        rows={2}
                        disabled={!config.enablePolicies}
                      />
                    </div>

                    <div className="form-group">
                      <label>Required Tools</label>
                      <MultiSelect
                        items={availableTools.map((tool) => ({
                          value: tool.name,
                          label: tool.name,
                          description: `${tool.app} - ${tool.description.substring(0, 60)}${
                            tool.description.length > 60 ? "..." : ""
                          }`,
                        }))}
                        selectedValues={policy.required_tools}
                        onChange={(values) => updatePolicy(policy.id, { required_tools: values })}
                        placeholder={toolsLoading ? "Loading tools..." : "Select tools requiring approval"}
                        disabled={!config.enablePolicies || toolsLoading}
                        allowWildcard={true}
                      />
                      <small>Tools that require approval before execution</small>
                    </div>

                    <div className="form-group">
                      <label>Required Apps (optional)</label>
                      <MultiSelect
                        items={availableApps.map((app) => ({
                          value: app.name,
                          label: app.name,
                          description: `${app.type} - ${app.tool_count} tool(s)`,
                        }))}
                        selectedValues={policy.required_apps || []}
                        onChange={(values) =>
                          updatePolicy(policy.id, { required_apps: values.length > 0 ? values : undefined })
                        }
                        placeholder={toolsLoading ? "Loading apps..." : "Select apps (optional)"}
                        disabled={!config.enablePolicies || toolsLoading}
                        allowWildcard={false}
                      />
                      <small>Optionally require approval for all tools from specific apps</small>
                    </div>

                    <div className="form-group">
                      <label>Approval Message (optional)</label>
                      <textarea
                        value={policy.approval_message || ""}
                        onChange={(e) => updatePolicy(policy.id, { approval_message: e.target.value || undefined })}
                        placeholder="This tool requires your approval before execution."
                        rows={3}
                        disabled={!config.enablePolicies}
                      />
                      <small>Custom message shown when requesting approval</small>
                    </div>

                    <div className="form-group">
                      <label>
                        <input
                          type="checkbox"
                          checked={policy.show_code_preview}
                          onChange={(e) => updatePolicy(policy.id, { show_code_preview: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        Show code preview in approval request
                      </label>
                    </div>

                    <div className="form-group">
                      <label>Auto-approve after (seconds, optional)</label>
                      <input
                        type="number"
                        value={policy.auto_approve_after || ""}
                        onChange={(e) => {
                          const value = e.target.value ? parseInt(e.target.value) : undefined;
                          updatePolicy(policy.id, { auto_approve_after: value });
                        }}
                        min="1"
                        placeholder="Leave empty for no auto-approve"
                        disabled={!config.enablePolicies}
                      />
                      <small>Automatically approve after N seconds (leave empty to disable)</small>
                    </div>

                    <div className="form-group">
                      <label>Priority</label>
                      <input
                        type="number"
                        value={policy.priority}
                        onChange={(e) => updatePolicy(policy.id, { priority: parseInt(e.target.value) })}
                        min="0"
                        max="100"
                        disabled={!config.enablePolicies}
                      />
                      <small>Higher priority approval policies are checked first</small>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {toolApprovals.length === 0 && (
          <div className="empty-state">
            <p>No tool approval policies configured. Click "Add Tool Approval" to create one.</p>
          </div>
        )}
      </div>
    );
  }

  function renderOutputFormatters() {
    return (
      <div className="config-card">
        <div className="section-header">
          <h3>Output Formatter Policies</h3>
          <button className="add-btn" onClick={addOutputFormatter} disabled={!config.enablePolicies}>
            <Plus size={16} />
            Add Output Formatter
          </button>
        </div>

        <div className="policies-list">
          {outputFormatters.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            const keywordTrigger = policy.triggers.find((t) => t.type === "keyword");
            const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];

            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <input
                      type="checkbox"
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <input
                      type="text"
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      className="agent-config-name"
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <button className="expand-btn" onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}>
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                    <button
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                  {!isExpanded && (
                    <div className="agent-summary">
                      <span className="agent-summary-item">
                        {policy.format_type === "direct"
                          ? "Direct"
                          : policy.format_type === "markdown"
                          ? "Markdown (LLM)"
                          : "JSON (LLM)"}
                      </span>
                      {keywords.length > 0 && (
                        <span className="agent-summary-item">
                          {keywords.length} keyword{keywords.length !== 1 ? "s" : ""}
                        </span>
                      )}
                      {policy.triggers.some((t) => t.type === "natural_language") && (
                        <span className="agent-summary-item">AI trigger</span>
                      )}
                      <span className="agent-summary-item">Priority: {policy.priority}</span>
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div className="agent-config-details">
                    <div className="form-group">
                      <label>Description</label>
                      <textarea
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        rows={2}
                        disabled={!config.enablePolicies}
                      />
                    </div>

                    <div className="form-group">
                      <label>Trigger Keywords (Optional)</label>
                      <TagInput
                        values={keywords}
                        onChange={(newKeywords) => {
                          const updatedTriggers = policy.triggers.filter((t) => t.type !== "keyword");
                          if (newKeywords.length > 0) {
                            const existingKeywordTrigger = policy.triggers.find((t) => t.type === "keyword");
                            updatedTriggers.push({
                              type: "keyword",
                              value: newKeywords,
                              target: "agent_response",
                              case_sensitive: false,
                              operator: existingKeywordTrigger?.operator || "and",
                            });
                          }
                          updatePolicy(policy.id, { triggers: updatedTriggers });
                        }}
                        placeholder="Type keyword and press Enter or comma"
                        disabled={!config.enablePolicies}
                      />
                      <small>
                        Keywords to match against the last AI message content. Leave empty to always format.
                      </small>
                    </div>

                    {keywords.length > 1 && (
                      <div className="form-group">
                        <label>Keyword Matching</label>
                        <select
                          value={keywordTrigger?.operator || "and"}
                          onChange={(e) => {
                            const operator = e.target.value as "and" | "or";
                            const updatedTriggers = policy.triggers.map((t) =>
                              t.type === "keyword" ? { ...t, operator } : t
                            );
                            updatePolicy(policy.id, { triggers: updatedTriggers });
                          }}
                          disabled={!config.enablePolicies}
                        >
                          <option value="and">Match ALL keywords (AND)</option>
                          <option value="or">Match ANY keyword (OR)</option>
                        </select>
                        <small>Choose whether all keywords or any keyword should trigger this formatter</small>
                      </div>
                    )}

                    {(() => {
                      const nlTrigger = policy.triggers.find((t) => t.type === "natural_language");
                      const nlTriggerValues = nlTrigger
                        ? Array.isArray(nlTrigger.value)
                          ? nlTrigger.value
                          : nlTrigger.value
                          ? [nlTrigger.value]
                          : []
                        : [];

                      return (
                        <div className="form-group">
                          <label>Natural Language Triggers</label>
                          {nlTrigger ? (
                            <>
                              <TagInput
                                values={nlTriggerValues}
                                onChange={(newValues) => {
                                  const updatedTriggers = policy.triggers.map((t) =>
                                    t.type === "natural_language" ? { ...t, value: newValues } : t
                                  );
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                placeholder="Type natural language trigger and press Enter"
                                disabled={!config.enablePolicies}
                              />
                              <div className="form-group" style={{ marginTop: "12px" }}>
                                <label>Similarity Threshold</label>
                                <input
                                  type="range"
                                  min="0.5"
                                  max="1.0"
                                  step="0.05"
                                  value={nlTrigger.threshold || 0.7}
                                  onChange={(e) => {
                                    const updatedTriggers = policy.triggers.map((t) =>
                                      t.type === "natural_language"
                                        ? { ...t, threshold: parseFloat(e.target.value) }
                                        : t
                                    );
                                    updatePolicy(policy.id, { triggers: updatedTriggers });
                                  }}
                                  disabled={!config.enablePolicies}
                                />
                                <small>
                                  Threshold: {(nlTrigger.threshold || 0.7).toFixed(2)} (higher = more strict matching)
                                </small>
                              </div>
                              <button
                                onClick={() => {
                                  const updatedTriggers = policy.triggers.filter((t) => t.type !== "natural_language");
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                disabled={!config.enablePolicies}
                                style={{
                                  marginTop: "8px",
                                  padding: "6px 12px",
                                  backgroundColor: "#ef4444",
                                  color: "white",
                                  border: "none",
                                  borderRadius: "4px",
                                  cursor: config.enablePolicies ? "pointer" : "not-allowed",
                                  fontSize: "12px",
                                }}
                              >
                                Remove Natural Language Trigger
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={() => {
                                const newTrigger: PolicyTrigger = {
                                  type: "natural_language",
                                  value: [],
                                  target: "agent_response",
                                  threshold: 0.7,
                                };
                                updatePolicy(policy.id, { triggers: [...policy.triggers, newTrigger] });
                              }}
                              disabled={!config.enablePolicies}
                              style={{
                                padding: "6px 12px",
                                backgroundColor: "#3b82f6",
                                color: "white",
                                border: "none",
                                borderRadius: "4px",
                                cursor: config.enablePolicies ? "pointer" : "not-allowed",
                                fontSize: "13px",
                              }}
                            >
                              + Add Natural Language Trigger
                            </button>
                          )}
                          <small>
                            Type natural language triggers and press Enter to add. AI will match similar responses using
                            semantic understanding.
                          </small>
                        </div>
                      );
                    })()}

                    <div className="form-group">
                      <label>Format Type</label>
                      <select
                        value={policy.format_type}
                        onChange={(e) =>
                          updatePolicy(policy.id, {
                            format_type: e.target.value as "markdown" | "json_schema" | "direct",
                          })
                        }
                        disabled={!config.enablePolicies}
                      >
                        <option value="direct">Direct Answer (No LLM)</option>
                        <option value="markdown">Markdown Instructions (LLM)</option>
                        <option value="json_schema">JSON Schema (LLM)</option>
                      </select>
                      <small>
                        {policy.format_type === "direct"
                          ? "Directly replace the response with the provided string (no LLM processing)"
                          : policy.format_type === "markdown"
                          ? "Use LLM to reformat the response according to markdown instructions"
                          : "Use LLM to extract and format the response as JSON matching the schema"}
                      </small>
                    </div>

                    <div className="form-group">
                      <label>
                        {policy.format_type === "direct"
                          ? "Direct Answer String"
                          : policy.format_type === "markdown"
                          ? "Formatting Instructions (Markdown)"
                          : "JSON Schema"}
                      </label>
                      <textarea
                        value={policy.format_config}
                        onChange={(e) => updatePolicy(policy.id, { format_config: e.target.value })}
                        placeholder={
                          policy.format_type === "direct"
                            ? "You are not allowed to view this sensitive data"
                            : policy.format_type === "markdown"
                            ? "Format the response in a clear, structured way with proper headings and bullet points."
                            : '{\n  "type": "object",\n  "properties": {\n    "summary": {"type": "string"},\n    "details": {"type": "array"}\n  }\n}'
                        }
                        rows={policy.format_type === "json_schema" ? 12 : policy.format_type === "direct" ? 4 : 8}
                        disabled={!config.enablePolicies}
                        style={{
                          fontFamily: policy.format_type === "direct" ? "inherit" : "monospace",
                          fontSize: "13px",
                        }}
                      />
                      <small>
                        {policy.format_type === "direct"
                          ? "This exact string will replace the AI response when triggers match (no LLM processing)"
                          : policy.format_type === "markdown"
                          ? "Markdown instructions for how to format the AI response (processed by LLM)"
                          : "JSON schema that the formatted response must match (processed by LLM)"}
                      </small>
                    </div>

                    <div className="form-group">
                      <label>Priority</label>
                      <input
                        type="number"
                        value={policy.priority}
                        onChange={(e) => updatePolicy(policy.id, { priority: parseInt(e.target.value) })}
                        min="0"
                        max="100"
                        disabled={!config.enablePolicies}
                      />
                      <small>Higher priority formatters are checked first</small>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {outputFormatters.length === 0 && (
          <div className="empty-state">
            <p>No output formatter policies configured. Click "Add Output Formatter" to create one.</p>
          </div>
        )}
      </div>
    );
  }
}