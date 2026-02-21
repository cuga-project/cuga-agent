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
    // Fetch streaming events
    const eventsResponse = await fetch(
      `/api/conversation-stream-events/${threadId}?agent_id=cuga-default&user_id=default_user`
    );

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

    for (const event of events) {
      console.log(`Processing event: ${event.event_name}`, event);

      const actualData = extractEventData(event.event_data);

      switch (event.event_name) {
        case "UserMessage":
          history.push({
            message: {
              id: generateMessageId(event.timestamp, "user"),
              input: {
                text: actualData,
                message_type: MessageInputType.TEXT,
              },
            } as MessageRequest,
            time: event.timestamp,
          });
          break;

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
    const response = await fetch(
      `/api/conversation-messages/${threadId}?agent_id=cuga-default&user_id=default_user`
    );

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