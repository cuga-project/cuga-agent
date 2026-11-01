import React from "react";
import { Button, ComposedModal, ModalHeader, ModalBody, ModalFooter } from "@carbon/react";
import { Download } from "@carbon/icons-react";
import Markdown from "@carbon/ai-chat-components/es/react/markdown.js";
import "./VariablePopup.css";

interface VariablePopupProps {
  variable: {
    name: string;
    type: string;
    description?: string;
    value_preview: string;
    count_items?: number;
  };
  onClose: () => void;
}

const VariablePopup: React.FC<VariablePopupProps> = ({ variable, onClose }) => {
  const handleDownload = () => {
    // Check if variable is a dict type and try to download as JSON
    if (variable.type === 'dict') {
      try {
        // Attempt to parse the value_preview as JSON
        const jsonData = JSON.parse(variable.value_preview);
        const content = JSON.stringify(jsonData, null, 2);
        // Use octet-stream to force the browser to respect the .json extension
        const blob = new Blob([content], { type: "application/octet-stream" });
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
    const blob = new Blob([content], { type: "text/markdown" });
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

  return (
    <ComposedModal aria-label="Variable Details" open onClose={onClose} size="lg">
      <ModalHeader title="Variable Details" label={variable.name} />
      <ModalBody className="variable-popup-body">
        <Markdown markdown={formattedContent} />
      </ModalBody>
      <ModalFooter>
        <Button kind="secondary" onClick={onClose}>Close</Button>
        <Button renderIcon={Download} onClick={handleDownload}>
          Download {variable.type === 'dict' ? 'JSON' : 'MD'}
        </Button>
      </ModalFooter>
    </ComposedModal>
  );
};

export default VariablePopup;

