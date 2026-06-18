# W8 Terminal Artifacts And Process Refs Handoff

Date: 2026-06-18

## Goal

Implement Agent Mode terminal artifact and process tracking. Terminal tool
results should preserve process refs, register explicit output artifacts under
the run output directory, mark only run-local tmp artifacts as cleanup-eligible,
and keep Open Terminal processes alive when an Agent Run is cancelled.

## Base

- Worktree:
  `/Users/liusihang/openwebui/.worktrees/agent-mode-w8-terminal-artifacts`
- Branch: `codex/agent-mode-w8-terminal-artifacts`
- Base commit: `056560b2f965b54861f7c0bc88ebce84c9a36e13`

## Owned Files

- `backend/open_webui/agent/artifacts.py`
- `backend/open_webui/agent/terminal_artifacts.py` if a separate helper is
  useful
- focused artifact/process tests under `backend/open_webui/test/agent/`
- scoped helper changes in `backend/open_webui/agent/tool_authority.py` and
  `backend/open_webui/agent/resources.py` only when needed for terminal artifact
  registration

## Shared-File Constraints

- Do not edit W7-owned destructive approval/classifier files.
- Avoid `backend/open_webui/routers/agent_service.py` unless an artifact callback
  endpoint is strictly necessary; prefer helper-level behavior first.
- Do not touch nested `open-terminal/`.
- Do not kill Open Terminal processes during cancel/cleanup.

## Non-Goals

- No destructive approval behavior.
- No frontend `Chat.svelte` work.
- No direct filesystem cleanup implementation beyond metadata/eligibility
  decisions for run-local tmp artifacts.

## Required First Step

Write failing tests first and record the red command/result here before
implementing production code.

Required behavior tests:

- default output path is `/workspace/agent-runs/<run_id>/outputs` unless the user
  explicitly requests another output directory;
- run-local tmp path is `/workspace/agent-runs/<run_id>/tmp` and only those tmp
  artifacts are cleanup-eligible;
- terminal `run_command` results preserve process refs and append them to run
  state/resource manager behavior;
- explicit output files are registered as artifacts idempotently;
- cancellation/terminal cleanup retains process refs and does not call a kill
  callback.

### RED 2026-06-18

Command:

```bash
uv run pytest backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_tool_authority.py -q
```

Result: failed during collection as expected because the W8 artifact helper does
not exist yet.

Key failure:

```text
ModuleNotFoundError: No module named 'open_webui.agent.artifacts'
```

Tests added before production code:

- `backend/open_webui/test/agent/test_terminal_artifacts.py`
- terminal artifact/process-ref case in
  `backend/open_webui/test/agent/test_tool_authority.py`

Next checkpoint: add the minimal artifact helper and optional tool-authority
integration needed to make these tests pass.

### GREEN 2026-06-18 Focused

Command:

```bash
uv run pytest backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_tool_authority.py -q
```

Result:

```text
9 passed, 1 warning in 12.40s
```

Implementation checkpoint:

- Added `backend/open_webui/agent/artifacts.py` for Agent Run outputs/tmp path
  helpers, explicit terminal artifact registration, idempotency keys, and
  cleanup metadata.
- Added optional terminal side-effect integration to
  `backend/open_webui/agent/tool_authority.py`: resource-manager process-ref
  registration and artifact registration run only when the caller supplies the
  helper objects.
- Existing default `AgentToolAuthority` construction remains unchanged for
  callers that do not yet wire W8 helpers.

## Verification To Record

- focused artifact/process pytest;
- adjacent tool/resource/compaction pytest;
- ruff on touched backend files;
- `git diff --check`;
- note whether `uv.lock` was restored after `uv run`.

### Verification 2026-06-18

Focused artifact/process:

```bash
uv run pytest backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_tool_authority.py -q
```

Result:

```text
9 passed, 1 warning in 12.40s
```

Adjacent tool/resource/compaction:

```bash
uv run pytest backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_resources.py backend/open_webui/test/agent/test_compaction.py -q
```

Result:

```text
13 passed, 1 warning in 1.82s
```

Agent artifact storage dependency:

```bash
uv run pytest backend/open_webui/test/models/test_agent_runs.py -q
```

Result:

```text
8 passed, 1 warning in 2.09s
```

Final combined focused gate after import cleanup:

```bash
uv run pytest backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_tool_authority.py backend/open_webui/test/agent/test_resources.py backend/open_webui/test/agent/test_compaction.py backend/open_webui/test/models/test_agent_runs.py -q
```

Result:

```text
21 passed, 1 warning in 2.13s
```

Full agent backend gate:

```bash
uv run pytest backend/open_webui/test/agent -q
```

Result:

```text
45 passed, 19 warnings in 76.15s
```

Ruff:

```bash
uv run ruff check backend/open_webui/agent/artifacts.py backend/open_webui/agent/tool_authority.py backend/open_webui/test/agent/test_terminal_artifacts.py backend/open_webui/test/agent/test_tool_authority.py
```

Result:

```text
All checks passed!
```

Whitespace:

```bash
git diff --check
```

Result: passed with no output.

`uv.lock`: `uv run` modified it during environment setup; restored with
`git checkout -- uv.lock` as instructed. Current status no longer includes
`uv.lock`.

Residual risks:

- W8 wires helper-level optional integration. The default service-router
  construction still needs a later integration slice to pass a real
  `AgentRunArtifactRegistrar` and `AgentRunResourceManager` into
  `AgentToolAuthority`.
- Explicit output artifact registration intentionally only uses declared
  `output_path`, `output_paths`, `artifact_path`, `artifact_paths`, or
  structured artifact paths. It does not scan shell side effects.
