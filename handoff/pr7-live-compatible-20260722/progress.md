# Progress

## 2026-07-22 initialization

- Prior guarded rollout from `4ddffcb1a` failed before serving traffic because the `9810f912a` source base expected the older blob-style `config` table while the live PR7 database has the newer per-key config schema.
- The old WebUI/runtime images, Agent Alembic head `e7f8a9b0c1d2`, runtime store schema v1, health endpoint, and unrelated container identities were fully restored.
- New isolated truth surface: `/Users/liusihang/openwebui/.worktrees/pr7-live-compatible-20260722`, branch `codex/pr7-live-compatible-20260722`, initial HEAD `c14bba3da3cc86ea3808b6156acba44ca492ae56`.
- The source branch is the exact live-compatible line whose WebUI/runtime product commits correspond to deployed image revisions `2a0c4c988884...` and `ed6b280f60d2...`.
- Current checkpoint: map only the missing UI/status-history/hardening changes; do not replay already-present durable/cancellation/response-envelope commits or deployment history blindly.

## 2026-07-22 hardening port checkpoint

- Cherry-picked `41acdf6c8` semantically. The live branch already contained all production behavior except two small test-contract differences; resulting commit is `18373524c`.
- Began semantic cherry-pick of `d72ffcaca`. Backend/runtime changes apply directly because the live line already contains the equivalent durable/cancellation/response-envelope prerequisites (`7dc6afd81`, `2a0c4c988`, `ed6b280f6`).
- Omitted the `9810f912a` branch's separate Agent Memory foundation files/migration from this port. They are not dependencies of the reported commentary/tool/final, finalization, cancellation, approval, or user-input defects, and the live branch intentionally uses a different memory migration lineage.
- Adapted the new MySQL/MariaDB DDL test to the live Agent run/decision schema rather than importing the unrelated Agent Memory models.
- Focused backend hardening/migration suite passes: `125 passed, 1 warning`.
- Complete AgentScope runtime suite passes: `240 passed, 1 warning`.
- Frontend conflict resolution is in progress in a separately owned file scope; backend/runtime files are no longer conflicted.

## 2026-07-22 local verification checkpoint

- Frontend semantic resolution completed while preserving the live collapsible/native-phase transcript. It adds run-state-gated approval/user-input actions, schema validation, accessible controls, privacy filtering, backfill-before-reconnect, permanent-error classification, and bounded retries.
- Live-compatible hardening commit created as `bc8953e91` (`fix(agent-mode): harden durable recovery and interaction`).
- The older build-script patch `4ddffcb1a` was not replayed because the live line already pipes both patch/build programs to `ssh ... bash -s`; its shell syntax and dedicated no-live-mutation rebuild test pass unchanged.
- A broad backend run initially found one stale baseline contract test requiring the removed blob-era `ConfigVar(...)` syntax. The actual flag is correctly exported, present in the per-key `DEFAULT_CONFIG`, and mapped into app config. The test was updated to assert the current per-key contract.
- Fresh final local gates after that correction: backend Agent/migration/MySQL/MariaDB/native/terminal set `373 passed, 26 warnings`; AgentScope runtime full suite `240 passed, 1 warning`; frontend Agent UI/API set `133 passed` across 9 files including 5 Svelte component compile checks; `git diff --check` passes.
- Remaining local release gate is the production image build. Deployment procedure still needs explicit target-config preflight and runtime SQLite backup/restore before the next switch.

## 2026-07-22 exact image and deployment-preflight checkpoint

- Exact image source commit: `4a4e43e2063484d9159e70b0c072866ec55286bd`.
- WebUI image `open-webui:agentmode-v0102-4a4e43e206-slim`: ID `sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4`, size `1989280689`, OCI revision matches the source commit. Production Svelte build completed with `6366 modules transformed` and `Wrote site to build / done`; only known repository-wide Svelte warnings were emitted.
- Runtime image `open-webui-pr7-agentscope-runtime:4a4e43e206-live-hardening`: ID `sha256:3ce6c0481aa575c856d42fd90587695408093ef98667e0e2d50fc9d29ca2bb22`, size `672062170`, OCI revision matches; store smoke reads schema v2.
- The live rebuild helper failed once before Docker build because it uploaded into an uncreated remote staging directory. Added and tested an explicit remote `mkdir -p` before `scp`; commit `e26765462` records the tooling fix. The exact product images remain truthfully labelled with the preceding source commit because this tooling-only change is not inside the image runtime.
- New switch assets add the missing rollback contract: SQLite online backup before target startup, restore before old runtime recreation, refusal gate through schema-version checks, database migration downgrade before old WebUI startup, and explicit `rollback_failed` status.
- New pre-switch target probe imports the target WebUI with migrations disabled and performs a real read from the live per-key config table. Remote read-only preflight passed: `target_config_schema=compatible`; target Alembic head `f8a9b0c1d2e3`; current live head `e7f8a9b0c1d2`; current runtime schema v1; merged Compose points only to the two target images and the existing named volume with one worker.
