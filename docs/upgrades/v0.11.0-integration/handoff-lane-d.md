# Lane D handoff: frontend/UI/accessibility

## Truth surface and ownership

- Worktree: `/Users/liusihang/.codex/worktrees/2618/openwebui`
- Branch: `codex/v011-integration-lane-d`
- Required base: `codex/v011-upstream-integration-base` at `1f93cd9a3b6d8db26f5abbccfd784052ab6e0b9d`
- Official donor: `f9590b8017199e56d5e953657e6498e3cef1d246` (`v0.11.0`)
- Shared upstream base: `ecd48e2f718220a6400ecf49eafd4867a38feb10` (`v0.10.2`)
- Owned paths: `src/**`, frontend-only tests/config when required, and frontend static assets/themes.
- Forbidden paths/surfaces: backend files, live service, live database, other lanes' work.

## Acceptance target

Integrate every non-excluded official v0.11 frontend behavior while preserving the custom AgentScope renderer and protocol contract. Verify focused frontend tests, touched-area Svelte/type checks where practical, a production frontend build, exclusion searches, and a clean scoped diff before committing.

## Protected exclusions

1. No official second Sub-agents runtime, admin toggle, `delegate_task` wiring, or duplicate stock subagent-result renderer.
2. No stock Agent renderer may replace or flatten custom commentary/final, approvals, user input, cancellation, reconnect/refresh recovery, artifacts, citations, tool timeline, or subagent attribution.
3. No frontend exposure of `list_chat_files`, `grep_chat_files`, `query_chat_files`, or a Files capability backed only by those excluded tools.

## Checkpoints

### CP0 — initialized (2026-07-28)

- Confirmed the worktree started at the exact required merge commit and both required parents are ancestors.
- Confirmed `codex/v011-upstream-integration-base` points to the required commit.
- Created the isolated lane branch `codex/v011-integration-lane-d`.
- Read `README.md`, `interfaces.md`, and `TODO.md` before implementation.
- Read the truth-surface, TDD, and verification-before-completion instructions.
- No backend, live service, or live database action performed.

## Current checkpoint

- Status: CP3 implementation and verification complete; ready to commit.
- Next: stage the audited frontend/docs diff, create the Lane D commit, and report the immutable SHA to the integration thread.
- Stop condition honored: all backend requirements are recorded below and no backend source, live service, or live database was changed.

### CP1 — git audit and RED contract established (2026-07-28)

- Official `v0.10.2..v0.11.0` frontend delta: 516 paths under `src/` and frontend `static/` assets.
- Provisional merge imported 515 changed paths relative to custom first parent.
- `git show --remerge-diff` identified 19 frontend content-conflict paths requiring semantic reconciliation:
  - admin settings: `Documents.svelte`, `General.svelte`, `Interface.svelte`, `Models.svelte`
  - chat: `Chat.svelte`, `MessageInput.svelte`, `Messages.svelte`, `Citations.svelte`, `ContentRenderer.svelte`, `StatusHistory.svelte`, `ModelSelector.svelte`, `ModelSelector/Selector.svelte`, `Navbar.svelte`
  - shared/layout/workspace: `ToolCallDisplay.svelte`, `Sidebar.svelte`, `KnowledgeBase.svelte`, app `+layout.svelte`
  - translations: `en-US`, `zh-CN`
- Auto-merged but excluded official frontend wiring found:
  - `Subagents.svelte` plus both admin settings entry points and `/configs/subagents` API helpers
  - `SubagentResultRow.svelte` plus `UserMessage.svelte` wiring
  - `delegate_task` special rendering in `structuredOutput.ts`
  - `subagents` and `files` model Builtin Tools capabilities
- Added `src/lib/components/chat/v011Integration.presentation.test.ts` before production changes.
- Installed dependencies with Node `v22.22.0` via `npm ci` (1122 packages; install succeeded; npm reported 23 dependency audit findings, unchanged by this lane).
- RED evidence: focused Vitest ran 5 tests and failed 4 for the intended missing integration/exclusion behavior; 1 settings URL/announcement assertion already passed.
- No backend, live service, or live database action performed.

### CP2 — exclusions and conflict-owned frontend contract integrated (2026-07-28)

- Removed the official Subagents admin component, both admin entry points, `/configs/subagents` frontend API helpers, and the duplicate stock `SubagentResultRow` path.
- Removed the `delegate_task` special renderer and the `subagents` Builtin Tools capability.
- Removed the official `files` Builtin Tools capability whose advertised behavior depended on the excluded chat-file tools; ordinary attachments, FileNav, Terminal Chatfile, and knowledge UI remain intact.
- Reconciled admin conflict paths:
  - General retains custom announcement normalization/validation and popup fields while restoring v0.11 settings sections, response watermark, WebUI URL, events, and redesigned banners.
  - Documents restores v0.11 PDF loader, Datalab Marker, external loader, and redesigned engine controls while retaining custom image-asset extraction and PaddleOCR/MinerU fields.
  - Interface retains `GLOBAL_SYSTEM_PROMPT` and restores `CONTEXT_COMPACTION_MODEL` in the redesigned section layout.
  - Models retains custom conversation-mode profiles in the v0.11 admin redesign.
- Reconciled chat conflict paths:
  - custom conversation-mode authority, profile revision, reasoning, AgentRun IDs, and Agent transcript remain intact;
  - restored v0.11 embedded note-chat history/close header, chat-variable fallback, embedded title updates, central chat-list refresh store, and response-scroll preference scheduling;
  - repaired provisional merge syntax/duplication in `Chat.svelte` and `ModelSelector.svelte`.
- Restored official translation keys lost from the `en-US` and `zh-CN` conflict hunks while retaining custom reasoning keys.
- Focused GREEN evidence: `v011Integration.presentation.test.ts` passes all 22 assertions, including Svelte compiler coverage for all 17 conflict-owned components.
- Initial production build reached `pyodide:fetch` but its SciPy CDN request stalled; the local build was safely interrupted and restarted with the configured Clash proxy. The proxied retry is still running at this checkpoint.
- No backend source, live service, or live database action performed.

### CP3 — structural repair and final verification (2026-07-28)

- The full proxied `npm run build` completed after fetching and hash-locking the missing Pyodide/PyPI wheels; a fresh final build then reused that local cache and completed with exit code 0.
- Production-build diagnostics exposed two provisional-conflict defects that ordinary compilation did not reject:
  - `Interface.svelte` implicitly closed two containers where the custom global-system-prompt block had been spliced around the official redesign;
  - `Navbar.svelte` rendered and received the official `title` prop without exporting it, and retained an ambiguous self-closing real `<button>`.
- Added RED assertions for those structural contracts, then restored the official section hierarchy, explicit DOM closure, `CONTEXT_COMPACTION_MODEL` initialization, Navbar `title` export, and explicit button closure while preserving the custom Agent mode selector and global system prompt.
- Final focused integration guard: 24/24 assertions pass; all 17 conflict-owned Svelte components compile.
- Final frontend suite: 35/35 files and 385/385 tests pass.
- Final production build: Pyodide cache reused, 6408 modules transformed, static adapter wrote `build`, exit code 0.
- `git diff --check` passes; `git diff --name-only -- backend` is empty; conflict-marker search is empty.
- Excluded-symbol search (`delegate_task`, the three chat-file tools, Subagents config helpers/components, and `SubagentResultRow`) finds only negative assertions in the guard test and no production source usage.
- No tracked Pyodide wheel or lock-file diff was produced; downloaded wheels remain ignored build cache.
- No backend source, live service, or live database action performed.

## Audit ledger

- Non-conflicting official v0.11 frontend additions and redesign paths are already present in the provisional merge and remain in scope for build/test verification.
- Conflict decisions must preserve custom behavior and add the official side rather than replace whole files.
- Custom AgentScope authority is rooted in `src/lib/components/chat/AgentEvents/**`, `agentModeRequest.ts`, conversation-mode profile modules, and the Agent branch in `ResponseMessage.svelte`; these must remain separate from ordinary chat rendering.

## Backend and cross-lane contracts required

Lane D did not implement or probe these server contracts. The combined integration must provide and test them before browser acceptance:

1. Admin general settings — `GET/POST /api/v1/auths/admin/config` must round-trip:
   - custom announcement fields `ANNOUNCEMENT_MODAL_ENABLED`, `ANNOUNCEMENT_MODAL_KEY`, `ANNOUNCEMENT_MODAL_TITLE`, `ANNOUNCEMENT_MODAL_CONTENT`;
   - official v0.11 fields `RESPONSE_WATERMARK`, `WEBUI_URL`, `CHANNEL_MODEL_RESPONSE_MODE`, `ENABLE_AUTOMATIONS`, `ENABLE_CALENDAR`, `ENABLE_CHANNELS`, `ENABLE_COMMUNITY_SHARING`, `ENABLE_FOLDERS`, `ENABLE_MEMORIES`, `ENABLE_MEMORY_SYSTEM_CONTEXT`, `ENABLE_MESSAGE_RATING`, `ENABLE_NOTES`, `ENABLE_USER_STATUS`, `ENABLE_USER_WEBHOOKS`, and `FOLDER_MAX_FILE_COUNT`.
2. Task/interface settings — `GET /api/v1/tasks/config` and `POST /api/v1/tasks/config/update` must round-trip the v0.11 task-model and generation settings already submitted by `Interface.svelte`.
3. Chat/interface settings — `GET/POST /api/v1/chats/config` must round-trip `GLOBAL_SYSTEM_PROMPT`, `CONTEXT_COMPACTION_MODEL`, `ENABLE_CONTEXT_COMPACTION`, `CONTEXT_COMPACTION_TOKEN_THRESHOLD`, `CONTEXT_COMPACTION_TOKEN_CAP`, `CONTEXT_COMPACTION_RETENTION_PERCENTAGE`, and `CONTEXT_COMPACTION_PROMPT_TEMPLATE`.
4. Retrieval settings — `GET /api/v1/retrieval/config` and `POST /api/v1/retrieval/config/update` must round-trip the official `PDF_LOADER_MODE` and complete `DATALAB_MARKER_*` family while preserving the custom `RAG_EXTRACT_DOCUMENT_IMAGE_ASSETS`, PaddleOCR-VL, MinerU, external-loader, MIME/extension, Docling, Document Intelligence, Mistral OCR, and Tika fields submitted by `Documents.svelte`.
5. Chat persistence/completion:
   - `POST /api/v1/chats/new` must accept `chat.mode` plus the top-level `variables` object and return `id` and, when applicable, `mode_profile_revision_id`;
   - `POST /api/chat/completions` must continue accepting custom `chat_mode`, `mode_profile_revision_id`, capability/profile payloads, reasoning, AgentRun identifiers, ordinary v0.11 request fields, and `chat_variables` for new/temporary chats;
   - the event stream must keep `event.chat_id` on `chat:title`; Lane D uses it to update embedded note-chat titles before refreshing the central chat list.
6. AgentScope authority — existing AgentRun endpoints/events must remain the only subagent runtime and must preserve commentary/final phases, approval, user input, cancellation, reconnect/refresh recovery, artifacts, citations, tool timeline, and subagent attribution. Do not register `/api/v1/configs/subagents`, `delegate_task`, or the official stock subagent result model.
7. Files boundary — do not register or advertise `list_chat_files`, `grep_chat_files`, or `query_chat_files`. Keep ordinary file attachments, FileNav/Terminal Chatfile, terminal tools, and knowledge/retrieval APIs intact.

Cross-lane integration note: this commit is frontend/docs-only and is based directly on `1f93cd9a3b6d8db26f5abbccfd784052ab6e0b9d`, like the other lanes. It can be cherry-picked in any order relative to disjoint backend lanes. After all lanes are combined, rerun the frontend suite/build and perform authenticated browser acceptance only against a disposable integration backend that implements contracts 1–7; do not point the UI at the formal live service for acceptance.

## Verification ledger

- `PATH=/Users/liusihang/.nvm/versions/node/v22.22.0/bin:$PATH npm ci`
  - exit 0; 1122 packages installed; npm reported 23 dependency audit findings.
- `PATH=...node-v22.22.0... npm run test:frontend -- src/lib/components/chat/v011Integration.presentation.test.ts --run`
  - final exit 0; 1 file, 24/24 tests.
- `PATH=...node-v22.22.0... npm run test:frontend -- src/lib/components/chat/ConversationMode.presentation.test.ts --run`
  - exit 0; 1 file, 28/28 tests.
- `PATH=...node-v22.22.0... npm run test:frontend -- --run`
  - final exit 0; 35/35 files, 385/385 tests.
- `node_modules/.bin/svelte-check --tsconfig ./tsconfig.json --output machine` filtered to Lane D paths
  - repository-wide check remains non-zero/noisy from existing strict-TS debt; after repair, `Interface.svelte` and `BuiltinTools.svelte` have no filtered diagnostics and the new compiler guards reject the discovered structural warnings. `Navbar.svelte` retains official/baseline type-model mismatches for runtime Settings/Config/banner fields; wider legacy conflict files retain pre-existing implicit-any/type debt. This lane does not claim a clean repository-wide type check.
- `NODE_OPTIONS=--max-old-space-size=8192 HTTPS_PROXY=http://192.168.2.201:7897 HTTP_PROXY=http://192.168.2.201:7897 ALL_PROXY=socks5://192.168.2.201:7897 PATH=...node-v22.22.0... npm run build`
  - final exit 0; Pyodide cache reused, 6408 modules transformed, adapter-static wrote `build`, Vite completed in 1m09s.
- `git diff --check`
  - exit 0.
- `git diff --name-only -- backend`
  - exit 0 with empty output.
- fixed-string searches for conflict markers and excluded production symbols
  - no conflict markers; excluded symbols appear only in negative test assertions, not production code.

## Residual risks

- The repository's broad `svelte-check` baseline is not clean. Official v0.11 and inherited custom files still emit strict-TS, unused-export, self-closing-element, hydration, and accessibility warnings. Production build and focused compile/contract tests pass, but a separate repository-wide type/a11y cleanup should not be conflated with this integration lane.
- No real browser, authenticated disposable backend, or multi-lane end-to-end run was performed in this lane. That requires the backend contracts above and must occur after lanes are combined; formal live remains untouched.
- `npm audit` reports 23 dependency findings from the locked dependency tree; this lane did not mutate dependency versions.
- Pyodide package acquisition depends on external CDNs. The required wheels are now in this ignored worktree cache, but a clean CI runner still needs working CDN access or an explicitly managed cache/mirror.
- The 515 non-conflicting official frontend paths were accepted from the provisional merge using git-delta/remerge audit plus full test/build verification. Only the 19 textual conflict paths received manual semantic reconciliation; combined-browser acceptance remains the final check against hidden cross-lane contract drift.
