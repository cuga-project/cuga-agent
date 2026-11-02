import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";

vi.mock("../api", () => ({
  getKnowledgeAgentId: vi.fn(() => "weatherbot"),
  getManageConfig: vi.fn(),
  getUiConfig: vi.fn(),
}));
vi.mock("@carbon/ai-chat", () => ({
  ButtonItemType: {},
  MessageResponseTypes: {},
  ReasoningStepOpenState: {},
  UserType: { BOT: "bot" },
}));

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  vi.mocked(api.getUiConfig).mockResolvedValue({ hide_cuga_logo: true } as any);
  vi.mocked(api.getManageConfig).mockImplementation(async (draft, agentId) =>
    new Response(JSON.stringify({ config: { agent: { name: `${agentId}-${draft ? "draft" : "published"}` } } })),
  );
});

describe("agent response profiles", () => {
  it("returns an immediate isolated profile without requesting configuration", async () => {
    const { getCachedResponseUserProfile, getResponseUserProfile } = await import("./carbonChatHelpers");
    const initial = getCachedResponseUserProfile(false, "weatherbot");
    expect(initial.nickname).toBe("weatherbot");
    expect(api.getManageConfig).not.toHaveBeenCalled();
    await getResponseUserProfile(false, "weatherbot");
    expect(getCachedResponseUserProfile(false, "weatherbot").nickname).toBe("weatherbot-published");
    expect(initial.nickname).toBe("weatherbot");
  });

  it("keeps names and cache entries separate for each agent", async () => {
    const { getResponseUserProfile } = await import("./carbonChatHelpers");
    expect((await getResponseUserProfile(false, "cuga-default")).nickname).toBe("cuga-default-published");
    expect((await getResponseUserProfile(false)).nickname).toBe("weatherbot-published");
    expect((await getResponseUserProfile(false, "pricebot")).nickname).toBe("pricebot-published");
    expect((await getResponseUserProfile(false, "weatherbot")).nickname).toBe("weatherbot-published");
    expect(api.getManageConfig).toHaveBeenCalledTimes(3);
    expect(api.getManageConfig).toHaveBeenCalledWith(false, "weatherbot");
  });

  it("keeps draft and published names separate for the same agent", async () => {
    const { getResponseUserProfile } = await import("./carbonChatHelpers");
    expect((await getResponseUserProfile(true, "weatherbot")).nickname).toBe("weatherbot-draft");
    expect((await getResponseUserProfile(false, "weatherbot")).nickname).toBe("weatherbot-published");
  });

  it("uses the selected ID on lookup failure and retries without borrowing another profile", async () => {
    const { getResponseUserProfile } = await import("./carbonChatHelpers");
    await getResponseUserProfile(false, "cuga-default");
    vi.mocked(api.getManageConfig).mockRejectedValueOnce(new Error("offline"));
    expect((await getResponseUserProfile(false, "weatherbot")).nickname).toBe("weatherbot");
    expect((await getResponseUserProfile(false, "weatherbot")).nickname).toBe("weatherbot-published");
  });
});
