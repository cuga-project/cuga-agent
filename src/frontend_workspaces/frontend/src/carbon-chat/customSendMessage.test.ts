import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@carbon/ai-chat", () => ({
  ButtonItemType: { CUSTOM_EVENT: "custom_event" },
  MessageResponseTypes: { TEXT: "text", CARD: "card", BUTTON: "button", USER_DEFINED: "user_defined" },
}));
vi.mock("./carbonChatHelpers", () => ({
  BUTTON_KIND: { PRIMARY: "primary", SECONDARY: "secondary", DANGER: "danger" },
  RESPONSE_USER_PROFILE: {},
  getCachedResponseUserProfile: () => ({}),
  parseReasoningStepContent: vi.fn(),
  parseAnswerEventData: vi.fn(),
  buildToolApprovalCard: vi.fn(),
  createReasoningStep: vi.fn(),
}));
vi.mock("./CarbonChat", () => ({ generateUUID: () => "response-id" }));
vi.mock("agentic_chat/Citations", () => ({ injectCitations: (text: string) => text }));
vi.mock("../api", () => ({
  getApiBaseUrl: () => "http://localhost",
  getKnowledgeAgentId: () => "cuga-default",
  postStream: vi.fn(),
  postStop: vi.fn(),
}));

import * as api from "../api";
import { customSendMessage } from "./customSendMessage";
import { showThread } from "./threadState";

function sseResponse(events: Array<[string, string]>): Response {
  const body = events.map(([name, data]) => `event: ${name}\ndata: ${data}\n\n`).join("");
  return new Response(body, { status: 200 });
}

function makeInstance() {
  return {
    updateIsMessageLoadingCounter: vi.fn(),
    messaging: { addMessage: vi.fn(), addMessageChunk: vi.fn() },
  };
}

async function send(
  events: Array<[string, string]>,
  onConfigNotice: (notice: "config_changed" | "pending_cancelled") => void,
) {
  vi.mocked(api.postStream).mockResolvedValue(sseResponse(events));
  const instance = makeInstance();
  await customSendMessage(
    { input: { text: "hello" } } as any,
    { signal: new AbortController().signal } as any,
    instance as any,
    false,
    false,
    undefined,
    undefined,
    undefined,
    onConfigNotice,
  );
  return instance;
}

function chunkedResponseTexts(instance: ReturnType<typeof makeInstance>): string[] {
  return instance.messaging.addMessageChunk.mock.calls
    .map(([chunk]) => chunk?.complete_item?.text)
    .filter((text): text is string => typeof text === "string");
}

describe("customSendMessage config notices", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, "log").mockImplementation(() => {});
    showThread("thread-1");
  });

  it("reports config_changed without adding a chat message", async () => {
    const onConfigNotice = vi.fn();
    const instance = await send(
      [
        ["config_changed", JSON.stringify({ previous_agent_hash: "aaa", current_agent_hash: "bbb" })],
        ["Complete", "done"],
      ],
      onConfigNotice,
    );

    expect(onConfigNotice).toHaveBeenCalledTimes(1);
    expect(onConfigNotice).toHaveBeenCalledWith("config_changed");
    expect(instance.messaging.addMessage).not.toHaveBeenCalled();
    expect(chunkedResponseTexts(instance).every((text) => !text.includes("previous_agent_hash"))).toBe(true);
  });

  it("reports pending_cancelled without adding a chat message", async () => {
    const onConfigNotice = vi.fn();
    const instance = await send(
      [["pending_cancelled", JSON.stringify({ reason: "config_changed" })]],
      onConfigNotice,
    );

    expect(onConfigNotice).toHaveBeenCalledTimes(1);
    expect(onConfigNotice).toHaveBeenCalledWith("pending_cancelled");
    expect(instance.messaging.addMessage).not.toHaveBeenCalled();
    expect(chunkedResponseTexts(instance).every((text) => !text.includes("reason"))).toBe(true);
  });

  it("does not create a response shell for a notice alone", async () => {
    const onConfigNotice = vi.fn();
    const instance = await send([["config_changed", "{}"]], onConfigNotice);

    const shellChunks = instance.messaging.addMessageChunk.mock.calls.filter(
      ([chunk]) => chunk?.partial_item?.text === "",
    );
    expect(shellChunks).toHaveLength(0);
  });

  it("ignores notices when no callback is given", async () => {
    vi.mocked(api.postStream).mockResolvedValue(sseResponse([["config_changed", "{}"]]));
    const instance = makeInstance();

    await expect(
      customSendMessage({ input: { text: "hello" } } as any, { signal: new AbortController().signal } as any, instance as any),
    ).resolves.toBeUndefined();
    expect(instance.messaging.addMessage).not.toHaveBeenCalled();
  });
});
