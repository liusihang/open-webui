# Handoff: afc44c174 true gap port (2026-04-26)

## Goal

- Replay the confirmed missing custom changes onto an `afc44c174`-based branch, not onto `140de548c`.
- Scope is limited to:
  - knowledge layering advanced layer
  - buildx upgrade script path
  - admin announcement popup
  - chat reasoning-depth presets
  - code block style package

## Branch / Workspace

- Branch: `codex/replay-afc44c174-true-gap-port`
- Worktree: `.worktrees/replay-afc44c174-true-gap-port`
- Base commit: `afc44c174`

## Correction Note

- A previous implementation attempt mistakenly used `140de548c` as the base.
- That base already contained part of the desired custom stack, so it was not a valid replay target.
- This worktree is the real replay target.

## Checkpoints

- [x] C0: Create afc44c174-based isolated worktree.
- [x] C1: Create corrected implementation handoff.
- [x] C2: Dispatch bounded replay tasks to subagents.
- [x] C3: Integrate subagent changes.
- [x] C4: Run targeted verification.
- [ ] C5: Summarize real replay status back into audit handoff.

## Scope Guard

- Do not widen into:
  - stateful anchor full package
  - bifrostapi advanced model discovery
  - adaptive file context
  - selected-text floating popup enhancement

## Implemented

- `knowledge layering` advanced layer:
  - `backend/open_webui/utils/layered_knowledge.py`
  - `backend/open_webui/utils/middleware.py`
  - new focused tests:
    - `backend/open_webui/test/util/test_native_attached_knowledge_bypass_gate.py`
    - `backend/open_webui/test/util/test_attached_knowledge_native_flow.py`
- `buildx` upgrade workflow:
  - `skills/open-webui-aiserver-upgrade/SKILL.md`
  - `skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh`
  - `skills/open-webui-aiserver-upgrade/tests/upgrade_aiserver_openwebui_test.sh`
- admin announcement popup:
  - `src/lib/components/AdminAnnouncementModal.svelte`
  - `src/routes/(app)/+layout.svelte`
  - `src/lib/components/admin/Settings/General.svelte`
- chat reasoning-depth presets:
  - `src/lib/components/chat/Chat.svelte`
  - `src/lib/components/chat/MessageInput.svelte`
  - `src/lib/components/chat/Placeholder.svelte`
- code block style package:
  - `src/lib/components/chat/Messages/CodeBlock.svelte`
  - `src/lib/components/common/ToolCallDisplay.svelte`
  - `src/app.css`
  - `src/routes/style-preview/+page.svelte`

## Verification

- `git diff --check`
- `/Users/liusihang/openwebui/.venv/bin/python -m py_compile backend/open_webui/utils/layered_knowledge.py backend/open_webui/utils/middleware.py backend/open_webui/main.py backend/open_webui/config.py backend/open_webui/models/files.py`
- `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_native_attached_knowledge_bypass_gate.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py -q`
  - result: `8 passed`
- `bash -n skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh skills/open-webui-aiserver-upgrade/tests/upgrade_aiserver_openwebui_test.sh`
- `bash skills/open-webui-aiserver-upgrade/tests/upgrade_aiserver_openwebui_test.sh`
- `npx prettier --write` on replayed frontend files

## Residual Notes

- Existing repository-wide `vite build` / `npm run check` output still contains large amounts of unrelated pre-existing Svelte accessibility and markup warnings on this branch family.
- Earlier attempts to use broad cherry-picks from the old custom branch were intentionally abandoned where they widened scope into unrelated UI/knowledge workspace changes.
