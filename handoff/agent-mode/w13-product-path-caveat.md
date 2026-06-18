# W13-2 Product-Path Caveat Audit

Date: 2026-06-18
Auditor: W13-2 Product-Path Caveat
Worktree: `/Users/liusihang/openwebui/.worktrees/agent-mode-w13-product-path-caveat`
Branch: `codex/agent-mode-w13-product-path-caveat`
Base commit: `00481b7ab`

## Goal

Decide whether the W12D-2 acceptance-only in-memory tool registry caveat is
acceptable for MVP release, or whether the integration branch needs one narrow
product-path smoke/fix before handoff.

## Inspected Files

- `handoff/agent-mode/controller.md`
- `handoff/agent-mode/w12d-tool-terminal.md`
- `handoff/agent-mode/w12d-tool-terminal-evidence.json`
- `backend/open_webui/agent/tool_authority.py`
- `backend/open_webui/agent/runtime_client.py`
- `backend/open_webui/utils/tools.py`
- `backend/open_webui/utils/middleware.py`
- `backend/open_webui/main.py`
- `backend/open_webui/test/agent/test_tool_authority.py`
- `backend/open_webui/test/agent/test_chat_entry_agent_mode.py`
- `/Users/liusihang/openwebui/docs/plans/2026-06-17-openwebui-agent-mode-agentscope-implementation.md`

## Findings

### Product tool resolution exists, but Agent Mode does not reach it

The normal product tool-loading path is still in `process_chat_payload`:

- `get_tools(...)` loads user-selected local DB tools and server/OpenAPI tools
  with access checks in `backend/open_webui/utils/tools.py`.
- MCP tools are connected and merged into `tools_dict` in
  `backend/open_webui/utils/middleware.py`.
- `get_terminal_tools(...)` resolves a configured Open Terminal connection,
  checks access, loads cached OpenAPI specs, adds `X-User-Id` and optional
  `X-Session-Id`, and builds callables that execute through
  `execute_tool_server(...)`.
- Builtin native tools are appended when native function calling is enabled.
- If any tools resolve, `process_chat_payload` writes them to
  `metadata["tools"]`.

However, the Agent Mode product chat entry returns before that function runs:

- `backend/open_webui/main.py` sets `request.state.metadata` and then, when
  `_is_agent_mode_product_chat(...)` is true, immediately returns
  `_start_agent_mode_chat(...)`.
- The later legacy path calls `process_chat_payload(...)`, but that is below the
  early Agent Mode return and is not reached for product Agent Mode chat.

### Runtime payload and run snapshot are hardcoded empty

`_agent_runtime_payload(...)` currently emits:

```python
'tool_access_envelope': {},
```

`_start_agent_mode_chat(...)` also creates the Agent Run with:

```python
tool_access_snapshot={},
```

There is no production call site for
`build_tool_access_envelope(metadata["tools"])`; `rg` finds it only in tests and
tool-authority helpers, not in `main.py` or middleware product handoff code.

### Tool authority envelope itself is suitable once populated

`build_tool_access_envelope(...)` is the right primitive for the product handoff:
it converts resolved tool dictionaries into model-visible opaque IDs, names,
types, and schemas while keeping callables only in the server-side registry.
It also preserves terminal types and source IDs such as `terminal:<id>`.

`AgentToolAuthority` can execute against the registry and has terminal
side-effect handling for process refs and output artifact registration. W12D-2
proved this authority path with an in-memory acceptance registry.

### What W12D-2 actually proved

W12D-2 live evidence proved:

- scenario 2: a single OpenWebUI-style tool call succeeds;
- scenario 3: Open Terminal `run_command` can produce output artifact metadata;
- scenario 4: tmp artifacts are retained and cleanup-eligible;
- scenario 5: destructive terminal write waits for approval and can be rejected;
- scenario 10: Agent Run cancellation leaves an Open Terminal process running.

But the W12D-2 handoff explicitly says the backend was launched with an
acceptance-only in-memory `AGENT_TOOL_REGISTRY`, and that product chat still
needed this final audit to decide whether real chat payload registry population
was in scope. The evidence file repeats that note.

Therefore W12D-2 proves the callback authority, normalization, artifact,
approval, and cancellation mechanics once tools are already registered. It does
not prove that product Agent Mode chat can populate real OpenWebUI, MCP,
OpenAPI, or Open Terminal tools.

### Focused verification attempted

Attempted focused existing tests:

```bash
WEBUI_SECRET_KEY=test-secret ENABLE_DB_MIGRATIONS=false uv run --frozen pytest -q \
  backend/open_webui/test/agent/test_chat_entry_agent_mode.py::test_agent_mode_enabled_creates_run_links_message_and_starts_runtime \
  backend/open_webui/test/agent/test_tool_authority.py::test_tool_access_envelope_exposes_schema_and_opaque_ids_without_callables
```

Result: collection failed before test execution because the frozen environment
created in this worktree did not include `aiosqlite`.

This does not change the audit conclusion because the static product path is
decisive: the only product runtime payload and run snapshot fields for tools are
currently `{}`.

## Severity

Classification: **release blocker for tool-enabled Agent Mode MVP**, not a
non-blocking caveat.

Reasoning from first principles:

- The MVP goal says longer tasks use the same run record, tool callback
  authorities, artifacts, cancellation, and final-answer phase.
- A release candidate whose product Agent Mode path cannot expose selected or
  configured tools to the runtime fails that core path.
- The acceptance-only registry caveat is acceptable as an authority-unit proof,
  but not as the final product-path proof.

If MVP is explicitly re-scoped to ordinary Q&A with no tools, then this becomes
a documented product limitation. Under the current Agent Mode plan, it is a
blocker.

## Go / No-Go

Recommendation: **No-go for MVP release as-is.**

The integration branch needs one narrow product-path smoke/fix before handoff.

## Proposed Narrow Follow-Up

Add the smallest product-path fix and smoke test that proves:

1. Agent Mode chat resolves tools before starting the runtime, using the same
   product authority path as legacy chat where practical.
2. `_agent_runtime_payload(...)` includes the envelope returned by
   `build_tool_access_envelope(...)`.
3. `AgentRuns.create_run(...)` stores the same envelope in
   `tool_access_snapshot`.
4. The server-side registry used by `get_agent_tool_authority(...)` contains the
   callables for the same opaque tool IDs during callback execution.
5. A focused test covers one representative product-resolved tool and one
   terminal-style tool dictionary without live Open Terminal dependency. If the
   code path can cheaply use mocked `process_chat_payload(...)` returning
   `metadata["tools"]`, keep it there; a full live tool-server smoke can remain
   outside this narrow fix.

Avoid broad suites for this follow-up. A focused
`test_chat_entry_agent_mode.py` case that fails on the current empty
`tool_access_envelope` is enough to guard the product-path handoff.
