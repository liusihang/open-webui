# Chat Open Performance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce the latency and UI jank when opening large chats by removing redundant full-chat reads, unblocking the first render, and deferring expensive message/source processing.

**Architecture:** Keep the existing chat data model and routes, but split hot-path reads into lightweight ownership/meta checks and make the chat page render after the detail payload arrives instead of waiting for follow-up requests. Treat large-message rendering and oversized retrieval sources as a second phase so the first patch is low-risk and easy to verify.

**Tech Stack:** FastAPI, SQLAlchemy, Svelte, Vitest, pytest, gzip via Caddy

---

### Task 1: Add lightweight chat read helpers

**Files:**
- Modify: `backend/open_webui/models/chats.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py`

**Step 1: Write the failing backend tests**

Add tests that exercise lightweight access patterns without depending on the full `chat` JSON:

```python
def test_is_chat_owner_returns_true_without_loading_full_chat(...):
    assert Chats.is_chat_owner(chat_id, user_id, db=session) is True


def test_get_chat_tags_by_id_uses_meta_only_helper(...):
    tag_ids = Chats.get_chat_tag_ids(chat_id, user_id, db=session)
    assert tag_ids == ["tag-a", "tag-b"]
```

**Step 2: Run the tests to confirm failure**

Run: `python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q`

Expected: failure because `get_chat_tag_ids` does not exist yet.

**Step 3: Implement minimal helpers**

Add small helpers in `backend/open_webui/models/chats.py`:

```python
def get_chat_tag_ids(self, id: str, user_id: str, db: Optional[Session] = None) -> list[str]:
    result = db.query(Chat.meta).filter_by(id=id, user_id=user_id).first()
    ...


def get_chat_owner_id(self, id: str, db: Optional[Session] = None) -> Optional[str]:
    result = db.query(Chat.user_id).filter_by(id=id).first()
    ...
```

Keep them column-only reads; do not load `Chat.chat`.

**Step 4: Run the tests to confirm pass**

Run: `python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/open_webui/models/chats.py backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py
git commit -m "perf: add lightweight chat lookup helpers"
```

### Task 2: Remove redundant full-chat reads from hot routes

**Files:**
- Modify: `backend/open_webui/routers/chats.py`
- Modify: `backend/open_webui/main.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py`

**Step 1: Write the failing route tests**

Add tests that verify tags and task-id routes work for large chats without needing a full chat payload:

```python
def test_get_chat_tags_by_id_returns_tags_for_large_chat(...):
    response = client.get(f"/api/v1/chats/{chat_id}/tags", headers=auth_headers)
    assert response.status_code == 200
    assert [tag.id for tag in response.json()] == ["tag-a", "tag-b"]


def test_list_tasks_by_chat_id_checks_owner_without_full_chat(...):
    response = client.get(f"/api/tasks/chat/{chat_id}", headers=auth_headers)
    assert response.status_code == 200
    assert "task_ids" in response.json()
```

**Step 2: Run the targeted tests**

Run: `python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q`

Expected: FAIL before the route refactor is wired up.

**Step 3: Refactor the routes**

- In `backend/open_webui/routers/chats.py`, replace `Chats.get_chat_by_id_and_user_id(...)` inside `get_chat_tags_by_id` with `Chats.get_chat_tag_ids(...)`.
- In `backend/open_webui/main.py`, replace `Chats.get_chat_by_id(chat_id)` inside `/api/tasks/chat/{chat_id}` with `Chats.get_chat_owner_id(chat_id)` or `Chats.is_chat_owner(chat_id, user.id)`.
- Preserve response shape exactly.

**Step 4: Re-run the targeted tests**

Run: `python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/open_webui/routers/chats.py backend/open_webui/main.py backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py
git commit -m "perf: avoid redundant full chat loads on open"
```

### Task 3: Unblock chat page first paint

**Files:**
- Modify: `src/lib/components/chat/Chat.svelte`
- Create: `src/lib/components/chat/loadChatLifecycle.test.ts`

**Step 1: Write the failing frontend test**

Extract the load ordering into a tiny helper or testable function and assert first paint does not wait on tags/task requests:

```ts
it('renders chat after detail fetch and defers secondary requests', async () => {
  const calls: string[] = [];
  await loadChatLifecycle({
    getChatById: async () => { calls.push('chat'); return mockChat; },
    getTagsById: async () => { calls.push('tags'); return []; },
    getTaskIdsByChatId: async () => { calls.push('tasks'); return { task_ids: [] }; },
  });
  expect(calls[0]).toBe('chat');
});
```

**Step 2: Run the targeted frontend test**

Run: `npm run test:frontend -- src/lib/components/chat/loadChatLifecycle.test.ts`

Expected: FAIL because the helper or deferred behavior does not exist yet.

**Step 3: Implement minimal UI sequencing**

In `src/lib/components/chat/Chat.svelte`:
- Keep `getChatById` as the blocking request.
- Move `getTagsById` and `getTaskIdsByChatId` to non-blocking follow-up work after `history`, `chatTitle`, and `chatFiles` are set.
- If practical, load tags and tasks in parallel with `Promise.allSettled`.
- Do not change API shape or chat behavior.

**Step 4: Run the test and type check**

Run:

```bash
npm run test:frontend -- src/lib/components/chat/loadChatLifecycle.test.ts
npm run check
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/lib/components/chat/Chat.svelte src/lib/components/chat/loadChatLifecycle.test.ts
git commit -m "perf: unblock first render when opening chats"
```

### Task 4: Cut unnecessary hot-path sanitize work

**Files:**
- Modify: `backend/open_webui/models/chats.py`
- Test: `backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py`

**Step 1: Write the failing regression test**

Add a test that confirms read paths do not mutate or sanitize large chats on every fetch:

```python
def test_get_chat_by_id_does_not_rewrite_clean_chat_row(...):
    chat = Chats.get_chat_by_id(chat_id, db=session)
    assert chat.id == chat_id
```

Use a spy/monkeypatch around `_sanitize_chat_row` if needed.

**Step 2: Run the test**

Run: `python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q`

Expected: FAIL before behavior is changed.

**Step 3: Implement the minimal change**

- Remove `_sanitize_chat_row` from the read path in `get_chat_by_id`.
- Keep sanitization in write/import/update paths only.
- If there is concern about legacy dirty data, add a guarded fallback path rather than unconditional recursive sanitize on every read.

**Step 4: Re-run the backend tests**

Run: `python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/open_webui/models/chats.py backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py
git commit -m "perf: stop sanitizing chat payloads on read"
```

### Task 5: Add a guarded second-phase payload reduction

**Files:**
- Modify: `src/lib/components/chat/Messages/Citations.svelte`
- Modify: `src/lib/components/chat/Messages/Markdown.svelte`
- Modify: `src/lib/components/chat/Messages.svelte`
- Optional Modify: `backend/open_webui/models/chat_messages.py`
- Test: `src/lib/components/chat/historySync.test.ts`

**Step 1: Write the failing UI regression test**

Add a test for large message handling that ensures expensive work is delayed:

```ts
it('does not eagerly process heavy citations before expansion', () => {
  expect(buildVisibleCitations(hugeSources, false)).toEqual([]);
});
```

**Step 2: Run the targeted test**

Run: `npm run test:frontend -- src/lib/components/chat/historySync.test.ts`

Expected: FAIL before adding the helper.

**Step 3: Implement minimal guarded optimizations**

- In `src/lib/components/chat/Messages/Citations.svelte`, defer heavy `sources.reduce(...)` work until citations are shown.
- In `src/lib/components/chat/Messages/Markdown.svelte`, add a large-content guard so extremely long content is parsed lazily or in chunks.
- In `src/lib/components/chat/Messages.svelte`, keep the current `messagesCount` behavior but avoid rebuilding more messages than necessary during initial open.
- Do not attempt full virtualization in this patch unless the first four tasks are insufficient.

**Step 4: Run frontend checks**

Run:

```bash
npm run test:frontend -- src/lib/components/chat/historySync.test.ts
npm run check
```

Expected: PASS.

**Step 5: Commit**

```bash
git add src/lib/components/chat/Messages/Citations.svelte src/lib/components/chat/Messages/Markdown.svelte src/lib/components/chat/Messages.svelte src/lib/components/chat/historySync.test.ts
git commit -m "perf: defer heavy chat rendering work"
```

### Task 6: Verify with targeted perf checks

**Files:**
- Modify: `scripts/openwebui_regression.py`
- Create: `docs/plans/chat-open-benchmark-notes.md`

**Step 1: Add a lightweight measurement mode**

Extend `scripts/openwebui_regression.py` with a small mode that times:
- `/api/v1/chats/{id}`
- `/api/v1/chats/{id}/tags`
- `/api/tasks/chat/{id}`

for a provided chat ID.

**Step 2: Run before/after checks**

Run:

```bash
python3 scripts/openwebui_regression.py quick --base-url https://ai.shuofang.cloud
```

If the script changes are too large for this patch, use repeatable `curl` commands and save the results in `docs/plans/chat-open-benchmark-notes.md`.

**Step 3: Validate no regressions**

Run:

```bash
python3 -m pytest backend/open_webui/test/apps/webui/routers/test_chat_open_performance.py -q
npm run test:frontend -- src/lib/components/chat/loadChatLifecycle.test.ts
npm run check
```

Expected: PASS.

**Step 4: Commit**

```bash
git add scripts/openwebui_regression.py docs/plans/chat-open-benchmark-notes.md
git commit -m "chore: add chat open perf verification"
```

**Step 5: Rollout**

- Deploy to a staging instance first.
- Verify one small chat and one known large chat.
- Compare first-contentful display and route timings before production rollout.

