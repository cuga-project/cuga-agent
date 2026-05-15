/**
 * E2E coverage for the slash-command chips (slices #22 + #23).
 *
 * We boot the real frontend in a headless Chromium, stub every backend
 * endpoint the chat hits, and assert what gets rendered inside Carbon AI
 * Chat's shadow DOM. Modern Playwright auto-pierces shadow roots, so the
 * locator selectors below work without explicit ``>>` chains.
 */
import { test, expect, Page, Route } from "@playwright/test";

const COMMANDS_PAYLOAD = [
  { name: "help", kind: "builtin", description: "Show available slash commands.", argument_hint: null },
  { name: "clear", kind: "builtin", description: "Start a fresh conversation.", argument_hint: null },
  { name: "skills", kind: "builtin", description: "List installed skills.", argument_hint: null },
  { name: "deck", kind: "skill", description: "Make slide decks.", argument_hint: null },
  { name: "summarize", kind: "skill", description: "Summarize text.", argument_hint: null },
  { name: "summary-report", kind: "skill", description: "Produce a summary report.", argument_hint: null },
];

const SKILL_SSE = [
  'event: UserMessage\ndata: /deck make 3 slides',
  'event: SlashSkillInvoked\ndata: {"resolved_name": "deck", "raw_input": "/deck make 3 slides", "raw_args": "make 3 slides"}',
  'event: Answer\ndata: Deck created.',
  'event: Complete\ndata: \n',
].join("\n\n") + "\n\n";

const SUGGESTIONS_SSE = [
  'event: UserMessage\ndata: /sumarize my notes',
  'event: SlashSuggestions\ndata: {"raw_input": "/sumarize", "suggestions": [' +
    '{"name": "summarize", "kind": "skill", "description": "Summarize text.", "score": 0.91},' +
    '{"name": "summary-report", "kind": "skill", "description": "Produce a summary report.", "score": 0.74}' +
    ']}',
  'event: Answer\ndata: Unknown command: /sumarize. Did you mean: /summarize, /summary-report?',
  'event: Complete\ndata: \n',
].join("\n\n") + "\n\n";

const HISTORY_PAYLOAD = {
  events: [
    {
      event_name: "UserMessage",
      event_data: "/deck make 3 slides",
      timestamp: "2026-05-14T12:00:00",
      sequence: 0,
    },
    {
      event_name: "SlashSkillInvoked",
      event_data: JSON.stringify({
        resolved_name: "deck",
        raw_input: "/deck make 3 slides",
        raw_args: "make 3 slides",
      }),
      timestamp: "2026-05-14T12:00:01",
      sequence: 1,
    },
    {
      event_name: "Answer",
      event_data: "Deck created.",
      timestamp: "2026-05-14T12:00:02",
      sequence: 2,
    },
    {
      event_name: "UserMessage",
      event_data: "/sumarize",
      timestamp: "2026-05-14T12:00:03",
      sequence: 3,
    },
    {
      event_name: "SlashSuggestions",
      event_data: JSON.stringify({
        raw_input: "/sumarize",
        suggestions: [
          { name: "summarize", kind: "skill", description: "Summarize text.", score: 0.91 },
        ],
      }),
      timestamp: "2026-05-14T12:00:04",
      sequence: 4,
    },
    {
      event_name: "Answer",
      event_data: "Unknown command: /sumarize. Did you mean: /summarize?",
      timestamp: "2026-05-14T12:00:05",
      sequence: 5,
    },
  ],
};

async function stubBootEndpoints(page: Page, historyEvents: object = { events: [] }) {
  // Playwright tries routes in REVERSE registration order — the most recent
  // wins — so the broad catch-all goes FIRST and is overridden below by the
  // specific stubs. The catch-all keeps the test from cratering on the dozen
  // knowledge/manage endpoints the chat touches during boot.
  await page.route("**/api/**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.route("**/api/auth/config", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify({ enabled: false, authorization_enabled: false }) }),
  );
  await page.route("**/api/ui/config", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify({ hide_cuga_logo: false, brand_name: "CUGA" }) }),
  );
  await page.route("**/api/commands", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify(COMMANDS_PAYLOAD) }),
  );
  await page.route("**/api/conversation-stream-events/**", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify(historyEvents) }),
  );
  await page.route("**/api/conversation-messages/**", (r) =>
    r.fulfill({ contentType: "application/json", body: JSON.stringify({ messages: [] }) }),
  );
}

async function stubStream(page: Page, sseBody: string) {
  await page.route("**/stream", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody,
      headers: { "Cache-Control": "no-cache" },
    }),
  );
}

/**
 * Find the Carbon chat composer textarea. Carbon renders it inside several
 * layers of shadow DOM; ARIA role pierces those reliably. ``getByRole`` also
 * works whether or not the homescreen is up.
 */
function composer(page: Page) {
  return page.getByRole("textbox").first();
}

async function sendInComposer(page: Page, text: string) {
  const ta = composer(page);
  await ta.waitFor({ state: "visible", timeout: 30_000 });
  await ta.fill(text);
  await ta.press("Enter");
}

test.describe("slash-command chips", () => {
  test("skill invocation renders a collapsed chip that expands on click (#22)", async ({ page }) => {
    await stubBootEndpoints(page);
    await stubStream(page, SKILL_SSE);

    await page.goto("/chat");
    await sendInComposer(page, "/deck make 3 slides");

    const chip = page.locator(".cuga-slash-skill-chip");
    await expect(chip).toBeVisible();
    await expect(chip.locator(".cuga-slash-skill-chip__name")).toHaveText("/deck");

    // Collapsed by default — details list is absent.
    await expect(chip.locator(".cuga-slash-skill-chip__details")).toHaveCount(0);

    await chip.locator(".cuga-slash-skill-chip__summary").click();
    const details = chip.locator(".cuga-slash-skill-chip__details");
    await expect(details).toBeVisible();
    await expect(details).toContainText("/deck make 3 slides");
    await expect(details).toContainText("make 3 slides");

    // Collapses again.
    await chip.locator(".cuga-slash-skill-chip__summary").click();
    await expect(chip.locator(".cuga-slash-skill-chip__details")).toHaveCount(0);
  });

  test("unknown command renders clickable suggestion chips, click writes to composer, plain Answer is suppressed (#23)", async ({ page }) => {
    await stubBootEndpoints(page);
    await stubStream(page, SUGGESTIONS_SSE);

    await page.goto("/chat");
    await sendInComposer(page, "/sumarize my notes");

    const suggestions = page.locator(".cuga-slash-suggestions");
    await expect(suggestions).toBeVisible();

    const chips = suggestions.locator(".cuga-slash-suggestion");
    await expect(chips).toHaveCount(2);
    await expect(chips.nth(0).locator(".cuga-slash-suggestion__name")).toHaveText("/summarize");
    await expect(chips.nth(1).locator(".cuga-slash-suggestion__name")).toHaveText("/summary-report");

    // Plain "Unknown command:" Answer must be suppressed when chips render.
    await expect(page.getByText(/Unknown command:/)).toHaveCount(0);

    // Clicking a chip drops "/summarize " (trailing space) into the composer.
    // Read the value back via a shadow-DOM walk (mirrors what slice #18 does
    // when locating the composer): Carbon's composer can be either a
    // ``<textarea>`` or a ``contenteditable`` host depending on the version;
    // ``getByRole('textbox').toHaveValue`` would false-fail on contenteditable.
    await chips.nth(0).click();
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const SEL =
              'textarea, input[type="text"], [contenteditable="true"], [contenteditable=""], [role="textbox"]';
            function find(root: Document | ShadowRoot): Element | null {
              const direct = root.querySelector(SEL);
              if (direct) return direct;
              for (const el of Array.from(root.querySelectorAll("*"))) {
                const sr = (el as Element & { shadowRoot?: ShadowRoot }).shadowRoot;
                if (sr) {
                  const found = find(sr);
                  if (found) return found;
                }
              }
              return null;
            }
            const el = find(document);
            if (!el) return null;
            if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
              return el.value;
            }
            return el.textContent ?? "";
          }),
        { timeout: 5_000 },
      )
      .toBe("/summarize ");
  });

  test("ThreadIdChanged rotates X-Thread-ID for the next request (#15)", async ({ page }) => {
    // Slice #15: `/clear` mints a fresh thread_id server-side. The frontend
    // must adopt it so the NEXT outbound request's `X-Thread-ID` header
    // points at the new thread, not the old one. Verifying this end-to-end
    // is the only way to catch a regression where the SSE event is parsed
    // but the setter is never called.
    await stubBootEndpoints(page);

    const NEW_THREAD_ID = "11111111-2222-4333-8444-555555555555";
    let firstThreadId: string | null = null;
    let secondThreadId: string | null = null;
    let streamCallCount = 0;

    await page.route("**/stream", (route: Route) => {
      streamCallCount += 1;
      const headers = route.request().headers();
      if (streamCallCount === 1) {
        firstThreadId = headers["x-thread-id"] ?? null;
        // `/clear` server response: rotate the thread id, then a normal Answer.
        const body =
          [
            "event: UserMessage\ndata: /clear",
            `event: ThreadIdChanged\ndata: {"thread_id": "${NEW_THREAD_ID}"}`,
            "event: Answer\ndata: Conversation cleared.",
            "event: Complete\ndata: \n",
          ].join("\n\n") + "\n\n";
        return route.fulfill({
          status: 200,
          contentType: "text/event-stream",
          body,
          headers: { "Cache-Control": "no-cache" },
        });
      }
      secondThreadId = headers["x-thread-id"] ?? null;
      const body =
        [
          "event: UserMessage\ndata: hello again",
          "event: Answer\ndata: Hi.",
          "event: Complete\ndata: \n",
        ].join("\n\n") + "\n\n";
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body,
        headers: { "Cache-Control": "no-cache" },
      });
    });

    await page.goto("/chat");

    await sendInComposer(page, "/clear");
    // Wait for the clear Answer to finish rendering so the SSE stream is
    // fully consumed (including the ThreadIdChanged event) before we send
    // the next turn.
    await expect(page.getByText("Conversation cleared.")).toBeVisible();

    await sendInComposer(page, "hello again");
    await expect(page.getByText("Hi.")).toBeVisible();

    expect(streamCallCount).toBe(2);
    expect(firstThreadId).not.toBe(NEW_THREAD_ID);
    expect(firstThreadId).not.toBe(null);
    expect(secondThreadId).toBe(NEW_THREAD_ID);
  });

  test("chips replay from history on page load (#22 + #23)", async ({ page }) => {
    await stubBootEndpoints(page, HISTORY_PAYLOAD);

    await page.goto("/chat");

    // Both chip types must replay through `renderUserDefinedResponse`.
    await expect(page.locator(".cuga-slash-skill-chip")).toBeVisible();
    await expect(page.locator(".cuga-slash-skill-chip__name")).toHaveText("/deck");

    const suggestionsChip = page.locator(".cuga-slash-suggestions");
    await expect(suggestionsChip).toBeVisible();
    await expect(suggestionsChip.locator(".cuga-slash-suggestion__name").first()).toHaveText("/summarize");

    // Suppression must hold on reload too.
    await expect(page.getByText(/Unknown command:/)).toHaveCount(0);
  });
});
