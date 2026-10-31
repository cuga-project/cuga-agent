import React, { useState } from "react";
import { IconButton, Select, SelectItem, Tag, ClickableTile } from "@carbon/react";
import { ChevronLeft, ChevronRight, DataTable } from "@carbon/icons-react";
import VariablePopup from "./VariablePopup";
import "./VariablesSidebar.css";

interface VariablesHistoryItem {
  id: string;
  title: string;
  timestamp: number;
  variables: Record<string, any>;
}

interface VariablesSidebarProps {
  variables: Record<string, any>;
  history?: VariablesHistoryItem[];
  selectedAnswerId?: string | null;
  onSelectAnswer?: (answerId: string) => void;
}

const VariablesSidebar: React.FC<VariablesSidebarProps> = ({ 
  variables, 
  history = [],
  selectedAnswerId,
  onSelectAnswer 
}) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [selectedVariable, setSelectedVariable] = useState<any>(null);
  const variableKeys = Object.keys(variables);

  console.log('VariablesSidebar render - variableKeys:', variableKeys.length, 'history:', history.length, 'selectedAnswerId:', selectedAnswerId);

  if (variableKeys.length === 0 && history.length === 0) {
    console.log('VariablesSidebar: No variables or history, not rendering');
    return null;
  }

  // Always show sidebar if there's history, even if no current variables
  const shouldShowSidebar = variableKeys.length > 0 || history.length > 0;

  const formatTimestamp = (timestamp: number) => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <>
      <div className={`variables-sidebar ${isExpanded ? 'expanded' : 'collapsed'}`}>
        <div className="variables-sidebar-header">
          <IconButton kind="ghost" size="sm" label={isExpanded ? "Collapse variables panel" : "Expand variables panel"}
            onClick={() => setIsExpanded(!isExpanded)}>
            {isExpanded ? <ChevronLeft /> : <ChevronRight />}
          </IconButton>
          {isExpanded && (
            <>
              <div className="variables-sidebar-title">
                <DataTable size={20} />
                <span>Variables</span>
                <Tag type="gray" size="sm">{variableKeys.length}</Tag>
              </div>
              {history.length > 0 && (
                <Select
                  id="variables-history-select"
                  size="sm"
                  labelText="Select which conversation turn to view variables from"
                  hideLabel
                  className="variables-history-select"
                  value={selectedAnswerId || ''}
                  onChange={(e) => onSelectAnswer && onSelectAnswer(e.target.value)}
                  onClick={(e) => e.stopPropagation()}
                  title="Select which conversation turn to view variables from"
                >
                  {history.map((item) => (
                    <SelectItem key={item.id} value={item.id}
                      text={`${item.title} - ${Object.keys(item.variables).length} variable${Object.keys(item.variables).length !== 1 ? 's' : ''} (${formatTimestamp(item.timestamp)})`} />
                  ))}
                </Select>
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
              {variableKeys.length === 0 && history.length > 0 ? (
                <div className="no-variables-message">
                  <p>No variables in current turn.</p>
                  <p>Select a previous turn from the dropdown above to view its variables.</p>
                </div>
              ) : (
                variableKeys.map((varName) => {
                  const variable = variables[varName];
                  return (
                    <ClickableTile
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
                    </ClickableTile>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      {/* Floating toggle button when sidebar is collapsed */}
      {!isExpanded && (
        <IconButton kind="tertiary" label={`Show variables panel (${variableKeys.length})`}
          className="variables-sidebar-floating-toggle" onClick={() => setIsExpanded(true)}><DataTable /></IconButton>
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

