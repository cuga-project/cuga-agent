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
  CustomSendMessageOptions,
  MessageRequest,
  MessageResponseTypes,
  ReasoningStepOpenState,
  type ReasoningStep,
  type StreamChunk,
} from "@carbon/ai-chat";

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
) {
  const userMessage = request.input.text?.trim() ?? "";
  
  if (!userMessage) {
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
      message_options: { reasoning: { steps: [] } },
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
    
    // Call CUGA backend /stream endpoint
    const response = await fetch(`${CUGA_BACKEND_URL}/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ query: userMessage }),
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
                message_options: { reasoning: { steps: [...collectedSteps, { title: currentStepTitle, content: currentStepContent }] } },
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
                message_options: { reasoning: { steps: [...collectedSteps, { title: currentStepTitle, content: currentStepContent }] } },
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
              message_options: { reasoning: { steps: collectedSteps } },
            },
            streaming_metadata: { response_id: responseID },
          } as StreamChunk);
          break;

        case "FinalAnswerAgent":
          // Skip FinalAnswerAgent - we'll handle the Answer event instead
          console.log("Skipping FinalAnswerAgent event, waiting for Answer...");
          break;

        case "Answer":
        case "FinalAnswer":
          console.log("Received Answer event, finalizing message...");
          
          // Parse the answer - it may be JSON with data/variables/policies
          let answerText = "";
          if (typeof event.data === "string") {
            try {
              const parsed = JSON.parse(event.data);
              // Extract just the data field if it's a structured response
              answerText = parsed.data || event.data;
            } catch {
              // If not JSON, use as-is
              answerText = event.data;
            }
          } else {
            answerText = event.data?.answer || JSON.stringify(event.data);
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
            finalResponse.final_response.message_options = { reasoning: { steps: collectedSteps } };
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
              message_options: collectedSteps.length > 0 ? { reasoning: { steps: collectedSteps } } : undefined,
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
                message_options: { reasoning: { steps: collectedSteps } },
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
          message_options: collectedSteps.length > 0 ? { reasoning: { steps: collectedSteps } } : undefined,
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