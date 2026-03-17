import React, { useState, useEffect } from "react";
import { Check, X, Send } from "lucide-react";

interface Option {
  value: string;
  label: string;
  description?: string;
}

interface FollowupActionProps {
  followupAction: {
    action_id: string;
    action_name: string;
    description?: string;
    type: string;
    button_text?: string;
    placeholder?: string;
    options?: Option[];
    max_selections?: number;
    min_selections?: number;
    required?: boolean;
    validation_pattern?: string;
    max_length?: number;
    min_length?: number;
    color?: string;
  };
  callback: (response: any) => void;
}

export const FollowupAction = ({ followupAction, callback }: FollowupActionProps) => {
  const [response, setResponse] = useState("");
  const [selectedValues, setSelectedValues] = useState<string[]>([]);
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [startTime] = useState(Date.now());
  const [isWaiting, setIsWaiting] = useState(true);

  // Safety check for followupAction
  if (!followupAction) {
    console.error("FollowupAction received null or undefined followupAction");
    return <div className="text-red-500 p-4">Error: Invalid action data</div>;
  }

  const {
    action_id,
    action_name,
    description,
    type,
    button_text,
    placeholder,
    options,
    max_selections,
    min_selections = 1,
    required = true,
    validation_pattern,
    max_length,
    min_length,
    color = "primary",
  } = followupAction;

  useEffect(() => {
    const timer = setTimeout(() => {
      setIsWaiting(false);
    }, 300);
    return () => clearTimeout(timer);
  }, []);

  const colorThemes = {
    primary: {
      button: "bg-blue-500 hover:bg-blue-600 text-white",
      accent: "text-blue-600 border-blue-200 bg-blue-50",
    },
    success: {
      button: "bg-green-500 hover:bg-green-600 text-white",
      accent: "text-green-600 border-green-200 bg-green-50",
    },
    warning: {
      button: "bg-yellow-500 hover:bg-yellow-600 text-white",
      accent: "text-yellow-600 border-yellow-200 bg-yellow-50",
    },
    danger: {
      button: "bg-red-500 hover:bg-red-600 text-white",
      accent: "text-red-600 border-red-200 bg-red-50",
    },
    secondary: {
      button: "bg-gray-500 hover:bg-gray-600 text-white",
      accent: "text-gray-600 border-gray-200 bg-gray-50",
    },
  };

  const theme = colorThemes[color as keyof typeof colorThemes] || colorThemes.primary;

  const createResponse = (responseData: any) => {
    const baseResponse = {
      action_id,
      response_type: type,
      timestamp: new Date().toISOString(),
      response_time_ms: Date.now() - startTime,
      client_info: {
        user_agent: navigator.userAgent,
        language: navigator.language,
        platform: navigator.platform,
      },
    };

    return { ...baseResponse, ...responseData };
  };

  const handleSubmit = (responseData: any) => {
    if (isSubmitted) return;

    setIsSubmitted(true);
    const fullResponse = createResponse(responseData);
    callback(fullResponse);
  };

  const handleTextSubmit = () => {
    if (!response.trim() && required) return;

    // Validation
    if (validation_pattern && !new RegExp(validation_pattern).test(response)) {
      // Replaced alert with a simple console log for demonstration.
      // In a real app, you'd use a custom modal or inline error message.
      console.error("Please enter a valid response");
      return;
    }

    if (min_length && response.length < min_length) {
      console.error(`Response must be at least ${min_length} characters`);
      return;
    }

    if (max_length && response.length > max_length) {
      console.error(`Response must be no more than ${max_length} characters`);
      return;
    }

    handleSubmit({ text_response: response });
  };

  const handleButtonClick = () => {
    handleSubmit({ button_clicked: true });
  };

  const handleConfirmation = (confirmed: boolean) => {
    handleSubmit({ confirmed });
  };

  const handleSelectChange = (value: string) => {
    let newSelection: string[];

    if (type === "multi_select") {
      if (selectedValues.includes(value)) {
        newSelection = selectedValues.filter((v) => v !== value);
      } else {
        if (max_selections && selectedValues.length >= max_selections) {
          return;
        }
        newSelection = [...selectedValues, value];
      }
    } else {
      newSelection = [value];
    }

    setSelectedValues(newSelection);

    if (type === "select") {
      const selectedOptions = (options || []).filter((opt) => newSelection.includes(opt.value));
      handleSubmit({
        selected_values: newSelection,
        selected_options: selectedOptions,
      });
    }
  };

  const handleMultiSelectSubmit = () => {
    if (selectedValues.length < min_selections) {
      console.error(`Please select at least ${min_selections} option(s)`);
      return;
    }

    const selectedOptions = (options || []).filter((opt) => selectedValues.includes(opt.value));
    handleSubmit({
      selected_values: selectedValues,
      selected_options: selectedOptions,
    });
  };

  const renderWaitingState = () => (
    <div className="flex items-center justify-center py-4">
      <span className="text-sm text-gray-500">Loading...</span>
    </div>
  );

  const renderActionContent = () => {
    if (isWaiting) {
      return renderWaitingState();
    }

    if (isSubmitted) {
      return (
        <div className="flex items-center justify-center py-4 text-green-600">
          <div className="flex items-center space-x-2 bg-green-50 px-4 py-2 rounded border border-green-200">
            <Check className="w-5 h-5" />
            <span className="text-sm font-medium">Response submitted successfully!</span>
          </div>
        </div>
      );
    }

    switch (type) {
      case "button":
        return (
          <button
            onClick={handleButtonClick}
            disabled={isSubmitted}
            className={`w-full px-4 py-3 rounded font-medium ${theme.button} flex items-center justify-center gap-2 ${
              isSubmitted ? "opacity-50 cursor-not-allowed" : ""
            }`}
          >
            <span>{button_text || action_name}</span>
          </button>
        );

      case "text_input":
      case "natural_language":
        return (
          <div className="space-y-3">
            <textarea
              value={response}
              onChange={(e) => setResponse(e.target.value)}
              placeholder={placeholder || "Enter your response..."}
              disabled={isSubmitted}
              className={`w-full px-4 py-3 border border-gray-200 rounded resize-none focus:outline-none focus:border-blue-500 text-sm ${response.trim() ? theme.accent : ""} ${
                isSubmitted ? "opacity-50 cursor-not-allowed bg-gray-50" : ""
              }`}
              rows={type === "natural_language" ? 3 : 1}
              maxLength={max_length}
            />
            {max_length && (
              <div className="text-xs text-gray-500 text-right">
                <span className={response.length > max_length * 0.8 ? "text-orange-500" : ""}>{response.length}</span>/
                {max_length}
              </div>
            )}
            <button
              onClick={handleTextSubmit}
              disabled={isSubmitted || (!response.trim() && required)}
              className={`px-4 py-2 rounded text-sm font-medium ${
                isSubmitted || (!response.trim() && required)
                  ? "bg-gray-200 text-gray-400 cursor-not-allowed"
                  : theme.button
              } flex items-center gap-2`}
            >
              <Send className="w-4 h-4" />
              Submit
            </button>
          </div>
        );

      case "select":
        return (
          <div className="space-y-2">
            {(options || []).map((option) => (
              <button
                key={option.value}
                onClick={() => handleSelectChange(option.value)}
                disabled={isSubmitted}
                className={`w-full px-4 py-3 text-left rounded border text-sm ${
                  selectedValues.includes(option.value)
                    ? theme.button
                    : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                } ${isSubmitted ? "opacity-50 cursor-not-allowed" : ""}`}
              >
                <div className="font-medium">{option.label}</div>
                {option.description && <div className="text-xs opacity-75 mt-1">{option.description}</div>}
              </button>
            ))}
          </div>
        );

      case "multi_select":
        return (
          <div className="space-y-3">
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {(options || []).map((option) => (
                <label
                  key={option.value}
                  className={`flex items-start gap-3 p-3 rounded border cursor-pointer ${
                    selectedValues.includes(option.value)
                      ? theme.accent
                      : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedValues.includes(option.value)}
                    onChange={() => handleSelectChange(option.value)}
                    className="mt-1 w-4 h-4 text-blue-600 rounded focus:ring-blue-500"
                    disabled={
                      isSubmitted ||
                      (!selectedValues.includes(option.value) &&
                      !!max_selections &&
                      selectedValues.length >= max_selections)
                    }
                  />
                  <div className="flex-1">
                    <div className="text-sm font-medium">{option.label}</div>
                    {option.description && <div className="text-xs text-gray-600 mt-1">{option.description}</div>}
                  </div>
                </label>
              ))}
            </div>
            {max_selections && (
              <div className="text-xs text-gray-500">
                <span className={selectedValues.length === max_selections ? "text-orange-500 font-medium" : ""}>
                  {selectedValues.length}
                </span>
                /{max_selections} selected
              </div>
            )}
            <button
              onClick={handleMultiSelectSubmit}
              disabled={isSubmitted || selectedValues.length < min_selections}
              className={`px-4 py-2 rounded text-sm font-medium ${
                isSubmitted || selectedValues.length < min_selections
                  ? "bg-gray-200 text-gray-400 cursor-not-allowed"
                  : theme.button
              } flex items-center gap-2`}
            >
              <Check className="w-4 h-4" />
              Submit ({selectedValues.length})
            </button>
          </div>
        );

      case "confirmation":
        // Check if this is a tool approval or agent approval action
        const isToolApproval = action_id === "tool_approval";
        const isAgentApproval = action_id === "agent_approval";
        const toolData = followupAction.additional_data?.tool;
        const codePreview = toolData?.code_preview || [];
        const requiredTools = toolData?.required_tools || [];
        const agentNames = toolData?.agent_names || [];
        const tasks = toolData?.tasks || {};
        const taskDescriptions = toolData?.task_descriptions || "";
        
        return (
          <div className="space-y-3">
            {/* Show tool approval details if available */}
            {isToolApproval && (codePreview.length > 0 || requiredTools.length > 0) && (
              <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm">
                {requiredTools.length > 0 && (
                  <div className="mb-2">
                    <span className="font-semibold text-amber-900">Tools requiring approval:</span>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {requiredTools.map((tool: string, idx: number) => (
                        <span key={idx} className="inline-block bg-amber-100 text-amber-800 px-2 py-0.5 rounded text-xs font-mono">
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {codePreview.length > 0 && (
                  <div>
                    <span className="font-semibold text-amber-900">Code Preview:</span>
                    <pre className="mt-1 bg-gray-800 text-green-400 p-3 rounded text-xs overflow-x-auto font-mono border border-gray-700">
                      {codePreview.join('\n')}
                    </pre>
                  </div>
                )}
              </div>
            )}
            
            {/* Show agent approval details if available */}
            {isAgentApproval && agentNames.length > 0 && (
              <div className="bg-blue-50 border border-blue-200 rounded p-3 text-sm">
                <div className="mb-2">
                  <span className="font-semibold text-blue-900">Agents to be executed:</span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {agentNames.map((agent: string, idx: number) => (
                      <span key={idx} className="inline-block bg-blue-100 text-blue-800 px-2 py-0.5 rounded text-xs font-medium">
                        {agent}
                      </span>
                    ))}
                  </div>
                </div>
                {taskDescriptions && (
                  <div className="mt-2">
                    <span className="font-semibold text-blue-900">Tasks for each agent:</span>
                    <div className="mt-1 bg-white border border-blue-200 rounded p-2 text-xs">
                      <div className="whitespace-pre-wrap text-gray-700">{taskDescriptions}</div>
                    </div>
                  </div>
                )}
              </div>
            )}
            
            {/* Confirmation buttons */}
            <div className="flex gap-3">
              <button
                onClick={() => handleConfirmation(true)}
                disabled={isSubmitted}
                className={`flex-1 px-4 py-3 ${
                  isToolApproval 
                    ? "bg-amber-500 hover:bg-amber-600" 
                    : isAgentApproval
                    ? "bg-blue-500 hover:bg-blue-600"
                    : "bg-green-500 hover:bg-green-600"
                } text-white rounded font-medium flex items-center justify-center gap-2 ${
                  isSubmitted ? "opacity-50 cursor-not-allowed" : ""
                }`}
              >
                <Check className="w-4 h-4" />
                {isToolApproval ? "Approve & Execute" : isAgentApproval ? "Approve & Execute Agents" : "Confirm"}
              </button>
              <button
                onClick={() => handleConfirmation(false)}
                disabled={isSubmitted}
                className={`flex-1 px-4 py-3 bg-gray-500 hover:bg-gray-600 text-white rounded font-medium flex items-center justify-center gap-2 ${
                  isSubmitted ? "opacity-50 cursor-not-allowed" : ""
                }`}
              >
                <X className="w-4 h-4" />
                {isToolApproval || isAgentApproval ? "Deny" : "Cancel"}
              </button>
            </div>
          </div>
        );

      default:
        return <div className="text-gray-500 text-sm">Unsupported action type: {type}</div>;
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded p-4 mx-auto">
      {!isWaiting && (
        <div className="mb-4">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="font-medium text-gray-900 text-sm">
              {action_name}
            </h3>
            {required && <span className="text-red-500 text-xs">*</span>}
          </div>
          {description && (
            <p className="text-gray-600 text-xs">
              {description}
            </p>
          )}
        </div>
      )}

      {renderActionContent()}
    </div>
  );
};
