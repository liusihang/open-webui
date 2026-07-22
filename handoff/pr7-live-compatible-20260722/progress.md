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

## 2026-07-22 live switch and true-final-streaming checkpoint

- Guarded switch completed successfully at `2026-07-22T05:13:33+08:00`. Live WebUI image ID is `sha256:fd6145b041f...`; runtime image ID is `sha256:3ce6c0481aa...`; both are healthy with zero restarts and no OOM. Alembic is `f8a9b0c1d2e3`, runtime store schema is v2, pending continuations are zero, and DB/Redis/terminal/main-WebUI container identities remained unchanged.
- Exact native-phase acceptance on Claude produced the public order commentary -> tool 1 -> output 1 -> commentary -> tool 2 -> output 2 -> final.started -> final.delta -> run.completed. Exact Bifrost record `56f1d675-8302-4f63-a738-52ce71795b5c` contains the same interleaved request history and was the only detail record inspected.
- Cancellation acceptance passed and browser reload preserved cancelled/completed/failed terminal state without console warnings. The failed GPT route was not an internal deadlock: Bifrost reported an unreachable IPv6 provider address after the route waited for its 300-second boundary.
- The Claude acceptance exposed one remaining defect: only one `final.delta` was persisted. Root-cause tracing showed `OpenWebUIAgentScopeModel._stream_model_call()` consumed the provider SSE fully into `final_text_parts` before yielding any intermediate `ChatResponse`, so the downstream 50 ms buffer operated on already-completed text.
- Added a failing timing test proving the first explicit final chunk was unavailable while the provider generator remained open, then changed the bridge to yield safe structured `final_answer` chunks immediately. Potential leading private/textual envelopes stay buffered. A malformed later tool/commentary now fails after any already-public final prefix instead of defeating genuine streaming through full-response validation.
- Runtime bridge regression now passes `84 passed`; complete runtime suite passes `241 passed, 1 warning`.

## 2026-07-22 runtime-only streaming rollout and final acceptance

- Product fix commit is `742f686182d6b1a885889fca803ea31b766bfda1` (`fix(agent-mode): stream declared final phase live`). Exact overlay image `open-webui-pr7-agentscope-runtime:742f686182-true-final-stream` has ID `sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9`, matching OCI revision and installed bridge-file hash. Build audit is `/home/aiserver/staging/openwebui-pr7-runtime-742f686182`; all six live container IDs were unchanged by the build.
- Runtime-only guarded switch finished at `2026-07-22T06:06:56+08:00`; rollback/audit backup is `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backup-before-runtime-742f686182-20260722-060556`. Only `openwebui-pr7-agentscope-runtime` was recreated. WebUI, DB, Redis, terminal, and main WebUI IDs remained unchanged.
- Provider-backed rechecks were blocked before any tool/final event: Claude `bifrostapi.claude-3-7-sonnet` twice ended with `Responses stream ended before a terminal event` and exact new Bifrost error record `398d8419-2cdc-42ae-aca3-9df9549cf774`; `bifrostapi.gpt-4.1` failed through `ZenMuxOAI` with IPv6 `network is unreachable`; `qwen-max-latest` and `Hermes Agent` returned `empty_model_response`. All runs reached terminal failed state and every temporary tool was deleted.
- To separate runtime correctness from provider availability, a temporary local Pipe model emitted two structured `final_answer` chunks one second apart while an unused temporary tool forced the general-Agent path. Live run `0ce8db32-c477-44ce-9fd8-9b9690903528` persisted `run.running -> final.started -> final.delta[0] -> final.delta[1] -> run.completed`; the two deltas were separated by `588.783 ms` and reconstructed the exact marker text. Audit: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-local-final-stream-20260722-063754.json`. Temporary function/tool deletion was verified both by API success and zero DB rows.
- Final live invariants: WebUI `df1ba2b48e...` / image `sha256:fd6145b041f...`; runtime `739472bd3274...` / image `sha256:f7396ba23e49...`; both healthy, zero restarts, no OOM. DB `293be53...`, Redis `d5012cf...`, terminals `51e8b699...`, and main WebUI `78faa81...` are unchanged. Alembic remains `f8a9b0c1d2e3`, runtime schema v2, pending continuations zero, and recent active runs zero.
- Release recommendation: keep the new runtime live. Code-level true streaming, durable state, cleanup, and service isolation pass. Real-provider native-phase revalidation remains blocked by current upstream/Bifrost route health, not by a runtime failure; rerun the tracked provider acceptance script when one route is restored.

## 2026-07-22 GPT-5.4 provider acceptance

- Bifrost `/api/models?limit=1000` confirmed `Cliproxy/gpt-5.4`, but the active OpenWebUI `bifrostapi` Pipe was using its fallback list and did not expose GPT-5.4. The production Pipe was not edited. A temporary isolated clone added only `Cliproxy/gpt-5.4`, copied the existing encrypted valves through the admin API without printing them, and stripped its clone-specific manifold prefix before forwarding the model name.
- GPT-5.4 live run `ffa222be-235e-4949-bd4d-d463d39fa155` passed the full native-phase acceptance. Public order was `run.running -> commentary(model-call-1) -> tool.requested -> tool.completed -> commentary(model-call-2) -> tool.requested -> tool.completed -> final.started -> 7 contiguous final.delta -> run.completed`.
- Exact Bifrost record `64b223ed-2419-4dff-8b28-3152d7ebccc9` resolved to provider `Cliproxy`, model `gpt-5.4`. Its request history indices were user 0, commentary 1, call 2, output 3, commentary 4, call 5, output 6. The acceptance inspected at most three newly-created detail records and did not scan full Bifrost logs.
- Audit: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-native-phase-20260722-084222.json`. The temporary Pipe and tool were deleted, verified as zero DB rows; the run is completed, recent active runs and pending continuations are zero, and WebUI/runtime remain healthy with zero restarts and no OOM.
