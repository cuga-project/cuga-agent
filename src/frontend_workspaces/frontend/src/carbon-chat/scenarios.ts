/*
 *  Copyright IBM Corp. 2025
 *
 *  This source code is licensed under the Apache-2.0 license found in the
 *  LICENSE file in the root directory of this source tree.
 *
 *  @license
 */

import type { ChatInstance } from "@carbon/ai-chat";
import {
  MessageResponseTypes,
  ReasoningStepOpenState,
  type MessageResponseOptions,
  type ReasoningStep,
  type StreamChunk,
  ChainOfThoughtStepStatus,
  type ChainOfThoughtStep,
} from "@carbon/ai-chat";

export const scenarios = {
  "Reasoning steps": {
    text: "Reasoning steps will auto-open while the model provides reasoning steps, and then auto-open the active reasoning step.\n\nIt will then hide once user-facing content starts streaming back.\n\nThis is the default behavior.",
  },
  "Controlled reasoning steps": {
    text: "This *controlled* reasoning example is set to keep all reasoning steps closed by default with a loading indicator visible instead.",
  },
  "Reasoning content": {
    text: "Reasoning content can stream in as a single trace without individual steps. Use this when you want a long-form rationale instead of expandable steps.",
  },
  "Chain of thought": {
    text: "Chain of thought is best suited for raw debugging or tool-call traces. In most cases you should use reasoning steps instead, because they are more user-friendly while still exposing the assistant's thinking.",
  },
};

export type ScenarioKey = keyof typeof scenarios;

export const scenarioOptions = Object.keys(scenarios).map((key) => {
  return { label: key, value: key };
});

const REASONING_STEPS = [
  {
    title: "Read the user request",
    content:
      "Scanning the prompt and preparing to choose a relevant example response.",
  },
  {
    title: "Pick a scenario",
    content:
      "Selecting the reasoning flow that matches the provided dropdown choice.",
  },
  {
    title: "Considering options",
  },
  {
    title: "Fetching data",
    content: `Calling the retrieval service for supporting facts:
\`\`\`json
{
  "endpoint": "/mock/retrieve",
  "results": [
    { "id": "doc-123", "score": 0.91 },
    { "id": "doc-456", "score": 0.88 }
  ]
}
\`\`\``,
  },
  {
    title: "Prepare the response",
  },
];

const REASONING_TRACE_CONTENT = REASONING_STEPS.map(
  (step) => step.content || step.title,
).join("\n\n");

const CHAIN_OF_THOUGHT_STEPS: ChainOfThoughtStep[] = [
  {
    title: "Vector search",
    description: "Retrieved similar documents.",
    tool_name: "semantic_search",
    request: { args: { query: "user request" } },
    response: { content: ["doc-1", "doc-2", "doc-3"] },
    status: ChainOfThoughtStepStatus.SUCCESS,
  },
  {
    title: "Summarize",
    description: "Summarizing the retrieved documents.",
    tool_name: "summarize",
    request: { args: { documents: ["doc-1", "doc-2", "doc-3"] } },
    response: { content: "Summary of the retrieved context." },
    status: ChainOfThoughtStepStatus.SUCCESS,
  },
  {
    title: "Generate response",
    description: "Drafting the user-facing reply.",
    tool_name: "generate",
    request: { args: { summary: "Summary of the retrieved context." } },
    response: { content: "Final chain of thought drafted." },
    status: ChainOfThoughtStepStatus.SUCCESS,
  },
];

const TEXT_STREAM_ID = "text-1";
const WORD_DELAY = 40;
const REASONING_STEP_DELAY = 3000;

async function sleep(milliseconds: number) {
  await new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

function createShellMessage(
  instance: ChatInstance,
  responseID: string,
  messageOptions?: MessageResponseOptions,
) {
  // Use streaming chunk to seed the message so reducers create the shell before steps stream in.
  instance.messaging.addMessageChunk({
    partial_item: {
      response_type: MessageResponseTypes.TEXT,
      text: "",
      streaming_metadata: { id: TEXT_STREAM_ID },
    },
    partial_response: {
      message_options: messageOptions,
    },
    streaming_metadata: { response_id: responseID },
  });
}

function pushMessageOptions(
  instance: ChatInstance,
  responseID: string,
  messageOptions: MessageResponseOptions,
) {
  instance.messaging.addMessageChunk({
    partial_item: {
      response_type: MessageResponseTypes.TEXT,
      text: "",
      streaming_metadata: { id: TEXT_STREAM_ID, cancellable: true },
    },
    partial_response: { message_options: messageOptions },
    streaming_metadata: { response_id: responseID },
  } as StreamChunk);
}

async function streamText(
  instance: ChatInstance,
  responseID: string,
  text: string,
  signal?: AbortSignal,
  onStart?: () => void,
  finalMessageOptions?: MessageResponseOptions,
) {
  const words = text.split(" ");
  let isCanceled = false;
  const timeouts: number[] = [];

  const abortHandler = () => {
    isCanceled = true;
    timeouts.forEach((timeoutId) => clearTimeout(timeoutId));
  };
  signal?.addEventListener("abort", abortHandler);

  onStart?.();

  try {
    words.forEach((word, index) => {
      const timeoutId = setTimeout(() => {
        if (!isCanceled) {
          instance.messaging.addMessageChunk({
            partial_item: {
              response_type: MessageResponseTypes.TEXT,
              text: `${word} `,
              streaming_metadata: { id: TEXT_STREAM_ID, cancellable: true },
            },
            streaming_metadata: { response_id: responseID },
          });
        }
      }, index * WORD_DELAY);
      timeouts.push(timeoutId as unknown as number);
    });

    await sleep(words.length * WORD_DELAY);

    if (!isCanceled) {
      const completeItem = {
        response_type: MessageResponseTypes.TEXT,
        text,
        streaming_metadata: { id: TEXT_STREAM_ID },
      };
      instance.messaging.addMessageChunk({
        complete_item: completeItem,
        streaming_metadata: { response_id: responseID },
      });

      const finalResponse: StreamChunk = {
        final_response: {
          id: responseID,
          output: { generic: [completeItem] },
        },
      };

      if (finalMessageOptions) {
        finalResponse.final_response.message_options = finalMessageOptions;
      }

      instance.messaging.addMessageChunk(finalResponse);
    }
  } finally {
    signal?.removeEventListener("abort", abortHandler);
  }
}

async function streamReasoningContentFirst(
  instance: ChatInstance,
  responseID: string,
  content: string,
  signal?: AbortSignal,
) {
  const tokens = content.match(/\S+\s*/g) ?? [content];
  let isCanceled = false;
  const abortHandler = () => {
    isCanceled = true;
  };

  signal?.addEventListener("abort", abortHandler);

  try {
    let partial = "";
    for (const token of tokens) {
      if (isCanceled) {
        break;
      }
      partial += token;
      pushMessageOptions(instance, responseID, {
        reasoning: { content: partial },
      });
      await sleep(WORD_DELAY);
    }
  } finally {
    signal?.removeEventListener("abort", abortHandler);
  }

  return !isCanceled;
}

export async function runReasoningStepsScenario(
  instance: ChatInstance,
  signal?: AbortSignal,
) {
  const responseID = crypto.randomUUID();
  const collectedSteps: ReasoningStep[] = [];

  createShellMessage(instance, responseID, { reasoning: { steps: [] } });

  for (const step of REASONING_STEPS) {
    collectedSteps.push(step);
    pushMessageOptions(instance, responseID, {
      reasoning: { steps: collectedSteps },
    });
    await sleep(REASONING_STEP_DELAY);
  }

  await streamText(
    instance,
    responseID,
    scenarios["Reasoning steps"].text,
    signal,
    undefined,
    { reasoning: { steps: collectedSteps } },
  );
}

export async function runControlledReasoningScenario(
  instance: ChatInstance,
  signal?: AbortSignal,
) {
  const responseID = crypto.randomUUID();

  const collectedSteps: ReasoningStep[] = [];

  createShellMessage(instance, responseID, { reasoning: { steps: [] } });

  instance.updateIsMessageLoadingCounter("increase", "Thinking...");

  for (const step of REASONING_STEPS) {
    collectedSteps.push({
      ...step,
      open_state: ReasoningStepOpenState.OPEN,
    });
    pushMessageOptions(instance, responseID, {
      reasoning: {
        open_state: ReasoningStepOpenState.CLOSE,
        steps: collectedSteps,
      },
    });
    instance.updateIsMessageLoadingCounter(undefined, `${step.title}...`);
    await sleep(REASONING_STEP_DELAY);
  }

  instance.updateIsMessageLoadingCounter("decrease");

  await streamText(
    instance,
    responseID,
    scenarios["Controlled reasoning steps"].text,
    signal,
    undefined,
    { reasoning: { steps: collectedSteps } },
  );
}

export async function runReasoningContentScenario(
  instance: ChatInstance,
  signal?: AbortSignal,
) {
  const responseID = crypto.randomUUID();

  createShellMessage(instance, responseID, { reasoning: { content: "" } });

  const completed = await streamReasoningContentFirst(
    instance,
    responseID,
    REASONING_TRACE_CONTENT,
    signal,
  );

  if (!completed) {
    return;
  }

  await streamText(
    instance,
    responseID,
    scenarios["Reasoning content"].text,
    signal,
    undefined,
    { reasoning: { content: REASONING_TRACE_CONTENT } },
  );
}

export async function runChainOfThoughtScenario(
  instance: ChatInstance,
  signal?: AbortSignal,
) {
  const responseID = crypto.randomUUID();
  createShellMessage(instance, responseID, { chain_of_thought: [] });

  await streamText(
    instance,
    responseID,
    scenarios["Chain of thought"].text,
    signal,
    undefined,
    { chain_of_thought: CHAIN_OF_THOUGHT_STEPS },
  );
}

export async function runScenario(
  scenario: ScenarioKey,
  instance: ChatInstance,
  signal?: AbortSignal,
) {
  switch (scenario) {
    case "Reasoning steps":
      await runReasoningStepsScenario(instance, signal);
      return;
    case "Controlled reasoning steps":
      await runControlledReasoningScenario(instance, signal);
      return;
    case "Reasoning content":
      await runReasoningContentScenario(instance, signal);
      return;
    case "Chain of thought":
      await runChainOfThoughtScenario(instance, signal);
      return;
    default:
      return;
  }
}