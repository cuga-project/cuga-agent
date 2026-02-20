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
  UserType,
  type ReasoningStep,
  type StreamChunk,
} from "@carbon/ai-chat";

// Button kind constants (matching Carbon Design System)
const BUTTON_KIND = {
  PRIMARY: 'primary',
  SECONDARY: 'secondary',
  TERTIARY: 'tertiary',
  GHOST: 'ghost',
  DANGER: 'danger',
  DANGER_TERTIARY: 'danger--tertiary',
  DANGER_GHOST: 'danger--ghost',
} as const;

const RESPONSE_USER_PROFILE = {
  id: "cuga-agent",
  nickname: "CUGA",
  user_type: UserType.BOT,
  profile_picture_url: "https://avatars.githubusercontent.com/u/230847519?s=200&v=4",
};

// CUGA backend endpoint - use window location for dynamic backend URL
const CUGA_BACKEND_URL = typeof window !== 'undefined'
  ? `${window.location.protocol}//${window.location.hostname}:7860`
  : "http://localhost:7860";

// Import thread ID management from CarbonChat
import { getOrCreateThreadId } from './CarbonChat';

// Function to call CUGA /stop endpoint
async function stopCugaAgent(threadId: string) {
  try {
    console.log(`Calling /stop for thread: ${threadId}`);
    const response = await fetch(`${CUGA_BACKEND_URL}/stop`, {
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
          // Add previous step if exists
          if (currentStepTitle && currentStepContent) {
            collectedSteps.push({
              title: currentStepTitle,
              content: currentStepContent,
              open_state: ReasoningStepOpenState.OPEN,
            });
          }
          
          currentStepTitle = "Code Agent";
          
          // Try to parse as JSON and extract code or execution_output
          try {
            const parsed = JSON.parse(event.data);
            if (parsed.code) {
              // Format code as markdown
              currentStepContent = `\`\`\`python\n${parsed.code}\n\`\`\``;
              if (parsed.summary) {
                currentStepContent = `${parsed.summary}\n\n${currentStepContent}`;
              }
            } else if (parsed.execution_output) {
              // Format execution output as markdown
              currentStepContent = `**Execution Output:**\n\`\`\`\n${parsed.execution_output}\n\`\`\``;
              if (parsed.summary) {
                currentStepContent = `${parsed.summary}\n\n${currentStepContent}`;
              }
            } else {
              // Use the whole JSON formatted
              currentStepContent = `\`\`\`json\n${JSON.stringify(parsed, null, 2)}\n\`\`\``;
            }
          } catch {
            // Not JSON, use as-is
            currentStepContent = event.data || "";
          }
          
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
          // Add reasoning step
          if (currentStepTitle && currentStepContent) {
            collectedSteps.push({
              title: currentStepTitle,
              content: currentStepContent,
              open_state: ReasoningStepOpenState.OPEN,
            });
          }
          
          // Use event name as title, data as content
          currentStepTitle = event.name.replace(/_/g, " "); // Make it readable
          currentStepContent = event.data || "";
          
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
          // Add tool/action as reasoning step
          const toolData = typeof event.data === "string" ? event.data : JSON.stringify(event.data, null, 2);
          collectedSteps.push({
            title: event.name,
            content: `\`\`\`json\n${toolData}\n\`\`\``,
            open_state: ReasoningStepOpenState.CLOSE,
          });
          
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
          
          // Parse the answer - it may be JSON with data/variables/policies
          let answerText = accumulatedText || ""; // Start with any accumulated text from FinalAnswerAgent
          let policyInfo = null;
          
          if (typeof event.data === "string") {
            try {
              const parsed = JSON.parse(event.data);
              
              // Check if parsed.data is a string that needs further parsing
              let innerData = parsed.data;
              if (typeof innerData === "string") {
                try {
                  innerData = JSON.parse(innerData);
                } catch {
                  // If inner parsing fails, use as-is
                }
              }
              
              // Check if this is a policy event (either in outer or inner data)
              const policyData = innerData?.type === "policy" ? innerData :
                                 (parsed.active_policies && parsed.active_policies.length > 0 ? parsed.active_policies[0] : null);
              
              if (policyData && (policyData.policy_blocked || policyData.policy_matched)) {
                // Extract policy information
                const isPlaybook = policyData.policy_type === "playbook";
                const playbookContent = policyData.metadata?.playbook_guidance || policyData.metadata?.playbook_content || policyData.content;
                
                policyInfo = {
                  response_content: policyData.metadata?.response_content || policyData.content || (isPlaybook ? "" : "This action is not allowed."),
                  policy_reasoning: policyData.metadata?.policy_reasoning || "Policy triggered",
                  policy_type: policyData.policy_type || policyData.metadata?.policy_type || "unknown",
                  policy_name: policyData.policy_name || policyData.metadata?.policy_name || "Policy",
                  is_playbook: isPlaybook,
                  playbook_content: playbookContent,
                };
                
                // Format the answer based on policy type
                if (policyData.policy_type === "tool_approval" && policyData.metadata?.approval_required) {
                  // Tool approval - create interactive card
                  const approvalMsg = policyData.metadata.approval_message || "This tool requires your approval before execution.";
                  const toolsList = policyData.metadata.required_tools || [];
                  const appsList = policyData.metadata.required_apps || [];
                  const codePreview = policyData.metadata.code_preview || [];
                  
                  // Create card body
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
                  
                  // Add tools list if available
                  if (toolsList.length > 0) {
                    const toolsText = toolsList.includes("*")
                      ? "**Tools requiring approval:** All tools"
                      : `**Tools requiring approval:** ${toolsList.join(', ')}`;
                    cardBody.push({
                      response_type: MessageResponseTypes.TEXT,
                      text: toolsText,
                    });
                  }
                  
                  // Add apps list if available
                  if (appsList.length > 0) {
                    cardBody.push({
                      response_type: MessageResponseTypes.TEXT,
                      text: `**Apps requiring approval:** ${appsList.join(', ')}`,
                    });
                  }
                  
                  // Add code preview if available
                  if (codePreview.length > 0) {
                    cardBody.push({
                      response_type: MessageResponseTypes.TEXT,
                      text: "**Code Preview:**",
                    });
                    cardBody.push({
                      response_type: MessageResponseTypes.TEXT,
                      text: `\`\`\`python\n${codePreview.join('\n')}\n\`\`\``,
                    });
                  }
                  
                  // Add the card with approval buttons
                  instance.messaging.addMessage({
                    output: {
                      generic: [
                        {
                          body: cardBody,
                          footer: [
                            {
                              kind: BUTTON_KIND.PRIMARY as any,
                              label: "Approve & Execute",
                              button_type: ButtonItemType.CUSTOM_EVENT as any,
                              response_type: MessageResponseTypes.BUTTON,
                              custom_event_name: "tool_approval_response",
                              user_defined: {
                                approved: true,
                                thread_id: threadId,
                              },
                            },
                            {
                              kind: BUTTON_KIND.DANGER as any,
                              label: "Deny",
                              button_type: ButtonItemType.CUSTOM_EVENT as any,
                              response_type: MessageResponseTypes.BUTTON,
                              custom_event_name: "tool_approval_response",
                              user_defined: {
                                approved: false,
                                thread_id: threadId,
                              },
                            },
                          ],
                          response_type: MessageResponseTypes.CARD,
                        },
                      ],
                    },
                  });
                  
                  // Don't finalize yet - wait for user response
                  return;
                } else if (isPlaybook) {
                  // For playbooks: use accumulated answer from FinalAnswerAgent, then show policy info only
                  if (!answerText) {
                    // If no FinalAnswerAgent answer yet, use a default message
                    answerText = "Following the playbook to guide you through this process.";
                  }
                  answerText += "\n\n";
                  answerText += "> ###### 📖 *Playbook Information*\n";
                  answerText += ">\n";
                  answerText += `> *Playbook Name:* **${policyInfo.policy_name}**\n`;
                  answerText += ">\n";
                  answerText += `> *Reasoning:* ${policyInfo.policy_reasoning}`;
                } else {
                  // For blocked policies, show response content and policy info
                  answerText = policyInfo.response_content;
                  answerText += "\n\n";
                  answerText += "> ###### 🛡️ *Policy Information*\n";
                  answerText += ">\n";
                  answerText += `> *Policy Name:* **${policyInfo.policy_name}**\n`;
                  answerText += ">\n";
                  answerText += `> *Policy Type:* \`${policyInfo.policy_type}\`\n`;
                  answerText += ">\n";
                  answerText += `> *Reasoning:* ${policyInfo.policy_reasoning}`;
                }
              } else {
                // No policy - use accumulated text or extract from data
                if (!answerText) {
                  answerText = typeof innerData === "string" ? innerData : (parsed.data || event.data);
                }
              }
            } catch (e) {
              console.error("Error parsing Answer event:", e);
              // If not JSON, use as-is or accumulated text
              if (!answerText) {
                answerText = event.data;
              }
            }
          } else {
            if (!answerText) {
              answerText = event.data?.answer || JSON.stringify(event.data);
            }
          }
          
          accumulatedText = answerText; // Use the answer directly
          
          // Finalize the message immediately after Answer
          if (currentStepTitle && currentStepContent) {
            collectedSteps.push({
              title: currentStepTitle,
              content: currentStepContent,
            });
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
          // Finalize the message
          if (currentStepTitle) {
            collectedSteps.push({
              title: currentStepTitle,
              content: currentStepContent,
            });
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
          // Handle other event types as reasoning steps
          if (event.data) {
            const stepContent = typeof event.data === "string" ? event.data : JSON.stringify(event.data);
            collectedSteps.push({
              title: event.name,
              content: stepContent,
              open_state: ReasoningStepOpenState.CLOSE,
            });
            
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
      instance.messaging.addMessage({
        output: {
          generic: [{
            response_type: MessageResponseTypes.TEXT,
            text: `Error: ${error.message || "Failed to connect to CUGA backend"}`,
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