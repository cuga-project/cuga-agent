/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */

import {
  ChatInstance,
  HistoryItem,
  MessageInputType,
  MessageRequest,
  MessageResponse,
  MessageResponseTypes,
  type ReasoningStep,
} from "@carbon/ai-chat";
import * as api from "../api";
import { injectCitations, type MessageSource } from "agentic_chat/Citations";
import {
  RESPONSE_USER_PROFILE,
  extractEventData,
  generateMessageId,
  parseReasoningStepContent,
  parseAnswerEventData,
  buildToolApprovalCard,
  createReasoningStep,
} from "./carbonChatHelpers";

interface StreamEvent {
  event_name: string;
  event_data: string;
  timestamp: string;
  sequence: number;
}

interface ConversationMessage {
  role: string;
  content: string;
  timestamp: string;
  metadata?: {
    type: string;
    message_type?: string;
    attachments?: Array<{
      knowledge_filename: string;
      display_name: string;
      mime_type?: string;
      size_bytes?: number;
      scope?: string;
    }>;
  };
}

async function customLoadHistory(
  _instance: ChatInstance,
  threadId?: string
): Promise<HistoryItem[]> {
  if (!threadId) {
    return [];
  }

  try {
    const eventsResponse = await api.getConversationStreamEvents(threadId);

    if (!eventsResponse.ok) {
      console.error("Failed to load conversation stream events");
      return await loadBasicMessages(threadId);
    }

    const eventsData = await eventsResponse.json();
    const events: StreamEvent[] = eventsData.events || [];

    if (events.length === 0) {
      // Fallback to basic messages if no stream events
      return await loadBasicMessages(threadId);
    }

    console.log(`Found ${events.length} stream events`);

    // Group events by conversation turn (user message + assistant response)
    const history: HistoryItem[] = [];
    let currentSteps: ReasoningStep[] = [];
    let currentAnswerText = "";
    const subAgentAccumulators: Map<string, Array<{ title: string; content: string }>> = new Map();

    for (const event of events) {
      console.log(`Processing event: ${event.event_name}`, event);

      const actualData = extractEventData(event.event_data);

      switch (event.event_name) {
        case "UserMessage": {
          let userText = actualData;
          try {
            const parsed = JSON.parse(actualData);
            if (typeof parsed?.text === "string") {
              userText = parsed.text;
            }
          } catch {
            // Keep legacy plain-text event payloads as-is.
          }
          history.push({
            message: {
              id: generateMessageId(event.timestamp, "user"),
              input: {
                text: userText,
                message_type: MessageInputType.TEXT,
              },
            } as MessageRequest,
            time: event.timestamp,
          });
          break;
        }

        case "FinalAnswerAgent":
          try {
            const parsed = JSON.parse(actualData);
            currentAnswerText = parsed.final_answer || parsed.data || actualData;
          } catch {
            currentAnswerText = actualData;
          }
          break;

        case "CodeAgent":
        case "CodeAgent_Reasoning":
        case "Thinking":
        case "Planning":
        case "Analyzing": {
          const stepResult = parseReasoningStepContent(
            actualData,
            event.event_name.replace(/_/g, " ")
          );
          currentSteps.push(createReasoningStep(stepResult.title, stepResult.content));
          break;
        }

        case "SubAgent": {
          let subAgentData: any = {};
          try { subAgentData = typeof actualData === "string" ? JSON.parse(actualData) : (actualData || {}); } catch { /* ignore */ }

          const agentName = subAgentData.agent_name || "sub-agent";
          const spawnKey = subAgentData.spawn_id || agentName;
          const agentLabel = subAgentData.spawn_id
            ? `Sub-Agent: ${agentName} (${String(subAgentData.spawn_id).slice(0, 12)})`
            : `Sub-Agent: ${agentName}`;

          if (!subAgentAccumulators.has(spawnKey)) {
            subAgentAccumulators.set(spawnKey, []);
          }
          const iterations = subAgentAccumulators.get(spawnKey)!;

          let newIteration: { title: string; content: string } | null = null;
          if (subAgentData.type === "start") {
            newIteration = { title: "Task", content: subAgentData.task || "" };
          } else if (subAgentData.type === "result") {
            const answer = subAgentData.answer || "";
            newIteration = {
              title: "Result",
              content: `**Execution Output:**\n\`\`\`\n${answer}\n\`\`\``,
            };
          } else if (subAgentData.code || subAgentData.execution_output) {
            const parsed = parseReasoningStepContent(actualData, "");
            newIteration = {
              title: subAgentData.code ? "Code" : "Execution Output",
              content: parsed.content,
            };
          }

          if (!newIteration) break;
          iterations.push(newIteration);

          // Replace or append the sub-agent step in currentSteps
          const existingIdx = currentSteps.findIndex((s) => s.title === agentLabel);
          const nestedContent = iterations
            .map(({ title, content }) => `<details>\n<summary>${title}</summary>\n\n${content}\n\n</details>`)
            .join("\n\n");
          const updatedStep = createReasoningStep(agentLabel, nestedContent);
          if (existingIdx >= 0) {
            currentSteps[existingIdx] = updatedStep;
          } else {
            currentSteps.push(updatedStep);
          }
          break;
        }

        case "Answer":
        case "FinalAnswer": {
          const parsed = parseAnswerEventData(actualData, currentAnswerText);

          if (parsed.isToolApproval && parsed.policyInfo && parsed.policyData && threadId) {
            const { body, footer } = buildToolApprovalCard(
              parsed.policyInfo,
              parsed.policyData,
              threadId
            );
            const cardMessage: any = {
              id: generateMessageId(event.timestamp, "assistant"),
              output: {
                generic: [
                  { body, footer, response_type: MessageResponseTypes.CARD },
                ],
              },
            };
            cardMessage.message_options = {
              ...(currentSteps.length > 0 ? { reasoning: { steps: currentSteps } } : {}),
              response_user_profile: RESPONSE_USER_PROFILE,
            };
            history.push({ message: cardMessage as MessageResponse, time: event.timestamp });
          } else {
            currentAnswerText = parsed.answerText;
            const messageId = generateMessageId(event.timestamp, "assistant");
            const answerSources = parsed.sources as MessageSource[];
            // Same transform as the live path (customSendMessage.ts): the
            // message id doubles as the `msg` key stamped on each chip.
            const genericItems: any[] = [
              {
                response_type: MessageResponseTypes.TEXT,
                text: injectCitations(currentAnswerText, answerSources, messageId),
              },
            ];
            if (answerSources.length > 0) {
              genericItems.push({
                response_type: MessageResponseTypes.USER_DEFINED,
                user_defined: {
                  type: "cuga_sources",
                  message_key: messageId,
                  sources: answerSources,
                },
              });
            }
            if (parsed.memoryUsage) {
              genericItems.push({
                response_type: MessageResponseTypes.USER_DEFINED,
                user_defined: {
                  type: "cuga_memory_usage",
                  count: parsed.memoryUsage.count,
                  entity_ids: parsed.memoryUsage.entityIds,
                },
              });
            }
            if (parsed.memorySaved) {
              genericItems.push({
                response_type: MessageResponseTypes.USER_DEFINED,
                user_defined: {
                  type: "cuga_memory_saved",
                  count: parsed.memorySaved.count,
                  entity_ids: parsed.memorySaved.entityIds,
                },
              });
            }
            const messageResponse: any = {
              id: messageId,
              output: { generic: genericItems },
            };
            messageResponse.message_options = {
              ...(currentSteps.length > 0 ? { reasoning: { steps: currentSteps } } : {}),
              response_user_profile: RESPONSE_USER_PROFILE,
            };
            history.push({ message: messageResponse as MessageResponse, time: event.timestamp });
          }

          currentSteps = [];
          currentAnswerText = "";
          subAgentAccumulators.clear();
          break;
        }

        default:
          currentSteps.push(
            createReasoningStep(event.event_name.replace(/_/g, " "), actualData)
          );
          break;
      }
    }

    console.log(`Loaded ${history.length} history items from ${events.length} events`);
    return history;
  } catch (error) {
    console.error("Error loading conversation history:", error);
    return [];
  }
}

// Fallback function to load basic messages
async function loadBasicMessages(threadId: string): Promise<HistoryItem[]> {
  try {
    const response = await api.getConversationMessages(threadId);

    if (!response.ok) {
      return [];
    }

    const data = await response.json();
    const messages: ConversationMessage[] = data.messages || [];

    return messages.map((msg) => {
      const isUserMessage = msg.role === "user" || msg.role === "human";
      const messageId = generateMessageId(msg.timestamp, "msg");

      if (isUserMessage) {
        return {
          message: {
            id: messageId,
            input: {
              text: msg.content,
              message_type: MessageInputType.TEXT,
            },
          } as MessageRequest,
          time: msg.timestamp,
        };
      } else {
        return {
          message: {
            id: messageId,
            output: {
              generic: [
                {
                  response_type: MessageResponseTypes.TEXT,
                  text: msg.content,
                },
              ],
            },
            message_options: { response_user_profile: RESPONSE_USER_PROFILE },
          } as MessageResponse,
          time: msg.timestamp,
        };
      }
    });
  } catch (error) {
    console.error("Error loading basic messages:", error);
    return [];
  }
}

export { customLoadHistory };
