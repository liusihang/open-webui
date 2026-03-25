# OpenWebUI Chat Performance Handoff

## Scope

This handoff covers the chat performance investigation and fixes completed in this thread, focused on:

- slow chat open for large chats
- lag when tool calls are numerous
- severe UI jank when citation/source events are numerous

This document is intended for the next agent to continue from the current state without re-discovering the same hotspots.

## Key Findings

### 1. Slow chat open was primarily payload-driven, not network-driven

Observed earlier:

- some chat detail payloads were very large, including examples around `6MB+` and `12MB+`
- `/api/v1/chats/{id}` was expensive for large chats
- `tags` and `task` side requests were also reloading the full chat row

Root causes identified:

- full chat JSON loaded multiple times during chat open
- read-time sanitization on `get_chat_by_id()`
- first render blocked by ancillary requests

### 2. Tool-heavy chats were expensive because output was repeatedly re-serialized and re-rendered

Observed in backend:

- recursive tool loop in `backend/open_webui/utils/middleware.py`
- repeated `chat:completion` events carrying large `output` and serialized content
- sources/citations emitted during tool recursion

Observed in frontend:

- repeated message-level updates
- deep message comparisons via `JSON.stringify(...)`
- many `source` events causing many updates for the same message

### 3. Citation/source-heavy chats were especially bad because source events arrived one-by-one

Observed in frontend:

- regular `source` events appended to `message.sources` immediately
- each append could trigger message sync and expensive response component updates

## Commits Already On Branch

- `340f98ff5` `perf: speed up chat open path`
- `d476be11f` `fix: smooth chat streaming and remove tool flicker`

These are already on `main`.

## Aiserver Runtime State

As of this handoff:

- live host: `aiserver`
- live compose: `/srv/openwebui-migration/compose.yaml`
- live image: `open-webui:d476be11f`
- live status: `running / healthy`

Recent compose backups:

- `/srv/openwebui-migration/compose.yaml.bak-20260325-002525-codex-switch-340f98ff5`
- `/srv/openwebui-migration/compose.yaml.bak-20260325-013036-codex-switch-d476be11f`

Remote build staging directories used:

- `/home/aiserver/staging/openwebui-340f98ff50`
- `/home/aiserver/staging/openwebui-d476be11f`

Remote images built during this thread:

- `open-webui:340f98ff5`
- `open-webui:d476be11f`

## Code Changes Made In This Thread

### Backend hot-path reductions

Files:

- `backend/open_webui/models/chats.py`
- `backend/open_webui/routers/chats.py`
- `backend/open_webui/main.py`
- `backend/open_webui/test/util/test_chat_open_performance.py`

Changes:

- added lightweight tag lookup helper
- switched task ownership check to `is_chat_owner()`
- removed read-time sanitize from `get_chat_by_id()`

### Chat-open first render improvements

Files:

- `src/lib/components/chat/Chat.svelte`
- `src/lib/components/chat/loadChatPageData.ts`
- `src/lib/components/chat/loadChatPageData.test.ts`

Changes:

- chat detail fetch remains blocking
- `tags` and `taskIds` moved off the critical path
- ancillary requests load after first useful render

### Citation/source rendering improvements

Files:

- `src/lib/components/chat/Messages/citations.ts`
- `src/lib/components/chat/Messages/citations.test.ts`
- `src/lib/components/chat/Messages/Citations.svelte`
- `src/lib/components/chat/Messages/Markdown.svelte`
- `src/lib/components/chat/Messages/markdownPerformance.ts`
- `src/lib/components/chat/Messages/markdownPerformance.test.ts`

Changes:

- citation summary is lightweight first
- full citation document grouping happens lazily
- large markdown parsing is deferred

### Source event batching and lighter message sync

Files:

- `src/lib/components/chat/sourceUpdates.ts`
- `src/lib/components/chat/sourceUpdates.test.ts`
- `src/lib/components/chat/Messages/messageSync.ts`
- `src/lib/components/chat/Messages/messageSync.test.ts`
- `src/lib/components/chat/Messages/ResponseMessage.svelte`
- `src/lib/components/chat/Messages/MultiResponseMessages.svelte`
- `src/lib/components/chat/Chat.svelte`

Changes:

- source events are queued by `message_id`
- queued sources flush on `requestAnimationFrame`
- response message sync no longer relies on whole-object `JSON.stringify`
- sync decisions use lighter signatures for sources/output/status/code execution

### Backend source event reduction and lighter intermediate completion payloads

Files:

- `backend/open_webui/utils/middleware.py`
- `backend/open_webui/test/util/test_tool_source_context.py`

Changes:

- added `build_source_event_payload()`
- when multiple sources exist, backend emits a single `source` event carrying `data.sources`
- added `build_chat_completion_event_data()`
- intermediate tool-state `chat:completion` events can omit serialized `content`

## Targeted Verification Already Run

Backend:

- `./.venv/bin/python -m pytest backend/open_webui/test/util/test_chat_open_performance.py -q`
- `./.venv/bin/python -m pytest backend/open_webui/test/util/test_tool_source_context.py backend/open_webui/test/util/test_chat_open_performance.py -q`

Frontend:

- `npx vitest run src/lib/components/chat/loadChatPageData.test.ts`
- `npx vitest run src/lib/components/chat/Messages/citations.test.ts`
- `npx vitest run src/lib/components/chat/Messages/markdownPerformance.test.ts`
- `npx vitest run src/lib/components/chat/sourceUpdates.test.ts src/lib/components/chat/Messages/messageSync.test.ts src/lib/components/chat/loadChatPageData.test.ts src/lib/components/chat/Messages/citations.test.ts src/lib/components/chat/Messages/markdownPerformance.test.ts`

Notes:

- `npm run check` is noisy due to many pre-existing repository-wide issues and was not used as the success gate for these changes

## Likely Remaining Hotspots

If more performance work is needed, the next highest-value investigation is:

### Recursive tool loop still sends large output payloads

File:

- `backend/open_webui/utils/middleware.py`

Hot area:

- recursive tool loop around lines ~`5454-5868`

Why it still matters:

- even after reducing some payloads, tool recursion still appends to a growing `output`
- later updates still send the full `output` array
- frontend still receives large structured tool state snapshots

Best next step:

- inspect whether later recursive `chat:completion` updates can be expressed as smaller deltas
- consider sending compact tool-state events instead of repeating the full output structure

### Response components still do structuredClone on sync

Files:

- `src/lib/components/chat/Messages/ResponseMessage.svelte`
- `src/lib/components/chat/Messages/MultiResponseMessages.svelte`

Why it still matters:

- sync got cheaper, but each accepted update still clones the full message object
- very large message payloads can still be expensive

Possible follow-up:

- split `sources` / `output` / `code_executions` into lighter stores or dedicated derived state

## Non-Thread Changes Present In Working Tree

There are unrelated working-tree changes not part of this performance stream. Do not accidentally sweep them into the next commit.

Examples currently visible:

- `README.md`
- `backend/open_webui/models/knowledge.py`
- `backend/open_webui/routers/files.py`
- `backend/open_webui/routers/knowledge.py`
- `src/lib/apis/files/index.ts`
- `src/lib/apis/knowledge/index.ts`
- `package-lock.json`
- various Zotero-related tests and plan docs
- `skills/open-webui-aiserver-upgrade/`

Before committing new performance work, stage files explicitly.

## Recommended Next-Agent Start

1. Read this handoff.
2. Run:

```bash
git status --short
./.venv/bin/python -m pytest backend/open_webui/test/util/test_tool_source_context.py backend/open_webui/test/util/test_chat_open_performance.py -q
npx vitest run src/lib/components/chat/sourceUpdates.test.ts src/lib/components/chat/Messages/messageSync.test.ts src/lib/components/chat/loadChatPageData.test.ts src/lib/components/chat/Messages/citations.test.ts src/lib/components/chat/Messages/markdownPerformance.test.ts
```

3. If continuing performance work, focus next on recursive tool-loop payload size in `backend/open_webui/utils/middleware.py`.
