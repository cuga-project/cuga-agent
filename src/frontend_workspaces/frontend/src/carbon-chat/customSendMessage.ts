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
  OptionItemPreference,
} from "@carbon/ai-chat";

import {
  ScenarioKey,
  runChainOfThoughtScenario,
  runControlledReasoningScenario,
  runReasoningContentScenario,
  runReasoningStepsScenario,
  scenarioOptions,
  scenarios,
} from "./scenarios";

const WELCOME_TEXT = `This example shows how to mock reasoning steps and chain of thought in Carbon AI Chat.

Choose a scenario from the dropdown below to see how the UI responds to different mocked streaming patterns.`;

function isScenarioKey(value: string): value is ScenarioKey {
  return Object.prototype.hasOwnProperty.call(scenarios, value);
}

function sendWelcome(instance: ChatInstance) {
  instance.messaging.addMessage({
    output: {
      generic: [
        {
          response_type: MessageResponseTypes.TEXT,
          text: WELCOME_TEXT,
        },
        {
          response_type: MessageResponseTypes.OPTION,
          title: "Pick a reasoning demo",
          description:
            "Each option shows a different mocked flow. You can edit the text for each scenario in src/scenarios.ts.",
          preference: OptionItemPreference.DROPDOWN,
          options: scenarioOptions.map((option) => ({
            label: option.label,
            value: { input: { text: option.value } },
          })),
        },
      ],
    },
  });
}

async function customSendMessage(
  request: MessageRequest,
  requestOptions: CustomSendMessageOptions,
  instance: ChatInstance,
) {
  const trimmedText = request.input.text?.trim() ?? "";

  if (isScenarioKey(trimmedText)) {
    switch (trimmedText) {
      case "Reasoning steps":
        await runReasoningStepsScenario(instance, requestOptions.signal);
        return;
      case "Controlled reasoning steps":
        await runControlledReasoningScenario(instance, requestOptions.signal);
        return;
      case "Reasoning content":
        await runReasoningContentScenario(instance, requestOptions.signal);
        return;
      case "Chain of thought":
        await runChainOfThoughtScenario(instance, requestOptions.signal);
        return;
      default:
        break;
    }
  }

  sendWelcome(instance);
}

export { customSendMessage };