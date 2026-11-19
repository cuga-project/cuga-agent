# Frontend development rules

Read these rules before changing any frontend workspace. They apply to the web
application and shared chat UI. Keep browser-extension and desktop host constraints
intact when sharing components. See also the repository [AGENTS.md](../../AGENTS.md).

## Start from the existing Carbon experience

- Use the installed `@carbon/react`, `@carbon/icons-react`, and Carbon AI Chat
  components. The workspace manifests and lockfile define supported versions;
  upgrade dependencies separately from feature work.
- Inspect a neighboring screen before adding a component. Reuse the existing
  shell, dialog, form, and loading patterns instead of introducing another variant.
- Keep product copy about the user's task. Show implementation details only when
  the user needs them to configure a connection or understand an error.

| Need | Use |
|---|---|
| Global destination | The existing `UnifiedHeader` navigation and a URL-backed view |
| Sections of the same task | Carbon `Tabs`; use grouped `SideNav` for many Studio sections |
| Choosing an agent or option | Carbon `Dropdown`, `Select`, or `ComboBox`, with a visible label |
| A form | Carbon inputs, `FormGroup`, inline validation, and concise helper text |
| One short create/edit task | Carbon `ComposedModal`, `ModalHeader`, `ModalBody`, `ModalFooter` |
| Destructive confirmation | A Carbon danger modal with the resource and consequence named |
| Display-only summary | Carbon `Tile`; keep its actions separate |
| One clickable resource | Carbon `ClickableTile` or a link, without nested buttons or links |
| Tabular resources | Carbon `DataTable`, with search/filter/pagination when needed |
| Selecting tools | Carbon checkboxes, server groups, and a visible selection count |
| Feedback | Carbon `InlineNotification` in the affected region; existing toasts for transient completion |

Do not add a global navigation item for a modal or an action. Preserve the existing
role/feature gates, active state, browser Back behavior, and mobile navigation.

## Tokens, layout, and scrolling

- Keep the existing Carbon themes and local IBM Plex fonts. Use `--cds-*` color,
  layer, border, spacing, and typography tokens; use the Carbon spacing scale.
- Keep native Carbon focus, hover, validation, disabled, and selection treatments.
  Do not restyle controls to resemble a different design system.
- Scope CSS to the owning component. Do not add global `.cds--*` overrides, broad
  descendant selectors, or `!important` without explaining the Carbon limitation.
- Let the app shell own viewport sizing. Nested flex/grid regions need
  `min-height: 0` / `min-width: 0` and a deliberate scroll owner. Do not make the
  document and a full-height pane scroll independently.
- Use default modal padding for forms; reserve `isFullWidth` for edge-to-edge
  content such as tables. Keep header, body, and footer as direct Carbon modal
  children; associate footer submit buttons with the form using its ID.
- In modals, keep the header and footer visible while the body scrolls. Avoid
  competing fixed heights on the form, body, and viewport.
- Check narrow panels as well as narrow screens. Wrap action groups, long names,
  URLs, and tags without clipping focus indicators or hiding actions.

## Loading and asynchronous actions are part of the design

- Reuse [`ContentSkeleton`, `AsyncButton`, and `useModalClose`](agentic_chat/src/LoadingState.tsx).
- Initial content loads use a skeleton matching the content geometry. Do not show
  empty-state copy until loading succeeds. Announce the region once.
- User-triggered actions use `AsyncButton` with a specific `busyLabel`, such as
  "Connecting…" or "Saving gateway…". Keep its space stable; prevent duplicate
  requests and show errors beside the action or form.
- A refresh keeps the existing content and selection visible while pending. A
  failed refresh preserves that content and offers a retry.
- Disable conflicting edits while saving and guard every modal dismissal path
  with `useModalClose` (Escape, header close, outside click, and Cancel).
- Use independent pending state for independent actions. Do not block chat on
  unrelated metadata or replace the whole screen with a spinner for a local action.
- Cancel requests or reject stale results when the owning agent, thread, resource,
  or connection changes. Never apply an old result to the new context.
- Preserve input on failure, restore focus where appropriate, and announce errors.
  Do not silently swallow an error that prevents the user's action from completing.

## Forms, accessibility, and tool connections

- Use visible labels, unique IDs, semantic headings, and meaningful button/link
  names. Icon-only actions need an accessible description; tooltips alone are not labels.
- Keep native keyboard interactions, focus trapping and restoration, visible focus,
  and screen-reader status announcements. Do not implement clickable `div` controls.
- A primary action must be valid before submission; guard the handler too. Keep
  form errors in the scrollable body so they remain reachable on small screens.
- Mask credentials, disable autocomplete where appropriate, and persist secret
  references through the existing API. Never fall back to saving plaintext after
  a secret-store failure. Respect the returned reference, including Vault references.
- Gateway tools start unselected. Keep explicit selections on refresh, leave new
  tools unselected, and invalidate discovery when connection URL or identity changes.
- Keep signed-in-user and service-ID behavior explicit. Do not change authentication
  or tool configuration contracts as a styling shortcut.

## Verify the shipped UI

- Add or update behavioral browser tests for the affected workflow: success,
  pending, failure/retry, empty results, editing, and duplicate-submission protection.
  Test observable behavior rather than copying implementation details.
- Check keyboard operation and desktop, narrow-panel, and 320–390px layouts. For
  layout or shared-control changes, use Chromium, Firefox, and WebKit when available.
- Reuse [`tests/e2e`](frontend/tests/e2e/README.md) and the existing API stubs. Mocked
  browser tests do not prove live authentication or gateway connectivity; report
  the distinction and any checks not run.
- Run a production build using `scripts/frontend_build.sh` from the repository
  root (or its documented production build delegate). The server ships
  `src/cuga/frontend/dist`; source edits alone do not update the shipped UI.
- Never hand-merge generated JavaScript or hashed asset names. Resolve source
  conflicts, rebuild, and stage the complete generated output, including removed
  obsolete chunks. Keep dependency and lockfile changes intentional.
- Include useful screenshots for visible changes, list validation, and keep
  unrelated worktree changes out of the diff.

Reference screens: [`AddToolModal`](frontend/src/AddToolModal.tsx),
[`ToolsConfig`](frontend/src/ToolsConfig.tsx), and
[`StudioPage`](frontend/src/events/StudioPage.tsx). Compare the applicable screen rather
than treating every existing override as a pattern to copy.

Official guidance: [components](https://carbondesignsystem.com/components/overview/),
[loading](https://carbondesignsystem.com/components/loading/usage/), and
[modal](https://carbondesignsystem.com/components/modal/usage/).
