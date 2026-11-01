import React, { useState } from "react";
import { Button, CodeSnippet, ContainedList, ContainedListItem, InlineNotification, Stack, Tag, Tile } from "@carbon/react";
import { Launch } from "@carbon/icons-react";
import { getApiBaseUrl } from "../../frontend/src/api";

interface ToolData {
  name: string;
  args?: Record<string, unknown>;
}

export default function ToolCallFlowDisplay({ toolData }: { toolData: ToolData }) {
  const [error, setError] = useState<string | null>(null);
  const formatArgValue = (value: unknown): string => {
    if (typeof value === "string") return `"${value}"`;
    return String(value);
  };

  return (
    <Tile>
      <Stack gap={5}>
        <div>
          <h3 className="cds--type-heading-compact-01">Flow Name</h3>
          <CodeSnippet type="single">{toolData.name}</CodeSnippet>
        </div>
        <ContainedList label="Inputs" kind="on-page">
          {Object.entries(toolData.args ?? {}).map(([key, value]) => (
            <ContainedListItem key={key}>
              <Stack gap={3}>
                <div><strong>{key}:</strong><Tag type="gray" size="sm">{typeof value}</Tag></div>
                <CodeSnippet type="single">{formatArgValue(value)}</CodeSnippet>
              </Stack>
            </ContainedListItem>
          ))}
        </ContainedList>
        {error && <InlineNotification kind="error" title="Error" subtitle={error} lowContrast
          onCloseButtonClick={() => setError(null)} />}
        {toolData.name !== "run_new_flow" && <Button kind="ghost" size="sm" renderIcon={Launch}
          onClick={() => {
            try { window.open(`${getApiBaseUrl()}/flows/flow.html`, "_blank", "noopener,noreferrer"); }
            catch { setError("Local server not running. Please start your development server."); }
          }}>Flow explained</Button>}
      </Stack>
    </Tile>
  );
}
