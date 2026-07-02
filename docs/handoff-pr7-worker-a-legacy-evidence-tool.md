# PR7 Worker A Legacy Evidence Tool Handoff

## Scope
- Worktree: `/Users/liusihang/.config/superpowers/worktrees/openwebui/codex-retrieval-manifest-opensearch-phase1`
- Branch: `codex/retrieval-manifest-opensearch-phase2-3`
- Do not write code in `/Users/liusihang/openwebui`.
- Do not touch live services.
- Do not modify `uv.lock`; it is already dirty in this worktree.
- Do not modify `backend/open_webui/retrieval/vector/multimodal.py` or `backend/open_webui/test/util/test_evidence_vector_search.py`; another worker owns vector score semantics.
- This slice only fixes the builtin tool entry contract so legacy knowledge scope also gets `query_knowledge_evidence`.

## Goal
- Use TDD to make `query_knowledge_evidence` inject whenever the model has any knowledge scope.
- Preserve the existing no-scope behavior: if the model has no scoped knowledge, do not inject the evidence tool.
- Preserve ACL and effective-scope enforcement at runtime.

## Checkpoints
- 2026-06-12 00:00 CST: Verified the assigned worktree and branch. Pre-existing dirty file at start is only `uv.lock`.
- 2026-06-12 00:00 CST: Confirmed the current gate is `backend/open_webui/utils/tools.py`, which still injects `query_knowledge_evidence` only when `has_evidence_enabled_knowledge_scope(model_knowledge)` is true.
- 2026-06-12 00:00 CST: Confirmed `backend/open_webui/test/util/test_query_knowledge_evidence_contract.py` still asserts legacy scoped knowledge does not get `query_knowledge_evidence`.
- 2026-06-12 00:01 CST: RED focused run of `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false .venv/bin/pytest -q backend/open_webui/test/util/test_query_knowledge_evidence_contract.py backend/open_webui/test/util/test_query_knowledge_evidence_runtime.py` failed exactly at `test_get_builtin_tools_adds_evidence_tool_for_any_scoped_knowledge` because legacy scoped knowledge still omits `query_knowledge_evidence`.
- 2026-06-12 00:02 CST: Minimal implementation landed in `backend/open_webui/utils/tools.py`: builtin `query_knowledge_evidence` now injects for any non-empty model knowledge scope; runtime ACL and effective-scope checks are unchanged.
- 2026-06-12 00:03 CST: GREEN rerun of `WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false .venv/bin/pytest -q backend/open_webui/test/util/test_query_knowledge_evidence_contract.py backend/open_webui/test/util/test_query_knowledge_evidence_runtime.py` reports `24 passed, 6 warnings`.

## Plan
- Flip the contract test first so legacy scoped knowledge should receive `query_knowledge_evidence`, then run the focused pytest target to capture a RED failure.
- Apply the smallest code change in the builtin tool injection path.
- Re-run the focused pytest pair requested by the user and record the GREEN result plus any remaining risk.
