/**
 * E2E coverage for slash-command skill invocation:
 *  - a slash command round-trips to a streamed answer (soft dispatch: the
 *    translated suggestion drives the planner, so there is no separate
 *    invocation step)
 *  - the autocomplete dropdown's ARIA combobox semantics
 *  - the caret-scoped trigger rule (slash semantics are SOFT — a ``/skill``
 *    mention anywhere in the message is a suggestion to the agent — so the
 *    dropdown engages whenever the caret sits inside a ``/``-token, mid-text
 *    included, and accepting replaces just that token)
 *  - pill decoration of known ``/command`` mentions in sent user bubbles
 *  - ghost-text completion of the caret's token, accepted with Tab
 *
 * We boot the real frontend in a headless Chromium, stub every backend
 * endpoint the chat hits, and assert what gets rendered inside Carbon AI
 * Chat's shadow DOM. Modern Playwright auto-pierces shadow roots, so the
 * locator selectors below work without explicit ``>>` chains.
 */
import { test, expect, Page, Route } from "@playwright/test";

const COMMANDS_PAYLOAD = [
  { name: "deck", kind: "skill", description: "Make slide decks.", argument_hint: null },
  { name: "summarize", kind: "skill", description: "Summarize text.", argument_hint: null },
  { name: "summary-report", kind: "skill", description: "Produce a summary report.", argument_hint: null },
];

const SKILL_SSE = [
  'event: UserMessage\ndata: /deck make 3 slides',
  'event: Answer\ndata: Deck created.',
  'event: Complete\ndata: \n',
].join("\n\n") + "\n\n";

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

/**
 * Read the live composer's text via the same shadow-DOM walk the production
 * resolver uses. Role-agnostic (the open dropdown flips the composer role to
 * ``combobox``, breaking ``getByRole('textbox')``), and reads ``value`` or
 * ``textContent`` depending on the composer flavor.
 */
function readComposerText(page: Page) {
  return page.evaluate(() => {
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
    const value = (el as HTMLTextAreaElement).value;
    return typeof value === "string" ? value : el.textContent ?? "";
  });
}

test.describe("slash-command skill invocation", () => {
  test("sending a slash command round-trips to a streamed answer", async ({ page }) => {
    // Soft dispatch: sending ``/deck ...`` translates to a planner suggestion
    // and streams back a normal answer — there is no separate "Skill invoked"
    // step or chip bubble. This case guards that the slash send flow
    // round-trips: the message sends and the streamed answer renders. (Pill
    // decoration of ``/command`` mentions is covered by its own test below.)
    await stubBootEndpoints(page);
    await stubStream(page, SKILL_SSE);

    await page.goto("/chat");
    await sendInComposer(page, "/deck make 3 slides");

    // The sent user message renders in a bubble…
    await expect(page.getByText("/deck make 3 slides").first()).toBeVisible({ timeout: 10_000 });
    // …and the streamed answer renders — no separate invocation step precedes it.
    await expect(page.getByText("Deck created.").first()).toBeVisible({ timeout: 10_000 });
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
        // COMMANDS_PAYLOAD: ``deck``.
        activedescendant: "cuga-slash-option-deck",
      });

    // (2) ArrowDown advances ``aria-activedescendant`` to the next option
    // (``summarize`` follows ``deck`` in COMMANDS_PAYLOAD).
    await page.keyboard.press("ArrowDown");
    await expect
      .poll(async () => (await readComposerAria())?.activedescendant, {
        timeout: 5_000,
      })
      .toBe("cuga-slash-option-summarize");

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
    // so ``getByRole('textbox').first()`` resolves the live composer. The
    // filled value carries arguments after the command name, so the dropdown
    // stays closed (space-state rule) and the Enter below falls straight
    // through to Carbon's submit. We still send it via ``page.keyboard`` so
    // a role-based locator never has to re-resolve mid-keystroke.
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
        activedescendant: "cuga-slash-option-deck",
      });
  });

  test("dropdown hides once a space follows the command name and re-arms on backspace", async ({ page }) => {
    // Caret-scoped rule (stateless, derived from live value + caret): the
    // dropdown is visible only while the caret sits inside a ``/``-token.
    // Typing a space after the command name moves the caret out of that
    // token, closing the dropdown for the CURRENT token; argument
    // keystrokes keep the caret inside plain-word tokens, so it stays
    // closed (regression for the review-reported bug where the popup kept
    // re-appearing over the composer on every argument keystroke). A NEW
    // mid-text ``/``-token re-opens it — that case is covered by the
    // mid-text re-trigger test below.
    await stubBootEndpoints(page);
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );

    await page.goto("/chat");

    const dropdown = page.locator(".cuga-slash-dropdown");
    const ta = composer(page);
    await ta.waitFor({ state: "visible", timeout: 30_000 });

    // While the command is still being named the dropdown is up.
    await ta.fill("/deck");
    await expect(dropdown).toBeVisible({ timeout: 10_000 });

    // The first space after the name hides it. Keystrokes go through
    // ``page.keyboard`` from here on: the open dropdown flips the composer
    // role to ``combobox``, so a ``getByRole('textbox')`` locator would no
    // longer resolve.
    await page.keyboard.type(" hello");
    await expect(dropdown).not.toBeVisible();

    // Further argument keystrokes must not resurrect it — this was the bug.
    await page.keyboard.type(" world");
    await expect(dropdown).not.toBeVisible();

    // Stateless means reversible: backspacing the args and the space away
    // ("/deck hello world" -> "/dec") legitimately re-shows the dropdown.
    for (let i = 0; i < "k hello world".length; i += 1) {
      await page.keyboard.press("Backspace");
    }
    await expect(dropdown).toBeVisible({ timeout: 5_000 });
  });

  test("accepting a suggestion leaves the dropdown closed while typing args", async ({ page }) => {
    // Accepting a suggestion writes ``/<name> `` — trailing space included —
    // into the composer, parking the caret after the space (outside any
    // ``/``-token). That must flip the dropdown closed and keep it closed
    // while arguments are typed.
    await stubBootEndpoints(page);
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );

    await page.goto("/chat");

    const dropdown = page.locator(".cuga-slash-dropdown");
    const ta = composer(page);
    await ta.waitFor({ state: "visible", timeout: 30_000 });

    await ta.fill("/dec");
    await expect(dropdown).toBeVisible({ timeout: 10_000 });
    // Wait for the fetched+filtered option list — Enter on an empty list
    // would fall through to Carbon's submit instead of accepting.
    await expect(dropdown.locator('[role="option"]')).toHaveCount(1, {
      timeout: 10_000,
    });

    // Enter accepts the highlighted "/deck" suggestion, inserting "/deck ".
    await page.keyboard.press("Enter");
    await expect(dropdown).not.toBeVisible();

    // The reported bug: the popup re-appeared on every arg keystroke after
    // acceptance. It must stay closed for the whole argument tail.
    await page.keyboard.type("make 3 slides");
    await expect(dropdown).not.toBeVisible();
  });

  test("mid-text /-token re-triggers the dropdown and accept replaces only that token", async ({ page }) => {
    // Slash semantics are soft: a ``/skill`` mention anywhere in the message
    // is a suggestion to the agent, so autocomplete must engage for tokens
    // typed mid-sentence, and acceptance must splice in just that token —
    // not overwrite the whole composer.
    await stubBootEndpoints(page);
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );

    await page.goto("/chat");

    const dropdown = page.locator(".cuga-slash-dropdown");
    const ta = composer(page);
    await ta.waitFor({ state: "visible", timeout: 30_000 });

    // Plain prose keeps the dropdown closed…
    await ta.fill("hi can I use ");
    await expect(dropdown).not.toBeVisible();

    // …but starting a ``/``-token mid-text re-opens it, filtered to the
    // token under the caret.
    await page.keyboard.type("/dec");
    await expect(dropdown).toBeVisible({ timeout: 10_000 });
    await expect(dropdown.locator('[role="option"]')).toHaveCount(1, {
      timeout: 10_000,
    });

    // Accepting replaces just the ``/dec`` token (name + trailing space) at
    // its position — the prose before it survives.
    await page.keyboard.press("Enter");
    await expect(dropdown).not.toBeVisible();
    await expect.poll(() => readComposerText(page), { timeout: 5_000 }).toBe(
      "hi can I use /deck ",
    );

    // The completed token's dropdown stays closed while prose continues…
    await page.keyboard.type("please");
    await expect(dropdown).not.toBeVisible();

    // …and a NEW mid-text ``/``-token re-opens it, filtered afresh
    // (``summ`` matches summarize + summary-report).
    await page.keyboard.type(" and /summ");
    await expect(dropdown).toBeVisible({ timeout: 10_000 });
    await expect(dropdown.locator('[role="option"]')).toHaveCount(2, {
      timeout: 10_000,
    });
  });

  test("the caret position, not the composer prefix, selects the active token", async ({ page }) => {
    // Regression for true caret-awareness: a ``/``-token typed in the MIDDLE
    // of existing text (caret nowhere near the end) must drive the dropdown,
    // and acceptance must splice at the caret's token without disturbing the
    // text after it.
    await stubBootEndpoints(page);
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );

    await page.goto("/chat");

    const dropdown = page.locator(".cuga-slash-dropdown");
    const ta = composer(page);
    await ta.waitFor({ state: "visible", timeout: 30_000 });

    await ta.fill("hi world");
    await expect(dropdown).not.toBeVisible();

    // Walk the caret back to just after "hi", then type a slash token there:
    // the composer reads "hi /de world" with the caret inside "/de".
    for (let i = 0; i < " world".length; i += 1) {
      await page.keyboard.press("ArrowLeft");
    }
    await page.keyboard.type(" /de");
    await expect(dropdown).toBeVisible({ timeout: 10_000 });
    await expect(dropdown.locator('[role="option"]')).toHaveCount(1, {
      timeout: 10_000,
    });

    // Accepting splices "/deck " over the token; the single space already
    // following the token is consumed so whitespace doesn't double up.
    await page.keyboard.press("Enter");
    await expect(dropdown).not.toBeVisible();
    await expect.poll(() => readComposerText(page), { timeout: 5_000 }).toBe(
      "hi /deck world",
    );
  });

  test("known /command mentions in sent bubbles get pill decoration", async ({ page }) => {
    // Rendered user messages decorate known ``/name`` mentions with a subtle
    // pill span (inline-styled — the bubble lives in Carbon's shadow root).
    // Unknown tokens stay plain text.
    await stubBootEndpoints(page);
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );
    await stubStream(page, SKILL_SSE);

    await page.goto("/chat");
    await sendInComposer(page, "use /deck and /nonexistent to make 3 slides");

    const pills = page.locator(".cuga-slash-command-pill");
    await expect(pills.first()).toBeVisible({ timeout: 10_000 });
    await expect(pills.first()).toHaveText("/deck");
    // "/nonexistent" is not a known command — exactly one pill.
    await expect(pills).toHaveCount(1);
  });

  test("ghost text previews the completion and Tab accepts it", async ({ page }) => {
    // Cursor-style inline autocomplete: while the caret's token ends the
    // composer content, the continuation of the highlighted match renders
    // as ghost text after the token, and Tab accepts it.
    await stubBootEndpoints(page);
    await page.route("**/api/commands", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ commands: COMMANDS_PAYLOAD }),
      }),
    );

    await page.goto("/chat");

    const ghost = page.locator(".cuga-slash-ghost");
    const ta = composer(page);
    await ta.waitFor({ state: "visible", timeout: 30_000 });

    await ta.fill("/de");
    await expect(ghost).toBeVisible({ timeout: 10_000 });
    await expect(ghost).toHaveText("ck");

    await page.keyboard.press("Tab");
    await expect.poll(() => readComposerText(page), { timeout: 5_000 }).toBe(
      "/deck ",
    );
    await expect(ghost).not.toBeVisible();

    // The completed known token now carries the inline pill overlay (the
    // translucent light-DOM rectangle drawn over the composer text).
    await expect(page.locator(".cuga-slash-inline-pill").first()).toBeVisible({
      timeout: 10_000,
    });
  });
});
