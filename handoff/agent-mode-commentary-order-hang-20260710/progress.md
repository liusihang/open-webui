# Native phase progress

## 2026-07-10

### Discovery and design

- [x] Reproduced the semantic mismatch against exact conversation/log surfaces.
- [x] Confirmed visible `I will use ...` text is runtime-generated.
- [x] Traced the provider, Pipe, callback parser, bridge, and finalizing boundaries.
- [x] Ran a bounded raw Responses SSE capability probe.
- [x] Verified commentary and final-answer phase arrive before their first text delta.
- [x] Selected native phase passthrough; rejected the extra finalizer call.
- [x] User approved the design.
- [x] Added and committed the design document as `72cf407c0`.

### Implementation

- [x] Pipe tests RED: three new assertions failed because assistant input and normalized text deltas lacked phase; invalid/non-assistant phase omission already passed.
- [x] Pipe implementation GREEN: strict assistant input phase preservation and output item/delta phase correlation added; full Pipe file 23 passed.
- [x] Callback parser tests RED: valid commentary phase raised `KeyError`; invalid phase omission passed.
- [x] Callback parser implementation GREEN: focused phase tests and the full parser file passed (22 tests).
- [x] Bridge tests RED: four focused tests failed because commentary was streamed as final text and strict protocol errors were absent.
- [x] Bridge implementation GREEN: commentary is buffered/persisted, final text alone remains streamed, and strict missing/final-with-tool errors pass four focused tests.
- [x] Synthetic narration removal RED: five success/failure/approval/replay assertions failed on ToolProxy-authored text.
- [x] Synthetic narration removal GREEN: focused five tests and full bridge file (19 tests) pass with structured tool events only.
- [x] App lifecycle focused tests: commentary remains running until persisted, first final delta starts finalizing, and missing phase fails without fallback.
- [x] App full file passed 41 tests after phase-aware fixture migration and synthetic narration expectation removal.
- [x] Runtime/backend regression: runtime 95, backend Agent/Responses 202, Responses payload 5, Agent frontend 97.
- [x] Independent review: all four Important findings reproduced or traced and fixed with focused tests; the Minor fixture concern is covered by raw-SSE, Pipe, parser, bridge, and app seam tests.
- [x] Production commit created as `5dfd7759d` before checkpoint amendment.
- [ ] Image rebuild and isolated acceptance.

## Test results

| Test | Expected | Actual | Status |
|---|---|---|---|
| Raw tool-phase probe | Commentary phase before text and tool | Confirmed | pass |
| Raw final-phase probe | Final phase before first final delta | Confirmed | pass |
| Pipe phase RED tests | Valid input/output phase tests fail on missing phase | 3 failed, 1 passed | pass |
| Pipe full file | Existing and new Pipe behavior remains compatible | 23 passed | pass |
| Callback parser focused phase tests | Valid phase preserved; invalid phase omitted | 2 passed | pass |
| Callback parser full file | No parser regression | 22 passed | pass |
| Bridge phase RED tests | Four tests fail on current untyped bridge behavior | 4 failed | pass |
| Bridge phase GREEN tests | Commentary/final split and protocol errors behave as designed | 4 passed | pass |
| Bridge full file after phase split | Existing phase-less fixtures expose the new strict contract | 3 failed, 14 passed | expected migration |
| Bridge full file after fixture migration | Phase-aware bridge regression | 17 passed | pass |
| ToolProxy narration RED | Success, failure, approval, and replay paths still synthesize text | 5 failed | pass |
| ToolProxy narration GREEN | No synthetic text; structured events preserved | 5 passed | pass |
| Bridge full file after ToolProxy cleanup | No bridge regression | 19 passed | pass |
| App full file before phase-aware fixture migration | Canned done payloads expose missing phase and old synthetic narration assertions | 9 failed, 30 passed | expected migration |
| App phase lifecycle focused tests | Commentary ordering and protocol-error closeout | 2 passed | pass |
| App full file | General Agent lifecycle regression | 41 passed | pass |
| Backend Agent/Responses regression initial run | Responses reasoning render exposed missing `html` import | 198 passed, 1 failed | real pre-existing bug |
| Bridge strict terminal classification RED | Commentary-only and reasoning-only empty responses were accepted | 2 failed | real review finding |
| Same-run Responses input ordering RED | Assistant content was dropped whenever the same message carried tool_calls | 1 failed | original request-body bug |
| Cross-participant commentary block RED | Leader and subagent model-call-1 used the same run-global block id | 1 failed | real concurrency bug |
| Silent-stream cancellation RED | Commentary/provider silence left runtime waiting indefinitely after cancellation | 1 failed | real lifecycle bug |
| Malformed tool delta RED | Invalid tool entry bypassed empty response validation | 1 failed | real protocol bug |
| Bridge strict terminal classification GREEN | Commentary-only and empty public responses fail explicitly | 2 passed | pass |
| Pipe same-run ordering GREEN | Commentary message precedes call/output transaction | 1 passed | pass |
| Pipe full file after ordering fix | Phase/input/output regression | 24 passed | pass |
| AgentScope runtime full suite after review fixes | Runtime, bridge, client, and lifecycle regression | 90 passed | pass |
| Agent frontend focused suite | AgentEvents, transcript, history sync, and API models | 97 passed | pass |
| Provider auxiliary routing RED | Web-search/image display chunks were treated as phase-less model text | 5 focused failures | real integration bug |
| Provider auxiliary routing GREEN | Pipe markers, client parsing, and bridge persistence | 5 focused passes | pass |
| Cross-runtime commentary block GREEN | Runtime session and participant make block ids run-global unique | 1 passed | pass |
| Silent-stream cancellation GREEN | Cancellation wakes and closes active stream | 1 passed | pass |
| Malformed tool delta GREEN | Invalid tool call fails explicitly | 1 passed | pass |
| Final AgentScope runtime suite | Complete runtime regression after review fixes | 95 passed | pass |
| Final backend Agent/Responses suite | Agent backend, Pipe, alias, and Responses rendering | 202 passed | pass |
| Final Responses payload suite | OpenAI Responses request construction | 5 passed | pass |
| Final frontend Agent suite | AgentEvents and history synchronization | 97 passed | pass |
| Static verification | High-signal Ruff, py_compile, and diff check | passed | pass |

## Error log

| Time | Error | Resolution |
|---|---|---|
| 2026-07-10 | agent-browser socket directory unavailable | Switched to in-app browser. |
| 2026-07-10 | Initial SSH denied by sandbox | User granted permission; retry passed. |
| 2026-07-10 | Exact log returned redacted virtual key | Used installed Pipe valves without exposing the key. |
| 2026-07-10 | Wrong connection key produced provider-blocked 403 | Traced and used the actual `bifrostapi` route. |
| 2026-07-10 | Three bridge regression fixtures omitted native phase or expected model commentary to be discarded | Marked tool prelude as commentary, final answers as final_answer, and asserted persisted model commentary. |
| 2026-07-10 | Nine app tests used a non-stream done shim with no phase | Replaced the canned-response shim with phase-aware chunk events matching the production callback contract. |
| 2026-07-10 | Native-phase lifecycle test waited for buffered commentary before a classification boundary | Blocked inside the commentary callback instead; this verifies the exact persisted-commentary-before-final transition ordering. |
| 2026-07-10 | Responses reasoning serialization raised `NameError: html is not defined` | Existing test supplied RED; added the missing standard-library import used by all HTML escaping paths. |
| 2026-07-10 | Commentary-only/no-tool and reasoning-only empty responses completed without a trustworthy final phase | Added explicit `model_final_phase_missing` and `empty_model_response` failures after persisting valid commentary. |
| 2026-07-10 | Pipe converted assistant content+tool_calls directly to calls and lost the model-authored pre-tool text | Emit a `phase=commentary` message before the contiguous function-call transaction. |
| 2026-07-10 | Leader/subagent commentary shared `model-call-1:model-commentary` despite run-global block deduplication | Include participant id in the commentary block id. |
| 2026-07-10 | Cancellation could not wake `_run_leader_streaming` while no final buffer existed | Add bounded cancellation polling and explicitly cancel/close the active stream iterator. |
| 2026-07-10 | Tool deltas missing a valid function/name completed as an empty text response | Validate merged tool calls before terminal classification and fail with `invalid_tool_call`. |
