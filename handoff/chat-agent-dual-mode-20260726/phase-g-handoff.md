# Phase G handoff

Truth surface: `/Users/liusihang/.codex/worktrees/d790/openwebui` on `codex/pr7-chat-agent-dual-mode-20260726`, starting from `531ae10b9`. Phase G integrates sanitized mode-profile defaults into new conversation state and model changes. Administrator UI, backend, Docker, remote, and live are out of scope.

## Required behavior

- Both modes continue to use the existing system default-model selection; profiles never select a model.
- Apply sanitized profile defaults exactly once when a new persistent or temporary draft is initialized.
- Explicit profile values override model metadata; explicit empty/null clears model defaults; omitted fields inherit model defaults.
- Filter all values against current model support, global gates, resources, and user permissions; show safe warnings.
- Users may temporarily adjust Terminal/tools/skills/filters/features per conversation.
- Switching models retains valid temporary choices and removes only incompatible/unauthorized choices; it never reapplies profile defaults or changes the bound revision.
- Reasoning Depth remains on its existing path and is not changed by mode profiles.
- Preserve the public revision hint in draft/request state until backend canonical binding is returned; never expose administrator Prompt in frontend state.
- Terminal state must not leak between conversations, modes, resets, or model changes.

## Checkpoints

- [x] G0 Verify HEAD, frontend baseline, and current initialization/reset/model-change seams.
- [x] G1 Add pure resolver RED tests.
- [x] G2 Implement resolver and warning contract.
- [x] G3 Integrate one-time new-chat/temporary initialization and revision hint.
- [x] G4 Integrate model-change retention and Terminal leakage fixes.
- [x] G5 Focused frontend tests, real compile checks, formatting, and scope audit.
- [x] G6 Independent spec/quality review and remediation.
- [x] G7 Commit verified Phase G files; no push.

## Evidence log

- 2026-07-27: created before Phase G edits. Existing modified Phase E handoff and untracked deploy/Phase D handoff remain outside this phase.
- 2026-07-27 G0: verified `HEAD=531ae10b9` on the assigned branch. Focused existing non-watch frontend baseline passed 6 files / 50 tests: conversation mode presentation/compiler, Agent request shaping, default features, history sync, and system terminal tools. The relevant seam is `Chat.svelte`: `resetInput()` currently clears selections and re-runs `setDefaults()` on a model change, while `selectedTerminalId` is a global store. Public sanitized profiles arrive as `$config.conversation_mode_profiles`; no frontend Profile Prompt field exists.
- 2026-07-27: resumed at `531ae10b9`; `git status` shows only handoff/deploy artifacts, with no Phase G business-code edits. The prior truncated spawn left no waitable agent state, so it will not be treated as implementation evidence.
- 2026-07-27: reviewed the approved Phase G plan and confirmed no design blocker. Execution will use test-first implementation followed by independent specification and quality reviews.
- 2026-07-27 G1 RED: `npm run test:frontend -- src/lib/components/chat/conversationModeProfiles.test.ts --run` failed as expected because a model-change selection retained both `profile-terminal` and `code_interpreter`.
- 2026-07-27 G3 RED: `npm run test:frontend -- src/lib/components/chat/ConversationMode.presentation.test.ts --run` failed as expected before Chat imported the resolver and emitted the revision hint.
- 2026-07-27 G5 GREEN: focused non-watch Vitest passed 6 files / 42 tests: resolver, mode presentation/compiler, request shaping, default features, and system terminal tools. Direct `svelte/compiler` compilation of `Chat.svelte` passed. `prettier --write` touched Phase G files without further changes and `git diff --check` passed. Touched-file `svelte-check` filtering still reports pre-existing broad `Chat.svelte` TypeScript diagnostics (implicit-any and incomplete store metadata types); no parser/compile failure occurred.
- 2026-07-27 G5 files: `src/lib/components/chat/Chat.svelte`, `src/lib/components/chat/conversationModeProfiles.ts`, `src/lib/components/chat/conversationModeProfiles.test.ts`, `src/lib/components/chat/ConversationMode.presentation.test.ts`, and this handoff.
- 2026-07-27 G6: implementer self-review confirmed no backend/admin/Docker/remote/live changes, no frontend Administrator Prompt field, no profile model selection, and no reasoning-depth mutation. Independent specification review is now running; independent quality review has not started.
- 2026-07-27 G7: committed the core Phase G frontend/tests/handoff files as `fe109611f` (`feat(chat): apply mode-specific capability defaults`), then committed the verified persistent-binding follow-up as `ed2aca6ee` (`fix(chat): preserve existing mode profile bindings`); no push performed.
- 2026-07-27 G6 follow-up review: reconciled a concurrent Phase G commit (`fe109611f`) with the assigned worktree. Verified the resolver's tri-state/default/filter/model-change contract, draft controller's one-time and canonical-hint behavior, request hint path, no Prompt/model/reasoning fields, and Terminal/Code Interpreter exclusion. The only validated follow-up defect was that an existing persistent chat could reach profile initialization through `setDefaults()` after loading; initialization is now guarded to new `draft:` state only. Preserved unrelated Phase E/F handoffs and deploy/Phase D artifacts untouched.
