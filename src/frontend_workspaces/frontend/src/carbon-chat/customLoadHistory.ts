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
import {
  RESPONSE_USER_PROFILE,
  extractEventData,
  generateMessageId,
  parseReasoningStepContent,
  parseAnswerEventData,
  buildToolApprovalCard,
  createReasoningStep,
} from "./carbonChatHelpers";
import {
  CUGA_USER_DEFINED_KIND,
  type SlashSuggestionsChipData,
  type SlashSuggestion,
} from "./SlashChips";

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
    // Set after a SlashSuggestions chip so the following redundant plain-text
    // "Unknown command..." Answer event is skipped on reload (mirrors the
    // live-turn suppression in customSendMessage.ts).
    let suppressNextAnswer = false;

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

        // Replay a resolved slash-skill invocation as a reasoning step
        // attached to the assistant message being assembled (mirrors the live
        // path in customSendMessage.ts). The step lands in the same "Show
        // details" panel as the planner reasoning that follows.
        case "SlashSkillInvoked": {
          try {
            const parsed = JSON.parse(actualData);
            const resolvedName = String(parsed?.resolved_name ?? "");
            const rawInput = String(parsed?.raw_input ?? "");
            const rawArgs = String(parsed?.raw_args ?? "");
            // A literal backtick in the user's input would close the inline
            // code span and break the rendered markdown — escape so the
            // span stays intact regardless of user content.
            const escapeBackticks = (s: string) => s.replace(/`/g, "\\`");
            const stepTitle = `Skill invoked: /${escapeBackticks(resolvedName)}`;
            const stepContent = [
              `**Input:** \`${escapeBackticks(rawInput)}\``,
              `**Resolved skill:** \`${escapeBackticks(resolvedName)}\``,
              `**Arguments:** \`${rawArgs ? escapeBackticks(rawArgs) : "(none)"}\``,
            ].join("\n\n");
            currentSteps.push(createReasoningStep(stepTitle, stepContent));
          } catch (e) {
            console.error("Error parsing SlashSkillInvoked history event:", e);
          }
          break;
        }

        // Replay unknown-command suggestion chips, and suppress the
        // redundant plain-text "Unknown command..." Answer that follows.
        case "SlashSuggestions": {
          try {
            const parsed = JSON.parse(actualData);
            const suggestions: SlashSuggestion[] = Array.isArray(parsed?.suggestions)
              ? parsed.suggestions.map((s: any) => ({
                  name: String(s?.name ?? ""),
                  kind: s?.kind === "skill" ? "skill" : "builtin",
                  description: typeof s?.description === "string" ? s.description : "",
                  score: typeof s?.score === "number" ? s.score : 0,
                }))
              : [];
            const chipData: SlashSuggestionsChipData = {
              cuga_kind: CUGA_USER_DEFINED_KIND.SLASH_SUGGESTIONS,
              raw_input: String(parsed?.raw_input ?? ""),
              suggestions,
            };
            history.push({
              message: {
                id: generateMessageId(event.timestamp, "assistant"),
                output: {
                  generic: [
                    {
                      response_type: MessageResponseTypes.USER_DEFINED,
                      user_defined: chipData,
                    },
                  ],
                },
                message_options: { response_user_profile: RESPONSE_USER_PROFILE },
              } as MessageResponse,
              time: event.timestamp,
            });
            suppressNextAnswer = true;
          } catch (e) {
            console.error("Error parsing SlashSuggestions history event:", e);
          }
          break;
        }

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

        case "Answer":
        case "FinalAnswer": {
          // Skip the redundant plain-text fallback that follows a
          // SlashSuggestions event — the chips already convey it.
          if (suppressNextAnswer) {
            suppressNextAnswer = false;
            currentSteps = [];
            currentAnswerText = "";
            break;
          }

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
            const messageResponse: any = {
              id: generateMessageId(event.timestamp, "assistant"),
              output: {
                generic: [
                  { response_type: MessageResponseTypes.TEXT, text: currentAnswerText },
                ],
              },
            };
            messageResponse.message_options = {
              ...(currentSteps.length > 0 ? { reasoning: { steps: currentSteps } } : {}),
              response_user_profile: RESPONSE_USER_PROFILE,
            };
            history.push({ message: messageResponse as MessageResponse, time: event.timestamp });
          }

          currentSteps = [];
          currentAnswerText = "";
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
