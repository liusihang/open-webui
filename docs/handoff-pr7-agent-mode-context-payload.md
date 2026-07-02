# PR7 Agent Mode Context Payload Handoff

## Scope
- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Start commit: `5e51a3aba04f68519e96c70f9d629e9d4b0ecdb8`
- Task: fix Agent Mode runtime payload so normal multi-turn chat context is passed through.
- Guardrails: no service restart, no deploy, no live OpenWebUI changes, no frontend changes, no model routing changes, no secrets printed, no commit.

## Checkpoints
- [x] Verified target worktree branch and start commit.
- [x] Inspected Agent Mode payload path in `backend/open_webui/main.py`.
- [x] Located focused Agent Mode chat-entry tests in `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`.
- [x] Add RED test proving runtime receives full chat completion messages.
- [x] Run focused RED test and record expected failure.
- [x] Implement minimal backend payload fix.
- [x] Run focused GREEN tests and record result.

## Notes
- Current suspected bug: `_agent_runtime_payload` sets `messages` from `metadata['user_message']` only, so the runtime sees just the current UI message object instead of the model-ready chat completion context.
- RED command: `uv run pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py::test_agent_mode_runtime_payload_preserves_chat_completion_context -q`
- RED result: failed as expected; `runtime_payload['messages']` was `[metadata['user_message']]` and omitted prior user/assistant messages from `form_data['messages']`.
- Fix: `_agent_runtime_payload` now uses `form_data['messages']` when it is a list, with the previous current-user-message fallback for callers without chat completion messages.
- GREEN command: `uv run pytest backend/open_webui/test/agent/test_chat_entry_agent_mode.py -q`
- GREEN result: 11 passed, 8 existing warnings.
