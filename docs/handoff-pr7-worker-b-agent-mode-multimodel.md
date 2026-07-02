# PR7 Worker B: Agent Mode multi-model backend guard

## Scope

- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Owned files: `backend/open_webui/main.py`, `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
- Constraints: do not touch live, do not deploy, do not revert unrelated worker changes, do not commit.

## Goal

When Agent Mode takes over a product chat with multiple selected models, bind the agent run to the assistant placeholder for the current leader model (`form_data["model"]`). Do not blindly use the first entry in `message_ids`; only fall back to the first available assistant id when no current-model entry exists.

## Checkpoints

- Started from clean worktree on `codex/pr7-review-security-fixes...origin/pr/7/head`.
- Read `_is_agent_mode_product_chat`, `_start_agent_mode_chat`, `_agent_runtime_payload`, and the focused Agent Mode chat-entry tests.
- Found current behavior: `_start_agent_mode_chat()` selects `assistant_message_id = _first_assistant_message_id(message_ids)` before deriving `leader_model_id = form_data.get("model")`, so a multi-model request can bind the run/upsert to another model's placeholder while the payload says the leader is the current model.
- Added focused regression `test_agent_mode_multimodel_binds_current_model_assistant_message`: request model is `model-a`, but `message_ids` lists `comparison-model` first and `model-a` second.
- RED confirmed with `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_chat_entry_agent_mode.py::test_agent_mode_multimodel_binds_current_model_assistant_message`: failed because `run.assistant_message_id` was `assistant-comparison` instead of `assistant-current`.
- Implemented `_assistant_message_id_for_model(message_ids, model_id)` and changed `_start_agent_mode_chat()` to derive `leader_model_id` first, then select that model's assistant id. If the leader model has no entry, it logs a warning and falls back to the first available assistant id.
- GREEN confirmed for the regression with the same single-test command: `1 passed, 8 warnings`.
- Focused backend verification passed with `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run pytest -q backend/open_webui/test/agent/test_chat_entry_agent_mode.py`: `10 passed, 8 warnings`.
- `git diff --check -- backend/open_webui/main.py backend/open_webui/test/agent/test_chat_entry_agent_mode.py docs/handoff-pr7-worker-b-agent-mode-multimodel.md` passed.
- `uv run` refreshed `uv.lock` during verification; restored `uv.lock` afterward to avoid unrelated lockfile churn.

## Current B-owned changes

- `backend/open_webui/main.py`: leader-model assistant id selection helper plus `_start_agent_mode_chat()` ordering fix.
- `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`: regression covering multi-model `message_ids` when current model is not first.
- `docs/handoff-pr7-worker-b-agent-mode-multimodel.md`: this handoff.
