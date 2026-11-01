# README media

The README shows CUGA's IBM Carbon workspace and a narrated tour of the Carbon UI refresh. Screens and videos are stored in the repository so the README remains portable and does not depend on an old attachment.

| Asset | Content |
| --- | --- |
| `../images/readme/hero.svg` and `hero-mobile.svg` | Existing editable introduction to sovereignty, execution policies, and inference efficiency. |
| `../images/readme/manager.jpg` | Carbon configuration workspace with connected CRM and Workspace tools, draft chat, and Publish. |
| `../images/readme/policies.jpg` | Carbon policy configuration dialog with sample CRM policies. |
| `../images/readme/agents.jpg` | Agent dashboard with individual agents and a supervisor. |
| `../images/readme/chat.jpg` | Carbon chat workspace for the sample Revenue Operations agent. |
| `../images/readme/events.jpg` | Events Studio with sample scheduled workflows and illustrative run history. |
| `../images/readme/memory.jpg` | Memory workspace with sample guidance, preferences, sources, and usage. |
| `../images/readme/memory-retention.jpg` | Administrator memory lifecycle settings with sample retention rules. |
| [Carbon experience tour](../images/readme/carbon-tour.mp4) | Approximately 44 seconds, 1280 × 960, H.264/AAC, Kokoro synthesized narration, embedded English captions. |
| [Tour poster](../images/readme/carbon-tour-poster.png) | Static, clickable preview for Markdown readers. No automatic animation. |
| [English captions](../images/readme/carbon-tour.vtt) | WebVTT companion for browser players. |
| [Transcript](carbon-tour.md) | Plain-language narration for readers who cannot play the video. |
| [media-fixture.json](media-fixture.json) | Illustrative data used for the fresh standalone screenshots. |

## What these visuals represent

Standalone screenshots were captured on November 1, 2026 from the packaged frontend on main at `3991f5a22`, with a 1440 × 1000 browser viewport (1440 × 1280 for the dashboard and Events Studio) and a device scale factor of 2. They preserve the actual UI and use the Revenue Operations fixture above. Screens are saved directly as 2880-pixel-wide JPEGs without resizing or repeated compression.

The tour reuses the narrated Carbon refresh demo assembled on November 1, 2026 from real screens captured during the UI work merged in [PR #1077](https://github.com/cuga-project/cuga-agent/pull/1077). It covers the dashboard, configuration, chat, knowledge, memory, Events Studio, loading states, and mobile layouts. Its samples use Weather specialist and CUGA supervisor examples; they are separate from the Revenue Operations screenshots. The tour is an edited visual overview, not a screen recording of a completed agent task.

Internal endpoints, model names, policy names, tool counts, version numbers, workflows, run statuses, memory records, usage counts, and retention settings are illustrative. No model inference, flow execution, channel delivery, retention operation, or business-tool connection was performed for these captures. The video labels its sample data; the README and website repeat that context beside the player.

Both header graphics retain the original [CUGA owl logo](../../src/frontend_workspaces/extension/src/assets/cuga-logo.png), embedded without changing its artwork.

## Memory retention controls

![IBM Carbon memory administration with illustrative lifecycle rules](../images/readme/memory-retention.jpg)

The sample rules flag unused guidance after 90 days and remove conversation memory after 180 days. No retention operation was run. Availability depends on the backend; durable retention uses PostgreSQL and the bundled Evolve HTTP service.

## Refresh the captures

1. Build the current frontend using the [workspace instructions](../../src/frontend_workspaces/README.md), or serve the packaged production build from `src/cuga/frontend/dist`.
2. Use an isolated fixture service with sample data and no credentials or private records. The JSON fixture above documents the standalone screenshot values.
3. Capture `/manage`, `/manage/revenue-ops`, the policy configuration dialog, `/studio`, and `/chat/revenue-ops`. Open **Memory**, then **Administration → Memory lifecycle** for retention controls. Wait for the intended data and fonts; do not capture loading placeholders as settled screens.
4. Save screenshots at native resolution. Check every capture for empty content, clipped controls, unwanted focus outlines, and errors before replacing the repository assets.
5. When changing the tour, update the MP4, poster, WebVTT captions, and transcript together. Keep the sample-data disclosure and distinguish a visual overview from a live run.
6. Keep the matching media in `cuga-project/landing-site` synchronized. The website serves its own files; neither PR requires the other to merge first. Verify desktop and mobile playback, captions, dialog dismissal, and focus restoration.

For a future live workflow demo, use an isolated CRM environment with a working model endpoint and disposable records. Show the request, real approval, resulting report, tool execution, and run receipt. Publish only after checking the result and removing credentials.
