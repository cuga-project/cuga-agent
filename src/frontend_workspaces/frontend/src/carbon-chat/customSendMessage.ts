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
  ChatInstance,
  CustomSendMessageOptions,
  MessageRequest,
  MessageResponseTypes,
  ReasoningStepOpenState,
  type ReasoningStep,
  type StreamChunk,
} from "@carbon/ai-chat";
import {
  BUTTON_KIND,
  RESPONSE_USER_PROFILE,
  parseReasoningStepContent,
  parseAnswerEventData,
  buildToolApprovalCard,
  createReasoningStep,
} from "./carbonChatHelpers";

declare global {
  interface Window {
    __CUGA_BACKEND_URL__?: string;
  }
}

function getCugaBackendUrl(): string {
  if (typeof window === 'undefined') return "http://localhost:7860";
  if (window.__CUGA_BACKEND_URL__) return window.__CUGA_BACKEND_URL__;
  const envUrl = typeof process !== 'undefined' && (process as any).env?.CUGA_BACKEND_URL;
  if (envUrl && String(envUrl).trim()) return String(envUrl).trim();
  const { hostname, protocol, origin } = window.location;
  if (hostname !== 'localhost' && hostname !== '127.0.0.1') return origin;
  return `${protocol}//${hostname}:7860`;
}

// Import thread ID management from CarbonChat
import { getOrCreateThreadId } from './CarbonChat';

export async function stopCugaAgent(threadId: string) {
  try {
    const url = getCugaBackendUrl();
    console.log(`Calling /stop for thread: ${threadId}`);
    const response = await fetch(`${url}/stop`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Thread-ID": threadId,
      },
    });
    
    if (response.ok) {
      const result = await response.json();
      console.log("Stop request successful:", result);
    } else {
      console.error("Stop request failed:", response.status);
    }
  } catch (error) {
    console.error("Error calling /stop endpoint:", error);
  }
}

interface CugaStreamEvent {
  name: string;
  data: any;
}

async function* parseCugaStream(response: Response): AsyncGenerator<CugaStreamEvent> {
  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  
  if (!reader) {
    throw new Error("No response body");
  }

  let buffer = "";
  let currentEvent: Partial<CugaStreamEvent> = {};

  try {
    while (true) {
      const { done, value } = await reader.read();
      
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      
      // Split by double newline to get complete events
      const events = buffer.split("\n\n");
      buffer = events.pop() || ""; // Keep incomplete event in buffer
      
      for (const eventBlock of events) {
        if (!eventBlock.trim()) continue;
        
        console.log("Raw event block:", JSON.stringify(eventBlock));
        
        const lines = eventBlock.split("\n");
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent.name = line.slice(7).trim();
            console.log("  Parsed event name:", currentEvent.name);
          } else if (line.startsWith("data: ")) {
            currentEvent.data = line.slice(6); // Keep the data as-is (may be plain text or JSON)
            console.log("  Parsed event data:", JSON.stringify(currentEvent.data));
          }
        }
        
        // Yield complete event
        if (currentEvent.name && currentEvent.data !== undefined) {
          console.log("Yielding complete event:", currentEvent);
          yield currentEvent as CugaStreamEvent;
          currentEvent = {};
        } else {
          console.warn("Incomplete event, not yielding:", currentEvent);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function customSendMessage(
  request: MessageRequest,
  requestOptions: CustomSendMessageOptions,
  instance: ChatInstance,
  useDraft: boolean = false,
  disableHistory: boolean = false,
  actionResponse?: any,
) {
  const userMessage = request.input.text?.trim() ?? "";
  
  // Allow empty message if we have an action response
  if (!userMessage && !actionResponse) {
    return;
  }

  const threadId = getOrCreateThreadId();
  const responseID = crypto.randomUUID();
  
  // Listen for abort signal to call /stop endpoint
  const abortHandler = () => {
    console.log("User cancelled request, calling /stop endpoint");
    stopCugaAgent(threadId);
  };
  
  if (requestOptions.signal) {
    requestOptions.signal.addEventListener("abort", abortHandler);
  }
  
  // Create shell message for streaming
  instance.messaging.addMessageChunk({
    partial_item: {
      response_type: MessageResponseTypes.TEXT,
      text: "",
      streaming_metadata: { id: "text-stream", cancellable: true },
    },
    partial_response: {
      message_options: { reasoning: { steps: [] }, response_user_profile: RESPONSE_USER_PROFILE },
    },
    streaming_metadata: { response_id: responseID },
  });

  const CUGA_BACKEND_URL = getCugaBackendUrl();
  try {
    console.log(`Connecting to CUGA backend at: ${CUGA_BACKEND_URL}/stream`);
    console.log(`Thread ID: ${threadId}`);
    console.log(`User message: ${userMessage}`);
    console.log(`Use Draft: ${useDraft}`);
    
    // Build headers
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Thread-ID": threadId,
    };
    
    // Add draft header if needed
    if (useDraft) {
      headers["X-Use-Draft"] = "true";
    }
    
    // Add disable history header if needed
    if (disableHistory) {
      headers["X-Disable-History"] = "true";
    }
    
    const body = actionResponse
      ? JSON.stringify(actionResponse)
      : JSON.stringify({ query: userMessage });
    
    const response = await fetch(`${CUGA_BACKEND_URL}/stream`, {
      method: "POST",
      headers,
      body,
      signal: requestOptions.signal,
    });

    console.log(`Response status: ${response.status}`);
    
    if (!response.ok) {
      const errorText = await response.text();
      console.error(`HTTP error response:`, errorText);
      throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
    }

    const collectedSteps: ReasoningStep[] = [];
    let accumulatedText = "";
    let currentStepTitle = "";
    let currentStepContent = "";

    // Process the stream
    for await (const event of parseCugaStream(response)) {
      // Check if cancelled
      if (requestOptions.signal?.aborted) {
        break;
      }

      console.log("CUGA Event:", event);

      switch (event.name) {
        case "CodeAgent":
          if (currentStepTitle && currentStepContent) {
            collectedSteps.push(createReasoningStep(currentStepTitle, currentStepContent));
          }

          const codeAgentResult = parseReasoningStepContent(event.data || "", "Code Agent");
          currentStepTitle = codeAgentResult.title;
          currentStepContent = codeAgentResult.content;

          console.log(`Code Agent step, content: ${currentStepContent}`);
          
          if (currentStepContent) {
            instance.messaging.addMessageChunk({
              partial_item: {
                response_type: MessageResponseTypes.TEXT,
                text: "",
                streaming_metadata: { id: "text-stream", cancellable: true },
              },
              partial_response: {
                message_options: { reasoning: { steps: [...collectedSteps, { title: currentStepTitle, content: currentStepContent }] }, response_user_profile: RESPONSE_USER_PROFILE },
              },
              streaming_metadata: { response_id: responseID },
            } as StreamChunk);
          }
          break;

        case "CodeAgent_Reasoning":
        case "Thinking":
        case "Planning":
        case "Analyzing":
          if (currentStepTitle && currentStepContent) {
            collectedSteps.push(createReasoningStep(currentStepTitle, currentStepContent));
          }

          const reasoningResult = parseReasoningStepContent(
            event.data || "",
            event.name.replace(/_/g, " ")
          );
          currentStepTitle = reasoningResult.title;
          currentStepContent = reasoningResult.content;
          
          console.log(`Reasoning step: ${currentStepTitle}, content: ${currentStepContent}`);
          
          // Only add if we have content
          if (currentStepContent) {
            instance.messaging.addMessageChunk({
              partial_item: {
                response_type: MessageResponseTypes.TEXT,
                text: "",
                streaming_metadata: { id: "text-stream", cancellable: true },
              },
              partial_response: {
                message_options: { reasoning: { steps: [...collectedSteps, { title: currentStepTitle, content: currentStepContent }] }, response_user_profile: RESPONSE_USER_PROFILE },
              },
              streaming_metadata: { response_id: responseID },
            } as StreamChunk);
          }
          break;

        case "ToolCall":
        case "Action":
          const toolData = typeof event.data === "string" ? event.data : JSON.stringify(event.data, null, 2);
          collectedSteps.push(
            createReasoningStep(event.name, `\`\`\`json\n${toolData}\n\`\`\``, ReasoningStepOpenState.CLOSE)
          );
          
          instance.messaging.addMessageChunk({
            partial_item: {
              response_type: MessageResponseTypes.TEXT,
              text: "",
              streaming_metadata: { id: "text-stream", cancellable: true },
            },
            partial_response: {
              message_options: { reasoning: { steps: collectedSteps }, response_user_profile: RESPONSE_USER_PROFILE },
            },
            streaming_metadata: { response_id: responseID },
          } as StreamChunk);
          break;

        case "SuggestHumanActions":
          console.log("Received SuggestHumanActions event");
          
          // Parse the action data
          try {
            const actionData = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
            
            // Create card body with action details
            const cardBody: any[] = [
              {
                response_type: MessageResponseTypes.TEXT,
                text: `### ${actionData.action_name || "Action Required"}`,
              },
            ];
            
            // Add description if available
            if (actionData.description) {
              cardBody.push({
                response_type: MessageResponseTypes.TEXT,
                text: actionData.description,
              });
            }
            
            // Add additional data if available (e.g., tool info, code preview)
            if (actionData.additional_data?.tool) {
              const toolData = actionData.additional_data.tool;
              
              // Add required tools
              if (toolData.required_tools && toolData.required_tools.length > 0) {
                cardBody.push({
                  response_type: MessageResponseTypes.TEXT,
                  text: `**Required Tools:** ${toolData.required_tools.join(', ')}`,
                });
              }
              
              // Add code preview
              if (toolData.code_preview && toolData.code_preview.length > 0) {
                cardBody.push({
                  response_type: MessageResponseTypes.TEXT,
                  text: "**Code Preview:**",
                });
                cardBody.push({
                  response_type: MessageResponseTypes.TEXT,
                  text: `\`\`\`python\n${toolData.code_preview.join('\n')}\n\`\`\``,
                });
              }
              
              // Add policy name if available
              if (toolData.policy_name) {
                cardBody.push({
                  response_type: MessageResponseTypes.TEXT,
                  text: `**Policy:** ${toolData.policy_name}`,
                });
              }
            }
            
            let buttonKind: string = BUTTON_KIND.PRIMARY;
            if (actionData.color === 'danger') {
              buttonKind = BUTTON_KIND.DANGER;
            } else if (actionData.color === 'warning') {
              buttonKind = BUTTON_KIND.PRIMARY;
            }

            const footer: any[] = [];
            if (actionData.button_text) {
              footer.push({
                kind: buttonKind as any,
                label: actionData.button_text,
                button_type: ButtonItemType.CUSTOM_EVENT as any,
                response_type: MessageResponseTypes.BUTTON,
                custom_event_name: 'suggest_human_action',
                user_defined: {
                  action_id: actionData.action_id,
                  approved: true,
                  thread_id: threadId,
                  callback_url: actionData.callback_url,
                  return_to: actionData.return_to,
                },
              });
            }
            if (actionData.type === 'confirmation') {
              footer.push({
                kind: BUTTON_KIND.SECONDARY as any,
                label: 'Cancel',
                button_type: ButtonItemType.CUSTOM_EVENT as any,
                response_type: MessageResponseTypes.BUTTON,
                custom_event_name: 'suggest_human_action',
                user_defined: {
                  action_id: actionData.action_id,
                  approved: false,
                  thread_id: threadId,
                  callback_url: actionData.callback_url,
                  return_to: actionData.return_to,
                },
              });
            }

            instance.messaging.addMessage({
              output: {
                generic: [
                  {
                    body: cardBody,
                    footer,
                    response_type: MessageResponseTypes.CARD,
                  },
                ],
              },
            });
            
            // Don't finalize yet - wait for user response
            return;
          } catch (e) {
            console.error("Error parsing SuggestHumanActions event:", e);
            // Fall through to default handling
          }
          break;

        case "FinalAnswerAgent":
          console.log("Received FinalAnswerAgent event");
          // For playbooks, FinalAnswerAgent contains the actual answer
          // We'll accumulate it but not finalize yet - wait for Answer event
          if (typeof event.data === "string") {
            try {
              const parsed = JSON.parse(event.data);
              const finalAnswer = parsed.final_answer || parsed.data || event.data;
              // Store this as accumulated text but don't finalize
              accumulatedText = finalAnswer;
              console.log("FinalAnswerAgent - stored answer:", finalAnswer);
            } catch {
              accumulatedText = event.data;
            }
          }
          // Don't finalize here - wait for Answer event
          break;

        case "Answer":
        case "FinalAnswer":
          console.log("Received Answer event, finalizing message...");

          let answerText = accumulatedText || "";
          if (typeof event.data === "string") {
            const parsed = parseAnswerEventData(event.data, accumulatedText);
            if (parsed.isToolApproval && parsed.policyInfo && parsed.policyData) {
              const { body, footer } = buildToolApprovalCard(
                parsed.policyInfo,
                parsed.policyData,
                threadId
              );
              instance.messaging.addMessage({
                output: {
                  generic: [
                    { body, footer, response_type: MessageResponseTypes.CARD },
                  ],
                },
              });
              return;
            }
            answerText = parsed.answerText;
          } else if (!answerText) {
            answerText = event.data?.answer || JSON.stringify(event.data);
          }

          accumulatedText = answerText;
          
          if (currentStepTitle && currentStepContent) {
            collectedSteps.push(createReasoningStep(currentStepTitle, currentStepContent));
          }
          
          console.log(`Finalizing with ${collectedSteps.length} reasoning steps`);
          
          const answerCompleteItem = {
            response_type: MessageResponseTypes.TEXT,
            text: accumulatedText,
            streaming_metadata: { id: "text-stream" },
          };
          
          instance.messaging.addMessageChunk({
            complete_item: answerCompleteItem,
            streaming_metadata: { response_id: responseID },
          });

          const finalResponse: StreamChunk = {
            final_response: {
              id: responseID,
              output: { generic: [answerCompleteItem] },
            },
          };

          if (collectedSteps.length > 0) {
            finalResponse.final_response.message_options = { reasoning: { steps: collectedSteps }, response_user_profile: RESPONSE_USER_PROFILE };
          } else {
            finalResponse.final_response.message_options = { response_user_profile: RESPONSE_USER_PROFILE };
          }

          instance.messaging.addMessageChunk(finalResponse);
          
          console.log("Message finalized successfully");
          return; // Exit after finalizing

        case "Error":
          // Handle error
          const errorMsg = typeof event.data === "string" ? event.data : JSON.stringify(event.data);
          instance.messaging.addMessage({
            output: {
              generic: [{
                response_type: MessageResponseTypes.TEXT,
                text: `Error: ${errorMsg}`,
              }],
            },
          });
          return;

        case "Complete":
        case "Done":
          if (currentStepTitle) {
            collectedSteps.push(createReasoningStep(currentStepTitle, currentStepContent));
          }
          
          const completeItem = {
            response_type: MessageResponseTypes.TEXT,
            text: accumulatedText || "Task completed.",
            streaming_metadata: { id: "text-stream" },
          };
          
          instance.messaging.addMessageChunk({
            complete_item: completeItem,
            streaming_metadata: { response_id: responseID },
          });

          instance.messaging.addMessageChunk({
            final_response: {
              id: responseID,
              output: { generic: [completeItem] },
              message_options: {
                ...(collectedSteps.length > 0 ? { reasoning: { steps: collectedSteps } } : {}),
                response_user_profile: RESPONSE_USER_PROFILE,
              },
            },
          });
          return;

        default:
          if (event.data) {
            const stepContent = typeof event.data === "string" ? event.data : JSON.stringify(event.data);
            collectedSteps.push(
              createReasoningStep(event.name, stepContent, ReasoningStepOpenState.CLOSE)
            );
            
            instance.messaging.addMessageChunk({
              partial_item: {
                response_type: MessageResponseTypes.TEXT,
                text: "",
                streaming_metadata: { id: "text-stream", cancellable: true },
              },
              partial_response: {
                message_options: { reasoning: { steps: collectedSteps }, response_user_profile: RESPONSE_USER_PROFILE },
              },
              streaming_metadata: { response_id: responseID },
            } as StreamChunk);
          }
          break;
      }
    }

    // If stream ended without Complete event, finalize
    if (!requestOptions.signal?.aborted) {
      const completeItem = {
        response_type: MessageResponseTypes.TEXT,
        text: accumulatedText || "Response completed.",
        streaming_metadata: { id: "text-stream" },
      };
      
      instance.messaging.addMessageChunk({
        complete_item: completeItem,
        streaming_metadata: { response_id: responseID },
      });

      instance.messaging.addMessageChunk({
        final_response: {
          id: responseID,
          output: { generic: [completeItem] },
          message_options: {
            ...(collectedSteps.length > 0 ? { reasoning: { steps: collectedSteps } } : {}),
            response_user_profile: RESPONSE_USER_PROFILE,
          },
        },
      });
    }

  } catch (error: any) {
    console.error("Error calling CUGA backend:", error);
    
    if (error.name === "AbortError") {
      instance.messaging.addMessage({
        output: {
          generic: [{
            response_type: MessageResponseTypes.TEXT,
            text: "Request was cancelled.",
          }],
        },
      });
    } else {
      const url = getCugaBackendUrl();
      const msg = error.message || "Failed to connect to CUGA backend";
      instance.messaging.addMessage({
        output: {
          generic: [{
            response_type: MessageResponseTypes.TEXT,
            text: `Error: ${msg}. Backend URL: ${url}. Check that the backend is running and reachable (e.g. CORS, firewall, proxy).`,
          }],
        },
      });
    }
  } finally {
    // Clean up abort listener
    if (requestOptions.signal) {
      requestOptions.signal.removeEventListener("abort", abortHandler);
    }
  }
}