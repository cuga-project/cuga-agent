import { ChatInstance, CustomSendMessageOptions, GenericItem, MessageRequest, StreamChunk } from "@carbon/ai-chat";
import { fetchStreamingData, streamViaBackground } from "./StreamingWorkflow";
import { setCardManagerState, resetCardManagerState } from "./renderUserDefinedResponse";
import { randomUUID } from "./uuid";
import { RESPONSE_USER_PROFILE } from "./constants";

const WELCOME_TEXT = `<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; padding: 8px 12px; color: white; box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3); margin: 8px 0; position: relative; overflow: hidden; width: 100%; min-width: 0;"><div style="position: absolute; top: -10px; right: -10px; width: 20px; height: 20px; background: rgba(255, 255, 255, 0.1); border-radius: 50%; animation: float 3s ease-in-out infinite;"></div><div style="position: relative; z-index: 2; display: flex; align-items: center; gap: 8px; width: 100%; min-width: 0; flex-wrap: wrap;"><div style="flex: 1; min-width: 0;"><h1 style="font-size: clamp(0.9rem, 2.5vw, 1.2rem); font-weight: 700; margin: 0 0 2px 0; background: linear-gradient(45deg, #fff, #e0e7ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">👋 I'm CUGA</h1><p style="font-size: clamp(0.6rem, 2vw, 0.8rem); margin: 0; opacity: 0.9; font-weight: 300; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Your Digital Agent</p></div><div style="text-align: right; min-width: 0; flex-shrink: 0;"><p style="margin: 0; font-size: clamp(0.5rem, 1.5vw, 0.7rem); font-weight: 500; opacity: 0.9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">✨ Just ask!</p></div></div></div><style>@keyframes float { 0%, 100% { transform: translateY(0px) rotate(0deg); } 50% { transform: translateY(-5px) rotate(180deg); } } @media (max-width: 200px) { .welcome-container { flex-direction: column !important; align-items: flex-start !important; gap: 4px !important; } .welcome-container .features { justify-content: flex-start !important; } }</style>`;

const TEXT =
  `Lorem ipsum odor amet, consectetuer adipiscing elit. \`Inline Code Venenatis\` aliquet non platea elementum morbi porta accumsan. Tortor libero consectetur dapibus volutpat porta vestibulum.

Quam scelerisque platea ridiculus sem placerat pharetra sed. Porttitor per massa venenatis fusce fusce ad cras. Vel congue semper, rhoncus tempus nisl nam. Purus molestie tristique diam himenaeos sapien lacus.

| Lorem        | Ipsum      | Odor    | Amet      |
|--------------|------------|---------|-----------|
| consectetuer | adipiscing | elit    | Venenatis |
| 0            | 1          | 2       | 3         |
| bibendum     | enim       | blandit | quis      |


- consectetuer
- adipiscing
- elit
- Venenatis

` +
  "\n```python\n" +
  `import random

def generate_lorem_ipsum(paragraphs=1):
    # Base words for Lorem Ipsum
    lorem_words = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
        "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
        "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure "
        "dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
        "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt "
        "mollit anim id est laborum."
    ).split()
    
    # Function to generate a random sentence
    def random_sentence():
        sentence_length = random.randint(4, 12)
        sentence = random.sample(lorem_words, sentence_length)
        return " ".join(sentence).capitalize() + "."
    
    # Function to generate a paragraph
    def random_paragraph():
        sentence_count = random.randint(3, 6)
        return " ".join(random_sentence() for _ in range(sentence_count))
    
    # Generate the requested number of paragraphs
    return "\\n\\n".join(random_paragraph() for _ in range(paragraphs))

# Example usage
print(generate_lorem_ipsum(2))  # Generates 2 paragraphs of Lorem Ipsum text
` +
  "\n\n```";

const WORD_DELAY = 40;

// eslint-disable-next-line @typescript-eslint/no-unused-vars
async function doFakeTextStreaming(instance: ChatInstance) {
  const responseID = randomUUID();
  const words = TEXT.split(" ");

  words.forEach((word, index) => {
    setTimeout(() => {
      instance.messaging.addMessageChunk({
        partial_item: {
          response_type: "text",
          text: `${word} `,
          streaming_metadata: {
            id: "1",
          },
        } as GenericItem,
        streaming_metadata: {
          response_id: responseID,
        },
      });
    }, index * WORD_DELAY);
  });

  await sleep(words.length * WORD_DELAY);

  const completeItem = {
    response_type: "text",
    text: `${TEXT}\n\nMore stuff on the end when adding as a complete item.`,
    streaming_metadata: {
      id: "1",
    },
  };
  instance.messaging.addMessageChunk({
    complete_item: completeItem,
    streaming_metadata: {
      response_id: responseID,
    },
  } as StreamChunk);

  const finalResponse = {
    id: responseID,
    output: {
      generic: [completeItem],
    },
  };

  instance.messaging.addMessageChunk({
    final_response: finalResponse,
  } as StreamChunk);
}

async function sleep(milliseconds: number) {
  await new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

async function customStreamMessage(
  request: MessageRequest,
  _requestOptions: CustomSendMessageOptions,
  instance: ChatInstance
) {
  if (request.input.text === "") {
    instance.messaging.addMessage({
      message_options: {
        response_user_profile: RESPONSE_USER_PROFILE
      },
      output: {
        generic: [
          {
            response_type: "text",
            text: WELCOME_TEXT,
          } as GenericItem,
        ],
      },
    });
  } else {
    switch (request.input.text) {
      default:
        await streamViaBackground(instance, request.input.text || "");
        break;
    }
  }
}

async function customSendMessage(
  request: MessageRequest,
  _requestOptions: CustomSendMessageOptions,
  instance: ChatInstance
) {
  if (request.input.text === "") {
    instance.messaging.addMessage({
      message_options: {
        response_user_profile: RESPONSE_USER_PROFILE
      },
      output: {
        generic: [
          {
            response_type: "text",
            text: WELCOME_TEXT,
          } as GenericItem,
        ],
      },
    });
  } else {
    console.log("Setting up card manager for new request");
    // Reset any previous card manager state
    resetCardManagerState();
    
    // No cross-card loader toggles needed anymore; loader is within each card while processing
    
    // Enable card manager for this request
    setCardManagerState(true, instance);
    console.log("Card manager state set:", { shouldShowCardManager: true, instance: !!instance });

    // Create the host user_defined message for CardManager without placeholder text
    console.log("Creating CardManager host message");
    const testWorkflowId = "test_workflow_" + Date.now();
    await instance.messaging.addMessage({
      message_options: {
        response_user_profile: RESPONSE_USER_PROFILE
      },
      output: {
        generic: [
          {
            id: testWorkflowId,
            response_type: "user_defined",
            user_defined: {
              user_defined_type: "my_unique_identifier",
              isCardManager: true,
            },
          } as GenericItem,
        ],
      },
    });
    console.log("CardManager host message created");

    switch (request.input.text) {
      default:
        await fetchStreamingData(instance, request.input.text || "");
        break;
    }
  }
}

export { customSendMessage, customStreamMessage };
