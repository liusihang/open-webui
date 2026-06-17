# W12B-5 Regression And Release Readiness Handoff

Date: 2026-06-18

## Goal

Run and document the combined regression/release-readiness gates for the
integrated Agent Mode branch.

## Scope

Owns:

- backend agent/storage regression gate;
- service-local AgentScope runtime tests;
- focused frontend Vitest gate;
- W12 dry-run/fixture/live-evidence validation once scenario workers provide
  evidence fragments;
- ruff and `git diff --check`;
- evidence file `handoff/agent-mode/w12b-regression-evidence.json`;
- this handoff.

Do not touch:

- feature behavior unless a regression gate proves a narrow fix is required;
- scenario-specific implementation owned by W12B-1 through W12B-4.

## Evidence Contract

Record exact commands and pass/fail results. If root `uv.lock` changes from
`uv run`, restore it or record that it is unstaged test churn. Keep
`services/agentscope-runtime/uv.lock` intact.

## Verification Log

Pending.

## Notes

You are not alone in the codebase. Other W12B workers may be editing their own
worktrees. Do not revert their changes or broaden your scope.
