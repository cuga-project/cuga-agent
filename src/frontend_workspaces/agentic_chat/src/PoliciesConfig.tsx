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
  Tabs,
  TabList,
  Tab,
  TabPanels,
  TabPanel,
  Stack,
  FormGroup,
  Tile,
  Toggle,
} from "@carbon/react";
import { Save, Add, TrashCan, ChevronDown, ChevronUp, Download, Upload } from "@carbon/icons-react";

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
  draftMode?: boolean;
  onSave?: (policies: { enablePolicies: boolean; policies: unknown[] }) => void;
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
        {values.length > 0 && (
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
        )}
        <TextInput
          id={`tag-input-${Math.random().toString(36).substr(2, 9)}`}
          labelText={values.length === 0 ? "" : "Add another tag"}
          hideLabel={values.length > 0}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => {
            if (inputValue.trim()) {
              addTag(inputValue);
            }
          }}
          placeholder={values.length === 0 ? placeholder : "Type and press enter..."}
          disabled={disabled}
          helperText={helperText}
        />
      </Stack>
    </FormGroup>
  );
}

export default function PoliciesConfig({ onClose, draftMode = false, onSave }: PoliciesConfigProps) {
  const importInputRef = useRef<HTMLInputElement>(null);
  const [config, setConfig] = useState<PoliciesConfigData>({
    enablePolicies: true,
    policies: [],
  });
  
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
      const endpoint = draftMode ? "/api/manage/config?draft=1" : "/api/manage/config";
      const response = await fetch(endpoint);

      if (response.ok) {
        const data = await response.json();
        const configData = data.config || {};
        const policiesData = configData.policies || {};
        
        const normalizedPolicies = (policiesData.policies ?? []).map((policy: Policy) => ({
          ...policy,
          triggers: policy.triggers.map((trigger: PolicyTrigger) => {
            if (trigger.type === "natural_language" && trigger.value !== undefined) {
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
      const endpoint = draftMode ? "/api/tools/list?draft=1" : "/api/tools/list";
      const response = await fetch(endpoint);

      if (response.ok) {
        const data = await response.json();
        setAvailableTools(data.tools || []);
        setAvailableApps(data.apps || []);
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
          const normalizedPolicies = importedData.policies.map((policy: Policy) => ({
            ...policy,
            triggers: policy.triggers.map((trigger: PolicyTrigger) => {
              if (trigger.type === "natural_language" && trigger.value !== undefined) {
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
    event.target.value = "";
  };

  const saveConfig = async () => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    setSaveStatus("saving");

    try {
      const loadEndpoint = draftMode ? "/api/manage/config?draft=1" : "/api/manage/config";
      const loadResponse = await fetch(loadEndpoint);
      
      let existingConfig = {};
      if (loadResponse.ok) {
        const loadData = await loadResponse.json();
        existingConfig = loadData.config || {};
      }
      
      const normalizedPolicies = config.policies.map((policy) => ({
        ...policy,
        triggers: policy.triggers.map((trigger) => {
          if (trigger.type === "natural_language" && trigger.value !== undefined) {
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

      const fullConfig = {
        ...existingConfig,
        policies: normalizedConfig,
      };
      
      const endpoint = draftMode ? "/api/manage/config/draft" : "/api/manage/config";
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: fullConfig }),
      });

      if (response.ok) {
        setSaveStatus("success");
        setToastMessage({
          kind: "success",
          title: "Policies saved successfully",
          subtitle: `${normalizedConfig.policies.length} ${normalizedConfig.policies.length === 1 ? 'policy' : 'policies'} saved`,
        });
        onSave?.(normalizedConfig);
        setTimeout(() => {
          setSaveStatus("idle");
          onClose();
        }, 1500);
      } else {
        const errorText = await response.text();
        setSaveStatus("error");
        
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
      setSaveStatus("error");
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
    setConfig({ ...config, policies: [...config.policies, newPolicy] });
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
    setConfig({ ...config, policies: [...config.policies, newPolicy] });
  };

  const addToolGuide = () => {
    const newPolicy: ToolGuidePolicy = {
      id: `tool_guide_${Date.now()}`,
      name: "New Tool Guide",
      description: "Add additional context to tool descriptions",
      policy_type: "tool_guide",
      enabled: true,
      triggers: [{ type: "always" }],
      target_tools: ["*"],
      target_apps: undefined,
      guide_content: "## Additional Guidelines\n\n- Follow best practices\n- Consider security implications",
      prepend: false,
      priority: 50,
    };
    setConfig({ ...config, policies: [...config.policies, newPolicy] });
  };

  const addToolApproval = () => {
    const newPolicy: ToolApprovalPolicy = {
      id: `tool_approval_${Date.now()}`,
      name: "New Tool Approval",
      description: "Require approval before executing specific tools",
      policy_type: "tool_approval",
      enabled: true,
      triggers: [], 
      required_tools: [],
      required_apps: undefined,
      approval_message: "This tool requires your approval before execution.",
      show_code_preview: true,
      auto_approve_after: undefined,
      priority: 50,
    };
    setConfig({ ...config, policies: [...config.policies, newPolicy] });
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
    setConfig({ ...config, policies: [...config.policies, newPolicy] });
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
  const outputFormatters = config.policies.filter((p) => p.policy_type === "output_formatter") as OutputFormatterPolicy[];

  return (
    <>
      <ComposedModal open onClose={onClose} size="lg" isFullWidth preventCloseOnClickOutside>
        <ModalHeader title="Policies Configuration" buttonOnClick={onClose} />

        <ModalBody hasScrollingContent>
          <Theme theme="white">
            <Stack gap={6} style={{ paddingBottom: "2rem" }}>
              {/* Actions Header Row */}
              {/* <Stack orientation="horizontal" gap={3} style={{ justifyContent: "flex-end" }}>
                <Button kind="secondary" size="sm" renderIcon={Download} onClick={exportPolicies} disabled={config.policies.length === 0}>
                  Export
                </Button>
                <Button kind="secondary" size="sm" renderIcon={Upload} onClick={() => importInputRef.current?.click()}>
                  Import
                </Button>
                <input ref={importInputRef} type="file" accept=".json" onChange={importPolicies} style={{ display: "none" }} />
              </Stack> */}

              {/* Global Settings Block */}
              {isLoading ? (
                <Tile>
                  <p>Loading policies...</p>
                </Tile>
              ) : (
                <Tile>
                  <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                    <Stack gap={2}>
                      <h3 style={{ fontSize: "1rem", fontWeight: 600 }}>Global Policy System</h3>
                      <p style={{ color: "var(--cds-text-secondary)", fontSize: "0.875rem" }}>
                        Master switch for all policy enforcement ({config.policies.length} policies configured)
                      </p>
                    </Stack>
                    <Toggle
                      id="enable-policies-toggle"
                      labelText="Enable Policy System"
                      labelA="Disabled"
                      labelB="Enabled"
                      toggled={config.enablePolicies}
                      onToggle={(checked) => setConfig({ ...config, enablePolicies: checked })}
                      hideLabel
                    />
                  </Stack>
                </Tile>
              )}

              {/* Centralized Carbon Native Tabs */}
              {!isLoading && (
                <Tabs>
                  <TabList aria-label="Policy Types">
                    <Tab>Intent Guards ({intentGuards.length})</Tab>
                    <Tab>Playbooks ({playbooks.length})</Tab>
                    <Tab>Tool Guide ({ToolGuides.length})</Tab>
                    <Tab>Tool Approval ({toolApprovals.length})</Tab>
                    <Tab>Output Formatter ({outputFormatters.length})</Tab>
                  </TabList>
                  <TabPanels>
                    <TabPanel>{renderIntentGuards()}</TabPanel>
                    <TabPanel>{renderPlaybooks()}</TabPanel>
                    <TabPanel>{renderToolGuides()}</TabPanel>
                    <TabPanel>{renderToolApprovals()}</TabPanel>
                    <TabPanel>{renderOutputFormatters()}</TabPanel>
                  </TabPanels>
                </Tabs>
              )}
            </Stack>
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
        <div style={{ position: "fixed", top: "3rem", right: "1rem", zIndex: 10000, maxWidth: "400px" }}>
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
      <Stack gap={5} style={{ paddingTop: "1rem" }}>
        <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ fontSize: "1.25rem", fontWeight: 400 }}>Intent Guards</h4>
          <Button size="sm" renderIcon={Add} onClick={addIntentGuard} disabled={!config.enablePolicies}>
            Add Intent Guard
          </Button>
        </Stack>

        {intentGuards.length === 0 ? (
          <Tile>
            <p style={{ color: "var(--cds-text-secondary)" }}>No intent guards configured. Click "Add Intent Guard" to create one.</p>
          </Tile>
        ) : (
          <Stack gap={4}>
            {intentGuards.map((policy) => {
              const isExpanded = expandedPolicy === policy.id;
              const keywordTrigger = policy.triggers.find((t) => t.type === "keyword");
              const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];

              return (
                <Tile key={policy.id} style={{ padding: 0, border: "1px solid var(--cds-border-subtle)" }}>
                  <div style={{ padding: "1rem", backgroundColor: isExpanded ? "var(--cds-layer-01)" : "transparent" }}>
                    <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", flex: 1 }}>
                        <Checkbox
                          id={`enabled-${policy.id}`}
                          labelText="Enable Policy"
                          hideLabel
                          checked={policy.enabled}
                          onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        <div style={{ flex: 1, maxWidth: "400px" }}>
                          <TextInput
                            id={`name-${policy.id}`}
                            labelText="Policy Name"
                            hideLabel
                            value={policy.name}
                            onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                            placeholder="Policy Name"
                            disabled={!config.enablePolicies}
                          />
                        </div>
                      </Stack>
                      <Stack orientation="horizontal" gap={2}>
                        <Button
                          kind="ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={isExpanded ? ChevronUp : ChevronDown}
                          iconDescription={isExpanded ? "Collapse" : "Expand"}
                          tooltipPosition="bottom"
                          onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                        />
                        <Button
                          kind="danger--ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={TrashCan}
                          iconDescription="Delete"
                          tooltipPosition="bottom"
                          onClick={() => removePolicy(policy.id)}
                          disabled={!config.enablePolicies}
                        />
                      </Stack>
                    </Stack>

                    {!isExpanded && (
                      <Stack orientation="horizontal" gap={4} style={{ marginTop: "0.5rem", marginLeft: "2.5rem", color: "var(--cds-text-secondary)", fontSize: "0.75rem" }}>
                        {keywords.length > 0 && <span>{keywords.length} keyword{keywords.length !== 1 ? "s" : ""}</span>}
                        {policy.triggers.some((t) => t.type === "natural_language") && <span>AI trigger</span>}
                        <span>Priority: {policy.priority}</span>
                      </Stack>
                    )}
                  </div>

                  {isExpanded && (
                    <div style={{ padding: "1.5rem", borderTop: "1px solid var(--cds-border-subtle)", backgroundColor: "var(--cds-layer-00)" }}>
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
                          const nlTriggerValues = nlTrigger ? (Array.isArray(nlTrigger.value) ? nlTrigger.value : nlTrigger.value ? [nlTrigger.value] : []) : [];

                          return (
                            <Stack gap={5}>
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
                                    helperText="Type natural language triggers and press Enter to add. AI will match similar intents."
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
                                <div>
                                  <Button
                                    kind="tertiary"
                                    size="sm"
                                    renderIcon={Add}
                                    onClick={() => {
                                      const newTrigger: PolicyTrigger = { type: "natural_language", value: [], target: "intent", threshold: 0.7 };
                                      updatePolicy(policy.id, { triggers: [...policy.triggers, newTrigger] });
                                    }}
                                    disabled={!config.enablePolicies}
                                  >
                                    Add Natural Language Trigger
                                  </Button>
                                </div>
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

                        <Stack orientation="horizontal" gap={5}>
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

                          <div style={{ paddingTop: "1.5rem" }}>
                            <Checkbox
                              id={`allow-override-${policy.id}`}
                              labelText="Allow Override"
                              checked={policy.allow_override}
                              onChange={(e) => updatePolicy(policy.id, { allow_override: e.target.checked })}
                              disabled={!config.enablePolicies}
                            />
                            <div style={{ color: "var(--cds-text-secondary)", fontSize: "0.75rem", marginTop: "0.25rem" }}>
                              User can bypass this policy
                            </div>
                          </div>
                        </Stack>
                      </Stack>
                    </div>
                  )}
                </Tile>
              );
            })}
          </Stack>
        )}
      </Stack>
    );
  }

  function renderPlaybooks() {
    return (
      <Stack gap={5} style={{ paddingTop: "1rem" }}>
        <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ fontSize: "1.25rem", fontWeight: 400 }}>Playbooks</h4>
          <Button size="sm" renderIcon={Add} onClick={addPlaybook} disabled={!config.enablePolicies}>
            Add Playbook
          </Button>
        </Stack>

        {playbooks.length === 0 ? (
          <Tile>
            <p style={{ color: "var(--cds-text-secondary)" }}>No playbooks configured. Click "Add Playbook" to create one.</p>
          </Tile>
        ) : (
          <Stack gap={4}>
            {playbooks.map((policy) => {
              const isExpanded = expandedPolicy === policy.id;
              const keywordTrigger = policy.triggers.find((t) => t.type === "keyword");
              const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];

              return (
                <Tile key={policy.id} style={{ padding: 0, border: "1px solid var(--cds-border-subtle)" }}>
                  <div style={{ padding: "1rem", backgroundColor: isExpanded ? "var(--cds-layer-01)" : "transparent" }}>
                    <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", flex: 1 }}>
                        <Checkbox
                          id={`enabled-playbook-${policy.id}`}
                          labelText="Enable Policy"
                          hideLabel
                          checked={policy.enabled}
                          onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        <div style={{ flex: 1, maxWidth: "400px" }}>
                          <TextInput
                            id={`name-playbook-${policy.id}`}
                            labelText="Policy Name"
                            hideLabel
                            value={policy.name}
                            onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                            placeholder="Playbook Name"
                            disabled={!config.enablePolicies}
                          />
                        </div>
                      </Stack>
                      <Stack orientation="horizontal" gap={2}>
                        <Button
                          kind="ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={isExpanded ? ChevronUp : ChevronDown}
                          iconDescription={isExpanded ? "Collapse" : "Expand"}
                          tooltipPosition="bottom"
                          onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                        />
                        <Button
                          kind="danger--ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={TrashCan}
                          iconDescription="Delete"
                          tooltipPosition="bottom"
                          onClick={() => removePolicy(policy.id)}
                          disabled={!config.enablePolicies}
                        />
                      </Stack>
                    </Stack>
                    {!isExpanded && (
                      <Stack orientation="horizontal" gap={4} style={{ marginTop: "0.5rem", marginLeft: "2.5rem", color: "var(--cds-text-secondary)", fontSize: "0.75rem" }}>
                        <span>{policy.steps.length} step{policy.steps.length !== 1 ? "s" : ""}</span>
                        {policy.triggers.length > 0 && (
                          <span>
                            {policy.triggers[0].type === "natural_language"
                              ? "AI trigger"
                              : `${keywords.length} keyword${keywords.length !== 1 ? "s" : ""}`}
                          </span>
                        )}
                      </Stack>
                    )}
                  </div>

                  {isExpanded && (
                    <div style={{ padding: "1.5rem", borderTop: "1px solid var(--cds-border-subtle)", backgroundColor: "var(--cds-layer-00)" }}>
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
                                triggers: [{ type: "natural_language", value: [], target: "intent", threshold: 0.7 }],
                              });
                            } else {
                              updatePolicy(policy.id, {
                                triggers: [{ type: "keyword", value: [], target: "intent", case_sensitive: false, operator: "and" }],
                              });
                            }
                          }}
                          disabled={!config.enablePolicies}
                        >
                          <SelectItem value="keyword" text="Keywords (Exact Match)" />
                          <SelectItem value="natural_language" text="Natural Language (AI Match)" />
                        </Select>

                        {policy.triggers.length > 0 && policy.triggers[0].type === "keyword" && (
                          <Stack gap={5}>
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
                              helperText="Type keywords and press Enter or comma to add."
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
                          </Stack>
                        )}

                        {policy.triggers.length > 0 && policy.triggers[0].type === "natural_language" && (
                          <Stack gap={5}>
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
                          </Stack>
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
                </Tile>
              );
            })}
          </Stack>
        )}
      </Stack>
    );
  }

  function renderToolGuides() {
    return (
      <Stack gap={5} style={{ paddingTop: "1rem" }}>
        <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ fontSize: "1.25rem", fontWeight: 400 }}>Tool Guide Policies</h4>
          <Button size="sm" renderIcon={Add} onClick={addToolGuide} disabled={!config.enablePolicies}>
            Add Tool Guide
          </Button>
        </Stack>

        {ToolGuides.length === 0 ? (
          <Tile>
            <p style={{ color: "var(--cds-text-secondary)" }}>No tool guide policies configured. Click "Add Tool Guide" to create one.</p>
          </Tile>
        ) : (
          <Stack gap={4}>
            {ToolGuides.map((policy) => {
              const isExpanded = expandedPolicy === policy.id;
              return (
                <Tile key={policy.id} style={{ padding: 0, border: "1px solid var(--cds-border-subtle)" }}>
                  <div style={{ padding: "1rem", backgroundColor: isExpanded ? "var(--cds-layer-01)" : "transparent" }}>
                    <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", flex: 1 }}>
                        <Checkbox
                          id={`enabled-toolguide-${policy.id}`}
                          labelText="Enable Policy"
                          hideLabel
                          checked={policy.enabled}
                          onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        <div style={{ flex: 1, maxWidth: "400px" }}>
                          <TextInput
                            id={`name-toolguide-${policy.id}`}
                            labelText="Policy Name"
                            hideLabel
                            value={policy.name}
                            onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                            placeholder="Policy Name"
                            disabled={!config.enablePolicies}
                          />
                        </div>
                      </Stack>
                      <Stack orientation="horizontal" gap={2}>
                        <Button
                          kind="ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={isExpanded ? ChevronUp : ChevronDown}
                          iconDescription={isExpanded ? "Collapse" : "Expand"}
                          tooltipPosition="bottom"
                          onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                        />
                        <Button
                          kind="danger--ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={TrashCan}
                          iconDescription="Delete"
                          tooltipPosition="bottom"
                          onClick={() => removePolicy(policy.id)}
                          disabled={!config.enablePolicies}
                        />
                      </Stack>
                    </Stack>
                    {!isExpanded && (
                      <Stack orientation="horizontal" gap={4} style={{ marginTop: "0.5rem", marginLeft: "2.5rem", color: "var(--cds-text-secondary)", fontSize: "0.75rem" }}>
                        <span>{policy.target_tools.includes("*") ? "All tools" : `${policy.target_tools.length} tool(s)`}</span>
                        {policy.target_apps && policy.target_apps.length > 0 && <span>{policy.target_apps.length} app(s)</span>}
                        <span>Priority: {policy.priority}</span>
                      </Stack>
                    )}
                  </div>

                  {isExpanded && (
                    <div style={{ padding: "1.5rem", borderTop: "1px solid var(--cds-border-subtle)", backgroundColor: "var(--cds-layer-00)" }}>
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
                </Tile>
              );
            })}
          </Stack>
        )}
      </Stack>
    );
  }

  function renderToolApprovals() {
    return (
      <Stack gap={5} style={{ paddingTop: "1rem" }}>
        <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ fontSize: "1.25rem", fontWeight: 400 }}>Tool Approval Policies</h4>
          <Button size="sm" renderIcon={Add} onClick={addToolApproval} disabled={!config.enablePolicies}>
            Add Tool Approval
          </Button>
        </Stack>

        {toolApprovals.length === 0 ? (
          <Tile>
            <p style={{ color: "var(--cds-text-secondary)" }}>No tool approval policies configured. Click "Add Tool Approval" to create one.</p>
          </Tile>
        ) : (
          <Stack gap={4}>
            {toolApprovals.map((policy) => {
              const isExpanded = expandedPolicy === policy.id;
              return (
                <Tile key={policy.id} style={{ padding: 0, border: "1px solid var(--cds-border-subtle)" }}>
                  <div style={{ padding: "1rem", backgroundColor: isExpanded ? "var(--cds-layer-01)" : "transparent" }}>
                    <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", flex: 1 }}>
                        <Checkbox
                          id={`enabled-toolapproval-${policy.id}`}
                          labelText="Enable Policy"
                          hideLabel
                          checked={policy.enabled}
                          onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        <div style={{ flex: 1, maxWidth: "400px" }}>
                          <TextInput
                            id={`name-toolapproval-${policy.id}`}
                            labelText="Policy Name"
                            hideLabel
                            value={policy.name}
                            onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                            placeholder="Policy Name"
                            disabled={!config.enablePolicies}
                          />
                        </div>
                      </Stack>
                      <Stack orientation="horizontal" gap={2}>
                        <Button
                          kind="ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={isExpanded ? ChevronUp : ChevronDown}
                          iconDescription={isExpanded ? "Collapse" : "Expand"}
                          tooltipPosition="bottom"
                          onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                        />
                        <Button
                          kind="danger--ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={TrashCan}
                          iconDescription="Delete"
                          tooltipPosition="bottom"
                          onClick={() => removePolicy(policy.id)}
                          disabled={!config.enablePolicies}
                        />
                      </Stack>
                    </Stack>
                    {!isExpanded && (
                      <Stack orientation="horizontal" gap={4} style={{ marginTop: "0.5rem", marginLeft: "2.5rem", color: "var(--cds-text-secondary)", fontSize: "0.75rem" }}>
                        <span>
                          {policy.required_tools.length === 0
                            ? "No tools selected"
                            : policy.required_tools.includes("*")
                            ? "All tools"
                            : `${policy.required_tools.length} tool(s)`}
                        </span>
                        {policy.required_apps && policy.required_apps.length > 0 && <span>{policy.required_apps.length} app(s)</span>}
                        <span>Priority: {policy.priority}</span>
                      </Stack>
                    )}
                  </div>

                  {isExpanded && (
                    <div style={{ padding: "1.5rem", borderTop: "1px solid var(--cds-border-subtle)", backgroundColor: "var(--cds-layer-00)" }}>
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
                </Tile>
              );
            })}
          </Stack>
        )}
      </Stack>
    );
  }

  function renderOutputFormatters() {
    return (
      <Stack gap={5} style={{ paddingTop: "1rem" }}>
        <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h4 style={{ fontSize: "1.25rem", fontWeight: 400 }}>Output Formatter Policies</h4>
          <Button size="sm" renderIcon={Add} onClick={addOutputFormatter} disabled={!config.enablePolicies}>
            Add Output Formatter
          </Button>
        </Stack>

        {outputFormatters.length === 0 ? (
          <Tile>
            <p style={{ color: "var(--cds-text-secondary)" }}>No output formatter policies configured. Click "Add Output Formatter" to create one.</p>
          </Tile>
        ) : (
          <Stack gap={4}>
            {outputFormatters.map((policy) => {
              const isExpanded = expandedPolicy === policy.id;
              const keywordTrigger = policy.triggers.find((t) => t.type === "keyword");
              const keywords = keywordTrigger && Array.isArray(keywordTrigger.value) ? keywordTrigger.value : [];

              return (
                <Tile key={policy.id} style={{ padding: 0, border: "1px solid var(--cds-border-subtle)" }}>
                  <div style={{ padding: "1rem", backgroundColor: isExpanded ? "var(--cds-layer-01)" : "transparent" }}>
                    <Stack orientation="horizontal" style={{ justifyContent: "space-between", alignItems: "center" }}>
                      <Stack orientation="horizontal" gap={4} style={{ alignItems: "center", flex: 1 }}>
                        <Checkbox
                          id={`enabled-outputformatter-${policy.id}`}
                          labelText="Enable Policy"
                          hideLabel
                          checked={policy.enabled}
                          onChange={(e) => updatePolicy(policy.id, { enabled: e.target.checked })}
                          disabled={!config.enablePolicies}
                        />
                        <div style={{ flex: 1, maxWidth: "400px" }}>
                          <TextInput
                            id={`name-outputformatter-${policy.id}`}
                            labelText="Policy Name"
                            hideLabel
                            value={policy.name}
                            onChange={(e) => updatePolicy(policy.id, { name: e.target.value })}
                            placeholder="Policy Name"
                            disabled={!config.enablePolicies}
                          />
                        </div>
                      </Stack>
                      <Stack orientation="horizontal" gap={2}>
                        <Button
                          kind="ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={isExpanded ? ChevronUp : ChevronDown}
                          iconDescription={isExpanded ? "Collapse" : "Expand"}
                          tooltipPosition="bottom"
                          onClick={() => setExpandedPolicy(isExpanded ? null : policy.id)}
                        />
                        <Button
                          kind="danger--ghost"
                          size="sm"
                          hasIconOnly
                          renderIcon={TrashCan}
                          iconDescription="Delete"
                          tooltipPosition="bottom"
                          onClick={() => removePolicy(policy.id)}
                          disabled={!config.enablePolicies}
                        />
                      </Stack>
                    </Stack>
                    {!isExpanded && (
                      <Stack orientation="horizontal" gap={4} style={{ marginTop: "0.5rem", marginLeft: "2.5rem", color: "var(--cds-text-secondary)", fontSize: "0.75rem" }}>
                        <span>
                          {policy.format_type === "direct"
                            ? "Direct"
                            : policy.format_type === "markdown"
                            ? "Markdown (LLM)"
                            : "JSON (LLM)"}
                        </span>
                        {keywords.length > 0 && <span>{keywords.length} keyword{keywords.length !== 1 ? "s" : ""}</span>}
                        {policy.triggers.some((t) => t.type === "natural_language") && <span>AI trigger</span>}
                        <span>Priority: {policy.priority}</span>
                      </Stack>
                    )}
                  </div>

                  {isExpanded && (
                    <div style={{ padding: "1.5rem", borderTop: "1px solid var(--cds-border-subtle)", backgroundColor: "var(--cds-layer-00)" }}>
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
                          const nlTriggerValues = nlTrigger ? (Array.isArray(nlTrigger.value) ? nlTrigger.value : nlTrigger.value ? [nlTrigger.value] : []) : [];

                          return (
                            <Stack gap={5}>
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
                                    helperText="Type natural language triggers and press Enter to add. AI will match similar responses."
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
                                <div>
                                  <Button
                                    kind="tertiary"
                                    size="sm"
                                    renderIcon={Add}
                                    onClick={() => {
                                      const newTrigger: PolicyTrigger = { type: "natural_language", value: [], target: "agent_response", threshold: 0.7 };
                                      updatePolicy(policy.id, { triggers: [...policy.triggers, newTrigger] });
                                    }}
                                    disabled={!config.enablePolicies}
                                  >
                                    Add Natural Language Trigger
                                  </Button>
                                </div>
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
                </Tile>
              );
            })}
          </Stack>
        )}
      </Stack>
    );
  }
}