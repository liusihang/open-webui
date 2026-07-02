# PR7 Agent Provider Diagnostics Worker Handoff

## Scope
- Worktree: `/Users/liusihang/.codex/worktrees/pr7-review-security-fixes/openwebui`
- Branch: `codex/pr7-review-security-fixes`
- Start commit confirmed: `5e51a3aba`
- Task: Agent Mode provider/config failure diagnostics and acceptance guardrails only.
- Do not restart services, deploy, edit live OpenWebUI, change provider config, or print credentials.

## Checkpoints
- [x] Confirmed target worktree/branch/commit and clean starting tree.
- [x] Root-cause read: runtime `_finalize_ordinary_qa()` accepts `call_model()` output, `_extract_model_text()` converts provider error-shaped content into final text, then transitions run to `completed`.
- [x] Added RED tests in `services/agentscope-runtime/tests/test_app.py` for provider auth error text and unknown-provider callback failure.
- [x] RED focused test run captured.
- [x] Implemented minimal runtime/provider-error classification.
- [x] GREEN focused test run captured.

## Current Findings
- The most direct guardrail is in the AgentScope runtime finalization path, before final deltas/state completion are written.
- Desired behavior: provider auth/unknown-provider failures should transition Agent Run to `failed`, emit structured diagnostics, preserve the raw provider message in error diagnostics, and avoid writing provider stack/noise as final answer text.
- Runtime classification is intentionally narrow: HTTP-context messages containing `auth_unavailable` / `no auth available`, or `unknown provider for model`.
- At this worker checkpoint, full `services/agentscope-runtime/tests/test_app.py` still had an unrelated cancellation failure (`test_cancel_during_model_call_prevents_finalization_callbacks`: expected `cancelled`, observed `completed`). The later cancel-path worker fixed that in the integrated tree.

## Commands
- RED: `cd services/agentscope-runtime && uv run pytest tests/test_app.py -k "provider_auth_error_text_from_model_call or unknown_provider_callback_failure"` -> 2 failed. Provider auth case observed `completed` instead of `failed`; unknown-provider case observed `runtime_finalization_failed` instead of `provider_configuration_unavailable`.
- GREEN: `cd services/agentscope-runtime && uv run pytest tests/test_app.py -k "provider_auth_error_text_from_model_call or unknown_provider_callback_failure"` -> 2 passed, 12 deselected.
- Regression focused: `cd services/agentscope-runtime && uv run pytest tests/test_app.py -k "run_start_finalizes_ordinary_qa_through_model_and_final_delta_callbacks or run_start_retries_queued_model_call_before_finalization_failure or provider_auth_error_text_from_model_call or unknown_provider_callback_failure"` -> 4 passed, 10 deselected.
- Broader check: `cd services/agentscope-runtime && uv run pytest tests/test_app.py` -> 13 passed, 1 failed on the out-of-scope cancellation test above.
