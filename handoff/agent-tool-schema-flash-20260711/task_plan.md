# Agent tool schema transient display investigation

## Goal

Determine why the `write_file` tool description is briefly visible in chat
`f251c1ab-4ee1-4142-99c4-279220d4379b` and then disappears. Do not modify code
until the source layer and persistence behavior are proven.

## Truth surfaces

- Browser: `http://192.168.2.238:18085/c/f251c1ab-4ee1-4142-99c4-279220d4379b`
- Backend: exact chat and associated Agent run/event rows only
- Runtime: current `open-webui-pr7` container and its configured database
- Source: `/Users/liusihang/openwebui/.worktrees/live-f8106c651-to-v0102`

## Phases

- [completed] Capture the current browser state and identify the affected message/run.
- [completed] Inspect exact persisted chat/run events without broad Bifrost log scans.
- [completed] Trace the observed event type through runtime and frontend rendering.
- [completed] Classify impact and decide whether a fix is warranted.
- [completed] If warranted, add a focused regression test before implementation.

## Guardrails

- Do not scan Bifrost full logs.
- Do not change or restart the live service during diagnosis.
- Do not treat browser state as proof of backend persistence.
- Avoid CSS/string-hide workarounds; any fix must address the originating event type.

## Errors encountered

- Local `docker` command was unavailable; live container inspection moved to
  the named `aiserver` host.
- The first remote environment probe had a Python f-string quoting error. It
  was replaced with a simpler read-only probe rather than retried unchanged.
- Browser-page `fetch()` is not exposed inside this restricted Playwright
  evaluate environment, so exact backend evidence is being read directly from
  the live database instead.
- The first full runtime suite run had one expected contract failure: an
  integration test still asserted that `tool.requested.summary` equals the
  full tool description. The test was updated to the new short-summary
  contract; all other 112 tests passed in that run.
- Ruff is not installed in either the active shell or root virtualenv, so a
  separate Ruff invocation was unavailable. Import/format correctness is
  covered by the full runtime pytest suite and `git diff --check`.

## Decision

Fix accepted because the problem affects every tool request and leaks internal
tool-contract prose into a user-facing lifecycle label. The fix remains narrow:
only the event summary changes; tool definitions, model request bodies,
approvals, execution, and final streaming are unchanged.
