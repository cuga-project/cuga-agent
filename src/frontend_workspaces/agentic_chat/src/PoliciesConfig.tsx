// eslint-disable-next-line @typescript-eslint/no-unused-vars
import React, { useState, useEffect, useRef } from "react";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  ToastNotification,
  TextInput,
  TextArea,
  Checkbox,
  NumberInput,
  Select,
  SelectItem,
  MultiSelect,
  Tag,
  Theme,
  Slider,
  Accordion,
  AccordionItem,
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  Stack,
  FormGroup,
} from "@carbon/react";
import { Save, Add, TrashCan, ChevronDown, ChevronUp, Download, Upload, Search } from "@carbon/icons-react";
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


interface TagInputProps {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  labelText?: string;
  helperText?: string;
}

function TagInput({ values, onChange, placeholder, disabled, labelText, helperText }: TagInputProps) {
  const [inputValue, setInputValue] = useState("");

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
    <FormGroup legendText={labelText}>
      <Stack gap={4}>
        <Stack orientation="horizontal" gap={2} style={{ flexWrap: "wrap" }}>
          {values.map((tag, index) => (
            <Tag
              key={index}
              type="blue"
              filter
              onClose={() => !disabled && removeTag(index)}
              disabled={disabled}
            >
              {tag}
            </Tag>
          ))}
        </Stack>
        <TextInput
          id={`tag-input-${Math.random()}`}
          labelText=""
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
          helperText={helperText}
        />
      </Stack>
    </FormGroup>
  );
}

export default function PoliciesConfig({ onClose, draftMode = false }: PoliciesConfigProps) {
  const importInputRef = useRef<HTMLInputElement>(null);
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
  const [toastMessage, setToastMessage] = useState<{ kind: "success" | "error" | "warning"; title: string; subtitle: string } | null>(null);

  useEffect(() => {
    loadConfig();
    loadTools();
  }, []);

  const loadConfig = async () => {
    setIsLoading(true);
    try {
      console.log("[PoliciesConfig] Loading policies from manage API...");
      // Load from manage API to get the full config including policies
      const endpoint = draftMode ? "/api/manage/config?draft=1" : "/api/manage/config";
      const response = await fetch(endpoint);
      console.log("[PoliciesConfig] Response status:", response.status);

      if (response.ok) {
        const data = await response.json();
        console.log("[PoliciesConfig] Loaded config:", data);

        // Extract policies from config
        const configData = data.config || {};
        const policiesData = configData.policies || {};
        
        // Normalize natural_language trigger values to always be arrays (for backward compatibility)
        const normalizedPolicies = (policiesData.policies ?? []).map((policy: Policy) => ({
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
          enablePolicies: policiesData.enablePolicies ?? true,
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
      // Add draft parameter when in draft mode
      const endpoint = draftMode ? "/api/tools/list?draft=1" : "/api/tools/list";
      const response = await fetch(endpoint);

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
    console.log("[PoliciesConfig] saveConfig called - starting save process");
    
    // Force blur on any focused input to ensure pending changes are saved
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }

    // Small delay to ensure blur event handlers complete
    await new Promise((resolve) => setTimeout(resolve, 50));

    console.log("[PoliciesConfig] Setting save status to 'saving'");
    setSaveStatus("saving");
    try {
      // First, load the current full config
      console.log("[PoliciesConfig] Loading current config to merge with policies...");
      const loadEndpoint = draftMode ? "/api/manage/config?draft=1" : "/api/manage/config";
      const loadResponse = await fetch(loadEndpoint);
      
      let existingConfig = {};
      if (loadResponse.ok) {
        const loadData = await loadResponse.json();
        existingConfig = loadData.config || {};
        console.log("[PoliciesConfig] Loaded existing config:", existingConfig);
      } else {
        console.warn("[PoliciesConfig] Could not load existing config, will save policies only");
      }
      
      // Normalize natural_language trigger values to always be arrays
      const normalizedPolicies = config.policies.map((policy) => ({
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
      }));

      const normalizedConfig = {
        enablePolicies: config.enablePolicies,
        policies: normalizedPolicies,
      };

      console.log("[PoliciesConfig] Normalized policies config:", normalizedConfig);
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
      
      // Merge policies into existing config
      const fullConfig = {
        ...existingConfig,
        policies: normalizedConfig,
      };
      
      console.log("[PoliciesConfig] About to send POST request to manage API");
      console.log("[PoliciesConfig] Draft mode:", draftMode);
      console.log("[PoliciesConfig] Full config to save:", JSON.stringify(fullConfig).substring(0, 300) + "...");
      
      // Use manage API endpoints instead of direct config/policies endpoint
      const endpoint = draftMode ? "/api/manage/config/draft" : "/api/manage/config";
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          config: fullConfig
        }),
      });

      console.log("[PoliciesConfig] Response received - status:", response.status);

      if (response.ok) {
        const result = await response.json();
        console.log("[PoliciesConfig] Save successful:", result);
        setSaveStatus("success");
        
        // Show success toast
        setToastMessage({
          kind: "success",
          title: "Policies saved successfully",
          subtitle: `${normalizedConfig.policies.length} ${normalizedConfig.policies.length === 1 ? 'policy' : 'policies'} saved`,
        });
        
        // Close modal after short delay
        setTimeout(() => {
          setSaveStatus("idle");
          onClose();
        }, 1500);
      } else {
        const errorText = await response.text();
        console.error("[PoliciesConfig] Save failed:", response.status, errorText);
        setSaveStatus("error");
        
        // Show error toast
        let errorMessage = "Failed to save policies";
        try {
          const errorData = JSON.parse(errorText);
          errorMessage = errorData.error || errorData.message || errorMessage;
        } catch {
          errorMessage = errorText || errorMessage;
        }
        
        setToastMessage({
          kind: "error",
          title: "Save failed",
          subtitle: errorMessage,
        });
        
        setTimeout(() => setSaveStatus("idle"), 2000);
      }
    } catch (error) {
      console.error("[PoliciesConfig] Save error:", error);
      setSaveStatus("error");
      
      // Show error toast
      const errorMessage = error instanceof Error ? error.message : "Network error occurred";
      setToastMessage({
        kind: "error",
        title: "Save failed",
        subtitle: errorMessage,
      });
      
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
    <>
      <ComposedModal open onClose={onClose} size="lg" isFullWidth preventCloseOnClickOutside>
        <ModalHeader title="Policies Configuration" buttonOnClick={onClose} />

      <ModalBody hasScrollingContent className="config-modal-body-wrap">
    <Theme theme="white">
        <div className="config-modal-actions-row">
          <Button
            kind="secondary"
            size="sm"
            renderIcon={Download}
            onClick={exportPolicies}
            disabled={config.policies.length === 0}
          >
            Export
          </Button>
          <Button kind="secondary" size="sm" renderIcon={Upload} onClick={() => importInputRef.current?.click()}>
            Import
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept=".json"
            onChange={importPolicies}
            style={{ display: "none" }}
          />
        </div>
        <div className="config-modal-tabs">
          <Button
            kind={activeTab === "intent_guard" ? "primary" : "ghost"}
            size="sm"
            className={`config-tab ${activeTab === "intent_guard" ? "active" : ""}`}
            onClick={() => setActiveTab("intent_guard")}
          >
            Intent Guards ({intentGuards.length})
          </Button>
          <Button
            kind={activeTab === "playbook" ? "primary" : "ghost"}
            size="sm"
            className={`config-tab ${activeTab === "playbook" ? "active" : ""}`}
            onClick={() => setActiveTab("playbook")}
          >
            Playbooks ({playbooks.length})
          </Button>
          <Button
            kind={activeTab === "tool_guide" ? "primary" : "ghost"}
            size="sm"
            className={`config-tab ${activeTab === "tool_guide" ? "active" : ""}`}
            onClick={() => setActiveTab("tool_guide")}
          >
            Tool Guide ({ToolGuides.length})
          </Button>
          <Button
            kind={activeTab === "tool_approval" ? "primary" : "ghost"}
            size="sm"
            className={`config-tab ${activeTab === "tool_approval" ? "active" : ""}`}
            onClick={() => setActiveTab("tool_approval")}
          >
            Tool Approval ({toolApprovals.length})
          </Button>
          <Button
            kind={activeTab === "output_formatter" ? "primary" : "ghost"}
            size="sm"
            className={`config-tab ${activeTab === "output_formatter" ? "active" : ""}`}
            onClick={() => setActiveTab("output_formatter")}
          >
            Output Formatter ({outputFormatters.length})
          </Button>
        </div>

        <div className="config-modal-content">
          {isLoading ? (
            <div className="config-card">
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
        </Theme>
      </ModalBody>
      <ModalFooter>
        <Button kind="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button
          kind="primary"
          renderIcon={Save}
          onClick={saveConfig}
          disabled={saveStatus === "saving"}
        >
          {saveStatus === "idle" && "Save Changes"}
          {saveStatus === "saving" && "Saving..."}
          {saveStatus === "success" && "Saved!"}
          {saveStatus === "error" && "Error!"}
        </Button>
      </ModalFooter>
      </ComposedModal>
      
      {/* Toast Notification */}
      {toastMessage && (
        <div
          style={{
            position: "fixed",
            top: "3rem",
            right: "1rem",
            zIndex: 10000,
            maxWidth: "400px",
          }}
        >
          <ToastNotification
            kind={toastMessage.kind}
            title={toastMessage.title}
            subtitle={toastMessage.subtitle}
            timeout={5000}
            onClose={() => setToastMessage(null)}
            lowContrast
          />
        </div>
      )}
    </>
  );

  function renderIntentGuards() {
    return (
      <div className="config-card">
        <div className="section-header">
          <h3>Intent Guards</h3>
          <Button kind="primary" size="sm" renderIcon={Add} onClick={addIntentGuard} disabled={!config.enablePolicies} className="add-btn">
            Add Intent Guard
          </Button>
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
                    <TextInput
                      id={`name-${policy.id}`}
                      labelText=""
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription={isExpanded ? "Collapse" : "Expand"}
                      renderIcon={isExpanded ? ChevronUp : ChevronDown}
                      className="expand-btn"
                      onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Delete"
                      renderIcon={TrashCan}
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    />
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
                    <Stack gap={6}>
                      <TextArea
                        id={`description-${policy.id}`}
                        labelText="Description"
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        placeholder="What this policy does..."
                        rows={2}
                        disabled={!config.enablePolicies}
                      />

                      <TagInput
                      labelText="Trigger Keywords (Optional)"
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
                        helperText="Type keywords and press Enter or comma to add. Click × to remove."
                      />

                      {keywords.length > 1 && (
                        <Select
                        id={`keyword-operator-${policy.id}`}
                        labelText="Keyword Matching"
                        value={keywordTrigger?.operator || "and"}
                        onChange={(e) => {
                          const operator = e.target.value as "and" | "or";
                          const updatedTriggers = policy.triggers.map((t) =>
                            t.type === "keyword" ? { ...t, operator } : t
                          );
                          updatePolicy(policy.id, { triggers: updatedTriggers });
                        }}
                        disabled={!config.enablePolicies}
                        helperText="Choose whether all keywords or any keyword should trigger this policy"
                      >
                        <SelectItem value="and" text="Match ALL keywords (AND)" />
                        <SelectItem value="or" text="Match ANY keyword (OR)" />
                      </Select>
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
                        <Stack gap={4}>
                          {nlTrigger ? (
                            <>
                              <TagInput
                                labelText="Natural Language Triggers"
                                values={nlTriggerValues}
                                onChange={(newValues) => {
                                  const updatedTriggers = policy.triggers.map((t) =>
                                    t.type === "natural_language" ? { ...t, value: newValues } : t
                                  );
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                placeholder="Type natural language trigger and press Enter"
                                disabled={!config.enablePolicies}
                                helperText="Type natural language triggers and press Enter to add. AI will match similar intents using semantic understanding."
                              />
                              <Slider
                                id={`threshold-${policy.id}`}
                                labelText={`Similarity Threshold: ${(nlTrigger.threshold || 0.7).toFixed(2)}`}
                                min={0.5}
                                max={1.0}
                                step={0.05}
                                value={nlTrigger.threshold || 0.7}
                                onChange={(e) => {
                                  const updatedTriggers = policy.triggers.map((t) =>
                                    t.type === "natural_language" ? { ...t, threshold: e.value } : t
                                  );
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                disabled={!config.enablePolicies}
                              />
                              <Button
                                kind="danger"
                                size="sm"
                                onClick={() => {
                                  const updatedTriggers = policy.triggers.filter((t) => t.type !== "natural_language");
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                disabled={!config.enablePolicies}
                              >
                                Remove Natural Language Trigger
                              </Button>
                            </>
                          ) : (
                            <Button
                              kind="tertiary"
                              size="sm"
                              renderIcon={Add}
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
                            >
                              Add Natural Language Trigger
                            </Button>
                          )}
                        </Stack>
                      );
                      })()}

                      <TextArea
                      id={`response-${policy.id}`}
                      labelText="Response Message"
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

                      <Stack orientation="horizontal" gap={4}>
                      <NumberInput
                        id={`priority-${policy.id}`}
                        label="Priority"
                        value={policy.priority}
                        onChange={(e, { value }) => updatePolicy(policy.id, { priority: typeof value === 'number' ? value : 0 })}
                        min={0}
                        max={100}
                        disabled={!config.enablePolicies}
                        helperText="Higher priority policies are checked first"
                      />

                      <Checkbox
                        id={`allow-override-${policy.id}`}
                        labelText="Allow Override"
                        checked={policy.allow_override}
                        onChange={(e) => updatePolicy(policy.id, { allow_override: e.target.checked })}
                        disabled={!config.enablePolicies}
                          helperText="User can bypass this policy"
                        />
                      </Stack>
                    </Stack>
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
          <Button kind="primary" size="sm" renderIcon={Add} onClick={addPlaybook} disabled={!config.enablePolicies} className="add-btn">
            Add Playbook
          </Button>
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
                    <Checkbox
                      id={`enabled-playbook-${policy.id}`}
                      labelText=""
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <TextInput
                      id={`name-playbook-${policy.id}`}
                      labelText=""
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      placeholder="Playbook Name"
                      disabled={!config.enablePolicies}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription={isExpanded ? "Collapse" : "Expand"}
                      renderIcon={isExpanded ? ChevronUp : ChevronDown}
                      className="expand-btn"
                      onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Delete"
                      renderIcon={TrashCan}
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    />
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
                    <Stack gap={6}>
                      <TextArea
                        id={`description-playbook-${policy.id}`}
                        labelText="Description"
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        placeholder="What this playbook guides the user through..."
                        rows={2}
                        disabled={!config.enablePolicies}
                      />

                      <Select
                        id={`trigger-type-playbook-${policy.id}`}
                        labelText="Trigger Type"
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
                        <SelectItem value="keyword" text="Keywords (Exact Match)" />
                        <SelectItem value="natural_language" text="Natural Language (AI Match)" />
                      </Select>

                    {policy.triggers.length > 0 && policy.triggers[0].type === "keyword" && (
                      <>
                        <TagInput
                          labelText="Trigger Keywords"
                          values={keywords}
                          onChange={(newKeywords) => {
                            const newTriggers = policy.triggers.map((t) =>
                              t.type === "keyword" ? { ...t, value: newKeywords } : t
                            );
                            updatePolicy(policy.id, { triggers: newTriggers });
                          }}
                          placeholder="Type keyword and press Enter or comma"
                          disabled={!config.enablePolicies}
                          helperText="Type keywords and press Enter or comma to add. Click × to remove."
                        />

                        {keywords.length > 1 && (
                          <Select
                            id={`keyword-operator-playbook-${policy.id}`}
                            labelText="Keyword Matching"
                            value={keywordTrigger?.operator || "and"}
                            onChange={(e) => {
                              const operator = e.target.value as "and" | "or";
                              const newTriggers = policy.triggers.map((t) =>
                                t.type === "keyword" ? { ...t, operator } : t
                              );
                              updatePolicy(policy.id, { triggers: newTriggers });
                            }}
                            disabled={!config.enablePolicies}
                            helperText="Choose whether all keywords or any keyword should trigger this playbook"
                          >
                            <SelectItem value="and" text="Match ALL keywords (AND)" />
                            <SelectItem value="or" text="Match ANY keyword (OR)" />
                          </Select>
                        )}
                      </>
                    )}

                    {policy.triggers.length > 0 && policy.triggers[0].type === "natural_language" && (
                      <>
                        <TagInput
                          labelText="Natural Language Triggers"
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
                          helperText="Type natural language triggers and press Enter to add. AI will match similar user requests."
                        />

                        <Slider
                          id={`threshold-playbook-${policy.id}`}
                          labelText={`Similarity Threshold: ${(policy.triggers[0].threshold || 0.7).toFixed(2)}`}
                          min={0.5}
                          max={1.0}
                          step={0.05}
                          value={policy.triggers[0].threshold || 0.7}
                          onChange={(e) => {
                            const newTriggers = policy.triggers.map((t, idx) =>
                              idx === 0 ? { ...t, threshold: e.value } : t
                            );
                            updatePolicy(policy.id, { triggers: newTriggers });
                          }}
                          disabled={!config.enablePolicies}
                        />
                      </>
                    )}

                    <TextArea
                      id={`markdown-playbook-${policy.id}`}
                      labelText="Markdown Content"
                      value={policy.markdown_content}
                      onChange={(e) => updatePolicy(policy.id, { markdown_content: e.target.value })}
                      placeholder="# Task Guide&#10;&#10;## Steps:&#10;&#10;1. First step&#10;2. Second step"
                      rows={8}
                      disabled={!config.enablePolicies}
                      helperText="Markdown-formatted guidance that will be shown to the agent"
                    />

                    <NumberInput
                      id={`priority-playbook-${policy.id}`}
                      label="Priority"
                      value={policy.priority}
                      onChange={(e, { value }) => updatePolicy(policy.id, { priority: typeof value === 'number' ? value : 0 })}
                      min={0}
                      max={100}
                      disabled={!config.enablePolicies}
                      helperText="Higher priority playbooks are checked first"
                    />
                    </Stack>
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
          <Button kind="primary" size="sm" renderIcon={Add} onClick={addToolGuide} disabled={!config.enablePolicies} className="add-btn">
            Add Tool Guide
          </Button>
        </div>

        <div className="sources-list">
          {ToolGuides.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <Checkbox
                      id={`enabled-toolguide-${policy.id}`}
                      labelText=""
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <TextInput
                      id={`name-toolguide-${policy.id}`}
                      labelText=""
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription={isExpanded ? "Collapse" : "Expand"}
                      renderIcon={isExpanded ? ChevronUp : ChevronDown}
                      className="expand-btn"
                      onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Delete"
                      renderIcon={TrashCan}
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    />
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
                    <Stack gap={6}>
                      <TextArea
                        id={`description-toolguide-${policy.id}`}
                        labelText="Description"
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        rows={2}
                        disabled={!config.enablePolicies}
                      />

                      <MultiSelect
                      id={`target-tools-${policy.id}`}
                      titleText="Target Tools"
                      label={toolsLoading ? "Loading tools..." : "Select tools to enrich"}
                      items={availableTools.map((tool) => ({
                        id: tool.name,
                        label: tool.name,
                        text: `${tool.name} (${tool.app})`,
                      }))}
                      initialSelectedItems={availableTools
                        .filter((tool) => policy.target_tools.includes(tool.name))
                        .map((tool) => ({
                          id: tool.name,
                          label: tool.name,
                          text: `${tool.name} (${tool.app})`,
                        }))}
                      onChange={(e) => {
                        const selectedIds = e.selectedItems?.map((item: any) => item.id) || [];
                        updatePolicy(policy.id, { target_tools: selectedIds });
                      }}
                      disabled={!config.enablePolicies || toolsLoading}
                        helperText="Select specific tools to enrich, or use * to enrich all tools"
                      />

                      <MultiSelect
                      id={`target-apps-${policy.id}`}
                      titleText="Target Apps (Optional)"
                      label={toolsLoading ? "Loading apps..." : "Select apps (optional)"}
                      items={availableApps.map((app) => ({
                        id: app.name,
                        label: app.name,
                        text: `${app.name} (${app.type})`,
                      }))}
                      initialSelectedItems={availableApps
                        .filter((app) => policy.target_apps?.includes(app.name))
                        .map((app) => ({
                          id: app.name,
                          label: app.name,
                          text: `${app.name} (${app.type})`,
                        }))}
                      onChange={(e) => {
                        const selectedIds = e.selectedItems?.map((item: any) => item.id) || [];
                        updatePolicy(policy.id, { target_apps: selectedIds.length > 0 ? selectedIds : undefined });
                      }}
                      disabled={!config.enablePolicies || toolsLoading}
                        helperText="Optionally filter by app name"
                      />

                      <TextArea
                        id={`guide-content-${policy.id}`}
                        labelText="Guide Content (Markdown)"
                        value={policy.guide_content}
                        onChange={(e) => updatePolicy(policy.id, { guide_content: e.target.value })}
                        placeholder="## Additional Guidelines&#10;&#10;- Follow best practices&#10;- Consider security"
                        rows={6}
                        disabled={!config.enablePolicies}
                        helperText="Markdown content to add to tool descriptions"
                      />

                      <Checkbox
                        id={`prepend-${policy.id}`}
                        labelText="Prepend content (add before existing description)"
                        checked={policy.prepend}
                        onChange={(e) => updatePolicy(policy.id, { prepend: e.target.checked })}
                        disabled={!config.enablePolicies}
                      />

                      <NumberInput
                        id={`priority-toolguide-${policy.id}`}
                        label="Priority"
                        value={policy.priority}
                        onChange={(e, { value }) => updatePolicy(policy.id, { priority: typeof value === 'number' ? value : 0 })}
                        min={0}
                        max={100}
                        disabled={!config.enablePolicies}
                        helperText="Higher priority guides are applied first"
                      />
                    </Stack>
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
          <Button kind="primary" size="sm" renderIcon={Add} onClick={addToolApproval} disabled={!config.enablePolicies} className="add-btn">
            Add Tool Approval
          </Button>
        </div>

        <div className="policies-list">
          {toolApprovals.map((policy) => {
            const isExpanded = expandedPolicy === policy.id;
            return (
              <div key={policy.id} className="agent-config-card">
                <div className="agent-config-header">
                  <div className="agent-config-top">
                    <Checkbox
                      id={`enabled-toolapproval-${policy.id}`}
                      labelText=""
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <TextInput
                      id={`name-toolapproval-${policy.id}`}
                      labelText=""
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription={isExpanded ? "Collapse" : "Expand"}
                      renderIcon={isExpanded ? ChevronUp : ChevronDown}
                      className="expand-btn"
                      onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Delete"
                      renderIcon={TrashCan}
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    />
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
                    <Stack gap={6}>
                      <TextArea
                        id={`description-toolapproval-${policy.id}`}
                        labelText="Description"
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        rows={2}
                        disabled={!config.enablePolicies}
                      />

                      <MultiSelect
                      id={`required-tools-${policy.id}`}
                      titleText="Required Tools"
                      label={toolsLoading ? "Loading tools..." : "Select tools requiring approval"}
                      items={availableTools.map((tool) => ({
                        id: tool.name,
                        label: tool.name,
                        text: `${tool.name} (${tool.app})`,
                      }))}
                      initialSelectedItems={availableTools
                        .filter((tool) => policy.required_tools.includes(tool.name))
                        .map((tool) => ({
                          id: tool.name,
                          label: tool.name,
                          text: `${tool.name} (${tool.app})`,
                        }))}
                      onChange={(e) => {
                        const selectedIds = e.selectedItems?.map((item: any) => item.id) || [];
                        updatePolicy(policy.id, { required_tools: selectedIds });
                      }}
                      disabled={!config.enablePolicies || toolsLoading}
                        helperText="Tools that require approval before execution"
                      />

                      <MultiSelect
                      id={`required-apps-${policy.id}`}
                      titleText="Required Apps (Optional)"
                      label={toolsLoading ? "Loading apps..." : "Select apps (optional)"}
                      items={availableApps.map((app) => ({
                        id: app.name,
                        label: app.name,
                        text: `${app.name} (${app.type})`,
                      }))}
                      initialSelectedItems={availableApps
                        .filter((app) => policy.required_apps?.includes(app.name))
                        .map((app) => ({
                          id: app.name,
                          label: app.name,
                          text: `${app.name} (${app.type})`,
                        }))}
                      onChange={(e) => {
                        const selectedIds = e.selectedItems?.map((item: any) => item.id) || [];
                        updatePolicy(policy.id, { required_apps: selectedIds.length > 0 ? selectedIds : undefined });
                      }}
                      disabled={!config.enablePolicies || toolsLoading}
                        helperText="Optionally require approval for all tools from specific apps"
                      />

                      <TextArea
                        id={`approval-message-${policy.id}`}
                        labelText="Approval Message (optional)"
                        value={policy.approval_message || ""}
                        onChange={(e) => updatePolicy(policy.id, { approval_message: e.target.value || undefined })}
                        placeholder="This tool requires your approval before execution."
                        rows={3}
                        disabled={!config.enablePolicies}
                        helperText="Custom message shown when requesting approval"
                      />

                      <Checkbox
                        id={`show-code-${policy.id}`}
                        labelText="Show code preview in approval request"
                        checked={policy.show_code_preview}
                        onChange={(e) => updatePolicy(policy.id, { show_code_preview: e.target.checked })}
                        disabled={!config.enablePolicies}
                      />

                      <NumberInput
                        id={`auto-approve-${policy.id}`}
                        label="Auto-approve after (seconds, optional)"
                        value={policy.auto_approve_after || 0}
                        onChange={(e, { value }) => {
                          const val = typeof value === 'number' && value > 0 ? value : undefined;
                          updatePolicy(policy.id, { auto_approve_after: val });
                        }}
                        min={1}
                        placeholder="Leave empty for no auto-approve"
                        disabled={!config.enablePolicies}
                        helperText="Automatically approve after N seconds (leave empty to disable)"
                      />

                      <NumberInput
                        id={`priority-toolapproval-${policy.id}`}
                        label="Priority"
                        value={policy.priority}
                        onChange={(e, { value }) => updatePolicy(policy.id, { priority: typeof value === 'number' ? value : 0 })}
                        min={0}
                        max={100}
                        disabled={!config.enablePolicies}
                        helperText="Higher priority approval policies are checked first"
                      />
                    </Stack>
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
          <Button kind="primary" size="sm" renderIcon={Add} onClick={addOutputFormatter} disabled={!config.enablePolicies} className="add-btn">
            Add Output Formatter
          </Button>
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
                    <Checkbox
                      id={`enabled-outputformatter-${policy.id}`}
                      labelText=""
                      checked={policy.enabled}
                      onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                      disabled={!config.enablePolicies}
                    />
                    <TextInput
                      id={`name-outputformatter-${policy.id}`}
                      labelText=""
                      value={policy.name}
                      onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                      placeholder="Policy Name"
                      disabled={!config.enablePolicies}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription={isExpanded ? "Collapse" : "Expand"}
                      renderIcon={isExpanded ? ChevronUp : ChevronDown}
                      className="expand-btn"
                      onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Delete"
                      renderIcon={TrashCan}
                      className="delete-btn"
                      onClick={() => removePolicy(policy.id)}
                      disabled={!config.enablePolicies}
                    />
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
                    <Stack gap={6}>
                      <TextArea
                        id={`description-outputformatter-${policy.id}`}
                        labelText="Description"
                        value={policy.description}
                        onChange={(e) => updatePolicy(policy.id, { description: e.target.value })}
                        rows={2}
                        disabled={!config.enablePolicies}
                      />

                      <TagInput
                        labelText="Trigger Keywords (Optional)"
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
                        helperText="Keywords to match against the last AI message content. Leave empty to always format."
                      />

                      {keywords.length > 1 && (
                        <Select
                          id={`keyword-operator-outputformatter-${policy.id}`}
                          labelText="Keyword Matching"
                          value={keywordTrigger?.operator || "and"}
                          onChange={(e) => {
                            const operator = e.target.value as "and" | "or";
                            const updatedTriggers = policy.triggers.map((t) =>
                              t.type === "keyword" ? { ...t, operator } : t
                            );
                            updatePolicy(policy.id, { triggers: updatedTriggers });
                          }}
                          disabled={!config.enablePolicies}
                          helperText="Choose whether all keywords or any keyword should trigger this formatter"
                        >
                          <SelectItem value="and" text="Match ALL keywords (AND)" />
                          <SelectItem value="or" text="Match ANY keyword (OR)" />
                        </Select>
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
                        <Stack gap={4}>
                          {nlTrigger ? (
                            <>
                              <TagInput
                                labelText="Natural Language Triggers"
                                values={nlTriggerValues}
                                onChange={(newValues) => {
                                  const updatedTriggers = policy.triggers.map((t) =>
                                    t.type === "natural_language" ? { ...t, value: newValues } : t
                                  );
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                placeholder="Type natural language trigger and press Enter"
                                disabled={!config.enablePolicies}
                                helperText="Type natural language triggers and press Enter to add. AI will match similar responses using semantic understanding."
                              />
                              <Slider
                                id={`threshold-output-${policy.id}`}
                                labelText={`Similarity Threshold: ${(nlTrigger.threshold || 0.7).toFixed(2)}`}
                                min={0.5}
                                max={1.0}
                                step={0.05}
                                value={nlTrigger.threshold || 0.7}
                                onChange={(e) => {
                                  const updatedTriggers = policy.triggers.map((t) =>
                                    t.type === "natural_language" ? { ...t, threshold: e.value } : t
                                  );
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                disabled={!config.enablePolicies}
                              />
                              <Button
                                kind="danger"
                                size="sm"
                                onClick={() => {
                                  const updatedTriggers = policy.triggers.filter((t) => t.type !== "natural_language");
                                  updatePolicy(policy.id, { triggers: updatedTriggers });
                                }}
                                disabled={!config.enablePolicies}
                              >
                                Remove Natural Language Trigger
                              </Button>
                            </>
                          ) : (
                            <Button
                              kind="tertiary"
                              size="sm"
                              renderIcon={Add}
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
                            >
                              Add Natural Language Trigger
                            </Button>
                          )}
                        </Stack>
                      );
                      })()}

                      <Select
                        id={`format-type-${policy.id}`}
                        labelText="Format Type"
                        value={policy.format_type}
                        onChange={(e) =>
                          updatePolicy(policy.id, {
                            format_type: e.target.value as "markdown" | "json_schema" | "direct",
                          })
                        }
                        disabled={!config.enablePolicies}
                        helperText={
                          policy.format_type === "direct"
                            ? "Directly replace the response with the provided string (no LLM processing)"
                            : policy.format_type === "markdown"
                            ? "Use LLM to reformat the response according to markdown instructions"
                            : "Use LLM to extract and format the response as JSON matching the schema"
                        }
                      >
                        <SelectItem value="direct" text="Direct Answer (No LLM)" />
                        <SelectItem value="markdown" text="Markdown Instructions (LLM)" />
                        <SelectItem value="json_schema" text="JSON Schema (LLM)" />
                      </Select>

                      <TextArea
                        id={`format-config-${policy.id}`}
                        labelText={
                          policy.format_type === "direct"
                            ? "Direct Answer String"
                            : policy.format_type === "markdown"
                            ? "Formatting Instructions (Markdown)"
                            : "JSON Schema"
                        }
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
                        helperText={
                          policy.format_type === "direct"
                            ? "This exact string will replace the AI response when triggers match (no LLM processing)"
                            : policy.format_type === "markdown"
                            ? "Markdown instructions for how to format the AI response (processed by LLM)"
                            : "JSON schema that the formatted response must match (processed by LLM)"
                        }
                      />

                      <NumberInput
                        id={`priority-outputformatter-${policy.id}`}
                        label="Priority"
                        value={policy.priority}
                        onChange={(e, { value }) => updatePolicy(policy.id, { priority: Number(value) })}
                        min={0}
                        max={100}
                        disabled={!config.enablePolicies}
                        helperText="Higher priority formatters are checked first"
                      />
                    </Stack>
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