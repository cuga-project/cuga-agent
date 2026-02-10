import React, { useState } from "react";
import VariablePopup from "./VariablePopup";
import "./VariablesSidebar.css";

interface VariablesHistoryItem {
  id: string;
  title: string;
  timestamp: number;
  variables: Record<string, any>;
  memories: Record<string, any[]>;
}

interface VariablesSidebarProps {
  variables: Record<string, any>;
  memories?: Record<string, any[]>;
  history?: VariablesHistoryItem[];
  selectedAnswerId?: string | null;
  onSelectAnswer?: (answerId: string) => void;
  mode?: "variables" | "memories";
}

interface MemoryFact {
  id?: string;
  category: string;
  content: string;
  key?: string | null;
  value?: string | null;
}

const VariablesSidebar: React.FC<VariablesSidebarProps> = ({ 
  variables, 
  memories = {},
  history = [],
  selectedAnswerId,
  onSelectAnswer,
  mode = "variables",
}) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [selectedVariable, setSelectedVariable] = useState<any>(null);
  const variableKeys = Object.keys(variables);

  const normalizeMemories = (memoriesValue: any): Record<string, any[]> => {
    if (!memoriesValue || typeof memoriesValue !== "object" || Array.isArray(memoriesValue)) {
      return {};
    }
    return memoriesValue as Record<string, any[]>;
  };

  const extractMemoryFacts = (memoriesValue: any): MemoryFact[] => {
    if (!memoriesValue || typeof memoriesValue !== "object" || Array.isArray(memoriesValue)) {
      return [];
    }

    const facts: MemoryFact[] = [];
    Object.entries(memoriesValue).forEach(([category, rawFacts]) => {
      if (!Array.isArray(rawFacts)) {
        return;
      }

      rawFacts.forEach((fact: any) => {
        const content = typeof fact?.content === "string" ? fact.content : "";
        if (!content) {
          return;
        }

        facts.push({
          id: typeof fact?.id === "string" ? fact.id : undefined,
          category,
          content,
          key: typeof fact?.key === "string" ? fact.key : null,
          value: typeof fact?.value === "string" ? fact.value : null,
        });
      });
    });

    return facts;
  };

  const currentMemories = normalizeMemories(memories);

  const memoryFacts = extractMemoryFacts(currentMemories);
  const countForActiveView = mode === "variables" ? variableKeys.length : memoryFacts.length;

  console.log(
    "VariablesSidebar render - variableKeys:",
    variableKeys.length,
    "memoryFacts:",
    memoryFacts.length,
    "history:",
    history.length,
    "mode:",
    mode,
    "selectedAnswerId:",
    selectedAnswerId
  );

  if (variableKeys.length === 0 && memoryFacts.length === 0 && history.length === 0) {
    console.log('VariablesSidebar: No variables or history, not rendering');
    return null;
  }

  const formatTimestamp = (timestamp: number) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  const getVariablesCountForHistoryItem = (item: VariablesHistoryItem): number => {
    return Object.keys(item.variables || {}).length;
  };

  const getMemoriesCountForHistoryItem = (item: VariablesHistoryItem): number => {
    const itemMemories = normalizeMemories(item.memories);
    const itemMemoryFacts = extractMemoryFacts(itemMemories);
    return itemMemoryFacts.length;
  };

  return (
    <>
      <div className={`variables-sidebar ${isExpanded ? 'expanded' : 'collapsed'}`}>
        <div className="variables-sidebar-header">
          <button
            className="variables-sidebar-toggle"
            onClick={() => setIsExpanded(!isExpanded)}
            title={isExpanded ? "Collapse variables panel" : "Expand variables panel"}
          >
            {isExpanded ? (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="15 18 9 12 15 6"></polyline>
              </svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            )}
          </button>
          {isExpanded && (
            <>
              <div className="variables-sidebar-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {mode === "variables" ? (
                    <path d="M4 7h16M4 12h16M4 17h16"></path>
                  ) : (
                    <path d="M12 3v18M3 12h18"></path>
                  )}
                </svg>
                <span>{mode === "variables" ? "Variables" : "Memories"}</span>
                <span className="variables-count">{countForActiveView}</span>
              </div>
              {history.length > 0 && (
                <select
                  className="variables-history-select"
                  value={selectedAnswerId || ''}
                  onChange={(e) => onSelectAnswer && onSelectAnswer(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  title="Select which conversation turn to view"
                >
                  {history.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.title} - {mode === "variables" ? getVariablesCountForHistoryItem(item) : getMemoriesCountForHistoryItem(item)} {mode === "variables" ? "variable" : "memory"}{(mode === "variables" ? getVariablesCountForHistoryItem(item) : getMemoriesCountForHistoryItem(item)) !== 1 ? "s" : ""} ({formatTimestamp(item.timestamp)})
                    </option>
                  ))}
                </select>
              )}
            </>
          )}
        </div>

        {isExpanded && (
          <div className="variables-sidebar-content">
            {history.length > 0 && (
              <div className="variables-history-info">
                Viewing: {history.find(h => h.id === selectedAnswerId)?.title || 'Latest turn'}
                <span className="history-count">{history.length} turns total</span>
              </div>
            )}
            <div className="variables-list">
              {mode === "variables" && variableKeys.length === 0 && history.length > 0 ? (
                <div className="no-variables-message">
                  <p>No variables in current turn.</p>
                  <p>Select a previous turn from the dropdown above to view its variables.</p>
                </div>
              ) : mode === "memories" && memoryFacts.length === 0 ? (
                <div className="no-variables-message">
                  <p>No memories found in current turn.</p>
                  <p>Ask a few questions so CUGA can store memory facts and show them here.</p>
                </div>
              ) : mode === "variables" ? (
                variableKeys.map((varName) => {
                  const variable = variables[varName];
                  return (
                    <div
                      key={varName}
                      className="variable-item"
                      onClick={() => setSelectedVariable({ name: varName, ...variable })}
                    >
                      <div className="variable-item-header">
                        <code className="variable-name">{varName}</code>
                        <span className="variable-type">{variable.type}</span>
                      </div>
                      {variable.description && (
                        <div className="variable-description">{variable.description}</div>
                      )}
                      {variable.count_items !== undefined && variable.count_items > 1 && (
                        <div className="variable-meta">
                          <span className="variable-count">{variable.count_items} items</span>
                        </div>
                      )}
                      <div className="variable-preview">
                        {variable.value_preview
                          ? variable.value_preview.substring(0, 80) + (variable.value_preview.length > 80 ? "..." : "")
                          : ""}
                      </div>
                    </div>
                  );
                })
              ) : (
                memoryFacts.map((memory, index) => {
                  const popupValue = JSON.stringify(memory, null, 2);
                  const memoryName = memory.key || memory.id || `memory_${index + 1}`;
                  return (
                    <div
                      key={`${memory.category}-${memory.id || index}`}
                      className="variable-item"
                      onClick={() =>
                        setSelectedVariable({
                          name: memoryName,
                          type: "memory",
                          description: `${memory.category.replace(/_/g, " ")} memory fact`,
                          value_preview: popupValue,
                          count_items: 1,
                        })
                      }
                    >
                      <div className="variable-item-header">
                        <code className="variable-name">{memoryName}</code>
                        <span className="variable-type memory-type">{memory.category}</span>
                      </div>
                      <div className="variable-description">{memory.content}</div>
                      <div className="variable-preview">
                        {popupValue.substring(0, 80) + (popupValue.length > 80 ? "..." : "")}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      {/* Floating toggle button when sidebar is collapsed */}
      {!isExpanded && (
        <button
          className="variables-sidebar-floating-toggle"
          onClick={() => setIsExpanded(true)}
          title="Show sidebar panel"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="9 18 15 12 9 6"></polyline>
          </svg>
          <span className="variables-floating-count">{countForActiveView}</span>
        </button>
      )}

      {selectedVariable && (
        <VariablePopup
          variable={selectedVariable}
          onClose={() => setSelectedVariable(null)}
        />
      )}
    </>
  );
};

export default VariablesSidebar;
