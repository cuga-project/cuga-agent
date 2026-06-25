/**
 * E2E coverage for the slash-command chips (``SlashSkillInvoked`` collapsed
 * chip and ``SlashSuggestions`` unknown-command chip) plus the
 * ``ThreadIdChanged`` round-trip triggered by ``/clear``.
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
  test("skill invocation surfaces in the reasoning panel rather than a separate chip", async ({ page }) => {
    // The skill invocation used to render as its own `.cuga-slash-skill-chip`
    // bubble; it now lands as a "Skill invoked: /<name>" reasoning step on
    // the assistant message. Two assertions guard the migration:
    //   (a) no orphan chip bubble appears;
    //   (b) the reasoning toggle is present and reveals the skill name.
    await stubBootEndpoints(page);
    await stubStream(page, SKILL_SSE);

    await page.goto("/chat");
    await sendInComposer(page, "/deck make 3 slides");

    // (a) The old separate-bubble chip must not appear anywhere.
    await expect(page.locator(".cuga-slash-skill-chip")).toHaveCount(0);

    // (b) The reasoning toggle, relabelled "Show details", must be present
    // and clickable. After clicking, the panel reveals the skill audit step.
    const showDetails = page.getByRole("button", { name: /Show details/i }).first();
    await expect(showDetails).toBeVisible({ timeout: 10_000 });
    await showDetails.click();

    // The audit step is titled "Skill invoked: /deck" and contains the raw
    // input verbatim. The reasoning panel is rendered inside Carbon's shadow
    // DOM; Playwright's auto-piercing locators reach in.
    await expect(page.getByText(/Skill invoked:\s*\/deck/i).first()).toBeVisible();
    await expect(page.getByText("/deck make 3 slides").first()).toBeVisible();
  });

  test("unknown command renders clickable suggestion chips, click writes to composer, plain Answer is suppressed", async ({ page }) => {
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
    // Read the value back via a shadow-DOM walk (mirrors what the autocomplete
    // dropdown does when locating the composer): Carbon's composer can be
    // either a ``<textarea>`` or a ``contenteditable`` host depending on the
    // version; ``getByRole('textbox').toHaveValue`` would false-fail on
    // contenteditable.
    await chips.nth(0).click();
    await expect
      .poll(
        () =>
          page.evaluate(() => {
            const SEL =
              'textarea, input[type="text"], [contenteditable="true"], [contenteditable=""], [role="textbox"]';
            const isVisible = (e: Element) => {
              const r = (e as HTMLElement).getBoundingClientRect?.();
              return !!r && r.width > 0 && r.height > 0;
            };
            // Mirrors composerTextarea.findComposerInput: prefer the visible
            // composer (Carbon Chat may leave an orphaned zero-rect node
            // attached briefly after submit), falling back to the first match.
            function find(root: Document | ShadowRoot): Element | null {
              let firstSeen: Element | null = null;
              const stack: Array<Document | ShadowRoot> = [root];
              while (stack.length) {
                const r = stack.shift()!;
                for (const c of Array.from(r.querySelectorAll(SEL))) {
                  if (!firstSeen) firstSeen = c;
                  if (isVisible(c)) return c;
                }
                for (const el of Array.from(r.querySelectorAll("*"))) {
                  const sr = (el as Element & { shadowRoot?: ShadowRoot }).shadowRoot;
                  if (sr) stack.push(sr);
                }
              }
              return firstSeen;
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

  test("ThreadIdChanged rotates X-Thread-ID for the next request", async ({ page }) => {
    // `/clear` mints a fresh thread_id server-side. The frontend must adopt
    // it so the NEXT outbound request's `X-Thread-ID` header points at the
    // new thread, not the old one. Verifying this end-to-end is the only
    // way to catch a regression where the SSE event is parsed but the
    // setter is never called.
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
    // the next turn. Carbon also pumps message text into an aria-live
    // announcement region, so the message text resolves to multiple
    // elements — `.last()` reliably targets the visible chat bubble.
    await expect(page.getByText("Conversation cleared.").last()).toBeVisible();

    await sendInComposer(page, "hello again");
    await expect(page.getByText("Hi.").last()).toBeVisible();

    expect(streamCallCount).toBe(2);
    expect(firstThreadId).not.toBe(NEW_THREAD_ID);
    expect(firstThreadId).not.toBe(null);
    expect(secondThreadId).toBe(NEW_THREAD_ID);
  });

  test("combobox ARIA attributes mirror dropdown state", async ({ page }) => {
    // Per the WAI-ARIA APG combobox pattern, the focused composer textbox —
    // not the listbox — must carry ``role="combobox"``, ``aria-controls``,
    // ``aria-expanded`` and ``aria-activedescendant``. Carbon Chat hosts the
    // composer inside a shadow root and replaces the node on every submit,
    // so the dropdown writes these attributes imperatively and re-applies
    // them whenever the composer identity changes. This test guards the
    // identity-tracking path: open the dropdown, walk the highlight, close
    // it, submit a message (forcing Carbon to swap composers), then reopen
    // and re-assert on the NEW node.
    await stubBootEndpoints(page);
    // ``stubBootEndpoints`` returns the commands as a bare array; the
    // autocomplete dropdown calls ``getCommands()`` which expects
    // ``{ commands: [...] }`` (see ``api.ts``). Override the route so the
    // dropdown actually populates and ``aria-activedescendant`` resolves.
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );
    await stubStream(page, SKILL_SSE);

    await page.goto("/chat");

    // Locate the composer via the same shadow-DOM walk as the production
    // resolver — once we apply ``role="combobox"`` the textbox role no
    // longer matches, so we re-find the live composer on each assertion
    // instead of caching a Locator.
    const readComposerAria = () =>
      page.evaluate(() => {
        const SEL =
          'textarea, input[type="text"], [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="combobox"]';
        const isVisible = (e: Element) => {
          const r = (e as HTMLElement).getBoundingClientRect?.();
          return !!r && r.width > 0 && r.height > 0;
        };
        function find(root: Document | ShadowRoot): Element | null {
          let firstSeen: Element | null = null;
          const stack: Array<Document | ShadowRoot> = [root];
          while (stack.length) {
            const r = stack.shift()!;
            for (const c of Array.from(r.querySelectorAll(SEL))) {
              if (!firstSeen) firstSeen = c;
              if (isVisible(c)) return c;
            }
            for (const el of Array.from(r.querySelectorAll("*"))) {
              const sr = (el as Element & { shadowRoot?: ShadowRoot }).shadowRoot;
              if (sr) stack.push(sr);
            }
          }
          return firstSeen;
        }
        const el = find(document);
        if (!el) return null;
        return {
          role: el.getAttribute("role"),
          controls: el.getAttribute("aria-controls"),
          expanded: el.getAttribute("aria-expanded"),
          activedescendant: el.getAttribute("aria-activedescendant"),
        };
      });

    // (1) Type ``/`` and assert combobox semantics are wired up.
    const ta = composer(page);
    await ta.waitFor({ state: "visible", timeout: 30_000 });
    await ta.fill("/");

    await expect
      .poll(readComposerAria, { timeout: 10_000 })
      .toMatchObject({
        role: "combobox",
        controls: "cuga-slash-options",
        expanded: "true",
        // First filtered match for the empty query is the first command in
        // COMMANDS_PAYLOAD: ``help``.
        activedescendant: "cuga-slash-option-help",
      });

    // (2) ArrowDown advances ``aria-activedescendant`` to the next option.
    // After ArrowDown the highlight moves to the second command (``clear``).
    await page.keyboard.press("ArrowDown");
    await expect
      .poll(async () => (await readComposerAria())?.activedescendant, {
        timeout: 5_000,
      })
      .toBe("cuga-slash-option-clear");

    // (3) Escape collapses the popup and clears ``aria-activedescendant``.
    await page.keyboard.press("Escape");
    await expect
      .poll(readComposerAria, { timeout: 5_000 })
      .toMatchObject({
        expanded: "false",
        // The attribute should be absent entirely — APG forbids pointing
        // at a non-existent option.
        activedescendant: null,
      });

    // (4) Submit a message — Carbon replaces the composer node — then
    // reopen the dropdown. The new composer must receive the same ARIA
    // wiring. This is the regression-critical assertion: the
    // identity-tracking path in the dropdown's ARIA effect.
    //
    // The composer's ``role`` is back to ``textbox`` after Escape (step 3),
    // so ``getByRole('textbox').first()`` resolves the live composer; once
    // we fill it with ``/deck …`` the dropdown reopens and flips the role
    // to ``combobox``, so we send the Enter via ``page.keyboard`` (which
    // doesn't re-resolve a role-based locator) to dodge that race.
    const ta2 = composer(page);
    await ta2.waitFor({ state: "visible", timeout: 30_000 });
    await ta2.fill("/deck make 3 slides");
    await page.keyboard.press("Enter");

    // Wait for the response so we know Carbon has finished swapping the
    // composer.
    const showDetails = page
      .getByRole("button", { name: /Show details/i })
      .first();
    await expect(showDetails).toBeVisible({ timeout: 10_000 });

    // After Carbon's submit the previous composer is replaced; the new
    // composer ships with ``role="textbox"`` again, so this locator
    // resolves to the FRESH node.
    const freshTa = composer(page);
    await freshTa.waitFor({ state: "visible", timeout: 10_000 });
    await freshTa.fill("/");
    await expect
      .poll(readComposerAria, { timeout: 10_000 })
      .toMatchObject({
        role: "combobox",
        controls: "cuga-slash-options",
        expanded: "true",
        activedescendant: "cuga-slash-option-help",
      });
  });

  // CUGA does not currently have an app-wide dark mode. The chip dark-mode
  // CSS rules were no-op'd; re-enable this test alongside any future dark-mode
  // work to guard against partial regressions.
  test.skip("skill chip picks up dark-mode styles under prefers-color-scheme: dark", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await stubBootEndpoints(page);
    await stubStream(page, SKILL_SSE);

    await page.goto("/chat");
    await sendInComposer(page, "/deck make 3 slides");

    const summary = page.locator(".cuga-slash-skill-chip__summary");
    await expect(summary).toBeVisible();
    await expect(summary).toHaveCSS("background-color", "rgb(33, 38, 45)");
  });

  test("skill invocation replays into the reasoning panel; suggestions chip replays as a bubble", async ({ page }) => {
    await stubBootEndpoints(page, HISTORY_PAYLOAD);

    await page.goto("/chat");

    // Skill invocation no longer renders a separate chip on history reload —
    // it lands in the reasoning panel of the assistant message it preceded.
    await expect(page.locator(".cuga-slash-skill-chip")).toHaveCount(0);

    // The reasoning toggle ("Show details") must be present; expanding it
    // reveals the audit step for /deck.
    const showDetails = page.getByRole("button", { name: /Show details/i }).first();
    await expect(showDetails).toBeVisible({ timeout: 10_000 });
    await showDetails.click();
    await expect(page.getByText(/Skill invoked:\s*\/deck/i).first()).toBeVisible();

    // Suggestions chip still replays as its own interactive bubble.
    const suggestionsChip = page.locator(".cuga-slash-suggestions");
    await expect(suggestionsChip).toBeVisible();
    await expect(suggestionsChip.locator(".cuga-slash-suggestion__name").first()).toHaveText("/summarize");

    // Suppression must hold on reload too.
    await expect(page.getByText(/Unknown command:/)).toHaveCount(0);
  });
});
