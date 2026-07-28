# Lane C handoff: Terminal, Skills, files, knowledge, retrieval, and tools

## Truth surface

- Worktree: `/Users/liusihang/.codex/worktrees/0ee1/openwebui`
- Branch: `codex/v011-integration-lane-c`
- Required base: `1f93cd9a3b6d8db26f5abbccfd784052ab6e0b9d`
- Base parents: custom `665221e1910a11cfd20e034d9967c93f5d4025d2`; official v0.11.0 `f9590b8017199e56d5e953657e6498e3cef1d246`
- Official comparison base: v0.10.2 `ecd48e2f718220a6400ecf49eafd4867a38feb10`
- Acceptance evidence: owned-path upstream diff audit, focused red/green tests, final focused suites, exclusion searches, and one verified commit.
- Live service and live database: out of scope and must remain untouched.

## Ownership boundary

- Owned: Terminal routers/utilities; terminal-backed Skills; file, knowledge, retrieval, audio extraction/streaming, built-in tools and registry; related backend models, utilities, retrieval/tool packages, and focused tests.
- Preserve: package-backed Terminal Skills, Chatfile/OnlyOffice, layered knowledge, multimodal evidence/citations, external lexical retrieval, document image assets, and pgvector fallback semantics.
- Remove or keep absent: official `utils/subagents.py`, `delegate_task` wiring, and `list_chat_files` / `grep_chat_files` / `query_chat_files` definitions and exposure.
- Do not edit: Lane A config/migrations, Lane B main/middleware/chat runtime, frontend, live services, or live database.
- Cross-lane needs must be recorded here rather than implemented outside ownership.

## Checkpoints

### Checkpoint 0: baseline verified

- Status: complete.
- Evidence: clean detached worktree at exact base SHA; merge parents and ancestry verified; lane branch created from that SHA.
- Current checkpoint: enumerate official v0.10.2 to v0.11.0 changes in owned paths and compare them with the provisional merge tree.
- Next verification: produce file/status lists for official changes, custom changes, and unresolved semantic deltas.
- Stop condition: any required change belongs to Lane A, Lane B, or frontend; record it under cross-lane notes instead.

### Checkpoint 1: owned-path diff and RED established

- Status: complete.
- Official audit: v0.10.2 to v0.11.0 changes 34 owned files (`2589` insertions, `642` deletions); 11 owned files were content-conflict paths in the provisional merge.
- Static failure inventory: `ruff --select F821` reports 35 undefined-name errors caused by conflict-side import/helper loss. Confirmed gaps include Knowledge sorting/embedding constants, external/Paddle loader gates, file media extraction policy, Terminal policy URL/auth tuple, and builtin notify/timer/bounded file helpers.
- Import RED: `test_terminal_session_auth.py` fails collection because official `timer` uses missing `Literal`; no behavior tests execute yet.
- Exclusion RED: new Lane C contract test reports 2 failures because the official Files trio/delegate functions remain and `utils/subagents.py` still exists; retained notify/timer/file/knowledge assertion already passes.
- Current checkpoint: remove excluded owned wiring and restore the official/custom combined imports and helpers so the focused behavioral tests can run.
- Next verification: Lane C contract GREEN, zero owned-path F821 errors, then run Terminal auth test to expose/fix the WebSocket tuple regression.

### Checkpoint 2: exclusions and Terminal security GREEN

- Status: complete.
- Removed official `delegate_task` and Files trio definitions/imports/registry wiring in owned files; deleted `backend/open_webui/utils/subagents.py`; retained `backend/open_webui/agent/subagents.py`.
- Timer remains registered independently under the existing time builtin for saved, non-internal, non-direct chats. Its parent lock is now local to `utils/timers.py`, so it does not depend on the excluded runtime.
- Restored all conflict-lost imports/helpers reported by F821, including bounded grep/view limits, notify dependencies, Knowledge sort/embedding constants, external/Paddle loader gates, and file media extraction policy.
- Terminal/Chatfile security: policy IDs are encoded as one path segment; system OAuth tokens are resolved server-side; session auth continues to mint custom short-lived user JWTs; WebSocket auth returns the verified user/connection pair and preserves custom upstream session auth.
- Verification: Lane C contract `3 passed`; owned-path `ruff --select F821` clean; Terminal + Chatfile suite `27 passed`.
- Current checkpoint: run and triage the remaining Skill, ACL, knowledge/retrieval, loader/vector, OnlyOffice, and registry suites; compare failures to official semantic commits before changing code.
- Next verification: focused suite matrix and exclusion search across the full repository, with out-of-lane references recorded rather than edited.

### Checkpoint 3: retrieval, loader, ACL, OnlyOffice, and registry audit

- Status: complete.
- Updated custom retrieval tests to the official v0.11 explicit `RetrievalConfig`, `collection_result`, native-hybrid, and request/event interfaces. Production call sites already use the new interfaces; no compatibility rollback was added.
- Added focused coverage for file/connection ACL filtering, audio chunk-order preservation, read-only tool source stripping, OpenSERP normalisation, SearXNG ordering/parameters, timer registration without the excluded runtime, and notify registration.
- Passing focused groups: Skill/registry `38`; retrieval router/config `11`; retrieval index/manifest/evidence `72`; document/loader/multimodal/vector `54`; pgvector `7`; OnlyOffice/Chatfile `47`; file ACL `3`; audio/tool security `2`; OpenSERP/SearXNG `2`; Terminal/Chatfile `27`.
- Optional test dependency: installed `pgvector==0.4.2` into the existing local test virtualenv only; no dependency or lockfile changed.
- Test import side effect removed tracked static assets once; they were verified clean at baseline and restored exactly from HEAD. No static/frontend change remains.
- Current checkpoint: run one combined fresh focused suite, compile/lint/exclusion checks, review final diff, then commit.
- Next verification: combined focused pytest exit 0, owned compile/F821/diff checks exit 0, exact out-of-lane exclusion references recorded.

### Checkpoint 4: final verification complete

- Status: complete; ready to commit.
- Fresh combined focused suite: `260 passed, 54 warnings` in 7.44 seconds. Warnings are existing dependency/deprecation and short test-key warnings; no failures, collection errors, or unhandled thread warnings remain.
- Static verification: owned compileall exit 0; owned `ruff --select F821` exit 0; `git diff --check` exit 0.
- Exclusion verification: owned definitions/imports/registry are clean and the duplicate `utils/subagents.py` is deleted. Remaining out-of-lane references are listed below for combined integration.
- Worktree hygiene: test-generated static deletions restored from HEAD after the final suite; final status contains only Lane C production/tests/handoff paths.
- Commit: the commit containing this handoff is the verified Lane C integration commit.

## Test ledger

| Area | RED command/outcome | GREEN command/outcome | Final command/outcome |
|---|---|---|---|
| Terminal | collection error, then WebSocket tuple/policy tests failed as expected | `22 passed` in Terminal file | included in `260 passed` |
| Skill packages | existing registration failed on request without state after registry change | `38 passed` with registry/contract | included in `260 passed` |
| File ACL / Chatfile / OnlyOffice | policy encoding and OAuth tests: 2 failed | ACL `3`; OnlyOffice/Chatfile `47` | included in `260 passed` |
| Knowledge / retrieval | v0.10.2-style tests failed against explicit v0.11 config/interfaces | retrieval `11`; index/manifest/evidence `72` | included in `260 passed` |
| Loaders / vectors | pgvector import missing | loaders/multimodal `54`; pgvector `7` | included in `260 passed` |
| Tool registry / exclusions | `test_v011_lane_c_contracts.py`: 2 failed, 1 passed | `5 passed`; owned F821 clean | included in `260 passed` |

## Cross-lane integration notes

- Lane A: remove official `ENABLE_SUBAGENTS` and `subagents.*` config/defaults from `backend/open_webui/config.py`; retain only custom AgentScope settings, if any. The current base still contains official keys around lines 2160 and 3243-3249.
- Lane B: remove/replace `backend/open_webui/main.py` import of `open_webui.utils.subagents.process_pending_internal_messages` (current base around line 3542). `utils/timers.py` no longer needs that module.
- Lane B: remove `query_chat_files` handling from `backend/open_webui/utils/middleware.py` (current base around lines 454 and 545), preserving `query_knowledge_files`/custom evidence handling.
- Lane D: remove official `delegate_task` renderer handling in `src/lib/components/chat/Messages/structuredOutput.ts` and official `src/lib/components/admin/Settings/Subagents.svelte`/related config UI; preserve custom AgentScope transcript attribution.
- Combined integration: Lane B currently has unrelated syntax conflicts in `backend/open_webui/socket/main.py` and `backend/open_webui/utils/middleware.py`. They block collection of knowledge-router, layered-knowledge, evidence-runtime, and Agent tool-authority tests; rerun those after Lane B merge.

## Residual risks

- Knowledge-router, layered-knowledge, evidence-runtime, and Agent tool-authority suites remain blocked until Lane B resolves syntax conflicts in `socket/main.py` and `utils/middleware.py`; these were not counted in the `260 passed` suite.
- The combined tree does not satisfy global exclusions until Lanes A/B/D remove the exact config, middleware/main, and frontend references listed above.
- The final combined integration still needs its own all-lane backend suite, migration validation, and isolated runtime acceptance; this lane did not touch live service or live database.
