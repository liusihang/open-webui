# Handoff: Agent Mode commentary ordering and hang

Truth surface: PR7 conversation `ea993aef-0b14-416f-a82c-7c6a9eea9149`, request log `c6560ad6-5c14-484e-9dab-60ff6b426fe3`, isolated container `open-webui-pr7`, production code commit `7e7fd83ca2f7`, and current worktree HEAD `21a20a683403`.

Execution owner: `/root`; read-only code trace delegated to `/root/commentary_order_code_trace` without a context fork.

Current checkpoint: the earlier replay-order implementation, review, commit, image build, isolated swap, and remote smoke remain complete. A new native-phase design has now been approved after a bounded upstream capability probe. The design document is `docs/plans/2026-07-10-agent-mode-native-phase-streaming-design.md`. No native-phase production code has been changed yet. The isolated PR7 WebUI remains healthy on `open-webui:agentmode-v0102-7e7fd83ca2f7-slim` / `sha256:1d6c1cf36751...`, restart count zero. Runtime, DB, Redis, and live anchors remain unchanged.

Browser verification is also complete on the user's original conversation. The new cross-turn turn invoked environment, timestamp, and command tools, completed in 18 seconds, rendered the expected final sentence, and produced no browser console warning/error. Do not run a broad Bifrost log scan; any future gateway verification must start from a known exact log ID or an already-produced targeted smoke artifact.

Stop/rollback condition: do not mutate the live service on port `18080`. If the isolated WebUI regresses, recreate only `open-webui-pr7` using the rollback chain ending in `compose.webui-b2e665078056.yaml`.

## Design checkpoint: model-authored commentary and native phase

No implementation change has been made for this checkpoint. The user requires tool-prelude commentary to be real model output (pseudo-stream display is acceptable), while the final answer must remain genuinely token-streamed. Runtime-generated first-person notes such as `I will use ...` are not acceptable.

Current code findings:

- Historical assistant input can carry Responses message `phase=commentary|final_answer`; this only labels replay input and does not prove that the provider emits output phase metadata.
- The model authority already passes provider SSE through to AgentScope, but `openwebui_client._parse_openai_chunk` only understands Chat Completions `choices[0].delta` and discards native Responses `response.output_item.*` / `response.output_text.delta` context.
- `_stream_model_call` converts every text chunk into an untyped `TextBlockDeltaEvent`, and `_run_leader_streaming` treats the first such chunk as final output. Native phase support therefore requires correlated Responses item parsing plus phase-aware runtime events; changing only replay ordering cannot solve it.
- If the upstream Responses stream provides `response.output_item.added.item.phase`, native passthrough can preserve real commentary and real final streaming without an extra model call. If Bifrost/model does not emit or preserve that field, the runtime cannot reconstruct it losslessly.
- Provider-independent alternative: a tool-enabled acting phase followed by one tool-free finalizer call. This reliably preserves genuine final streaming but adds one model call. A prompt-level `COMMENTARY` / `FINAL` marker or hidden final-answer tool can avoid native phase dependence, but both introduce a model-compliance protocol and are less reliable than native metadata.

Next checkpoint before implementation: use a bounded raw-SSE capability probe against the exact configured model/gateway path (or the exact known log artifact), never a broad Bifrost log scan. Decide native phase first if the phase field is present; otherwise choose the two-stage finalizer design.

## Capability probe result: native phase is available

The bounded probe is complete. No broad Bifrost log scan was performed.

Exact surfaces checked:

- Existing request detail: `GET http://192.168.2.238:18080/api/logs/c6560ad6-5c14-484e-9dab-60ff6b426fe3`.
- PR7 model: `bifrostapi.Cliproxy/gpt-5.4`.
- Pipe upstream: `http://192.168.2.238:18080/v1/responses`, model `Cliproxy/gpt-5.4`.
- Credentials were read only in remote memory from the installed `bifrostapi` function valves and were neither printed nor written to disk.

Observed upstream order:

1. Tool probe:
   - sequence 2: `response.output_item.added`, message phase `commentary`;
   - sequence 4: first `response.output_text.delta`, already correlated to `commentary`;
   - sequence 13: commentary message completed;
   - sequence 14: `response.output_item.added`, `function_call`;
   - sequence 23: `response.completed`, containing commentary message followed by function call.
2. Final-answer probe:
   - sequence 4: `response.output_item.added`, message phase `final_answer`;
   - sequence 6: first `response.output_text.delta`, already correlated to `final_answer`;
   - sequence 15: `response.completed` with final-answer message.

Decision: use native phase passthrough. It satisfies model-authored commentary and genuinely streamed final answers without an extra model call.

Confirmed local loss points:

- `tools/openwebui/functions/bifrostapi.py::_messages_to_responses_input` reconstructs assistant message items without copying `phase`, so replay phase is lost before Bifrost.
- `tools/openwebui/functions/bifrostapi.py::_parse_responses_event` ignores message items in `response.output_item.added|done` and converts every `response.output_text.delta` to untyped `choices[0].delta.content`.
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py::_parse_openai_chunk` only extracts content/reasoning/tool calls and drops any local phase metadata.
- `agentscope_bridge.py::_stream_model_call` converts all content into the same `TextBlockDeltaEvent`, while `app.py::_run_leader_streaming` treats every such event as final output.
- `OpenWebUIToolProxy` still generates first-person `I will use ...` and result-summary text independently of model output.

Recommended implementation boundary:

1. Preserve valid historical assistant phase in the repo-managed `bifrostapi` Pipe.
2. Correlate Responses message item `output_index` / `item_id` with phase and include that phase on normalized content chunks.
3. Parse phase in the AgentScope callback client and route model-authored commentary directly to public `text.delta`; only `final_answer` becomes streamed `TextBlockDeltaEvent` / `final.delta`.
4. Remove runtime-generated first-person tool intent/result narration; retain `tool.requested`, `tool.completed`, `tool.failed`, approval, artifact, and user-input events.
5. Treat missing/unknown phase strictly: with tool calls, buffer text until the model response is classified; without a trustworthy final phase, do not prematurely transition to `finalizing`.

Required verification:

- Pipe unit tests for input phase preservation and output item/delta phase correlation.
- Runtime parser/bridge tests for commentary-before-tool and final-only streaming.
- App lifecycle tests proving commentary never starts `finalizing`.
- Existing replay, parallel-tool, multi-round, approval, user-input, cancellation, and idempotency suites.
- One isolated PR7 raw-SSE + browser acceptance run; live service remains untouched.

Design approval: confirmed by the user on 2026-07-10. Next action is to commit the design/handoff checkpoint, create the file-backed implementation plan, then implement with tests first.

## Isolated deployment evidence

- Compose chain: `compose.yaml`, `compose.webui-rebuild-eaff69b0d317.yaml`, `compose.webui-eaff69-no-migrations.yaml`, `compose.webui-7e7fd83ca2f7.yaml`.
- New container: `583e10f1c7729e5c8c67deba13db995ef1d78adb622bf83339800cdc5fd1a681`.
- Target image: `open-webui:agentmode-v0102-7e7fd83ca2f7-slim` / `sha256:1d6c1cf367519128b13baca625a435e547b9da9887e024afb4e0405f46eb3f83`.
- Cold start reached healthy with restart count zero; `/health` returned true and `/api/version` returned `0.10.2`.
- Startup logs contained no `Traceback`, `CRITICAL`, `Application startup failed`, or `Exception in ASGI application`.
- Order smoke: passed, run `6097213b-aca3-423c-aebb-e5d16f96092d`; provider order `user -> calls[1,2] -> outputs[3,4]`.
- Multiround smoke: passed, run `a0049080-df66-4fc7-905f-6b5651e4712a`; provider order `call1 -> output1 -> call2 -> output2`.
- Final verification: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/final-verification-7e7fd83ca2f7-20260710-131649.txt`.
- Rollback command: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/rollback-command-7e7fd83ca2f7.txt`.

## Original-conversation browser verification

- Conversation: `ea993aef-0b14-416f-a82c-7c6a9eea9149` on PR7 port `18085`.
- Prompt marker: `REPLAY-7E7F-OK`.
- Actual tools: `get_environment`, `get_current_timestamp`, `run_command`.
- Visible completion: `Processed 18s`; final answer included Linux x86_64, `/home/user`, `/bin/bash`, the current UTC timestamp, and successful `REPLAY-7E7F-OK` output.
- UI order: each tool intent and completed result remained in sequence, followed by the final answer; no new hang occurred.
- Browser console: zero warning/error entries after completion.
