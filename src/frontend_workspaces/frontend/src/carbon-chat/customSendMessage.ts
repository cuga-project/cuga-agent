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

import * as api from "../api";
import { injectCitations, type MessageSource } from "agentic_chat/Citations";
import type { KnowledgeAttachmentSnapshot } from "../knowledge/useSessionKnowledgeAttachments";

// Import thread ID management from CarbonChat
import { getOrCreateThreadId, generateUUID } from './CarbonChat';


export async function stopCugaAgent(threadId: string) {
  try {
    console.log(`Calling /stop for thread: ${threadId}`);
    const response = await api.postStop(threadId);
    
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

        // Per the SSE spec, every ``data:`` line in an event contributes one
        // line to the event payload (joined by ``\n``). The previous version
        // overwrote ``currentEvent.data`` on each line, which silently
        // truncated multi-line responses to their last line.
        const dataLines: string[] = [];
        const lines = eventBlock.split("\n");
        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEvent.name = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            // Trim a single leading space (syntactic per the spec), preserve everything else.
            const raw = line.slice(5);
            dataLines.push(raw.startsWith(" ") ? raw.slice(1) : raw);
          }
        }
        if (dataLines.length > 0) {
          currentEvent.data = dataLines.join("\n");
        }

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
  attachmentSnapshot?: KnowledgeAttachmentSnapshot[],
) {
  const userMessage = request.input.text?.trim() ?? "";
  
  // Allow empty message if we have an action response
  if (!userMessage && !actionResponse) {
    return;
  }

  const threadId = getOrCreateThreadId();
  const responseID = generateUUID();
  
  // Listen for abort signal to call /stop endpoint
  const abortHandler = () => {
    console.log("User cancelled request, calling /stop endpoint");
    stopCugaAgent(threadId);
  };
  
  if (requestOptions.signal) {
    requestOptions.signal.addEventListener("abort", abortHandler);
  }
  
  // Show Carbon's built-in loading indicator while waiting for the first SSE event.
  // The shell message is intentionally deferred until the first event arrives so that
  // Carbon doesn't suppress the indicator upon seeing an immediate addMessageChunk call.
  console.log("[Loading indicator ON — waiting for first SSE event");
  instance.updateIsMessageLoadingCounter("increase");
  let shellMessageCreated = false;
  let loadingIndicatorCleared = false;
  const collectedSteps: ReasoningStep[] = [];
  const clearLoadingIndicator = () => {
    if (!loadingIndicatorCleared) {
      loadingIndicatorCleared = true;
      console.log("[Loading indicator OFF — first SSE event received");
      instance.updateIsMessageLoadingCounter("decrease");
    }
  };
  const ensureShellMessage = () => {
    if (!shellMessageCreated) {
      shellMessageCreated = true;
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
    }
  };
  const finalizeStream = (text: string, steps: ReasoningStep[] = collectedSteps) => {
    if (!shellMessageCreated) return;
    const completeItem = {
      response_type: MessageResponseTypes.TEXT,
      text,
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
          ...(steps.length > 0 ? { reasoning: { steps } } : {}),
          response_user_profile: RESPONSE_USER_PROFILE,
        },
      },
    });
  };
  const handleCancellation = () => {
    clearLoadingIndicator();
    if (shellMessageCreated) {
      finalizeStream("Request was cancelled.");
    } else {
      instance.messaging.addMessage({
        output: {
          generic: [{
            response_type: MessageResponseTypes.TEXT,
            text: "Request was cancelled.",
          }],
        },
      });
    }
  };

  const baseUrl = api.getApiBaseUrl();

  try {
    console.log(`Connecting to CUGA backend at: ${baseUrl}/stream`);
    console.log(`Thread ID: ${threadId}`);
    console.log(`User message: ${userMessage}`);
    console.log(`Use Draft: ${useDraft}`);

    let accumulatedText = "";
    let currentStepTitle = "";
    let currentStepContent = "";
    let currentSubAgentName = "";
    const subAgentAccumulators: Map<string, Array<{ title: string; content: string }>> = new Map();
    // Track collectedSteps index per spawnKey so A→B→A interleaving replaces rather than duplicates.
    const subAgentStepIndexes: Map<string, number> = new Map();

    const flushPendingStep = () => {
      if (!currentStepTitle || !currentStepContent) return;
      const step = createReasoningStep(currentStepTitle, currentStepContent);
      if (currentSubAgentName) {
        const existingIdx = subAgentStepIndexes.get(currentSubAgentName);
        if (existingIdx !== undefined) {
          collectedSteps[existingIdx] = step;
        } else {
          subAgentStepIndexes.set(currentSubAgentName, collectedSteps.length);
          collectedSteps.push(step);
        }
      } else {
        collectedSteps.push(step);
      }
      currentStepTitle = "";
      currentStepContent = "";
    };

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

    const response = await api.postStream(
      actionResponse || {
        query: userMessage,
        ...(attachmentSnapshot && attachmentSnapshot.length > 0
          ? { attachments: attachmentSnapshot }
          : {}),
      },
      {
        threadId,
        useDraft,
        disableHistory,
        signal: requestOptions.signal,
      }
    );

    console.log(`Response status: ${response.status}`);

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`HTTP error response:`, errorText);
      throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`);
    }

    // Process the stream
    for await (const event of parseCugaStream(response)) {
      // Check if cancelled
      if (requestOptions.signal?.aborted) {
        break;
      }

      // First event means backend is responding — create shell and hide loading indicator
      ensureShellMessage();
      clearLoadingIndicator();

      console.log("CUGA Event:", event);

      switch (event.name) {
        case "CodeAgent":
          flushPendingStep();
          currentSubAgentName = "";

          const codeAgentResult = parseReasoningStepContent(event.data || "", "Code Agent");
          currentStepTitle = codeAgentResult.title;
          currentStepContent = codeAgentResult.content;

          console.log(`Code Agent step, content: ${currentStepContent}`);

          if (currentStepContent) {
            instance.messaging.addMessageChunk({
              partial_item: {
                response_type: MessageResponseTypes.TEXT,
                text: " ",
                streaming_metadata: { id: "text-stream", cancellable: true },
              },
              partial_response: {
                message_options: { reasoning: { steps: [...collectedSteps, createReasoningStep(currentStepTitle, currentStepContent)] }, response_user_profile: RESPONSE_USER_PROFILE },
              },
              streaming_metadata: { response_id: responseID },
            } as StreamChunk);
          }
          break;

        case "SubAgent": {
          let subAgentData: any = {};
          try { subAgentData = typeof event.data === "string" ? JSON.parse(event.data) : (event.data || {}); } catch { /* ignore */ }

          const agentName = subAgentData.agent_name || "sub-agent";
          const spawnKey = subAgentData.spawn_id || agentName;
          const agentLabel = subAgentData.spawn_id
            ? `Sub-Agent: ${agentName} (${String(subAgentData.spawn_id).slice(0, 12)})`
            : `Sub-Agent: ${agentName}`;

          // Switching to a different sub-agent or entering for the first time — flush pending step
          if (currentSubAgentName !== spawnKey) {
            flushPendingStep();
            currentSubAgentName = spawnKey;
            if (!subAgentAccumulators.has(spawnKey)) {
              subAgentAccumulators.set(spawnKey, []);
            }
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
            const parsed = parseReasoningStepContent(event.data || "", "");
            newIteration = {
              title: subAgentData.code ? "Code" : "Execution Output",
              content: parsed.content,
            };
          } else {
            break;
          }

          iterations.push(newIteration);

          // Each iteration becomes a <details> block — gives a second-level dropdown inside the step
          const nestedContent = iterations
            .map(({ title, content }) => `<details>\n<summary>${title}</summary>\n\n${content}\n\n</details>`)
            .join("\n\n");

          // Keep sub-agent steps in collectedSteps (replace-in-place) so A→B→A
          // interleaving never appends a second copy of A.
          const updatedStep = createReasoningStep(agentLabel, nestedContent);
          const existingIdx = subAgentStepIndexes.get(spawnKey);
          if (existingIdx !== undefined) {
            collectedSteps[existingIdx] = updatedStep;
          } else {
            subAgentStepIndexes.set(spawnKey, collectedSteps.length);
            collectedSteps.push(updatedStep);
          }
          currentStepTitle = "";
          currentStepContent = "";

          instance.messaging.addMessageChunk({
            partial_item: {
              response_type: MessageResponseTypes.TEXT,
              text: " ",
              streaming_metadata: { id: "text-stream", cancellable: true },
            },
            partial_response: {
              message_options: {
                reasoning: { steps: [...collectedSteps] },
                response_user_profile: RESPONSE_USER_PROFILE,
              },
            },
            streaming_metadata: { response_id: responseID },
          } as StreamChunk);
          break;
        }

        case "CodeAgent_Reasoning":
        case "Thinking":
        case "Planning":
        case "Analyzing":
          flushPendingStep();
          currentSubAgentName = "";

          const reasoningResult = parseReasoningStepContent(
            event.data || "",
            event.name.replace(/_/g, " ")
          );
          currentStepTitle = reasoningResult.title;
          currentStepContent = reasoningResult.content;

          console.log(`Reasoning step: ${currentStepTitle}, content: ${currentStepContent}`);

          if (currentStepContent) {
            instance.messaging.addMessageChunk({
              partial_item: {
                response_type: MessageResponseTypes.TEXT,
                text: " ",
                streaming_metadata: { id: "text-stream", cancellable: true },
              },
              partial_response: {
                message_options: { reasoning: { steps: [...collectedSteps, createReasoningStep(currentStepTitle, currentStepContent)] }, response_user_profile: RESPONSE_USER_PROFILE },
              },
              streaming_metadata: { response_id: responseID },
            } as StreamChunk);
          }
          break;

        case "ToolCall":
        case "Action":
          const toolData = typeof event.data === "string" ? event.data : JSON.stringify(event.data, null, 2);
          collectedSteps.push(
            createReasoningStep(event.name, `\`\`\`json\n${toolData}\n\`\`\``)
          );

          instance.messaging.addMessageChunk({
            partial_item: {
              response_type: MessageResponseTypes.TEXT,
              text: " ",
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
          let answerSources: MessageSource[] = [];
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
            answerSources = parsed.sources as MessageSource[];
          } else if (!answerText) {
            answerText = event.data?.answer || JSON.stringify(event.data);
          }

          accumulatedText = answerText;

          flushPendingStep();

          console.log(`Finalizing with ${collectedSteps.length} reasoning steps`);

          // The final_response id doubles as the message key stamped on each
          // <cuga-cite> chip so the host can resolve which message's sources a
          // click belongs to (chips only carry `n`, which repeats per message).
          const answerCompleteItem = {
            response_type: MessageResponseTypes.TEXT,
            text: injectCitations(accumulatedText, answerSources, responseID),
            streaming_metadata: { id: "text-stream" },
          };

          instance.messaging.addMessageChunk({
            complete_item: answerCompleteItem,
            streaming_metadata: { response_id: responseID },
          });

          const answerGenericItems: any[] = [answerCompleteItem];

          if (answerSources.length > 0) {
            const sourcesItem = {
              response_type: MessageResponseTypes.USER_DEFINED,
              user_defined: {
                type: "cuga_sources",
                message_key: responseID,
                sources: answerSources,
              },
              streaming_metadata: { id: "cuga-sources" },
            };

            instance.messaging.addMessageChunk({
              complete_item: sourcesItem,
              streaming_metadata: { response_id: responseID },
            } as StreamChunk);

            answerGenericItems.push(sourcesItem);
          }

          const finalResponse: StreamChunk = {
            final_response: {
              id: responseID,
              output: { generic: answerGenericItems },
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
          flushPendingStep();

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
              createReasoningStep(event.name, stepContent)
            );

            instance.messaging.addMessageChunk({
              partial_item: {
                response_type: MessageResponseTypes.TEXT,
                text: " ",
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
    if (requestOptions.signal?.aborted) {
      handleCancellation();
    } else {
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
      handleCancellation();
    } else {
      ensureShellMessage();
      clearLoadingIndicator();
      const url = api.getApiBaseUrl();
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
    clearLoadingIndicator();
    // Clean up abort listener
    if (requestOptions.signal) {
      requestOptions.signal.removeEventListener("abort", abortHandler);
    }
  }
}
