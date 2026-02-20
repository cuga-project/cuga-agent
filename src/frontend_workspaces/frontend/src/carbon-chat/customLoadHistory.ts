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
  ReasoningStepOpenState,
  UserType,
  type ReasoningStep,
} from "@carbon/ai-chat";

const RESPONSE_USER_PROFILE = {
  id: "cuga-agent",
  nickname: "CUGA",
  user_type: UserType.BOT,
  profile_picture_url: "https://avatars.githubusercontent.com/u/230847519?s=200&v=4",
};

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

      // Extract the actual data from the SSE format
      // The event_data contains "event: EventName\ndata: actual_data\n\n"
      let actualData = event.event_data;
      if (actualData.includes('data: ')) {
        const dataMatch = actualData.match(/data: (.+?)(?:\n\n|$)/s);
        if (dataMatch) {
          actualData = dataMatch[1].trim();
        }
      }

      // Process different event types
      switch (event.event_name) {
        case "UserMessage":
          // User message event - add it to history
          const userMessageId = `msg-${event.timestamp}-user-${Math.random().toString(36).substring(2, 11)}`;
          history.push({
            message: {
              id: userMessageId,
              input: {
                text: actualData,
                message_type: MessageInputType.TEXT,
              },
            } as MessageRequest,
            time: event.timestamp,
          });
          break;

        case "CodeAgent":
        case "CodeAgent_Reasoning":
        case "Thinking":
        case "Planning":
        case "Analyzing":
          // Parse and add as reasoning step
          try {
            const parsed = JSON.parse(actualData);
            let content = "";
            let title = event.event_name.replace(/_/g, " ");

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

            currentSteps.push({
              title,
              content,
              open_state: ReasoningStepOpenState.OPEN,
            });
          } catch {
            // Not JSON, use as-is
            currentSteps.push({
              title: event.event_name.replace(/_/g, " "),
              content: actualData,
              open_state: ReasoningStepOpenState.OPEN,
            });
          }
          break;

        case "Answer":
        case "FinalAnswer":
          // Final answer - create the response with all collected steps
          try {
            const parsed = JSON.parse(actualData);
            currentAnswerText = parsed.data || actualData;
          } catch {
            currentAnswerText = actualData;
          }

          // Add assistant response with reasoning steps
          const assistantMessageId = `msg-${event.timestamp}-assistant-${Math.random().toString(36).substring(2, 11)}`;
          const messageResponse: any = {
            id: assistantMessageId,
            output: {
              generic: [
                {
                  response_type: MessageResponseTypes.TEXT,
                  text: currentAnswerText,
                },
              ],
            },
          };

          messageResponse.message_options = {
            ...(currentSteps.length > 0 ? { reasoning: { steps: currentSteps } } : {}),
            response_user_profile: RESPONSE_USER_PROFILE,
          };

          history.push({
            message: messageResponse as MessageResponse,
            time: event.timestamp,
          });

          // Reset for next turn
          currentSteps = [];
          currentAnswerText = "";
          break;

        default:
          // Other events - add as reasoning steps
          currentSteps.push({
            title: event.event_name.replace(/_/g, " "),
            content: actualData,
            open_state: ReasoningStepOpenState.OPEN,
          });
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
      const messageId = `msg-${msg.timestamp}-${Math.random().toString(36).substring(2, 11)}`;

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