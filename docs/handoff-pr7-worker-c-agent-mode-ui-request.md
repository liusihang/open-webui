# PR7 Worker C: Agent Mode UI request constraint

## Scope

- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Owned files: `src/lib/components/chat/Chat.svelte`, focused frontend tests/helper under `src/lib/components/chat`
- Constraints: do not touch live, do not deploy, do not revert other workers, do not commit.

## Goal

Evaluate and, if safe, constrain Agent Mode UI request construction so Agent Mode uses one leader model and one assistant placeholder instead of ordinary multi-model compare placeholders.

## Checkpoints

- Started from `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`.
- Read `Chat.svelte` model selection, placeholder creation, `messageIdsMap`, and `generateOpenAIChatCompletion` payload construction.
- Found current frontend behavior: `sendMessage()` uses `selectedModels` directly when no explicit `modelId` is passed, creates one assistant response message per selected model, builds `messageIdsMap`, then sends one request with `model` set to the first selected model and `message_ids` set only when more than one model is selected.
- Read backend Agent Mode gate: backend only enters Agent Mode when `ENABLE_AGENT_MODE` is true and product-chat metadata plus an assistant message id exist. During integration, backend `/api/config` was updated to expose `ENABLE_AGENT_MODE` as `config.features.enable_agent_mode`.
- Decision: frontend must not unconditionally collapse multi-model requests while the flag is invisible, because that would break normal compare mode when Agent Mode is disabled. Minimal safe frontend work is to add a pure helper and wire it so any future visible `config.features.enable_agent_mode` flag causes Chat.svelte to use only the first valid leader model/placeholder.
- Added `src/lib/components/chat/agentModeRequest.ts` and focused tests. Helper preserves ordinary multi-model compare unless a frontend-visible Agent Mode flag is explicitly true, then selects the first non-empty leader model. The helper type allows other `features` keys so existing config payloads remain structurally compatible.
- Wired `Chat.svelte` to use the helper for user-message `models` metadata and response placeholder/request model ids.
- Current runtime behavior: with authenticated `config.features.enable_agent_mode === true`, this frontend constraint is active and collapses Agent Mode chat requests to one leader model. Backend guard remains as protection if older clients send multi-model `message_ids`.

## Verification

- RED: `PATH=/Users/liusihang/.nvm/versions/node/v22.22.0/bin:$PATH npm run test:frontend -- src/lib/components/chat/agentModeRequest.test.ts` failed because `./agentModeRequest` did not exist.
- GREEN: `PATH=/Users/liusihang/.nvm/versions/node/v22.22.0/bin:$PATH npm run test:frontend -- --run src/lib/components/chat/agentModeRequest.test.ts` passed: 1 file, 3 tests.
- Svelte compile: `PATH=/Users/liusihang/.nvm/versions/node/v22.22.0/bin:$PATH node --input-type=module -e "import { readFileSync } from 'node:fs'; import { compile } from 'svelte/compiler'; compile(readFileSync('src/lib/components/chat/Chat.svelte','utf8'), { filename: 'src/lib/components/chat/Chat.svelte', generate: false }); console.log('Chat.svelte compile ok');"` passed.

## Next Actions

- After redeploy, verify authenticated `/api/config` includes `features.enable_agent_mode: true`.
- Minimal UI acceptance path after redeploy: with `features.enable_agent_mode: true` and two selected models, submit one chat and confirm the local history has one assistant child, the request model is the first selected model, and no compare placeholders remain.
