# Streaming Smoothing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make streaming chat text appear at a steadier cadence in the main chat UI while preserving the existing fade effect for streaming text.

**Architecture:** Keep the backend streaming protocol unchanged and add a lightweight frontend reveal buffer in the chat rendering path. The renderer tracks the latest target content, drains queued suffix text on a short fixed cadence, and flushes immediately when streaming completes or content changes non-monotonically.

**Tech Stack:** Svelte, TypeScript, Vitest

---

### Task 1: Add streaming smoothing helpers

**Files:**
- Create: `src/lib/components/chat/streaming.ts`
- Test: `src/lib/components/chat/streaming.test.ts`

**Step 1: Write the failing test**

Add tests that cover monotonic suffix queuing, adaptive draining, and immediate reset on non-prefix updates.

**Step 2: Run test to verify it fails**

Run: `npm run test:frontend -- src/lib/components/chat/streaming.test.ts`

Expected: FAIL because the helper module does not exist yet.

**Step 3: Write minimal implementation**

Implement a small pure helper that:
- keeps `rendered`, `target`, and `queue`
- appends only new suffix text for normal streaming growth
- drains queue in bounded chunks
- snaps immediately on non-prefix changes

**Step 4: Run test to verify it passes**

Run: `npm run test:frontend -- src/lib/components/chat/streaming.test.ts`

Expected: PASS

### Task 2: Integrate smoothing into message rendering

**Files:**
- Modify: `src/lib/components/chat/Messages/ContentRenderer.svelte`
- Reuse: `src/lib/components/chat/Messages/ResponseMessage.svelte`

**Step 1: Write the failing test**

Use the helper tests from Task 1 as the regression guard for the smoothing behavior and verify the renderer still compiles with the helper wired in.

**Step 2: Run test/type check to verify the current integration is missing**

Run: `npm run check`

Expected: FAIL after importing the helper until the renderer integration is complete.

**Step 3: Write minimal implementation**

Update `ContentRenderer.svelte` to:
- keep a local smoothed `renderedContent`
- use the helper while `done === false`
- flush immediately when `done === true`
- pass the smoothed content into `Markdown`

**Step 4: Run verification**

Run: `npm run test:frontend -- src/lib/components/chat/streaming.test.ts`

Run: `npm run check`

Expected: PASS
