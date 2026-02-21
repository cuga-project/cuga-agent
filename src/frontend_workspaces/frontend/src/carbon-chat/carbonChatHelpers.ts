/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */

import {
  ButtonItemType,
  MessageResponseTypes,
  ReasoningStepOpenState,
  UserType,
  type ReasoningStep,
} from "@carbon/ai-chat";

export const RESPONSE_USER_PROFILE = {
  id: "cuga-agent",
  nickname: "CUGA",
  user_type: UserType.BOT,
  profile_picture_url: "https://avatars.githubusercontent.com/u/230847519?s=200&v=4",
};

export const BUTTON_KIND = {
  PRIMARY: "primary",
  SECONDARY: "secondary",
  TERTIARY: "tertiary",
  GHOST: "ghost",
  DANGER: "danger",
  DANGER_TERTIARY: "danger--tertiary",
  DANGER_GHOST: "danger--ghost",
} as const;

export function generateMessageId(timestamp: string, prefix: string): string {
  return `msg-${timestamp}-${prefix}-${Math.random().toString(36).substring(2, 11)}`;
}

export function extractEventData(eventData: string): string {
  if (eventData.includes("data: ")) {
    const dataMatch = eventData.match(/data: (.+?)(?:\n\n|$)/s);
    if (dataMatch) {
      return dataMatch[1].trim();
    }
  }
  return eventData;
}

export function parseReasoningStepContent(
  data: string,
  title: string
): { title: string; content: string } {
  try {
    const parsed = JSON.parse(data);
    let content = "";

    if (parsed.code) {
      content = `\`\`\`python\n${parsed.code}\n\`\`\``;
      if (parsed.summary) {
        content = `${parsed.summary}\n\n${content}`;
      }
    } else if (parsed.execution_output) {
      content = `**Execution Output:**\n\`\`\`\n${parsed.execution_output}\n\`\`\``;
      if (parsed.summary) {
        content = `${parsed.summary}\n\n${content}`;
      }
    } else {
      content = `\`\`\`json\n${JSON.stringify(parsed, null, 2)}\n\`\`\``;
    }

    return { title, content };
  } catch {
    return { title, content: data };
  }
}

export function createReasoningStep(
  title: string,
  content: string,
  openState: ReasoningStepOpenState = ReasoningStepOpenState.OPEN
): ReasoningStep {
  return { title, content, open_state: openState };
}

export interface PolicyInfo {
  response_content: string;
  policy_reasoning: string;
  policy_type: string;
  policy_name: string;
  is_playbook: boolean;
  playbook_content?: string;
}

export interface ParsedAnswerResult {
  answerText: string;
  policyInfo: PolicyInfo | null;
  isToolApproval: boolean;
  policyData: any;
}

export function parseAnswerEventData(
  data: string,
  accumulatedText: string = ""
): ParsedAnswerResult {
  const result: ParsedAnswerResult = {
    answerText: accumulatedText,
    policyInfo: null,
    isToolApproval: false,
    policyData: null,
  };

  try {
    const parsed = JSON.parse(data);
    let innerData = parsed.data;

    if (typeof innerData === "string") {
      try {
        innerData = JSON.parse(innerData);
      } catch {
        // use as-is
      }
    }

    const policyData =
      innerData?.type === "policy"
        ? innerData
        : parsed.active_policies?.[0] ?? null;

    if (policyData && (policyData.policy_blocked || policyData.policy_matched)) {
      const isPlaybook = policyData.policy_type === "playbook";
      const playbookContent =
        policyData.metadata?.playbook_guidance ||
        policyData.metadata?.playbook_content ||
        policyData.content;

      result.policyInfo = {
        response_content:
          policyData.metadata?.response_content ||
          policyData.content ||
          (isPlaybook ? "" : "This action is not allowed."),
        policy_reasoning:
          policyData.metadata?.policy_reasoning || "Policy triggered",
        policy_type:
          policyData.policy_type ||
          policyData.metadata?.policy_type ||
          "unknown",
        policy_name:
          policyData.policy_name ||
          policyData.metadata?.policy_name ||
          "Policy",
        is_playbook: isPlaybook,
        playbook_content: playbookContent,
      };
      result.policyData = policyData;
      result.isToolApproval =
        policyData.policy_type === "tool_approval" &&
        policyData.metadata?.approval_required;

      if (result.isToolApproval) {
        return result;
      }

      if (isPlaybook) {
        result.answerText = accumulatedText || "Following the playbook to guide you through this process.";
        result.answerText += "\n\n";
        result.answerText += "> ###### 📖 *Playbook Information*\n";
        result.answerText += ">\n";
        result.answerText += `> *Playbook Name:* **${result.policyInfo.policy_name}**\n`;
        result.answerText += ">\n";
        result.answerText += `> *Reasoning:* ${result.policyInfo.policy_reasoning}`;
      } else {
        result.answerText = result.policyInfo.response_content;
        result.answerText += "\n\n";
        result.answerText += "> ###### 🛡️ *Policy Information*\n";
        result.answerText += ">\n";
        result.answerText += `> *Policy Name:* **${result.policyInfo.policy_name}**\n`;
        result.answerText += ">\n";
        result.answerText += `> *Policy Type:* \`${result.policyInfo.policy_type}\`\n`;
        result.answerText += ">\n";
        result.answerText += `> *Reasoning:* ${result.policyInfo.policy_reasoning}`;
      }
    } else {
      result.answerText =
        accumulatedText ||
        (typeof innerData === "string" ? innerData : parsed.data ?? data);
    }
  } catch {
    result.answerText = accumulatedText || data;
  }

  return result;
}

export function buildToolApprovalCard(policyInfo: PolicyInfo, policyData: any, threadId: string) {
  const approvalMsg =
    policyData.metadata?.approval_message ||
    "This tool requires your approval before execution.";
  const toolsList = policyData.metadata?.required_tools || [];
  const appsList = policyData.metadata?.required_apps || [];
  const codePreview = policyData.metadata?.code_preview || [];

  const cardBody: any[] = [
    {
      response_type: MessageResponseTypes.TEXT,
      text: `### ✋ ${policyInfo.policy_name}`,
    },
    {
      response_type: MessageResponseTypes.TEXT,
      text: approvalMsg,
    },
  ];

  if (toolsList.length > 0) {
    const toolsText = toolsList.includes("*")
      ? "**Tools requiring approval:** All tools"
      : `**Tools requiring approval:** ${toolsList.join(", ")}`;
    cardBody.push({
      response_type: MessageResponseTypes.TEXT,
      text: toolsText,
    });
  }

  if (appsList.length > 0) {
    cardBody.push({
      response_type: MessageResponseTypes.TEXT,
      text: `**Apps requiring approval:** ${appsList.join(", ")}`,
    });
  }

  if (codePreview.length > 0) {
    cardBody.push({
      response_type: MessageResponseTypes.TEXT,
      text: "**Code Preview:**",
    });
    cardBody.push({
      response_type: MessageResponseTypes.TEXT,
      text: `\`\`\`python\n${codePreview.join("\n")}\n\`\`\``,
    });
  }

  const footer = [
    {
      kind: BUTTON_KIND.PRIMARY as any,
      label: "Approve & Execute",
      button_type: ButtonItemType.CUSTOM_EVENT as any,
      response_type: MessageResponseTypes.BUTTON,
      custom_event_name: "tool_approval_response",
      user_defined: { approved: true, thread_id: threadId },
    },
    {
      kind: BUTTON_KIND.DANGER as any,
      label: "Deny",
      button_type: ButtonItemType.CUSTOM_EVENT as any,
      response_type: MessageResponseTypes.BUTTON,
      custom_event_name: "tool_approval_response",
      user_defined: { approved: false, thread_id: threadId },
    },
  ];

  return { body: cardBody, footer };
}
