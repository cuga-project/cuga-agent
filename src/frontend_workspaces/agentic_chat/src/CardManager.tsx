import React, { useState, useEffect, useCallback, useRef } from "react";
import { marked } from "marked";

// Simple ChatInstance interface (no Carbon dependency)
interface ChatInstance {
  messaging: {
    addMessage?: (message: any) => Promise<void>;
    addMessageChunk?: (chunk: any) => void;
  };
  on?: (options: { type: string; handler: (event: any) => void }) => void;
}
import "./CardManager.css";
import "./CustomResponseStyles.css";
// Import components from CustomResponseExample
import TaskStatusDashboard from "./task_status_component";
import ActionStatusDashboard from "./action_status_component";
import CoderAgentOutput from "./coder_agent_output";
import AppAnalyzerComponent from "./app_analyzer_component";
import TaskDecompositionComponent from "./task_decomposition";
import ShortlisterComponent from "./shortlister";
import SingleExpandableContent from "./generic_component";
import ActionAgent from "./action_agent";
import QaAgentComponent from "./qa_agent";
import { FollowupAction } from "./Followup";
import { fetchStreamingData } from "./StreamingWorkflow";
import ToolCallFlowDisplay from "./ToolReview";
import PolicyBlockComponent from "./PolicyBlockComponent";
import PolicyPlaybookComponent from "./PolicyPlaybookComponent";

interface Step {
  id: string;
  title: string;
  content: string;
  expanded: boolean;
  isNew?: boolean;
  timestamp: number;
  completed?: boolean;
}

// Color constant for highlighting important information
const HIGHLIGHT_COLOR = "#4e00ec";

interface CardManagerProps {
  chatInstance: ChatInstance;
  threadId?: string;
  useDraftAgent?: boolean;
}

// Extend the global interface typing to include the new loader API
declare global {
  interface Window {
    aiSystemInterface?: {
      addStep: (title: string, content: string) => void;
      getAllSteps: () => Step[];
      stopProcessing: () => void;
      isProcessingStopped: () => boolean;
      setProcessingComplete: (isComplete: boolean) => void;
      forceReset: () => void;
      hasStepWithTitle: (title: string) => boolean;
      showNextCardLoader?: (show: boolean) => void;
    };
    CUGA_DEBUG_LOADERS?: boolean;
  }
}

const CardManager: React.FC<CardManagerProps> = ({ chatInstance, threadId, useDraftAgent }) => {
  const [currentSteps, setCurrentSteps] = useState<Step[]>([]);
  const [currentCardId, setCurrentCardId] = useState<string | null>(null);
  const [isProcessingComplete, setIsProcessingComplete] = useState(false);
  const [showDetails, setShowDetails] = useState<{ [key: string]: boolean }>({});
  const [isReasoningCollapsed, setIsReasoningCollapsed] = useState(false);
  const [hasFinalAnswer, setHasFinalAnswer] = useState(false);
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [isStopped, setIsStopped] = useState(false);
  const [viewMode, setViewMode] = useState<"inplace" | "append">("inplace");
  const [globalVariables, setGlobalVariables] = useState<Record<string, any>>({});
  const [variablesHistory, setVariablesHistory] = useState<
    Array<{
      id: string;
      title: string;
      timestamp: number;
      variables: Record<string, any>;
    }>
  >([]);
  const [selectedAnswerId, setSelectedAnswerId] = useState<string | null>(null);
  const [expandedCodePreviews, setExpandedCodePreviews] = useState<{ [key: string]: boolean }>({});
  // Loader for next step within this card is derived from processing state
  const cardRef = useRef<HTMLDivElement>(null);
  const stepRefs = useRef<{ [key: string]: HTMLDivElement | null }>({});

  // Function to mark a step as completed
  const markStepCompleted = useCallback((stepId: string) => {
    setCurrentSteps((prev) => prev.map((step) => (step.id === stepId ? { ...step, completed: true } : step)));
  }, []);

  // Initialize global interface

  // No cross-card loader logic needed; loader will be shown within the card while processing

  useEffect(() => {
    if (typeof window !== "undefined") {
      console.log("Setting up global aiSystemInterface");
      window.aiSystemInterface = {
        addStep: (title: string, content: string) => {
          console.log("🎯 addStep called:", title, content);
          console.log("🎯 Content type:", typeof content);
          console.log("🎯 Current steps before adding:", currentSteps.length);

          // If content is JSON string, try to parse and log it
          if (typeof content === "string" && (content.startsWith("{") || content.startsWith("["))) {
            try {
              const parsed = JSON.parse(content);
              console.log("🎯 Parsed content:", parsed);
              console.log("🎯 Has variables:", !!parsed.variables);
              console.log("🎯 Variables keys:", parsed.variables ? Object.keys(parsed.variables) : []);
            } catch (e) {
              console.log("🎯 Failed to parse content as JSON");
            }
          }

          const newStep: Step = {
            id: `step-${Date.now()}-${Math.random()}`,
            title,
            content,
            expanded: true,
            isNew: true,
            timestamp: Date.now(),
          };

          setCurrentSteps((prev) => {
            console.log("🎯 setCurrentSteps called with prev length:", prev.length);
            // If this is the first step, start a new card
            if (prev.length === 0) {
              const newCardId = `card-${Date.now()}`;
              setCurrentCardId(newCardId);
              console.log("🎯 First step - creating new card:", newCardId);
              return [newStep];
            }
            // Otherwise, add to current card
            console.log("🎯 Adding to existing card");
            return [...prev, newStep];
          });

          // Handle in-place card switching vs append mode
          if (viewMode === "inplace") {
            if (currentSteps.length > 0) {
              setCurrentStepIndex((prev) => prev + 1);
            } else {
              setCurrentStepIndex(0);
            }
          }

          // Auto-expand "Waiting for your input" components and collapse reasoning
          if (title === "SuggestHumanActions") {
            setShowDetails((prev) => ({
              ...prev,
              [newStep.id]: true,
            }));
            // Collapse reasoning process when user action is needed
            setIsReasoningCollapsed(true);
          }

          // Check if this is a final answer step (only Answer, not FinalAnswerAgent)
          if (title === "Answer") {
            console.log("🎯 Final answer detected, triggering reasoning collapse");
            setHasFinalAnswer(true);
            // Collapse reasoning immediately when final answer arrives
            setIsReasoningCollapsed(true);
            // Show details by default for final answer
            setShowDetails((prev) => ({
              ...prev,
              [newStep.id]: true,
            }));

            // Emit event to notify parent that final answer is complete
            setTimeout(() => {
              const event = new CustomEvent("finalAnswerComplete", {
                detail: { stepId: newStep.id },
              });
              window.dispatchEvent(event);
              console.log("🎯 Emitted finalAnswerComplete event");
            }, 500);
          }
        },
        // No external loader toggle needed for within-card loading
        getAllSteps: () => currentSteps,
        stopProcessing: () => {
          setIsStopped(true);
          setIsProcessingComplete(true);
          setIsReasoningCollapsed(true);
          setShowDetails({});
          setExpandedCodePreviews({});
        },
        isProcessingStopped: () => isProcessingComplete,
        setProcessingComplete: (isComplete: boolean) => {
          setIsProcessingComplete(isComplete);
        },
        forceReset: () => {
          setCurrentSteps([]);
          setIsProcessingComplete(false);
          setCurrentCardId(null);
          setIsReasoningCollapsed(false);
          setHasFinalAnswer(false);
          setCurrentStepIndex(0);
          setIsStopped(false);
          setShowDetails({});
          setExpandedCodePreviews({});
          stepRefs.current = {};
          // Note: variablesHistory is preserved across conversations
        },
        hasStepWithTitle: (title: string) => {
          return currentSteps.some((step) => step.title === title);
        },
      };
    }
  }, [currentSteps, currentCardId, isProcessingComplete, viewMode]);

  // Auto-scroll to latest step
  useEffect(() => {
    if (currentSteps.length > 0) {
      const timeoutId = setTimeout(() => {
        const latestStep = currentSteps[currentSteps.length - 1];
        const latestStepRef = stepRefs.current[latestStep.id];

        if (latestStepRef) {
          latestStepRef.scrollIntoView({
            behavior: "smooth",
            block: "center",
          });
        } else if (cardRef.current) {
          // Fallback to container scroll if step ref not found
          cardRef.current.scrollIntoView({
            behavior: "smooth",
            block: "center",
          });
        }
      }, 100);

      return () => clearTimeout(timeoutId);
    }
  }, [currentSteps.length]);

  // Cleanup step refs on unmount
  useEffect(() => {
    return () => {
      stepRefs.current = {};
    };
  }, []);

  // Extract variables from final answer steps and track by turn
  useEffect(() => {
    console.log("[Variables Debug] Processing steps, total:", currentSteps.length);
    const newHistory: Array<{
      id: string;
      title: string;
      timestamp: number;
      variables: Record<string, any>;
    }> = [];

    let turnNumber = 0;

    currentSteps.forEach((step) => {
      console.log("[Variables Debug] Step:", step.title, "Type:", typeof step.content);

      // Only process Answer or FinalAnswerAgent steps
      if (step.title !== "Answer" && step.title !== "FinalAnswerAgent") {
        return;
      }

      console.log("[Variables Debug] Processing Answer/FinalAnswerAgent step");

      try {
        let parsedContent: any;
        let variables: Record<string, any> = {};

        if (typeof step.content === "string") {
          try {
            parsedContent = JSON.parse(step.content);
            console.log("[Variables Debug] Parsed JSON content:", parsedContent);

            // Check if we have variables in the parsed content
            if (parsedContent.data !== undefined && parsedContent.variables) {
              variables = parsedContent.variables;
              console.log("[Variables Debug] Found variables in data:", variables);
            } else if (parsedContent.variables) {
              variables = parsedContent.variables;
              console.log("[Variables Debug] Found variables directly:", variables);
            }
          } catch (e) {
            console.log("[Variables Debug] Failed to parse JSON:", e);
          }
        } else if (step.content && typeof step.content === "object" && "variables" in step.content) {
          const contentWithVars = step.content as { variables?: Record<string, any> };
          if (contentWithVars.variables) {
            variables = contentWithVars.variables;
            console.log("[Variables Debug] Found variables in object:", variables);
          }
        }

        // Only add to history if this step has variables
        if (Object.keys(variables).length > 0) {
          console.log("[Variables Debug] Adding to history with", Object.keys(variables).length, "variables");
          newHistory.push({
            id: step.id,
            title: `Turn ${turnNumber}`,
            timestamp: step.timestamp,
            variables: variables,
          });
          turnNumber++;
        } else {
          console.log("[Variables Debug] No variables found in this step");
        }
      } catch (e) {
        console.log("[Variables Debug] Error processing step:", e);
      }
    });

    // Update history only if it actually changed
    setVariablesHistory((prev) => {
      // Check if history actually changed
      if (prev.length !== newHistory.length) {
        console.log("Variables history updated: length changed", prev.length, "->", newHistory.length);
        return newHistory;
      }

      // Check if any entries are different
      const hasChanges = prev.some((entry, index) => {
        const newEntry = newHistory[index];
        return (
          !newEntry ||
          entry.id !== newEntry.id ||
          JSON.stringify(entry.variables) !== JSON.stringify(newEntry.variables)
        );
      });

      if (hasChanges) {
        console.log("Variables history updated: content changed");
      }

      return hasChanges ? newHistory : prev;
    });

    // Update selectedAnswerId based on available history
    setSelectedAnswerId((currentSelectedId) => {
      // If we have new history from current steps, use that
      if (newHistory.length > 0) {
        if (currentSelectedId && newHistory.find((e) => e.id === currentSelectedId)) {
          // Keep current selection if it still exists in new history
          return currentSelectedId;
        }
        // Auto-select most recent from new history
        console.log("Auto-selecting most recent turn:", newHistory[newHistory.length - 1].title);
        return newHistory[newHistory.length - 1].id;
      }

      // No new history from current steps, check if we have existing history
      // This happens when forceReset is called - we want to preserve selection
      if (variablesHistory.length > 0) {
        if (currentSelectedId && variablesHistory.find((e) => e.id === currentSelectedId)) {
          // Keep current selection if it exists in existing history
          return currentSelectedId;
        }
        // Auto-select most recent from existing history
        console.log("Preserving selection from existing history:", variablesHistory[variablesHistory.length - 1].title);
        return variablesHistory[variablesHistory.length - 1].id;
      }

      // No history at all
      return null;
    });
  }, [currentSteps]);

  // Update globalVariables based on selected answer
  useEffect(() => {
    if (selectedAnswerId) {
      const selected = variablesHistory.find((e) => e.id === selectedAnswerId);
      if (selected) {
        setGlobalVariables(selected.variables);
      }
    } else if (variablesHistory.length > 0) {
      // Default to most recent
      setGlobalVariables(variablesHistory[variablesHistory.length - 1].variables);
    } else {
      setGlobalVariables({});
    }
  }, [selectedAnswerId, variablesHistory]);

  // Emit variables updates to App.tsx
  useEffect(() => {
    const event = new CustomEvent("variablesUpdate", {
      detail: {
        variables: globalVariables,
        history: variablesHistory,
      },
    });
    window.dispatchEvent(event);
  }, [globalVariables, variablesHistory]);

  // Toggle code preview expansion
  const toggleCodePreview = useCallback((stepId: string) => {
    setExpandedCodePreviews((prev) => ({ ...prev, [stepId]: !prev[stepId] }));
  }, []);

  // Helper function to check if a step should be rendered
  const shouldRenderStep = useCallback((step: Step) => {
    if (step.title === "simple_text") {
      return true;
    }

    // Parse content for description
    let parsedContent;
    try {
      if (typeof step.content === "string") {
        try {
          parsedContent = JSON.parse(step.content);
          const keys = Object.keys(parsedContent);
          if (keys.length === 1 && keys[0] === "data") {
            parsedContent = parsedContent.data;
          }
        } catch (e) {
          parsedContent = step.content;
        }
      } else {
        parsedContent = step.content;
      }
    } catch (error) {
      parsedContent = step.content;
    }

    const description = getCaseDescription(step.id, step.title, parsedContent);
    return description !== null;
  }, []);

  // Function to generate natural language descriptions for each case
  const getCaseDescription = (stepId: string, stepTitle: string, parsedContent: any) => {
    switch (stepTitle) {
      case "PlanControllerAgent":
        if (parsedContent.subtasks_progress && parsedContent.next_subtask) {
          const completed = parsedContent.subtasks_progress.filter((status: string) => status === "completed").length;
          const total = parsedContent.subtasks_progress.length;

          if (total === 0) {
            return `I'm managing the overall task progress. There's <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">one next task</span>. ${
              parsedContent.conclude_task
                ? "The task is ready to be concluded."
                : `Next up: <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.next_subtask}</span>`
            }`;
          }

          return `I'm managing the overall task progress. Currently <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${completed} out of ${total} subtasks</span> are completed. ${
            parsedContent.conclude_task
              ? "The task is ready to be concluded."
              : `Next up: <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.next_subtask}</span>`
          }`;
        }
        return "I'm analyzing the task structure and planning the execution approach.";

      case "TaskDecompositionAgent":
        const taskCount = parsedContent.task_decomposition?.length || 0;
        return `I've broken down your request into <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${taskCount} manageable steps</span>. Each step is designed to work with specific applications and accomplish a specific part of your overall goal.`;

      case "APIPlannerAgent":
        if (
          parsedContent.action &&
          (parsedContent.action_input_coder_agent ||
            parsedContent.action_input_shortlisting_agent ||
            parsedContent.action_input_conclude_task)
        ) {
          const actionType = parsedContent.action;
          if (actionType === "CoderAgent") {
            return `I'm preparing to write code for you. The task involves: <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${
              parsedContent.action_input_coder_agent?.task_description || "Code generation task"
            }</span>`;
          } else if (actionType === "ApiShortlistingAgent") {
            const taskDesc = parsedContent.action_input_shortlisting_agent?.task_description;
            if (taskDesc) {
              const preview = taskDesc.length > 60 ? taskDesc.substring(0, 60) + "..." : taskDesc;
              return `I'm analyzing available APIs, <span style="color:${HIGHLIGHT_COLOR}; font-weight:500;">${preview}</span>`;
            }
            return `I'm analyzing available APIs to find the best options for your request. This will help me understand what tools are available to accomplish your task.`;
          } else if (actionType === "ConcludeTask") {
            const taskDesc = parsedContent.action_input_conclude_task?.final_response;
            if (taskDesc) {
              const preview = taskDesc.length > 60 ? taskDesc.substring(0, 60) + "..." : taskDesc;
              return `I'm ready to provide you with the final answer based on all the work completed so far. <span style="color:${HIGHLIGHT_COLOR}; font-weight:500;">${preview}</span>`;
            }
            return `I'm ready to provide you with the final answer based on all the work completed so far.`;
          }
        }
        return "I'm reflecting on the code and planning the next steps in the workflow.";

      case "Policy":
        // Handle all policy events with unified display
        if (parsedContent && parsedContent.type === "policy") {
          const policyName = parsedContent.policy_name || "Policy";
          const policyType = parsedContent.policy_type || "unknown";
          const isBlocked = parsedContent.policy_blocked || false;

          const icon = isBlocked
            ? "🛑"
            : policyType === "playbook"
            ? "📖"
            : policyType === "tool_guide"
            ? "🔧"
            : policyType === "tool_approval"
            ? "✋"
            : "📋";
          const color = isBlocked ? "#ff6b6b" : "#3b82f6";
          const action = isBlocked
            ? "Blocked"
            : policyType === "playbook"
            ? "Activated"
            : policyType === "tool_guide"
            ? "Enriched"
            : policyType === "tool_approval"
            ? "Requires Approval"
            : "Active";

          return `<span style="color: ${color}; font-weight: 600;">${icon} Policy ${action}</span> - <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${policyName}</span> (${policyType})`;
        }
        return "Policy enforcement in progress...";

      case "PolicyBlock":
        // Legacy support
        if (parsedContent && parsedContent.type === "policy_block") {
          const policyName = parsedContent.metadata?.policy_name || "Security Policy";
          return `<span style="color: #ff6b6b; font-weight: 600;">🛡️ Intent Blocked</span> - Your request was blocked by <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${policyName}</span> for security reasons.`;
        }
        return "Policy enforcement in progress...";

      case "PolicyPlaybook":
        // Legacy support
        if (parsedContent && parsedContent.type === "policy_playbook") {
          const playbookName = parsedContent.metadata?.policy_name || "Workflow Playbook";
          return `<span style="color: #3b82f6; font-weight: 600;">📖 Playbook Activated</span> - Following <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${playbookName}</span> to guide you through this process.`;
        }
        return "A security policy has been applied to your request.";

      case "CodeAgent":
        // Check if we have meaningful content
        const hasCode = parsedContent.code && parsedContent.code.trim().length > 0;
        const hasOutput = parsedContent.execution_output && parsedContent.execution_output.trim().length > 0;

        if (hasCode || hasOutput) {
          // Handle case where we have code
          const codeLines = hasCode ? parsedContent.code.split("\n").length : 0;
          const allCodeLines = hasCode ? parsedContent.code.split("\n") : [];
          const isExpanded = expandedCodePreviews[stepId];
          const maxPreviewLines = 4;
          const codePreviewLines = isExpanded ? allCodeLines : allCodeLines.slice(0, maxPreviewLines);
          const hasMoreLines = codeLines > maxPreviewLines;

          return (
            <div>
              {hasCode && (
                <>
                  {parsedContent.execution_output ? (
                    <span>
                      I've generated and executed{" "}
                      <span style={{ color: HIGHLIGHT_COLOR, fontWeight: 600 }}>{codeLines} lines of code</span>. Code
                      preview:
                    </span>
                  ) : (
                    <span>
                      I've generated{" "}
                      <span style={{ color: HIGHLIGHT_COLOR, fontWeight: 600 }}>{codeLines} lines of code</span> to
                      accomplish your request. Preview:
                    </span>
                  )}
                  <div
                    style={{
                      color: "#6366f1",
                      fontFamily: "monospace",
                      background: "#eef2ff",
                      padding: "8px 10px",
                      borderRadius: "4px",
                      fontSize: "11px",
                      lineHeight: "1.4",
                      marginTop: "6px",
                      borderLeft: "3px solid #6366f1",
                      position: "relative",
                      overflowX: "auto",
                      maxWidth: "100%",
                    }}
                  >
                    {codePreviewLines.map((line: string, idx: number) => {
                      return (
                        <div key={idx} style={{ whiteSpace: "pre" }}>
                          {line || "\u00A0"}
                        </div>
                      );
                    })}
                    {!isExpanded && hasMoreLines && <div style={{ color: "#94a3b8" }}>...</div>}
                    {hasMoreLines && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleCodePreview(stepId);
                        }}
                        style={{
                          position: "absolute",
                          right: "8px",
                          bottom: "8px",
                          background: "#6366f1",
                          color: "white",
                          border: "none",
                          borderRadius: "3px",
                          padding: "2px 8px",
                          fontSize: "10px",
                          cursor: "pointer",
                          fontFamily: "sans-serif",
                        }}
                        onMouseOver={(e) => (e.currentTarget.style.background = "#4f46e5")}
                        onMouseOut={(e) => (e.currentTarget.style.background = "#6366f1")}
                      >
                        {isExpanded ? "▲ Less" : "▼ More"}
                      </button>
                    )}
                  </div>
                </>
              )}

              {!hasCode && parsedContent.execution_output && <span>Code execution completed. Output:</span>}

              {parsedContent.execution_output &&
                (() => {
                  const output = parsedContent.execution_output.trim();
                  const outputLines = output.split("\n");
                  const isOutputExpanded = expandedCodePreviews[`${stepId}_output`];
                  const maxPreviewLines = 3;
                  const previewLines = isOutputExpanded ? outputLines : outputLines.slice(0, maxPreviewLines);
                  const hasMoreOutput = outputLines.length > maxPreviewLines || output.length > 300;

                  return (
                    <div style={{ marginTop: "8px" }}>
                      <div style={{ fontSize: "12px", color: "#059669", fontWeight: 500, marginBottom: "4px" }}>
                        Execution Output:
                      </div>
                      <div
                        style={{
                          color: "#065f46",
                          fontFamily: "monospace",
                          background: "#f0fdf4",
                          padding: "8px 10px",
                          borderRadius: "4px",
                          fontSize: "11px",
                          lineHeight: "1.4",
                          borderLeft: "3px solid #10b981",
                          position: "relative",
                          maxHeight: isOutputExpanded ? "none" : "150px",
                          overflow: isOutputExpanded ? "auto" : "hidden",
                          overflowX: "auto",
                          maxWidth: "100%",
                        }}
                      >
                        {previewLines.map((line: string, idx: number) => {
                          return (
                            <div key={idx} style={{ whiteSpace: "pre" }}>
                              {line || "\u00A0"}
                            </div>
                          );
                        })}
                        {!isOutputExpanded && hasMoreOutput && <div style={{ color: "#94a3b8" }}>...</div>}
                        {hasMoreOutput && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleCodePreview(`${stepId}_output`);
                            }}
                            style={{
                              position: "absolute",
                              right: "8px",
                              bottom: "8px",
                              background: "#10b981",
                              color: "white",
                              border: "none",
                              borderRadius: "3px",
                              padding: "2px 8px",
                              fontSize: "10px",
                              cursor: "pointer",
                              fontFamily: "sans-serif",
                            }}
                            onMouseOver={(e) => (e.currentTarget.style.background = "#059669")}
                            onMouseOut={(e) => (e.currentTarget.style.background = "#10b981")}
                          >
                            {isOutputExpanded ? "▲ Less" : "▼ More"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })()}
            </div>
          );
        }
        // Return null to skip rendering empty CodeAgent events
        return null;

      case "CodeAgent_Reasoning":
        // Text response from LLM (no code)
        if (typeof parsedContent === "string" && parsedContent) {
          const isExpanded = expandedCodePreviews[stepId];
          const maxPreviewLength = 200;
          const hasMoreContent = parsedContent.length > maxPreviewLength;
          const displayContent = isExpanded ? parsedContent : parsedContent.substring(0, maxPreviewLength);

          return (
            <div>
              <span>I'm reasoning about your request:</span>
              <div
                style={{
                  color: "#475569",
                  fontStyle: "italic",
                  background: "#f8fafc",
                  padding: "8px 10px",
                  borderRadius: "4px",
                  fontSize: "12px",
                  lineHeight: "1.5",
                  marginTop: "6px",
                  borderLeft: "3px solid #94a3b8",
                  position: "relative",
                  whiteSpace: "pre-wrap",
                  overflowX: "auto",
                  maxWidth: "100%",
                }}
              >
                {displayContent}
                {!isExpanded && hasMoreContent && <span style={{ color: "#94a3b8" }}>...</span>}
                {hasMoreContent && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleCodePreview(stepId);
                    }}
                    style={{
                      position: "absolute",
                      right: "8px",
                      bottom: "8px",
                      background: "#64748b",
                      color: "white",
                      border: "none",
                      borderRadius: "3px",
                      padding: "2px 8px",
                      fontSize: "10px",
                      cursor: "pointer",
                      fontFamily: "sans-serif",
                    }}
                    onMouseOver={(e) => (e.currentTarget.style.background = "#475569")}
                    onMouseOut={(e) => (e.currentTarget.style.background = "#64748b")}
                  >
                    {isExpanded ? "▲ Less" : "▼ More"}
                  </button>
                )}
              </div>
            </div>
          );
        }
        // Return null to skip rendering empty CodeAgent_Reasoning events
        return null;

      case "ShortlisterAgent":
        if (parsedContent.result) {
          const apiCount = parsedContent.result.length;
          const topResult = parsedContent.result[0];
          const topScore = topResult?.relevance_score || 0;
          const apiName = topResult?.name || topResult?.title || "Unknown API";
          const truncatedName = apiName.length > 30 ? apiName.substring(0, 30) + "..." : apiName;
          return `I've analyzed and shortlisted <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${apiCount} relevant APIs</span> for your request. The top match is <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${truncatedName}</span> with a <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${Math.round(
            topScore * 100
          )}% relevance score</span>.`;
        }
        return "I'm analyzing available APIs to find the most relevant ones for your request.";

      case "TaskAnalyzerAgent":
        if (parsedContent && Array.isArray(parsedContent)) {
          const appNames = parsedContent
            .map((app) => `<span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${app.name}</span>`)
            .join(", ");
          return `I've identified <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${parsedContent.length} integrated applications</span> that can help with your request: ${appNames}. These apps are ready to be used in the workflow.`;
        }
        return "I'm analyzing the available applications to understand what tools we can use.";

      case "PlannerAgent":
        return `I'm planning the next action in the workflow. This involves determining the best approach to continue working on your request.`;

      case "QaAgent":
        if (parsedContent.name && parsedContent.answer) {
          return `I've analyzed the question "<span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${
            parsedContent.name
          }</span>" and provided a comprehensive answer with <span style="color:${HIGHLIGHT_COLOR}; font-weight: 600;">${
            parsedContent.answer.split(" ").length
          } words</span>.`;
        }
        return "I'm processing a question and preparing a detailed answer.";

      case "FinalAnswerAgent":
        if (parsedContent.final_answer) {
          return `I've completed your request and prepared the final answer.`;
        }
        return "I'm preparing the final answer to your request.";

      case "ReuseAgent":
        if (typeof parsedContent === "string") return parsedContent.split("\n")[0];
        return "Save and reuse operation completed.";

      case "SuggestHumanActions":
        if (parsedContent.action_id) {
          return "I'm waiting for your input to continue. Please review the suggested action and let me know how you'd like to proceed.";
        }
        return "I'm preparing suggestions for your next action.";
      case "APICodePlannerAgent":
        const contentPreview = typeof parsedContent === "string" ? parsedContent : JSON.stringify(parsedContent);
        const preview = contentPreview.length > 80 ? contentPreview.substring(0, 80) + "..." : contentPreview;
        return `I've generated a plan for the coding agent to follow. Plan preview: <span style="color:${HIGHLIGHT_COLOR}; font-weight:500;">${preview}</span>`;
      default:
        return "";
    }
  };

  // Memoized function to render the appropriate component based on step title and content
  const renderStepContent = useCallback(
    (step: Step, allSteps?: Step[]) => {
      try {
        let parsedContent: any;

        if (typeof step.content === "string") {
          try {
            parsedContent = JSON.parse(step.content);
            const keys = Object.keys(parsedContent);

            console.log(`[${step.title}] Raw parsed content:`, parsedContent);
            console.log(`[${step.title}] Has data:`, parsedContent.data !== undefined);
            console.log(`[${step.title}] Has variables:`, !!parsedContent.variables);

            // Check if we have variables in the parsed content
            if (parsedContent.data !== undefined && parsedContent.variables) {
              console.log(`[${step.title}] Processing with variables...`);

              // Parse data if it's a JSON string
              let dataValue = parsedContent.data;
              let extractedPolicies: any[] = parsedContent.active_policies || [];
              
              if (typeof dataValue === "string") {
                try {
                  const parsedData = JSON.parse(dataValue);
                  // Check if the parsed data is a policy object
                  if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                    extractedPolicies = [parsedData];
                    // Use the content as final_answer if it's a policy
                    dataValue = parsedData.content || parsedData.response_content || dataValue;
                  }
                } catch (e) {
                  // Not JSON, keep as string
                }
              } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
                // Data is already a policy object
                extractedPolicies = [dataValue];
                dataValue = dataValue.content || dataValue.response_content || "";
              }

              // For Answer step with variables: treat data as final_answer
              if (step.title === "Answer" || step.title === "FinalAnswerAgent") {
                parsedContent = {
                  final_answer: dataValue,
                  variables: parsedContent.variables,
                  active_policies: extractedPolicies,
                };
                console.log(`[${step.title}] Converted to final_answer format:`, parsedContent);
              } else if (typeof parsedContent.data === "object" && !Array.isArray(parsedContent.data)) {
                // Keep both data and variables if data is an object
                parsedContent = {
                  ...parsedContent.data,
                  variables: parsedContent.variables,
                  active_policies: extractedPolicies,
                };
              } else {
                // If data is not an object, keep as is with variables
                parsedContent = {
                  data: dataValue,
                  variables: parsedContent.variables,
                  active_policies: extractedPolicies,
                };
              }
            } else if (keys.length === 1 && keys[0] === "data") {
              // Only data, no variables - check if data is a policy JSON string
              let dataValue = parsedContent.data;
              let extractedPolicies: any[] = [];
              
              if (typeof dataValue === "string") {
                try {
                  const parsedData = JSON.parse(dataValue);
                  if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                    extractedPolicies = [parsedData];
                    // For playbook, the content is the guide, not the final answer
                    // The final answer should come from elsewhere or be empty
                    const isPlaybook = parsedData.policy_type === "playbook";
                    parsedContent = {
                      final_answer: isPlaybook ? "" : (parsedData.content || parsedData.response_content || ""),
                      active_policies: extractedPolicies,
                    };
                  } else {
                    parsedContent = dataValue;
                  }
                } catch (e) {
                  parsedContent = dataValue;
                }
              } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
                extractedPolicies = [dataValue];
                const isPlaybook = dataValue.policy_type === "playbook";
                parsedContent = {
                  final_answer: isPlaybook ? "" : (dataValue.content || dataValue.response_content || ""),
                  active_policies: extractedPolicies,
                };
              } else {
                parsedContent = dataValue;
              }
            } else if (parsedContent.active_policies) {
              // Preserve active_policies even if no variables
              parsedContent = {
                ...parsedContent,
                active_policies: parsedContent.active_policies,
              };
            }
          } catch (e) {
            parsedContent = step.content; // fallback
          }
        } else {
          parsedContent = step.content; // already an object
        }
        let outputElements = [];
        // Only render ToolCallFlowDisplay for non-SuggestHumanActions steps
        // SuggestHumanActions uses FollowupAction component which handles its own display
        if (
          step.title !== "SuggestHumanActions" &&
          parsedContent &&
          parsedContent.additional_data &&
          parsedContent.additional_data.tool
        ) {
          const newElem = <ToolCallFlowDisplay toolData={parsedContent.additional_data.tool} />;
          outputElements.push(newElem);
        }

        let mainElement = null;

        switch (step.title) {
          case "PlanControllerAgent":
            if (parsedContent.subtasks_progress && parsedContent.next_subtask) {
              mainElement = <TaskStatusDashboard taskData={parsedContent} />;
            }
            break;
          case "TaskDecompositionAgent":
            mainElement = <TaskDecompositionComponent decompositionData={parsedContent} />;
            break;
          case "APIPlannerAgent":
            if (
              parsedContent.action &&
              (parsedContent.action_input_coder_agent ||
                parsedContent.action_input_shortlisting_agent ||
                parsedContent.action_input_conclude_task)
            ) {
              mainElement = <ActionStatusDashboard actionData={parsedContent} />;
            } else {
              mainElement = <SingleExpandableContent title={"Code Reflection"} content={parsedContent} />;
            }
            break;
          case "CodeAgent":
            if (parsedContent.code || parsedContent.execution_output) {
              mainElement = <CoderAgentOutput coderData={parsedContent} />;
            }
            break;
          case "Policy":
            // Handle all policy events with unified JSON display
            if (parsedContent && parsedContent.type === "policy") {
              const policyType = parsedContent.policy_type || "unknown";
              const policyName = parsedContent.policy_name || "Unknown Policy";
              const isBlocked = parsedContent.policy_blocked || false;

              mainElement = (
                <div
                  style={{
                    padding: "16px",
                    backgroundColor: isBlocked ? "#fee" : "#e3f2fd",
                    border: `2px solid ${isBlocked ? "#d32f2f" : "#2196f3"}`,
                    borderRadius: "8px",
                    marginTop: "8px",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      marginBottom: "12px",
                    }}
                  >
                    <span style={{ fontSize: "24px" }}>
                      {isBlocked
                        ? "🛑"
                        : policyType === "playbook"
                        ? "📖"
                        : policyType === "tool_guide"
                        ? "🔧"
                        : policyType === "tool_approval"
                        ? "✋"
                        : "📋"}
                    </span>
                    <h3 style={{ margin: 0, color: isBlocked ? "#d32f2f" : "#1976d2" }}>
                      {isBlocked ? "Policy Blocked" : "Policy Active"}: {policyName}
                    </h3>
                  </div>
                  <div
                    style={{
                      backgroundColor: "#fff",
                      padding: "12px",
                      borderRadius: "4px",
                      fontSize: "13px",
                      fontFamily: "monospace",
                      maxHeight: "400px",
                      overflow: "auto",
                    }}
                  >
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {JSON.stringify(parsedContent, null, 2)}
                    </pre>
                  </div>
                </div>
              );
            }
            break;
          case "PolicyBlock":
            // Legacy support - redirect to Policy
            if (parsedContent && parsedContent.type === "policy_block") {
              mainElement = <PolicyBlockComponent data={parsedContent} />;
            }
            break;
          case "PolicyPlaybook":
            // Legacy support - redirect to Policy
            if (parsedContent && parsedContent.type === "policy_playbook") {
              mainElement = <PolicyPlaybookComponent data={parsedContent} />;
            }
            break;
          case "CodeAgent_Reasoning":
            // Display reasoning text in a clean format
            if (typeof parsedContent === "string" || parsedContent) {
              const textContent =
                typeof parsedContent === "string" ? parsedContent : JSON.stringify(parsedContent, null, 2);
              mainElement = (
                <div
                  style={{
                    fontSize: "14px",
                    lineHeight: "1.6",
                    color: "#475569",
                    padding: "12px",
                    backgroundColor: "#f8fafc",
                    borderRadius: "6px",
                    border: "1px solid #e2e8f0",
                    fontStyle: "italic",
                  }}
                  dangerouslySetInnerHTML={{ __html: marked(textContent) }}
                />
              );
            }
            break;
          case "ShortlisterAgent":
            if (parsedContent) {
              mainElement = <ShortlisterComponent shortlisterData={parsedContent} />;
            }
            break;
          case "WaitForResponse":
            return null;
          case "TaskAnalyzerAgent":
            if (parsedContent && Array.isArray(parsedContent)) {
              mainElement = <AppAnalyzerComponent appData={parsedContent} />;
            }
            break;
          case "PlannerAgent":
            if (parsedContent) {
              mainElement = <ActionAgent agentData={parsedContent} />;
            }
            break;
          case "simple_text_box":
            if (parsedContent) {
              mainElement = parsedContent;
            }
            break;
          case "QaAgent":
            if (parsedContent) {
              mainElement = <QaAgentComponent qaData={parsedContent} />;
            }
            break;
          case "Answer":
          case "FinalAnswerAgent":
            if (parsedContent) {
              console.log("Answer/FinalAnswerAgent - parsedContent:", parsedContent);
              
              // Check if data field contains a policy object - if so, extract answer and policies
              let answerText = parsedContent.final_answer || (typeof parsedContent === "string" ? parsedContent : null);
              let activePolicies = parsedContent.active_policies || [];
              
              if (parsedContent.data && typeof parsedContent.data === "object" && parsedContent.data.type === "policy") {
                // Data is a policy object - extract policies and check for answer
                const policyData = parsedContent.data;
                activePolicies = [policyData];
                
                // For playbook, answer comes separately, but for other policies, content might be the answer
                if (policyData.policy_type !== "playbook" && !answerText) {
                  answerText = policyData.content || policyData.metadata?.response_content || null;
                }
              }
              
              // Find playbook policy if any
              const playbookPolicy = activePolicies.find((policy: any) => policy.policy_type === "playbook");
              
              // Extract playbook guide content
              let playbookGuideContent = "";
              if (playbookPolicy) {
                playbookGuideContent = playbookPolicy.metadata?.playbook_content 
                  || playbookPolicy.metadata?.playbook_guidance 
                  || "";
                if (!playbookGuideContent && playbookPolicy.content) {
                  const content = playbookPolicy.content;
                  const cleanedContent = content.replace(/^## 📖[^\n]*\n\n?/, "");
                  playbookGuideContent = cleanedContent;
                }
              }
              
              // For Answer events with playbook policy, ALWAYS look for FinalAnswerAgent step to get the actual final answer
              // The FinalAnswerAgent's final_answer should take precedence over any other answer text
              // IMPORTANT: For playbook policies, the answer should NEVER be the playbook guide content
              if (step.title === "Answer" && playbookPolicy && allSteps) {
                console.log("Answer - Playbook policy detected, looking for FinalAnswerAgent step in", allSteps.length, "steps");
                const finalAnswerStep = allSteps.find((s: Step) => s.title === "FinalAnswerAgent");
                if (finalAnswerStep) {
                  console.log("Answer - Found FinalAnswerAgent step:", finalAnswerStep.id);
                  try {
                    let finalAnswerContent: any;
                    if (typeof finalAnswerStep.content === "string") {
                      finalAnswerContent = JSON.parse(finalAnswerStep.content);
                    } else {
                      finalAnswerContent = finalAnswerStep.content;
                    }
                    console.log("Answer - FinalAnswerAgent parsed content:", finalAnswerContent);
                    const finalAnswerFromAgent = finalAnswerContent.final_answer || null;
                    if (finalAnswerFromAgent) {
                      // Prioritize FinalAnswerAgent's final_answer over any other answer text
                      answerText = finalAnswerFromAgent;
                      console.log("Answer - Using FinalAnswerAgent's final_answer:", answerText);
                    } else {
                      console.log("Answer - FinalAnswerAgent step found but no final_answer field");
                      // Don't use playbook content as answer - leave answerText as null/empty
                      answerText = null;
                    }
                  } catch (e) {
                    console.log("Answer - Error parsing FinalAnswerAgent content:", e);
                    // Don't use playbook content as answer - leave answerText as null/empty
                    answerText = null;
                  }
                } else {
                  console.log("Answer - Playbook policy found but no FinalAnswerAgent step available yet");
                  // Don't use playbook content as answer - leave answerText as null/empty
                  answerText = null;
                }
              } else if (step.title === "Answer" && playbookPolicy) {
                // Playbook policy but no allSteps available - don't use playbook content as answer
                console.log("Answer - Playbook policy found but allSteps not available");
                answerText = null;
              }

              console.log("Answer/FinalAnswerAgent - answerText:", answerText);
              console.log("Answer/FinalAnswerAgent - activePolicies:", activePolicies);
              console.log("Answer/FinalAnswerAgent - playbookGuideContent:", playbookGuideContent);

              if (answerText) {
                let renderedContent: string;
                
                if (typeof answerText === "string") {
                  // Check if content is in markdown HTML code block (```html ... ```)
                  const htmlCodeBlockMatch = answerText.match(/^```html\s*\n([\s\S]*?)\n```$/);
                  if (htmlCodeBlockMatch) {
                    // Extract HTML from code block and render as HTML
                    renderedContent = htmlCodeBlockMatch[1];
                  } else if (/<[a-z][\s\S]*>/i.test(answerText)) {
                    // Direct HTML content
                    renderedContent = answerText;
                  } else {
                    // Regular markdown content
                    renderedContent = marked(answerText) as string;
                  }
                } else {
                  renderedContent = marked(String(answerText)) as string;
                }

                mainElement = (
                  <div>
                    {/* 1. Answer text - always show */}
                    <div
                      style={{
                        fontSize: "14px",
                        lineHeight: "1.6",
                        color: "#1e293b",
                        marginBottom: (playbookGuideContent || activePolicies.length > 0) ? "20px" : "0",
                      }}
                      dangerouslySetInnerHTML={{ __html: renderedContent }}
                    />
                    
                    {/* 2. Playbook guide content (collapsible if available) */}
                    {playbookGuideContent && (
                      <div style={{ marginTop: "20px", paddingTop: "20px", borderTop: "1px solid #e5e7eb" }}>
                        <div
                          style={{
                            fontSize: "14px",
                            fontWeight: 600,
                            color: "#0ea5e9",
                            marginBottom: "12px",
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                          }}
                        >
                          <span style={{ fontSize: "18px" }}>📖</span>
                          <span>Task Guide</span>
                        </div>
                        <details style={{ position: "relative" }}>
                          <summary
                            style={{
                              cursor: "pointer",
                              fontSize: "13px",
                              fontWeight: 500,
                              color: "#64748b",
                              padding: "6px 0",
                              userSelect: "none",
                              display: "flex",
                              alignItems: "center",
                              gap: "6px",
                              listStyle: "none",
                            }}
                          >
                            <span>Steps:</span>
                            <span style={{ marginLeft: "auto", fontSize: "11px", color: "#94a3b8" }}>▼ Show steps</span>
                          </summary>
                          <div
                            style={{
                              fontSize: "14px",
                              lineHeight: "1.6",
                              color: "#1e293b",
                              marginTop: "12px",
                              paddingTop: "12px",
                              borderTop: "1px solid #e5e7eb",
                            }}
                          >
                            <div dangerouslySetInnerHTML={{ __html: marked(playbookGuideContent) }} />
                          </div>
                        </details>
                      </div>
                    )}
                    
                    {/* 3. Policy reasoning (all policies including playbook) */}
                    {activePolicies.length > 0 && (
                      <div style={{ marginTop: "20px", paddingTop: "20px", borderTop: "1px solid #e5e7eb" }}>
                        {activePolicies.map((policy: any, index: number) => {
                            const policyMetadata = policy.metadata || {};
                            const policyReasoning = policyMetadata.policy_reasoning || "";
                            const policyType = policy.policy_type || "unknown";
                            const policyName = policy.policy_name || "Unknown Policy";
                            const isBlocked = policy.policy_blocked || false;

                            if (!policyReasoning) return null;

                            // More subtle design for intent_guard policies
                            const isIntentGuard = policyType === "intent_guard";
                            
                            const backgroundColor = isIntentGuard
                              ? "#f8fafc"
                              : isBlocked
                              ? "#fef2f2"
                              : policyType === "tool_approval"
                              ? "#fffbeb"
                              : policyType === "playbook"
                              ? "#eff6ff"
                              : policyType === "tool_guide"
                              ? "#f0fdf4"
                              : policyType === "output_formatter"
                              ? "#fef3f2"
                              : "#f1f5f9";

                            const borderColor = isIntentGuard
                              ? "#e2e8f0"
                              : isBlocked
                              ? "#ef4444"
                              : policyType === "tool_approval"
                              ? "#f59e0b"
                              : policyType === "playbook"
                              ? "#3b82f6"
                              : policyType === "tool_guide"
                              ? "#10b981"
                              : policyType === "output_formatter"
                              ? "#f97316"
                              : "#64748b";

                            const icon = isIntentGuard
                              ? "ℹ️"
                              : policyType === "playbook"
                              ? "📖"
                              : policyType === "tool_guide"
                              ? "🔧"
                              : policyType === "tool_approval"
                              ? "✋"
                              : policyType === "output_formatter"
                              ? "✨"
                              : "📋";

                            return (
                              <div
                                key={index}
                                style={{
                                  marginTop: index > 0 ? "12px" : "0",
                                  padding: isIntentGuard ? "12px 16px" : "16px",
                                  backgroundColor: backgroundColor,
                                  border: isIntentGuard ? `1px solid ${borderColor}` : `2px solid ${borderColor}`,
                                  borderRadius: "8px",
                                  boxShadow: isIntentGuard ? "none" : "0 1px 3px rgba(0, 0, 0, 0.1)",
                                }}
                              >
                                <div
                                  style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "8px",
                                    marginBottom: isIntentGuard ? "8px" : "12px",
                                  }}
                                >
                                  {isBlocked ? (
                                    <div
                                      style={{
                                        display: "inline-flex",
                                        alignItems: "center",
                                        gap: "6px",
                                        backgroundColor: "#dc2626",
                                        color: "#ffffff",
                                        padding: "5px 12px",
                                        borderRadius: "6px",
                                        fontSize: "12px",
                                        fontWeight: 700,
                                        border: "none",
                                        boxShadow: "0 1px 2px rgba(220, 38, 38, 0.3)",
                                        textTransform: "uppercase",
                                        letterSpacing: "0.5px",
                                      }}
                                    >
                                      <span
                                        style={{
                                          width: "8px",
                                          height: "8px",
                                          borderRadius: "50%",
                                          backgroundColor: "#ffffff",
                                          display: "inline-block",
                                          boxShadow: "0 0 0 2px #dc2626",
                                        }}
                                      />
                                      Blocked
                                    </div>
                                  ) : (
                                    <span style={{ fontSize: isIntentGuard ? "16px" : "20px", opacity: isIntentGuard ? 0.7 : 1 }}>
                                      {icon}
                                    </span>
                                  )}
                                  {!isBlocked && (
                                    <h4
                                      style={{
                                        margin: 0,
                                        fontSize: "14px",
                                        fontWeight: 600,
                                        color: isIntentGuard ? "#64748b" : borderColor,
                                      }}
                                    >
                                      {policyName}
                                    </h4>
                                  )}
                                  {isBlocked && (
                                    <h4
                                      style={{
                                        margin: 0,
                                        fontSize: "14px",
                                        fontWeight: 600,
                                        color: "#dc2626",
                                      }}
                                    >
                                      {policyName}
                                    </h4>
                                  )}
                                  <span
                                    style={{
                                      fontSize: "10px",
                                      color: isIntentGuard ? "#94a3b8" : isBlocked ? "#991b1b" : "#64748b",
                                      backgroundColor: isIntentGuard ? "#f1f5f9" : isBlocked ? "#fee2e2" : "#e5e7eb",
                                      padding: "2px 8px",
                                      borderRadius: "12px",
                                      textTransform: "capitalize",
                                      fontWeight: isIntentGuard ? 400 : 500,
                                    }}
                                  >
                                    {policyType.replace("_", " ")}
                                  </span>
                                </div>
                                <div
                                  style={{
                                    fontSize: "12px",
                                    lineHeight: "1.6",
                                    color: isIntentGuard ? "#64748b" : "#374151",
                                    padding: isIntentGuard ? "8px 0" : "12px",
                                    backgroundColor: isIntentGuard ? "transparent" : "#ffffff",
                                    borderRadius: isIntentGuard ? "0" : "6px",
                                    border: isIntentGuard ? "none" : "1px solid #e5e7eb",
                                    fontFamily: "system-ui, -apple-system, sans-serif",
                                  }}
                                >
                                  {!isIntentGuard && (
                                    <div
                                      style={{
                                        fontWeight: 500,
                                        color: "#6b7280",
                                        marginBottom: "6px",
                                        fontSize: "12px",
                                        textTransform: "uppercase",
                                        letterSpacing: "0.5px",
                                      }}
                                    >
                                      Policy Reasoning
                                    </div>
                                  )}
                                  <div style={{ color: isIntentGuard ? "#64748b" : "#1f2937", fontStyle: isIntentGuard ? "normal" : "normal" }}>
                                    {policyReasoning}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                } else if (playbookGuideContent || activePolicies.length > 0) {
                  // No answer text, but we have playbook or policies to show
                  mainElement = (
                    <div>
                      {/* Playbook guide content (collapsible if available) */}
                      {playbookGuideContent && (
                        <div style={{ marginBottom: activePolicies.length > 0 ? "20px" : "0" }}>
                          <div
                            style={{
                              fontSize: "14px",
                              fontWeight: 600,
                              color: "#0ea5e9",
                              marginBottom: "12px",
                              display: "flex",
                              alignItems: "center",
                              gap: "8px",
                            }}
                          >
                            <span style={{ fontSize: "18px" }}>📖</span>
                            <span>Task Guide</span>
                          </div>
                          <details style={{ position: "relative" }}>
                            <summary
                              style={{
                                cursor: "pointer",
                                fontSize: "13px",
                                fontWeight: 500,
                                color: "#64748b",
                                padding: "6px 0",
                                userSelect: "none",
                                display: "flex",
                                alignItems: "center",
                                gap: "6px",
                                listStyle: "none",
                              }}
                            >
                              <span>Steps:</span>
                              <span style={{ marginLeft: "auto", fontSize: "11px", color: "#94a3b8" }}>▼ Show steps</span>
                            </summary>
                            <div
                              style={{
                                fontSize: "14px",
                                lineHeight: "1.6",
                                color: "#1e293b",
                                marginTop: "12px",
                                paddingTop: "12px",
                                borderTop: "1px solid #e5e7eb",
                              }}
                            >
                              <div dangerouslySetInnerHTML={{ __html: marked(playbookGuideContent) }} />
                            </div>
                          </details>
                        </div>
                      )}
                      
                      {/* Policy reasoning (all policies including playbook) */}
                      {activePolicies.length > 0 && (
                        <div style={{ marginTop: playbookGuideContent ? "20px" : "0", paddingTop: playbookGuideContent ? "20px" : "0", borderTop: playbookGuideContent ? "1px solid #e5e7eb" : "none" }}>
                          {activePolicies.map((policy: any, index: number) => {
                            const policyMetadata = policy.metadata || {};
                            const policyReasoning = policyMetadata.policy_reasoning || "";
                            const policyType = policy.policy_type || "unknown";
                            const policyName = policy.policy_name || "Unknown Policy";
                            const isBlocked = policy.policy_blocked || false;

                            if (!policyReasoning) return null;

                            const isIntentGuard = policyType === "intent_guard";
                            
                            const backgroundColor = isIntentGuard
                              ? "#f8fafc"
                              : isBlocked
                              ? "#fef2f2"
                              : policyType === "tool_approval"
                              ? "#fffbeb"
                              : policyType === "playbook"
                              ? "#eff6ff"
                              : policyType === "tool_guide"
                              ? "#f0fdf4"
                              : policyType === "output_formatter"
                              ? "#fef3f2"
                              : "#f1f5f9";

                            const borderColor = isIntentGuard
                              ? "#e2e8f0"
                              : isBlocked
                              ? "#ef4444"
                              : policyType === "tool_approval"
                              ? "#f59e0b"
                              : policyType === "playbook"
                              ? "#3b82f6"
                              : policyType === "tool_guide"
                              ? "#10b981"
                              : policyType === "output_formatter"
                              ? "#f97316"
                              : "#64748b";

                            const icon = isIntentGuard
                              ? "ℹ️"
                              : policyType === "playbook"
                              ? "📖"
                              : policyType === "tool_guide"
                              ? "🔧"
                              : policyType === "tool_approval"
                              ? "✋"
                              : policyType === "output_formatter"
                              ? "✨"
                              : "📋";

                            return (
                              <div
                                key={index}
                                style={{
                                  marginTop: index > 0 ? "12px" : "0",
                                  padding: isIntentGuard ? "12px 16px" : "16px",
                                  backgroundColor: backgroundColor,
                                  border: isIntentGuard ? `1px solid ${borderColor}` : `2px solid ${borderColor}`,
                                  borderRadius: "8px",
                                  boxShadow: isIntentGuard ? "none" : "0 1px 3px rgba(0, 0, 0, 0.1)",
                                }}
                              >
                                <div
                                  style={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: "8px",
                                    marginBottom: isIntentGuard ? "8px" : "12px",
                                  }}
                                >
                                  {isBlocked ? (
                                    <div
                                      style={{
                                        display: "inline-flex",
                                        alignItems: "center",
                                        gap: "6px",
                                        backgroundColor: "#dc2626",
                                        color: "#ffffff",
                                        padding: "5px 12px",
                                        borderRadius: "6px",
                                        fontSize: "12px",
                                        fontWeight: 700,
                                        border: "none",
                                        boxShadow: "0 1px 2px rgba(220, 38, 38, 0.3)",
                                        textTransform: "uppercase",
                                        letterSpacing: "0.5px",
                                      }}
                                    >
                                      <span
                                        style={{
                                          width: "8px",
                                          height: "8px",
                                          borderRadius: "50%",
                                          backgroundColor: "#ffffff",
                                          display: "inline-block",
                                          boxShadow: "0 0 0 2px #dc2626",
                                        }}
                                      />
                                      Blocked
                                    </div>
                                  ) : (
                                    <span style={{ fontSize: isIntentGuard ? "16px" : "20px", opacity: isIntentGuard ? 0.7 : 1 }}>
                                      {icon}
                                    </span>
                                  )}
                                  {!isBlocked && (
                                    <h4
                                      style={{
                                        margin: 0,
                                        fontSize: "14px",
                                        fontWeight: 600,
                                        color: isIntentGuard ? "#64748b" : borderColor,
                                      }}
                                    >
                                      {policyName}
                                    </h4>
                                  )}
                                  {isBlocked && (
                                    <h4
                                      style={{
                                        margin: 0,
                                        fontSize: "14px",
                                        fontWeight: 600,
                                        color: "#dc2626",
                                      }}
                                    >
                                      {policyName}
                                    </h4>
                                  )}
                                  <span
                                    style={{
                                      fontSize: "10px",
                                      color: isIntentGuard ? "#94a3b8" : isBlocked ? "#991b1b" : "#64748b",
                                      backgroundColor: isIntentGuard ? "#f1f5f9" : isBlocked ? "#fee2e2" : "#e5e7eb",
                                      padding: "2px 8px",
                                      borderRadius: "12px",
                                      textTransform: "capitalize",
                                      fontWeight: isIntentGuard ? 400 : 500,
                                    }}
                                  >
                                    {policyType.replace("_", " ")}
                                  </span>
                                </div>
                                <div
                                  style={{
                                    fontSize: "12px",
                                    lineHeight: "1.6",
                                    color: isIntentGuard ? "#64748b" : "#374151",
                                    padding: isIntentGuard ? "8px 0" : "12px",
                                    backgroundColor: isIntentGuard ? "transparent" : "#ffffff",
                                    borderRadius: isIntentGuard ? "0" : "6px",
                                    border: isIntentGuard ? "none" : "1px solid #e5e7eb",
                                    fontFamily: "system-ui, -apple-system, sans-serif",
                                  }}
                                >
                                  {!isIntentGuard && (
                                    <div
                                      style={{
                                        fontWeight: 500,
                                        color: "#6b7280",
                                        marginBottom: "6px",
                                        fontSize: "12px",
                                        textTransform: "uppercase",
                                        letterSpacing: "0.5px",
                                      }}
                                    >
                                      Policy Reasoning
                                    </div>
                                  )}
                                  <div style={{ color: isIntentGuard ? "#64748b" : "#1f2937", fontStyle: isIntentGuard ? "normal" : "normal" }}>
                                    {policyReasoning}
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                }
            }
            break;
          case "SuggestHumanActions":
            if (parsedContent && parsedContent.action_id) {
              console.log("[SuggestHumanActions] Rendering FollowupAction with:", parsedContent);
              mainElement = (
                <FollowupAction
                  followupAction={parsedContent}
                  callback={async (d: any) => {
                    console.log("📤 Sending approval response:", d);
                    console.log("📤 Using threadId:", threadId);
                    // Mark this step as completed before proceeding
                    markStepCompleted(step.id);
                    await fetchStreamingData(chatInstance, "", d, threadId, useDraftAgent);
                  }}
                />
              );
            } else {
              console.error("[SuggestHumanActions] Invalid parsedContent:", parsedContent);
              mainElement = (
                <div className="text-red-500 p-4 border border-red-300 rounded">
                  Error: Invalid action data received
                </div>
              );
            }
            break;
          default:
            const isJSONLike =
              parsedContent !== null &&
              (typeof parsedContent === "object" || Array.isArray(parsedContent)) &&
              !(parsedContent instanceof Date) &&
              !(parsedContent instanceof RegExp);
            if (isJSONLike) {
              parsedContent = JSON.stringify(parsedContent, null, 2);
              parsedContent = `\`\`\`json\n${parsedContent}\n\`\`\``;
            }
            if (!parsedContent) {
              parsedContent = "";
            }
            mainElement = <SingleExpandableContent title={step.title} content={parsedContent} />;
        }

        // Add main element to outputElements if it exists
        if (mainElement) {
          outputElements.push(mainElement);
        }

        return <div>{outputElements}</div>;
      } catch (error) {
        console.log(`Failed to parse JSON for step ${step.title}:`, error);
        return null;
      }
    },
    [chatInstance, markStepCompleted, currentSteps]
  );

  // Memoized button click handler
  const handleToggleDetails = useCallback(
    (stepId: string) => {
      console.log("Button clicked for step:", stepId, "Current state:", showDetails[stepId]);
      setShowDetails((prev) => ({ ...prev, [stepId]: !prev[stepId] }));
    },
    [showDetails]
  );

  // Handle reasoning collapse toggle
  const handleToggleReasoning = useCallback(() => {
    setIsReasoningCollapsed((prev) => !prev);
  }, []);

  const mapStepTitle = (stepTitle: string, parsedContent?: any) => {
    // Handle CodeAgent dynamically based on execution output
    if (stepTitle === "CodeAgent" && parsedContent) {
      const hasExecutionOutput = parsedContent.execution_output && parsedContent.execution_output.trim().length > 0;
      return hasExecutionOutput ? "Executed Code" : "Generated Code";
    }

    const titleMap = {
      TaskDecompositionAgent: "Decomposed task into steps",
      TaskAnalyzerAgent: "Analyzed available applications",
      PlanControllerAgent: "Controlled task execution",
      SuggestHumanActions: (
        <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div className="w-4 h-4 border-2 border-blue-200 border-t-blue-500 rounded-full animate-spin"></div>
          <span>Waiting for your input</span>
        </span>
      ),
      APIPlannerAgent: "Planned API actions",
      APICodePlannerAgent: "Planned steps for coding agent",
      CodeAgent_Reasoning: "Reasoning about approach",
      ShortlisterAgent: "Shortlisted relevant APIs",
      QaAgent: "Answered question",
      FinalAnswerAgent: "Completed final answer",
      Answer: "Answer",
    };

    return (titleMap as any)[stepTitle] || stepTitle;
  };

  console.log("CardManager render - currentSteps:", currentSteps.length, "isProcessingComplete:", isProcessingComplete);

  // Check if there's an error step
  const hasErrorStep = currentSteps.some((step) => step.title === "Error");

  // Separate final answer steps and active user action steps from reasoning steps
  // Only show Answer events as final answers (not FinalAnswerAgent)
  const finalAnswerSteps = currentSteps.filter((step) => {
    return step.title === "Answer" && shouldRenderStep(step);
  });

  // Show SuggestHumanActions as active if it's not marked as completed
  const userActionSteps = currentSteps.filter((step) => {
    const isUserAction = step.title === "SuggestHumanActions" && !step.completed;
    return isUserAction && shouldRenderStep(step);
  });

  // Include completed SuggestHumanActions in reasoning steps, excluding empty ones
  // FinalAnswerAgent goes in reasoning steps (collapsed), only Answer is shown as final answer
  const reasoningSteps = currentSteps.filter((step) => {
    const isNotFinalOrUserAction =
      step.title !== "Answer" &&
      !(step.title === "SuggestHumanActions" && !step.completed);

    // Also exclude steps that shouldn't be rendered (empty CodeAgent events, etc.)
    return isNotFinalOrUserAction && shouldRenderStep(step);
  });

  // Get current step to display (before final answer or user action)
  const currentStep = currentSteps[currentStepIndex];
  const isShowingCurrentStep =
    !isStopped && viewMode === "inplace" && !hasFinalAnswer && userActionSteps.length === 0 && currentStep;
  const isLoading =
    !isStopped &&
    currentSteps.length > 0 &&
    !isProcessingComplete &&
    !hasFinalAnswer &&
    userActionSteps.length === 0 &&
    !hasErrorStep;

  // Helper function to render a single step card
  const renderStepCard = (step: Step, isCurrentStep: boolean = false) => {
    // Parse content for description - match the logic in renderStepContent
    let parsedContent;
    try {
      if (typeof step.content === "string") {
        try {
          parsedContent = JSON.parse(step.content);
          const keys = Object.keys(parsedContent);
          
          // Check if we have variables in the parsed content (matching renderStepContent logic)
          if (parsedContent.data !== undefined && parsedContent.variables) {
            // Parse data if it's a JSON string
            let dataValue = parsedContent.data;
            let extractedPolicies: any[] = parsedContent.active_policies || [];
            
            if (typeof dataValue === "string") {
              try {
                const parsedData = JSON.parse(dataValue);
                // Check if the parsed data is a policy object
                if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                  extractedPolicies = [parsedData];
                  // Use the content as final_answer if it's a policy
                  dataValue = parsedData.content || parsedData.response_content || dataValue;
                }
              } catch (e) {
                // Not JSON, keep as string
              }
            } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
              // Data is already a policy object
              extractedPolicies = [dataValue];
              dataValue = dataValue.content || dataValue.response_content || "";
            }

            // For Answer step with variables: treat data as final_answer
            if (step.title === "Answer" || step.title === "FinalAnswerAgent") {
              parsedContent = {
                final_answer: dataValue,
                variables: parsedContent.variables,
                active_policies: extractedPolicies,
              };
            } else if (typeof parsedContent.data === "object" && !Array.isArray(parsedContent.data)) {
              // Keep both data and variables if data is an object
              parsedContent = {
                ...parsedContent.data,
                variables: parsedContent.variables,
                active_policies: extractedPolicies,
              };
            } else {
              // If data is not an object, keep as is with variables
              parsedContent = {
                data: dataValue,
                variables: parsedContent.variables,
                active_policies: extractedPolicies,
              };
            }
          } else if (keys.length === 1 && keys[0] === "data") {
            // Only data, no variables - check if data is a policy JSON string
            let dataValue = parsedContent.data;
            let extractedPolicies: any[] = [];
            
            if (typeof dataValue === "string") {
              try {
                const parsedData = JSON.parse(dataValue);
                if (parsedData && typeof parsedData === "object" && parsedData.type === "policy") {
                  extractedPolicies = [parsedData];
                  parsedContent = {
                    final_answer: parsedData.content || parsedData.response_content || "",
                    active_policies: extractedPolicies,
                  };
                } else {
                  parsedContent = dataValue;
                }
              } catch (e) {
                parsedContent = dataValue;
              }
            } else if (dataValue && typeof dataValue === "object" && dataValue.type === "policy") {
              extractedPolicies = [dataValue];
              parsedContent = {
                final_answer: dataValue.content || dataValue.response_content || "",
                active_policies: extractedPolicies,
              };
            } else {
              parsedContent = dataValue;
            }
          }
        } catch (e) {
          parsedContent = step.content;
        }
      } else {
        parsedContent = step.content;
      }
    } catch (error) {
      parsedContent = step.content;
    }

    if (step.title === "simple_text") {
      return (
        <div key={step.id} style={{ marginBottom: "10px" }}>
          {step.content}
        </div>
      );
    }

    // Get description for rendering
    const description = getCaseDescription(step.id, step.title, parsedContent);

    // Skip rendering if description is null (e.g., empty CodeAgent events)
    if (description === null) {
      return null;
    }

    // Only render component content if details are shown
    const componentContent = showDetails[step.id] ? renderStepContent(step, currentSteps) : null;

    return (
      <div
        key={step.id}
        ref={(el) => {
          stepRefs.current[step.id] = el;
        }}
        className={`component-container ${step.isNew ? "new-component" : ""} ${isCurrentStep ? "current-step" : ""}`}
        style={{
          marginBottom: "16px",
          padding: "12px",
          paddingTop: "28px",
          backgroundColor: "#ffffff",
          borderRadius: "6px",
          border: "1px solid #e2e8f0",
          boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
          position: "relative",
        }}
      >
        {/* Component Title */}
        <div
          style={{
            marginBottom: "12px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <h3
            style={{
              fontSize: "14px",
              fontWeight: "500",
              color: "#475569",
              margin: "0",
              display: "flex",
              alignItems: "center",
              gap: "6px",
            }}
          >
            {mapStepTitle(step.title, parsedContent)}
          </h3>
        </div>

        {/* Natural Language Description */}
        <div
          style={{
            marginBottom: "12px",
          }}
        >
          <div
            style={{
              margin: "0",
              fontSize: "13px",
              color: "#64748b",
              lineHeight: "1.4",
            }}
          >
            {/* Reuse the description computed earlier */}
            {React.isValidElement(description) ? (
              description
            ) : (
              <span dangerouslySetInnerHTML={{ __html: description as string }} />
            )}
          </div>
        </div>

        {/* Component Content - Only show if showDetails is true */}
        {componentContent && <div>{componentContent}</div>}

        {/* Top-right details toggle */}
        <button
          onClick={() => handleToggleDetails(step.id)}
          style={{
            position: "absolute",
            right: "8px",
            top: "8px",
            display: "flex",
            alignItems: "center",
            gap: "6px",
            background: "transparent",
            border: "1px solid #e5e7eb",
            borderRadius: "12px",
            padding: "4px 8px",
            fontSize: "11px",
            color: showDetails[step.id] ? "#3b82f6" : "#64748b",
            cursor: "pointer",
          }}
          onMouseOver={(e) => {
            (e.currentTarget as HTMLButtonElement).style.backgroundColor = "#f8fafc";
          }}
          onMouseOut={(e) => {
            (e.currentTarget as HTMLButtonElement).style.backgroundColor = "transparent";
          }}
        >
          <span
            style={{
              display: "inline-block",
              transform: showDetails[step.id] ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 0.2s ease",
              fontSize: "12px",
            }}
          >
            ▼
          </span>
          <span>details</span>
        </button>
      </div>
    );
  };

  return (
    <>
      <style>{`
        .components-container details summary::-webkit-details-marker {
          display: none;
        }
        .components-container details summary::marker {
          display: none;
        }
      `}</style>
      <div className="components-container" ref={cardRef}>
      {/* View mode toggle */}
      {!isStopped && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "6px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ fontSize: "11px", color: "#64748b" }}>View:</span>
            <button
              onClick={() => setViewMode("inplace")}
              style={{
                padding: "2px 6px",
                backgroundColor: viewMode === "inplace" ? "#2563eb" : "transparent",
                color: viewMode === "inplace" ? "#ffffff" : "#64748b",
                border: "1px solid #e5e7eb",
                borderRadius: "3px",
                fontSize: "10px",
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              In-place
            </button>
            <button
              onClick={() => setViewMode("append")}
              style={{
                padding: "2px 6px",
                backgroundColor: viewMode === "append" ? "#2563eb" : "transparent",
                color: viewMode === "append" ? "#ffffff" : "#64748b",
                border: "1px solid #e5e7eb",
                borderRadius: "3px",
                fontSize: "10px",
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              Append
            </button>
          </div>
        </div>
      )}

      {/* Append mode */}
      {!isStopped &&
        viewMode === "append" &&
        currentSteps.length > 0 &&
        (hasFinalAnswer ? (
          <div>
            {/* Collapsed Reasoning wrapper with prior steps */}
            {reasoningSteps.length > 0 && (
              <div
                style={{
                  marginBottom: "16px",
                  padding: "12px",
                  backgroundColor: "#f8fafc",
                  borderRadius: "8px",
                  border: "1px solid #e2e8f0",
                  boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    cursor: "pointer",
                    userSelect: "none",
                  }}
                  onClick={handleToggleReasoning}
                >
                  <h3
                    style={{
                      fontSize: "16px",
                      fontWeight: "600",
                      color: "#374151",
                      margin: "0",
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                    }}
                  >
                    <span
                      style={{
                        transform: isReasoningCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
                        transition: "transform 0.3s ease",
                        fontSize: "14px",
                      }}
                    >
                      ▼
                    </span>
                    Reasoning Process
                    <span
                      style={{
                        fontSize: "12px",
                        fontWeight: "400",
                        color: "#6b7280",
                        backgroundColor: "#e5e7eb",
                        padding: "2px 8px",
                        borderRadius: "12px",
                      }}
                    >
                      {reasoningSteps.length} steps
                    </span>
                  </h3>
                  <div
                    style={{
                      fontSize: "12px",
                      color: "#6b7280",
                      fontStyle: "italic",
                    }}
                  >
                    {isReasoningCollapsed ? "Click to expand" : "Click to collapse"}
                  </div>
                </div>

                <div
                  style={{
                    maxHeight: isReasoningCollapsed ? "0" : "10000px",
                    overflow: "hidden",
                    transition: "max-height 0.5s ease-in-out, opacity 0.3s ease-in-out",
                    opacity: isReasoningCollapsed ? 0 : 1,
                  }}
                >
                  <div style={{ marginTop: "12px" }}>{reasoningSteps.map((step) => renderStepCard(step, false))}</div>
                </div>
              </div>
            )}

            {/* Final Answer card(s) */}
            {finalAnswerSteps.map((step) => renderStepCard(step, false))}
          </div>
        ) : (
          <div>
            {currentSteps.map((step) => (
              <div key={step.id}>{renderStepCard(step, false)}</div>
            ))}
          </div>
        ))}
      {/* When stopped, show a collapsed Reasoning section containing all steps */}
      {isStopped && currentSteps.length > 0 && (
        <div
          style={{
            marginBottom: "16px",
            padding: "12px",
            backgroundColor: "#f8fafc",
            borderRadius: "8px",
            border: "1px solid #e2e8f0",
            boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              cursor: "pointer",
              userSelect: "none",
            }}
            onClick={handleToggleReasoning}
          >
            <h3
              style={{
                fontSize: "16px",
                fontWeight: "600",
                color: "#374151",
                margin: "0",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <span
                style={{
                  transform: isReasoningCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
                  transition: "transform 0.3s ease",
                  fontSize: "14px",
                }}
              >
                ▼
              </span>
              Reasoning Process
              <span
                style={{
                  fontSize: "12px",
                  fontWeight: "400",
                  color: "#6b7280",
                  backgroundColor: "#e5e7eb",
                  padding: "2px 8px",
                  borderRadius: "12px",
                }}
              >
                {currentSteps.length} steps
              </span>
            </h3>
            <div
              style={{
                fontSize: "12px",
                color: "#6b7280",
                fontStyle: "italic",
              }}
            >
              {isReasoningCollapsed ? "Click to expand" : "Click to collapse"}
            </div>
          </div>

          <div
            style={{
              maxHeight: isReasoningCollapsed ? "0" : "10000px",
              overflow: "hidden",
              transition: "max-height 0.5s ease-in-out, opacity 0.3s ease-in-out",
              opacity: isReasoningCollapsed ? 0 : 1,
            }}
          >
            <div style={{ marginTop: "12px" }}>{currentSteps.map((step) => renderStepCard(step, false))}</div>
          </div>
        </div>
      )}

      {/* Final outside card indicating interruption */}
      {isStopped && (
        <div style={{ marginTop: "8px" }}>
          <div
            style={{
              marginBottom: "16px",
              padding: "12px",
              backgroundColor: "#ffffff",
              borderRadius: "6px",
              border: "1px solid #e2e8f0",
              boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
            }}
          >
            <div
              style={{
                marginBottom: "12px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <h3
                style={{
                  fontSize: "14px",
                  fontWeight: "500",
                  color: "#475569",
                  margin: "0",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                Task Interrupted
              </h3>
            </div>
            <div>
              <p
                style={{
                  margin: "0",
                  fontSize: "13px",
                  color: "#64748b",
                  lineHeight: "1.4",
                }}
              >
                The task was stopped by the user.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Reasoning Section - Collapsible when final answer or user action is present */}
      {!isStopped &&
        viewMode === "inplace" &&
        (hasFinalAnswer || userActionSteps.length > 0) &&
        reasoningSteps.length > 0 && (
          <div
            style={{
              marginBottom: "16px",
              padding: "12px",
              backgroundColor: "#f8fafc",
              borderRadius: "8px",
              border: "1px solid #e2e8f0",
              boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                cursor: "pointer",
                userSelect: "none",
              }}
              onClick={handleToggleReasoning}
            >
              <h3
                style={{
                  fontSize: "16px",
                  fontWeight: "600",
                  color: "#374151",
                  margin: "0",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <span
                  style={{
                    transform: isReasoningCollapsed ? "rotate(-90deg)" : "rotate(0deg)",
                    transition: "transform 0.3s ease",
                    fontSize: "14px",
                  }}
                >
                  ▼
                </span>
                Reasoning Process
                <span
                  style={{
                    fontSize: "12px",
                    fontWeight: "400",
                    color: "#6b7280",
                    backgroundColor: "#e5e7eb",
                    padding: "2px 8px",
                    borderRadius: "12px",
                  }}
                >
                  {reasoningSteps.length} steps
                </span>
              </h3>
              <div
                style={{
                  fontSize: "12px",
                  color: "#6b7280",
                  fontStyle: "italic",
                }}
              >
                {isReasoningCollapsed ? "Click to expand" : "Click to collapse"}
              </div>
            </div>

            <div
              style={{
                maxHeight: isReasoningCollapsed ? "0" : "10000px",
                overflow: "hidden",
                transition: "max-height 0.5s ease-in-out, opacity 0.3s ease-in-out",
                opacity: isReasoningCollapsed ? 0 : 1,
              }}
            >
              <div style={{ marginTop: "12px" }}>{reasoningSteps.map((step) => renderStepCard(step, false))}</div>
            </div>
          </div>
        )}

      {/* Current Step Display - Shows one step at a time with smooth transitions */}
      {!isStopped && viewMode === "inplace" && isShowingCurrentStep && (
        <div
          className={`current-step-container ${isLoading ? "loading-border" : ""}`}
          style={{
            position: "relative",
            minHeight: "200px",
          }}
        >
          {renderStepCard(currentStep, true)}
        </div>
      )}

      {/* Final Answer Steps - Always visible (in-place mode) */}
      {!isStopped && viewMode === "inplace" && finalAnswerSteps.map((step) => renderStepCard(step, false))}

      {/* User Action Steps - Always visible when present (in-place mode) */}
      {!isStopped && viewMode === "inplace" && userActionSteps.map((step) => renderStepCard(step, false))}

      {/* Loading indicator - Only show when processing and no current step */}
      {!isStopped &&
        viewMode === "inplace" &&
        currentSteps.length > 0 &&
        !isProcessingComplete &&
        !hasFinalAnswer &&
        userActionSteps.length === 0 &&
        !hasErrorStep &&
        !isShowingCurrentStep && (
          <div style={{ marginTop: "8px", marginBottom: "2px" }}>
            <div
              style={{
                fontSize: "10px",
                color: "#94a3b8",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: "4px",
                userSelect: "none",
              }}
            >
              <span>CUGA is thinking..</span>
            </div>
            <div
              style={{
                height: "4px",
                position: "relative",
                overflow: "hidden",
                background: "#eef2ff",
                borderRadius: "9999px",
                boxShadow: "inset 0 0 0 1px #e5e7eb",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: "28%",
                  background: "linear-gradient(90deg, #a78bfa 0%, #6366f1 100%)",
                  borderRadius: "9999px",
                  animation: "cugaShimmer 1.7s infinite",
                  boxShadow: "0 0 6px rgba(99,102,241,0.25)",
                }}
              />
            </div>
            <style>
              {`
              @keyframes cugaShimmer {
                0% { transform: translateX(-100%); }
                100% { transform: translateX(300%); }
              }
            `}
            </style>
          </div>
        )}
    </div>
    </>
  );
};

export default CardManager;
