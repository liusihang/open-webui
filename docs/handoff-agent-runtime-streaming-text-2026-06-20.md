# Agent Runtime: 流式 text delta + idempotency 修复

## Scope

- 分支：`codex/pr7-agent-mode-status-history-upgrade`
- 目标：为 PR7 agent mode UI 重构后用户反馈的 3 个问题提供完整修复
  1. **Final answer 不流式** — agentscope 等模型完全输出后才推一次 `final.delta`（delta_index=0，完整 answer）
  2. **Task 功能报 `idempotency_conflict` + UI 不更新** — `AgentRunOperationConflict` 409 直接打断 agent run，agentscope 无重试直接抛异常，`run.completed` 没发，前端停在"思考中"
  3. **工具调用间的思考文字没展示** — Claude Code 风格"思考→工具→更多思考→更多工具"的交错呈现缺失

**根因**：
- 问题 1+3 同根：agentscope runtime 调 `leader.reply()`（阻塞），ReAct 循环内 model 输出的 text block 完全不发；循环结束后 `_emit_final_answer` 把完整 answer 作为一个 delta 推
- 问题 2 独立：`claim_operation` 对"同一 idempotency_key 但 request_hash 不同"抛 409，agentscope 收到 409 直接崩

**目标**：
- 修问题 2（阻塞 bug，先做）
- 修问题 1+3（完整 Claude Code 风格：流式 text delta + tool 事件按 seq 交错）
- subagent（Task）暂不流式，保留现状

## 3 个 Phase

### Phase 1: 修 idempotency_conflict（1 天）✅ 已完成 2026-06-20

**目标**：消除 Task 功能的 409 阻塞。

**openwebui 侧**（`backend/open_webui/`）：
- `models/agent_runs.py:593-617` 新增 `find_operation_by_idempotency_key(run_id, operation_type, idempotency_key)` 方法，从 `AgentRunOperation` 表查已存 operation
- `routers/agent_service.py:995-1033` 改造 `_append_agent_event_with_operation`：catch `AgentRunOperationConflict`，查询已存 operation 并通过 `_cached_event_operation_response` 返回（保留 in_progress→202、failed→409 语义）
- **关键约束**：只对 `event.append` 放宽；`state.transition` / `final.delta` 仍严格（这些操作的正确性依赖 request_hash 一致）

**agentscope 侧**（`services/agentscope-runtime/agentscope_runtime/openwebui_client.py:38-80`）：
- `append_event` 收到 409 时当作成功，返回响应 JSON（含 `{detail: 'idempotency_conflict', seq: N}`）；响应体空时合成 fallback payload
- 其他 status code 仍按原逻辑（503 等 is_error 抛 RuntimeError）

**测试**：
- `backend/open_webui/test/agent/test_agent_service_idempotency.py`（新增 6 tests）：
  1. event.append 不同 payload 返回已存 event（200）
  2. event.append 相同 payload 返回已存 event（200）
  3. state.transition 不同 payload 仍 409
  4. final.delta 不同 payload 仍 409
  5. event.append operation in_progress 返回 202
  6. event.append operation failed 返回 409
- `backend/open_webui/test/agent/test_agent_run_routes_db_store.py:225-271` 更新现有测试 `test_agent_service_event_callback_retries_are_idempotent_and_conflicting_bodies_return_existing`（原测冲突 409，现测返回已存 200）
- `services/agentscope-runtime/tests/test_openwebui_client.py:99-155` 扩展 2 tests：
  - `test_append_event_treats_409_idempotency_conflict_as_success`
  - `test_append_event_treats_409_with_empty_body_as_idempotency_conflict`

**验证**：
- openwebui 侧：`pytest open_webui/test/agent/ open_webui/test/util/ --ignore=test_pgvector_search.py` → **604 passed**（含 8 个新/改测试）
- agentscope 侧：`uv run --extra test pytest tests/test_openwebui_client.py` → **13 passed**（含 2 个新测试）

### Phase 2: 协议层 text.delta（3 天）⏳ 进行中

#### 2.1 openwebui 后端协议 ⏳

- `backend/open_webui/agent/protocol.py`：`AgentEventType` 新增 `TEXT_DELTA = 'text.delta'`
- `backend/open_webui/agent/events.py`：新增 `TextDeltaAppend` model + `append_text_delta` 函数（复用 `append_final_delta` 去重逻辑，同时调 `store.append_final_text_delta` 写入 final_text store）
- `backend/open_webui/models/agent_runs.py`：复用 `append_final_text_delta`（用 block_id 作为 final_stream_id）
- `backend/open_webui/routers/agent_service.py`：新增 `POST /runs/{run_id}/text-delta` endpoint + `_append_text_delta_with_operation`

#### 2.2 agentscope 流式改造 ⏳

- `services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py`：`OpenWebUIAgentScopeModel` stream=True；`_call_api` 返 `AsyncGenerator`；消费流式 chunk，推 text.delta（含 block_id + delta_index）；末尾 yield 完整 ChatResponse（is_last=True）
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`：新增 `append_text_delta` 方法
- `services/agentscope-runtime/agentscope_runtime/app.py`：`_finalize_general_agent_run` 用 `leader.reply_stream()`；`_emit_final_answer` 不再发 final.delta，保留 final.started + state transition + run.completed

### Phase 3: 前端渲染（2 天）⏳

- `src/lib/components/chat/AgentEvents/agentStatusAdapter.ts`：新增 `'text'` kind + `text.delta` case + `markTextSegmentsDone`
- `src/lib/components/chat/AgentEvents/eventFold.ts`：保留 `final.delta` 向后兼容，新增 `text.delta` 处理
- `src/lib/components/chat/AgentEvents/AgentRunStatusBridge.svelte`：路由 `text.delta`
- `src/lib/components/chat/AgentEvents/types.ts`：`AGENT_RUN_EVENT_TYPES` 加 `text.delta`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`：分派 `'text'` kind
- 新增 `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/TextStatusRow.svelte`：节流渲染（delta 阶段纯文本，done 时 markdown）

## 文件清单

### 已修改（Phase 1 完成）
- `backend/open_webui/models/agent_runs.py` — `find_operation_by_idempotency_key`
- `backend/open_webui/routers/agent_service.py` — `_append_agent_event_with_operation` 放宽
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py` — `append_event` 409 当成功

### 已新增（Phase 1 完成）
- `backend/open_webui/test/agent/test_agent_service_idempotency.py` — 6 tests

### 已扩展（Phase 1 完成）
- `backend/open_webui/test/agent/test_agent_run_routes_db_store.py` — 更新现有 conflict 测试
- `services/agentscope-runtime/tests/test_openwebui_client.py` — +2 tests

## 验证命令

### Phase 1
```bash
cd /Users/liusihang/openwebui/backend
uv run pytest open_webui/test/agent/test_agent_service_idempotency.py -v
uv run pytest open_webui/test/agent/ open_webui/test/util/ --ignore=open_webui/test/util/test_pgvector_search.py

cd /Users/liusihang/openwebui/services/agentscope-runtime
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY uv run --extra test pytest tests/test_openwebui_client.py -v
```

### Phase 2 / 3
待实施完成后补全。

## 下一步

1. Phase 2.1 先做（openwebui 后端协议层，跟 agentscope 解耦）
2. Phase 2.2 跟进（agentscope 流式改造，联调时合并）
3. Phase 3 在 Phase 2 联调通过后做（前端渲染依赖协议层稳定）
4. 每个 Phase 完成后 commit + 更新本文件

## 关键约束

- **subagent 非流式**：用户选了"只 leader 流式"。subagent 内部仍是黑盒（created → completed/failed）。Phase 2.2 只改 leader 路径
- **tool_call_id 不一致**：agentscope `ToolCallBlock.id`（来自 model 响应）vs bridge `tool-call-{N}`（bridge.py:264 自分配）。本次不修，文档化此约束
- **旧 run 兼容**：改造前创建的 run 可能用了 `final.delta`。前端保留 `final.delta` 分支，确保旧 run 仍能渲染
- **final_text 持久化**：text.delta 必须写入 `append_final_text_delta` store，否则完成消息无内容
