# ManagePage Save Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract ManagePage save/submit paths into one React hook per family under `src/manage/hooks/` with no user-facing behavior change (#432).

**Architecture:** Move each save family (knowledge, tools, LLM, agent, special instructions, full draft, publish) into its own hook that owns timers/AbortControllers/API/toasts. `ManagePage.tsx` keeps form state + JSX and wires hooks. Shared `isAbortError` lives in `saveHelpers.ts`.

**Tech Stack:** React 18, TypeScript, existing `./api` client, Carbon toasts already in ManagePage.

**Spec:** `docs/superpowers/specs/2026-07-09-managepage-save-hooks-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `src/frontend_workspaces/frontend/src/manage/hooks/saveHelpers.ts` | `isAbortError` |
| `.../useKnowledgeDraftSave.ts` | Knowledge debounced PATCH + draftSaveStatus + slow/fail timers + adaptation/409 |
| `.../useToolsDraftSave.ts` | Tools debounced PATCH effect |
| `.../useLlmDraftSave.ts` | LLM blur-scheduled PATCH |
| `.../useAgentDraftSave.ts` | Agent name/description PATCH |
| `.../useSpecialInstructionsDraftSave.ts` | Special instructions debounced PATCH |
| `.../useFullDraftSave.ts` | Full draft POST + import-triggered save |
| `.../usePublishConfig.ts` | Publish click, reindex confirm, postManageConfig + poll |
| `ManagePage.tsx` | Orchestration only for saves |

Toast type used by hooks:

```ts
export type AddToast = (
  kind: "error" | "info" | "success" | "warning",
  title: string,
  subtitle: string,
) => void;
```

---

### Task 1: `saveHelpers.ts` + unit test

**Files:**
- Create: `src/frontend_workspaces/frontend/src/manage/hooks/saveHelpers.ts`
- Create: `src/frontend_workspaces/frontend/src/manage/hooks/saveHelpers.test.ts`
- Modify: `src/frontend_workspaces/frontend/package.json` (add vitest script if missing — prefer running via `npx vitest` without new deps if vitest already in workspace; else add vitest as devDep of frontend or run with node assert)

- [ ] **Step 1: Write failing test for `isAbortError`**

```ts
import { describe, it, expect } from "vitest";
import { isAbortError } from "./saveHelpers";

describe("isAbortError", () => {
  it("detects DOMException AbortError", () => {
    expect(isAbortError(new DOMException("aborted", "AbortError"))).toBe(true);
  });
  it("detects Error with name AbortError", () => {
    const e = new Error("aborted");
    e.name = "AbortError";
    expect(isAbortError(e)).toBe(true);
  });
  it("rejects other errors", () => {
    expect(isAbortError(new Error("network"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test — expect fail (module missing)**

- [ ] **Step 3: Implement `saveHelpers.ts` by moving the existing function from ManagePage**

```ts
export function isAbortError(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof Error && err.name === "AbortError") return true;
  return false;
}

export type AddToast = (
  kind: "error" | "info" | "success" | "warning",
  title: string,
  subtitle: string,
) => void;
```

- [ ] **Step 4: Run test — expect pass**

- [ ] **Step 5: Commit** `test: add isAbortError helper for manage save hooks`

---

### Task 2: `useLlmDraftSave` + `useAgentDraftSave` + `useSpecialInstructionsDraftSave`

**Files:**
- Create the three hook files
- Modify `ManagePage.tsx`: remove inline implementations; call hooks; keep UI handlers pointing at returned functions

- [ ] **Step 1: Extract `useLlmDraftSave`** — move `saveLlmDraft`, `scheduleLlmDraftSave`, `llmAbortRef`, `llmBlurSaveRef` from ManagePage (~1077–1108). Hook owns abort + timer; receives `llmConfigRef`, `effectiveAgentId`, `addToast`, `setDraftSaving`, `setCurrentVersion`. Return `{ saveLlmDraft, scheduleLlmDraftSave }`. Abort on unmount inside hook.

- [ ] **Step 2: Extract `useAgentDraftSave`** — move `saveAgentDraft` + `agentAbortRef`. Return `{ saveAgentDraft }`.

- [ ] **Step 3: Extract `useSpecialInstructionsDraftSave`** — move save + schedule + refs. Return `{ saveSpecialInstructionsDraft, scheduleSpecialInstructionsDraftSave }`.

- [ ] **Step 4: Wire into ManagePage; delete moved code and page-level abort refs for these families; remove them from the page unmount abort effect (hooks clean up themselves).

- [ ] **Step 5: Commit** `refactor: extract LLM/agent/special-instructions draft save hooks`

---

### Task 3: `useToolsDraftSave`

**Files:**
- Create: `useToolsDraftSave.ts`
- Modify: `ManagePage.tsx` (remove tools autosave effect ~1177–1220)

- [ ] **Step 1: Move tools debounced effect into hook** — params: `tools`, `effectiveAgentId`, `addToast`, `skipDraftSaveRef`, `setDraftSaving`, `setCurrentVersion`. Own `toolsAbortRef` + timeout; abort on unmount.

- [ ] **Step 2: Wire ManagePage; remove tools refs from page unmount cleanup.**

- [ ] **Step 3: Commit** `refactor: extract tools draft autosave hook`

---

### Task 4: `useKnowledgeDraftSave`

**Files:**
- Create: `useKnowledgeDraftSave.ts`
- Modify: `ManagePage.tsx`

- [ ] **Step 1: Move into hook:**
  - `DraftSaveStatus` type + `draftSaveStatus` state
  - slow/fail 25s/90s effect
  - knowledge autosave effect (abort, forceImmediate, 422/409/auto-reindex)
  - `knowledgeAbortRef`
  - Return `{ draftSaveStatus, setDraftSaveStatus, knowledgeAbortRef }` (or hide abort ref if only used internally — page currently aborts on 90s via ref inside the slow effect, so keep abort inside hook)

- [ ] **Step 2: Params:** `knowledgeConfig`, `effectiveAgentId`, `addToast`, `skipDraftSaveRef`, `forceImmediateSaveRef`, setters for `currentVersion`, `adaptationServerError`, `autoReindexTrigger`.

- [ ] **Step 3: Wire ManagePage UI that reads `draftSaveStatus` / `setDraftSaveStatus` / `forceImmediateSaveRef`.

- [ ] **Step 4: Commit** `refactor: extract knowledge draft autosave hook`

---

### Task 5: `useFullDraftSave`

**Files:**
- Create: `useFullDraftSave.ts`
- Modify: `ManagePage.tsx`

- [ ] **Step 1: Move `performDraftSave` + importStatus effect that calls it.**

- [ ] **Step 2: Params:** `assembleConfig`, `effectiveAgentId`, `addToast`, `setDraftSaving`, `setCurrentVersion`, `importStatus`.

- [ ] **Step 3: Return `{ performDraftSave }`.**

- [ ] **Step 4: Commit** `refactor: extract full draft save hook`

---

### Task 6: `usePublishConfig`

**Files:**
- Create: `usePublishConfig.ts`
- Modify: `ManagePage.tsx`

- [ ] **Step 1: Move `handleSaveClick`, `saveConfig`, `showReindexConfirm` state (or keep confirm state on page and only move `saveConfig` — prefer moving both confirm state + handlers into hook for cohesion).**

- [ ] **Step 2: Params:** `assembleConfig`, `agentName`, `knowledgeConfig`, `knowledgeReindexNeeded`, `knowledgeDocCount`, `effectiveAgentId`, `addToast`, setters for save status / version / live knowledge / knowledge snapshot / doc count, `refreshKnowledgeHealth`, `loadHistory`.

- [ ] **Step 3: Return `{ saveStatus, setSaveStatus, showReindexConfirm, setShowReindexConfirm, handleSaveClick, saveConfig }` — if `saveStatus` already used elsewhere on page for load errors, keep `saveStatus` on page and only move publish handlers; match current ownership (page already has `saveStatus` for load + publish — keep state on page, pass setters).

- [ ] **Step 4: Commit** `refactor: extract publish config hook`

---

### Task 7: Final cleanup + verify

- [ ] **Step 1:** Confirm ManagePage has no remaining inline PATCH/POST save bodies for the seven families; `isAbortError` imported from helpers or unused.

- [ ] **Step 2:** Run TypeScript check: `pnpm exec tsc --noEmit -p src/frontend_workspaces/frontend` (or project-equivalent).

- [ ] **Step 3:** Run `saveHelpers` tests.

- [ ] **Step 4:** Commit any leftover wiring: `refactor: finish ManagePage save-hook extraction (#432)`

- [ ] **Step 5:** Do **not** open a PR unless asked.

---

## Verification checklist (manual)

- Knowledge field edit → Saving… → Saved pill; preset Use → immediate save
- Tools change → draft toast after debounce
- LLM blur / agent blur / special instructions
- JSON import → full draft save
- Publish (+ reindex confirm when knowledge index fields changed with docs)
