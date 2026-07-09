# Design: Split ManagePage save/submit into focused hooks

**Issue:** [#432](https://github.com/cuga-project/cuga-agent/issues/432)  
**Date:** 2026-07-09  
**Status:** Approved for implementation planning

## Goal

Extract save/submit behavior from `ManagePage.tsx` (~2,800 lines) into one React hook per save family so domain changes (e.g. knowledge autosave) live in a dedicated file. No intentional user-facing behavior change.

## Non-goals

- Moving JSX / section UI out of `ManagePage.tsx`
- Changing API contracts, debounce timings, toast copy, or abort/cancellation semantics
- Introducing a generic “debounced PATCH factory” abstraction
- Full structural split of ManagePage into section components (follow-up)

## Approach

**One hook per save family** under a flat folder, mirroring the backend `manage_routes` package split (#429).

```
src/frontend_workspaces/frontend/src/manage/hooks/
  useKnowledgeDraftSave.ts
  useToolsDraftSave.ts
  useLlmDraftSave.ts
  useAgentDraftSave.ts
  useSpecialInstructionsDraftSave.ts
  useFullDraftSave.ts
  usePublishConfig.ts
  saveHelpers.ts
```

`ManagePage.tsx` remains composition/orchestration: form state, loaders, and UI wire into these hooks.

## Boundaries

Each hook:

- **Owns:** debounce timers, per-family `AbortController`s, domain-specific save status (e.g. knowledge `draftSaveStatus`), API calls for that path, toast messages for that path
- **Receives:** `effectiveAgentId`, `addToast`, relevant config values/refs, and page-owned setters (`setCurrentVersion`, `setDraftSaving`, etc.)
- **Does not own:** form field state, JSX, `loadLatest` / history loaders

Page-owned cross-cutting refs (passed into hooks as needed):

- `skipDraftSaveRef` — suppress autosave during load
- `forceImmediateSaveRef` — bypass knowledge debounce on preset clicks

Shared helper module:

- `isAbortError` (moved from `ManagePage.tsx`)
- Only other tiny pure helpers if extraction requires them; no speculative utilities

## Data flow & error handling (parity)

| Hook | Trigger | Success | Failure |
|------|---------|---------|---------|
| `useKnowledgeDraftSave` | Debounced effect on `knowledgeConfig` (0ms if force-immediate) | `draftSaveStatus: saved`; auto-reindex trigger; clear adaptation error | 422 → adaptation error + failed pill; 409 reindex → toast + failed; other → failed pill |
| `useToolsDraftSave` | Debounced effect on `tools` | Success toast; partial `tool_errors` → warnings | Error toast |
| `useLlmDraftSave` | Blur-scheduled (~100ms) | Success toast | Error toast |
| `useSpecialInstructionsDraftSave` | Debounced schedule; optional toast | Optional success toast | Toast only when `showToast` |
| `useAgentDraftSave` | Explicit call (blur/save) | Success toast | Error toast |
| `useFullDraftSave` | `performDraftSave` + import-status effect | Success / partial warnings | Error toast |
| `usePublishConfig` | Publish click → optional reindex confirm → `postManageConfig` | Version/history refresh; live knowledge snapshot; reindex poll | Name validation + error toasts; keep saving spinner during reindex poll |

Abort: each family keeps its own controller; superseded requests stay silent via `isAbortError`, matching `CLIENT_CANCELLATION_CONTRACT.md`.

## Testing / verification

- Preserve existing behavior; no new automated test suite required for this refactor unless trivial pure helpers are extracted
- Manual smoke on Manage page: knowledge autosave (including 422/409 paths if exercisable), tools debounce, LLM blur save, special instructions, agent meta, full draft save after import, Publish (+ reindex confirm when needed)

## Success criteria

- Each save family lives in its own hook file under `manage/hooks/`
- `ManagePage.tsx` no longer contains the inline save/PATCH effect bodies for those families
- Behavior and UX match pre-refactor (toasts, pills, abort silence, debounce timings)
- No PR until explicitly requested; work lands on a dedicated branch
