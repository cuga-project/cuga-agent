# Playwright e2e tests

Browser-driven verification for slash-command skill invocations
(soft dispatch: a `/skill` send round-trips to a streamed answer) and the
`ThreadIdChanged` round-trip triggered by `/clear`.

`@playwright/test` is in `devDependencies`, so `pnpm install` brings it in.
The Chromium binary is **not** auto-downloaded — run the install step on
first use, then build + test:

```bash
cd src/frontend_workspaces/frontend
pnpm exec playwright install chromium    # one-time per machine, ~150MB
pnpm run build                           # produces dist/ which the test server serves
pnpm run test:e2e                        # runs ./playwright.config.ts -> tests/e2e/*.spec.ts
```

## What it covers

- `slash-chips.spec.ts` boots the production build in headless Chromium, stubs
  every backend endpoint via `page.route` (no real backend needed), and asserts:
  - **Skill invocation** — typing `/deck make 3 slides` soft-dispatches: the
    send round-trips to a streamed answer with no separate invocation step or
    chip bubble, and the known `/deck` mention decorates the sent bubble with a
    pill.
  - **Combobox ARIA attributes** — the composer's `aria-expanded` /
    `aria-controls` / `aria-activedescendant` mirror the dropdown state as it
    opens, the active option changes, and it closes.

## Architecture notes

- `static-server.cjs` is a zero-dependency Node static server (port 3002) with
  SPA fallback. It is used instead of `serve` / `webpack-dev-server` because
  both pull in `path-to-regexp@6.x`, which is incompatible with Node 24+.
- The spec uses `page.route` registered in reverse order: broad catch-all
  first, specific stubs after, so the most-recent (most-specific) rule wins —
  this matches Playwright's route precedence rules.
- The Carbon AI Chat composer in `@carbon/ai-chat@1.6` is a `contenteditable`
  host with `role="textbox"`, not a `<textarea>`. `composerTextarea.ts`
  handles both shapes; the spec reads back the composer value via a shadow-DOM
  walk so the assertion works regardless of which element shape Carbon uses.

## Why the Chromium download is separate

`@playwright/test` is a small JS package that ships in `devDependencies`,
but the browser binary itself is ~150MB and the regular dev/build/typecheck
loop doesn't need it. Keeping `pnpm exec playwright install chromium` as a
deliberate step means new contributors who never run e2e tests don't pay
the download tax just from `pnpm install`.
