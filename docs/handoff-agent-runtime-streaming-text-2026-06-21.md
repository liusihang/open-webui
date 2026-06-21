# Agent Runtime: 流式 text delta + idempotency 修复 — Handoff (2026-06-21)

## 1. 任务背景与目标

**分支**：`codex/pr7-agent-mode-status-history-upgrade`
**主分支**：`main`
**Committer**：`shuofang <916931057@qq.com>`

PR7 agent mode UI 重构（commit `3be4f1315`）合并后，用户反馈了 3 个问题，需要完整修复：

1. **Final answer 不流式** — agentscope 等模型完全输出后才推一次 `final.delta`（delta_index=0，完整 answer）
2. **Task 功能报 `idempotency_conflict` + UI 不更新** — `AgentRunOperationConflict` 409 直接打断 agent run，agentscope 无重试直接抛异常，`run.completed` 没发，前端停在"思考中"
3. **工具调用间的思考文字没展示** — 缺失 Claude Code 风格"思考→工具→更多思考→更多工具"的交错呈现

**根因**：

- 问题 1+3 同根：agentscope runtime 调 `leader.reply()`（阻塞），ReAct 循环内 model 输出的 text block 完全不发；循环结束后 `_emit_final_answer` 把完整 answer 作为一个 delta 推
- 问题 2 独立：`claim_operation` 对"同一 idempotency_key 但 request_hash 不同"抛 409，agentscope 收到 409 直接崩

**实施方案**（已经讨论确认）：3 个 Phase

- Phase 1：修 idempotency_conflict（阻塞 bug，先做）
- Phase 2.1：openwebui 后端 text.delta 协议层
- Phase 2.2：agentscope 流式改造（leader 流式，subagent 暂保留非流式）
- Phase 3：前端渲染（text 段作为 statusHistory 条目，按 seq 交错）

完整实施 plan 见 `/Users/liusihang/.claude/plans/joyful-pondering-sifakis.md`。

---

## 2. 当前进度

| Phase                                  | 状态                               | Commit      |
| -------------------------------------- | ---------------------------------- | ----------- |
| Phase 1: idempotency_conflict 修复     | ✅ 已完成、已提交                  | `391a891cf` |
| Phase 2.1: openwebui text.delta 协议层 | ✅ 已完成、已提交                  | `6e3e644a1` |
| Phase 2.2: agentscope 流式改造         | ✅ 已完成、已提交                  | `3502e1c68` |
| Phase 3: 前端 text 段渲染              | 🚧 代码与单测已完成，待 E2E/commit | -           |

最近 commit:

```
3502e1c68 feat(agent-runtime): stream model text via text.delta during ReAct loop         ← Phase 2.2
6e3e644a1 feat(agent-runtime): add text.delta event protocol for streaming model text  ← Phase 2.1
391a891cf fix(agent-runtime): relax event.append idempotency to unblock subagent flow  ← Phase 1
46efc3811 feat(agent-runtime): unblock tool consolidation for all-agent-mode
3be4f1315 refactor(agent-mode): rebuild agent UI on StatusHistory                     ← PR7
```

### 2.1 本会话最新进展（2026-06-21 持续更新）

- ✅ Phase 2.2 已按计划单独提交：`3502e1c68`
- ✅ Phase 2.2 提交前回归：
  - `cd /Users/liusihang/openwebui/services/agentscope-runtime && env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u all_proxy uv run --extra test pytest tests/ -q`
    - `49 passed in 2.21s`
  - `cd /Users/liusihang/openwebui/backend && uv run pytest open_webui/test/agent/ -q`
    - `145 passed, 9 warnings in 13.13s`
- ✅ Phase 3 代码已落地（未 commit）：
  - `types.ts`：注册 `text.delta`
  - `agentStatusAdapter.ts`：新增 `text` kind、`upsertText`、`markTextSegmentsDone`
  - `eventFold.ts`：把 `text.delta` 累积进 `finalText`，按事件 `seq` 还原跨 block 顺序
  - `StatusItem.svelte` + `TextStatusRow.svelte`：新增 text 段渲染
- ✅ Phase 3 单测回归：
  - `npx vitest run src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts src/lib/components/chat/AgentEvents/eventFold.test.ts --exclude '.worktrees/**'`
    - `2 passed, 36 tests`
  - `npx vitest run src/lib/components/chat/AgentEvents/*.test.ts --exclude '.worktrees/**'`
    - `3 passed, 38 tests`
  - `npx vitest run src/lib/components/chat/historySync.test.ts --exclude '.worktrees/**'`
    - `1 passed, 16 tests`
- ✅ Agent Mode 工具链健康检查：
  - `ENABLE_AGENT_MODE=true AGENT_RUNTIME_BASE_URL=http://127.0.0.1:8097 AGENT_RUNTIME_SERVICE_TOKEN=test-service-token AGENT_TEAM_MAX_SUBAGENTS=5 python3 scripts/agent_mode/healthcheck.py --check-env --skip-runtime`
    - `Agent Mode healthcheck: ok`
  - `python3 scripts/agent_mode/acceptance_harness.py dry-run`
    - `case contract: 0/12 satisfied`
    - `live acceptance: pending`
    - 说明 harness 本身可运行，但**没有执行任何 live 场景**
- ⚠️ `npx svelte-check --threshold error` 当前**不能**作为本任务验收门槛：
  - 会直接命中仓库现存的全局 TypeScript 噪音（`RichTextInput.svelte` / `AutoCompletion.js` / `listDragHandlePlugin.js` 等）
  - 本次运行结果：`9365 errors and 276 warnings in 390 files`
  - 这些报错与本次 `text.delta` 改动无直接关联，但说明要做真正的全局类型收敛需要单独任务
- ⏳ **真实 E2E 场景（浏览器 + 本地 agent-mode 栈 + 模型/provider/tool 配置）仍未执行**
  - 当前没有现成本地运行栈监听 `5173/8080/8090`
  - 如果下一位接手者要补 live 验证，不应把 `dry-run` / healthcheck / 单测误当成通过

---

## 3. Phase 2.2 已完成内容（待 commit）

### 3.1 修改的文件

```
backend/open_webui/agent/model_authority.py        |   92 +-
backend/open_webui/routers/agent_service.py        |   43 +-
services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py   |  252 +-
services/agentscope-runtime/agentscope_runtime/app.py                 |   87 +-
services/agentscope-runtime/agentscope_runtime/openwebui_client.py    |  166 ++
services/agentscope-runtime/tests/test_agentscope_bridge.py           |  114 +-
services/agentscope-runtime/tests/test_app.py                         |  104 +-
docs/handoff-pr7-agent-mode-status-history-upgrade-2026-06-20.md      |   38 +
```

`uv.lock` 也有变化（之前的依赖更新顺带带的，跟本次任务无关，可以一并 commit）。

### 3.2 关键改动概览

#### openwebui 后端：`/model-call` 支持 SSE 流式

**`backend/open_webui/agent/model_authority.py`** 新增 `stream_model_call` 方法：

- Bypass operation store（流式响应没有单一 canonical payload 可缓存）
- agentscope runtime 自己负责 model_call_id 级别的幂等性
- 直接把 provider 的 StreamingResponse 字节透传给 SSE
- 末尾追加一个 `data: {"type":"stream_end",...}` 事件作为终结信号
- 非流式 fallback：把完整响应包成一个 `done` 事件后 `stream_end`

**`backend/open_webui/routers/agent_service.py`**：

- `/runs/{run_id}/model-call` endpoint 增加 stream 分支：当 `call.stream=True` 时返回 `StreamingResponse(authority.stream_model_call(...), media_type='text/event-stream')`，header `Cache-Control: no-cache`、`X-Accel-Buffering: no`
- 错误处理：`AgentRunOperationConflict` → 409，`ModelRunRejected` → 403，其他 → 502/500

#### agentscope 运行时：消费流式响应、推 text.delta

**`services/agentscope-runtime/agentscope_runtime/openwebui_client.py`**：

- 新增 `append_text_delta` 方法（POST `/runs/{run_id}/text-delta`）
- 新增 `call_model_stream` async generator：POST `/model-call` with `stream=True`，yield 事件 dict：
  - `{'type': 'chunk', 'delta': {'content': str|None, 'tool_calls': list|None}}`
  - `{'type': 'done', 'payload': {...}}` （非流式 fallback）
  - `{'type': 'stream_end'}` （终结）
- 新增 `_iter_sse_events`、`_parse_openai_chunk`、`_safe_response_json_sync`、`_ModelCallOperationInProgress` 辅助
- **重要修复**：之前重构时 `call_tool` 和 `_post_callback` 被错误地嵌套到 `_ModelCallOperationInProgress` 里，已修正为 `OpenWebUIClient` 的方法

**`services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py`**：

- `OpenWebUIAgentScopeModel.__init__`：`stream=True`、`max_retries=0`
- `_call_api` 改成 `async def`（不是 async generator function，没有 yield），`return self._stream_model_call(...)`，匹配 agentscope `ChatModelBase.__call__` 的 `return await self._call_api(...)` 模式 —— `_reasoning_impl:802` 用 `inspect.isasyncgen(res)` 来识别返回的是 async generator
- 新增 `_stream_model_call` async generator：
  - 用 `uuid.uuid4().hex` 生成 per-model-call `block_id`
  - 消费 `call_model_stream` 的事件流：
    - `chunk` 事件含 `delta.content` → push `append_text_delta`（idempotency_key=`text:{run_id}:{participant_id}:{block_id}:{delta_index}`），yield 中间 `ChatResponse(is_last=False)`
    - `chunk` 事件含 `delta.tool_calls` → 累积 tool_call deltas
    - `done` 事件（非流式 fallback）→ extract 完整 text，push 单个 text.delta（delta_index=0）
    - `stream_end` → break
  - 末尾 yield 完整 `ChatResponse(content=[TextBlock+ToolCallBlocks], is_last=True)`，agentscope `_reasoning_impl:805-806` 要求最后一个 chunk `is_last=True` 且含完整 blocks（line 857 用来 `_save_to_context`）
- `OpenWebUIBridgeCallbacks` Protocol 增加 `append_text_delta` 签名
- 新增 `_merge_tool_calls` 辅助：合并 OpenAI 风格 tool_call deltas（按 `index` 累积 `function.arguments`）
- 新增 `_prepend_event` 辅助 + 改造 retry：retry 必须在第一次 `__anext__()` 时检测错误，因为 `call_model_stream(...)` 调用本身只返回 generator object，不会抛错

**`services/agentscope-runtime/agentscope_runtime/app.py`**：

- `RuntimeCallbackClient` Protocol 增加 `append_text_delta`
- `_finalize_general_agent_run`：`leader.reply(...)` → `_run_leader_streaming(leader, session, messages)`
- 新增 `_run_leader_streaming`：消费 `leader.reply_stream(messages)`，捕获终止 `AssistantMsg` 用于 fallback 文本提取
- `_emit_final_answer`：**不再发 `final.delta`**，但保留：state transition `running→finalizing→completed`、`final.started` 事件、`run.completed` 事件
- `_finalize_ordinary_qa`（无工具的简单 Q&A 路径，bypass bridge）：在调 `_emit_final_answer` 之前，自己 push 一次 text.delta（idempotency_key=`text:{run_id}:leader:answer:0`，block_id=`answer`），保证 `final_text` store 有内容供 completion handler 读取
- `_msg_text(msg)`：增加 `None` 兜底（如果 reply_stream 没产出 AssistantMsg 时返回空字符串）

### 3.3 测试更新

**`services/agentscope-runtime/tests/test_app.py`**：

- `RecordingOpenWebUIClient` 增加 `text_deltas: list[dict]` 字段、`append_text_delta` 方法、`call_model_stream` async generator（壳：调内部 `call_model` 拿响应，包成 `done` + `stream_end`）
- 所有 `final_deltas[0]["delta"] == X` 断言改为 `text_deltas[-1]["delta"] == X`（流式 shim 每次 model call 产出一个 text delta，最后一次的就是最终答案）
- subagent 测试用 `any(... in delta["delta"] for delta in text_deltas)` 检查整体内容
- `final_deltas == []` 断言保留（验证不再发 final.delta）

**`services/agentscope-runtime/tests/test_agentscope_bridge.py`**：

- `RecordingBridgeCallbacks` 增加 `text_deltas`、`append_text_delta`、`call_model_stream`
- 把 `response = await model(...)` 改为 `response = None; async for chunk in await model(...): response = chunk`（model call 现在返回 async generator，要先 `await` 再 `async for`）
- `ToolCallingCallbacks`（`test_model_bridge_preserves_openwebui_tool_calls_as_agentscope_blocks`）：`call_model` 已 override，但因为流式路径直接调 `call_model_stream`，并没有真正经过 `call_model`，要确认测试是不是还能验证 tool_calls 解析。**当前测试通过了**，因为 `RecordingBridgeCallbacks.call_model_stream` 调内部 `call_model`（被 `ToolCallingCallbacks` override 返回带 tool_calls 的响应），整体路径仍然在测 tool_calls 提取

### 3.4 测试结果

```bash
cd /Users/liusihang/openwebui/services/agentscope-runtime
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u all_proxy \
  uv run --extra test pytest tests/ -v
# → 49 passed

cd /Users/liusihang/openwebui/backend
uv run pytest open_webui/test/agent/ open_webui/test/util/ \
  --ignore=open_webui/test/util/test_pgvector_search.py -q
# → 614 passed
```

---

## 4. 接下来要做的事（按顺序）

### 4.1 Step 1: Commit Phase 2.2（已完成）

```bash
cd /Users/liusihang

# 检查改动
git status -uno
git --no-pager diff --stat HEAD

# 把 Phase 2.2 改动 stage 上（注意：uv.lock 跟本次无关，不要带上；如果跟其他改动冲突也可以单独处理）
git add backend/open_webui/agent/model_authority.py
git add backend/open_webui/routers/agent_service.py
git add services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py
git add services/agentscope-runtime/agentscope_runtime/app.py
git add services/agentscope-runtime/agentscope_runtime/openwebui_client.py
git add services/agentscope-runtime/tests/test_agentscope_bridge.py
git add services/agentscope-runtime/tests/test_app.py
git add docs/handoff-pr7-agent-mode-status-history-upgrade-2026-06-20.md

# 不要 add：uv.lock（除非确认是本次任务带的）
# 跑一遍验证
cd services/agentscope-runtime
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u all_proxy \
  uv run --extra test pytest tests/ -q
cd ../../backend
uv run pytest open_webui/test/agent/ -q

cd /Users/liusihang
git commit -m "feat(agent-runtime): stream model text via text.delta during ReAct loop"
# 实际提交：3502e1c68
```

### 4.2 Step 2: Phase 3 — 前端 text 段渲染（代码与单测已完成，待 commit）

#### 4.2.1 protocol 类型层

**`src/lib/components/chat/AgentEvents/types.ts`**：

- `AGENT_RUN_EVENT_TYPES` 数组里在 `'final.delta'` 之后新增 `'text.delta'`：
  ```ts
  export const AGENT_RUN_EVENT_TYPES = [
      'run.queued',
      ...
      'final.started',
      'final.delta',
      'text.delta',         // ← 新增
      'run.completed',
      ...
  ] as const;
  ```
- `AgentRunEventCategory` 新增 `'text'`（如果想让 text 进入 counts；可选）

#### 4.2.2 Status adapter

**`src/lib/components/chat/AgentEvents/agentStatusAdapter.ts`**：

1. `AgentStatusKind` 加 `'text'`：

   ```ts
   export type AgentStatusKind =
   	| 'tool'
   	| 'approval'
   	| 'artifact'
   	| 'subagent'
   	| 'thinking'
   	| 'text' // ← 新增
   	| 'step'
   	| 'error';
   ```

2. `AgentStatusDetail` 加 `text`：

   ```ts
   export type AgentStatusDetail = {
       ...
       text?: { blockId: string; content: string; participantId?: string | null };
   };
   ```

3. `applyEvent` 增加 `case 'text.delta':` 分支（建议放在 `case 'tool.requested':` 前，确保 tool/approval 之前 emit 的 text 段先 markDone）：

   ```ts
   case 'text.delta':
       return upsertText(history, event);

   case 'tool.requested':
   case 'tool.started':
       return upsertTool(markTextSegmentsDone(history, event), event, false);

   // ↑ 注意：tool/approval/subagent.created 之前都要先 markTextSegmentsDone
   case 'approval.requested':
       return upsertApproval(markTextSegmentsDone(history, event), event);

   case 'subagent.created':
   case 'subagent.updated':
       return upsertSubagent(markTextSegmentsDone(history, event), event, false);

   case 'run.completed':
       return markThinkingDone(markTextSegmentsDone(history, event), event);
   ```

4. 新增 `upsertText`：

   ```ts
   const upsertText = (history: AgentStatusEntry[], event: AgentRunEvent): AgentStatusEntry[] => {
   	const blockId = firstString(event.payload.block_id, event.payload.blockId);
   	if (!blockId) return history;
   	const id = `text:${blockId}`;
   	const delta = firstString(event.payload.delta, event.payload.text) ?? '';
   	const existing = history.find((entry) => entry.id === id);
   	const previousContent = existing?.detail?.text?.content ?? '';
   	return upsert(history, id, {
   		done: false,
   		action: 'agent_text',
   		description: '', // text 用 detail.text 渲染，description 不用
   		kind: 'text',
   		seq: event.seq,
   		created_at: event.created_at,
   		detail: {
   			text: {
   				blockId,
   				content: previousContent + delta,
   				participantId: event.participant_id ?? null
   			}
   		}
   	});
   };
   ```

5. 新增 `markTextSegmentsDone`：

   ```ts
   const markTextSegmentsDone = (
   	history: AgentStatusEntry[],
   	event: AgentRunEvent
   ): AgentStatusEntry[] => {
   	let changed = false;
   	const next = history.map((entry) => {
   		if (entry.kind !== 'text' || entry.done) return entry;
   		changed = true;
   		return { ...entry, done: true, seq: event.seq, created_at: event.created_at };
   	});
   	return changed ? next : history;
   };
   ```

6. `shallowEqual` 注意事项：text entry 的 detail 每次 delta 都会变（content 累积），所以 shallowEqual 在 detail 字段对象不同时会判定不等，会触发更新 —— 这是想要的。

#### 4.2.3 EventFold（向后兼容 + 可选 text 段聚合）

**`src/lib/components/chat/AgentEvents/eventFold.ts`**：

1. 保留 `final.delta` 分支不动（旧 run 兼容）
2. （可选）增加 `text.delta` 分支：把 text 累积到 `state.finalText`，这样 `AgentRunStatusBridge` 的 `dispatch('final', ...)` 流程仍然能拿到完整内容供 `ResponseMessage.svelte` 的 `agentFinalAnswer` 使用。
   - 关键：因为后端的 `_finalize_ordinary_qa` 也在 push text.delta，UI 这边要把所有 text.delta 串起来当 finalText。可以仿照 `finalDeltaChunks` 用 `textDeltaChunks: Map<string, {blockId, deltaIndex, text, participantId}>`。

但更简单的做法：**`AgentRunStatusBridge.svelte` 直接从 statusHistory 的 text entry 拼 finalText**，避免 eventFold.ts 维护两份状态。例如：

```ts
$: if (state.finalText || hasTextEntry(statusHistory)) {
    const text = state.finalText || joinTextEntries(statusHistory);
    dispatch('final', { content: text, done: ..., status: ... });
}
```

#### 4.2.4 渲染层

**新增 `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/TextStatusRow.svelte`**：

```svelte
<script lang="ts">
	import ContentRenderer from '../../ContentRenderer.svelte';
	// ↑ 检查这个路径：Messages/ContentRenderer.svelte 还是 Messages/ResponseMessage/ContentRenderer.svelte？
	//   实际可能要用 Markdown.svelte，参考 ResponseMessage.svelte 是怎么渲染最终答案的

	export let detail: { text?: { blockId: string; content: string } } = {};
	export let done = false;

	$: text = detail?.text?.content ?? '';
</script>

{#if text}
	<div class="agent-text-segment py-1 w-full">
		{#if done}
			<!-- 完整 markdown 渲染 -->
			<ContentRenderer content={text} />
		{:else}
			<!-- 流式期间纯文本，避免每个 delta 都重渲染 markdown -->
			<pre
				class="whitespace-pre-wrap font-sans text-sm text-gray-900 dark:text-gray-100">{text}<span
					class="agent-text-cursor">▍</span
				></pre>
		{/if}
	</div>
{/if}

<style>
	.agent-text-cursor {
		animation: blink 1s steps(2, start) infinite;
		opacity: 0.6;
	}
	@keyframes blink {
		to {
			visibility: hidden;
		}
	}
</style>
```

**`src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`**：在 `kind === 'subagent'` 之后加：

```svelte
{:else if status?.kind === 'text'}
    <TextStatusRow done={status?.done !== false} detail={status?.detail} />
```

并在顶部 `import TextStatusRow from './TextStatusRow.svelte';`。

注意 `StatusItem.svelte` 当前模板有 `class="status-description flex items-center gap-2 py-0.5 w-full text-left"` 的最外层 div，对 text 段来说可能太窄；TextStatusRow 内部用 `w-full` 应该能撑满，但需要在浏览器里验证。如果 layout 不对，可能要在 StatusItem.svelte 给 text kind 单独一个外层 div。

#### 4.2.5 Bridge 路由

**`src/lib/components/chat/AgentEvents/AgentRunStatusBridge.svelte`**：

`AGENT_RUN_EVENT_TYPES` import 来自 types.ts，加完后会自动生效。`ingestEvent` 不用改，因为 `foldAgentRunEvent` 和 `foldAgentEventIntoStatusHistory` 都会处理。

如果选了 4.2.3 的"从 statusHistory 拼 finalText"方案，需要修改 `$: if (state.finalText) { dispatch(...) }` 为：

```ts
$: textFromHistory = joinTextEntries(statusHistory); // 从 status entries 拼
$: combined = state.finalText || textFromHistory;
$: if (combined) {
	dispatch('final', {
		content: combined,
		done: isTerminalAgentRunStatus(state.runStatus),
		status: state.runStatus
	});
}
```

#### 4.2.6 测试

**`src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts`** 增加测试用例（参考已有测试结构）：

1. `text.delta` 累积 content（同 block_id 多次 delta）
2. 多 block_id 隔离（两个不同 block_id 产生两个 text entries）
3. `markTextSegmentsDone`：
   - 收到 `tool.requested` 时把 done:false 的 text entry 标 done
   - 收到 `run.completed` 时同上
4. text 段在 statusHistory 里跟 tool entry 按 seq 交错
5. `text.delta` 缺 `block_id` 时 graceful skip

测试运行：

```bash
cd /Users/liusihang
npm run test:frontend -- --run src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts
```

#### 4.2.7 Svelte 编译检查

确保新增的 svelte 组件能通过 svelte-check：

```bash
cd /Users/liusihang
npm run check
# 或者只跑 svelte-check：
npx svelte-check --threshold error
```

### 4.3 Step 3: E2E 验证（dev server 上人工测试）

启动 dev server（假设有 docker compose 或 npm 脚本）：

```bash
# 后端
cd /Users/liusihang/backend && uv run uvicorn open_webui.main:app --reload --port 8080
# agentscope runtime
cd /Users/liusihang/services/agentscope-runtime && env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY \
  uv run --extra dev uvicorn agentscope_runtime.app:app --port 8090
# 前端
cd /Users/liusihang && npm run dev
```

E2E 场景：

1. **普通 Q&A**（无 tool）：流式显示最终答案，跟 chatbot 模式体验一致
2. **单次 tool 调用**：模型先流式输出"我先确认一下..."（text 段），然后 tool pill（tool 段），然后最终答案流式（text 段）
3. **多次 tool 调用交错**：text → tool → text → tool → text，每段按 seq 顺序交错显示
4. **Task（subagent）**：subagent.created → 等待 → subagent.completed，**不再报 idempotency_conflict**
5. **失败 run**：text 段 + 红色错误条
6. **chatbot 模式回归**：web_search/knowledge_search 行为完全不变

### 4.4 Step 4: 最后 commit + push

```bash
git add src/lib/components/chat/AgentEvents/types.ts
git add src/lib/components/chat/AgentEvents/agentStatusAdapter.ts
git add src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts
git add src/lib/components/chat/AgentEvents/eventFold.ts                  # 如果有改
git add src/lib/components/chat/AgentEvents/AgentRunStatusBridge.svelte
git add src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte
git add src/lib/components/chat/Messages/ResponseMessage/StatusHistory/TextStatusRow.svelte

git commit -m "$(cat <<'EOF'
feat(agent-mode): render text.delta segments interleaved with tool calls

Phase 3 of streaming-text rollout. Renders the new text.delta event
type as a `text` kind StatusHistory entry so the front-end shows the
Claude Code style "thinking → tool → more thinking" interleaved
narrative.

- types.ts: add 'text.delta' to AGENT_RUN_EVENT_TYPES
- agentStatusAdapter: new 'text' kind + text.delta upsert (accumulate
  by block_id) + markTextSegmentsDone helper that closes any open
  text entry whenever the run pivots to a tool/approval/subagent or
  terminates. text segments and tool entries interleave naturally by
  seq.
- StatusItem: dispatch 'text' kind to new TextStatusRow.
- TextStatusRow: streams plain pre-wrapped text with a blinking cursor
  while the segment is open, then renders full markdown once done
  (avoids re-parsing markdown on every delta).
- AgentRunStatusBridge: derives finalText from text entries to
  preserve the existing AgentFinalAnswer rendering hook.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

# push (新建分支或现有分支)
git push origin codex/pr7-agent-mode-status-history-upgrade
```

---

## 5. 关键约束与陷阱

### 5.1 不能动的设计约束

- **subagent 非流式**：用户明确选了"只 leader 流式"。Phase 2.2 只改 leader 路径，subagent 内部仍是 created → completed 黑盒。不要试图扩展到 subagent
- **旧 run 兼容**：改造前创建的 run 可能有 `final.delta`。前端 `final.delta` 分支必须保留（`eventFold.ts:82-100`），删了会导致旧 run 渲染不出最终答案
- **`final_text` 持久化**：text.delta 必须写入 `append_final_text_delta` store（Phase 2.1 已实现，`block_id` 当 `final_stream_id` 用），否则 completion handler 读不到消息内容、聊天记录里会是空消息

### 5.2 Phase 2.2 实施过程中踩过的坑

1. **agentscope `__call__` 用 `await self._call_api(...)`**：所以 `_call_api` 本身**不能**是 async generator function（不能在函数体里 yield），而是要 `async def` 然后 `return self._stream_model_call(...)`。这跟 ollama 的实现一致（参考 `agentscope/model/_ollama/_model.py:125-191`）

2. **`_reasoning_impl:805-806` 要求最后一个 chunk `is_last=True` 且含完整 blocks**（用于 `_save_to_context` 和 `ToolCallBlock` 检测）。bridge 必须在 stream 结束后单独 yield 完整 ChatResponse

3. **retry 必须在 `__anext__()` 时**：`call_model_stream(...)` 返回 generator object 不抛错，错误（httpx timeout 等）只在第一次 `__anext__()` 时抛出。`_prepend_event` 把已经 pull 出来的第一个事件接回 stream

4. **`call_tool` / `_post_callback` 嵌套 bug**：早期重构时这两个方法被错误地放进了 `_ModelCallOperationInProgress` 类里，导致 `OpenWebUIClient` 没有这两个方法。已修复（见 `openwebui_client.py:241-308`）

5. **ordinary QA 路径 bypass bridge**：`_finalize_ordinary_qa` 直接调 `_call_leader_model`（非流式 `call_model`），不走 bridge。要在 `_finalize_ordinary_qa` 自己 push 一次 text.delta，否则 `final_text` store 没内容

6. **`_msg_text(None)`**：reply_stream 不一定产出 AssistantMsg（比如 cancel 时），`_msg_text` 要 None 兜底

### 5.3 Phase 3 容易踩的坑

1. **delta 阶段不要渲染 markdown**：每个 delta 都全量重 parse markdown 会非常卡，尤其长答案。TextStatusRow 必须 `done` 时才用 ContentRenderer/Markdown，期间用 `<pre class="whitespace-pre-wrap">` 渲染纯文本

2. **`shallowEqual` 在 text entry 上的行为**：text entry 的 `detail` 每次都是新对象（content 累积），`shallowEqual` 会判定不等触发更新 —— 这是想要的。但要注意不要意外把 detail 改成可变对象，否则 svelte 检测不到变更

3. **`StatusItem.svelte` 外层布局**：当前模板是 `flex items-center gap-2`，对 text 段（多行 markdown）可能太窄。需要在浏览器实际验证：如果文字溢出或被压在一行，要单独给 `kind === 'text'` 一个 `flex-col` 或 `block` 容器

4. **finalText 来源**：现在 `state.finalText` 来自 eventFold 的 `finalDeltaChunks`，而 text 段在 statusHistory 里。如果不在 eventFold 加 text.delta 处理，`AgentRunStatusBridge.dispatch('final', ...)` 就拿不到内容，`AgentFinalAnswer.svelte` 不会渲染最终答案。两个选择：
   - **方案 A**（推荐）：在 eventFold.ts 也处理 text.delta，累积到 `finalText`。代码量少
   - **方案 B**：从 statusHistory 拼。需要改 AgentRunStatusBridge.svelte 的反应式声明
   - **方案 C**：删掉 AgentFinalAnswer，让所有内容都走 statusHistory（包括最终答案）。需要更大幅改动 ResponseMessage.svelte，影响面广，**不推荐这次做**

   建议先用方案 A，最快、改动最小。

5. **eventFold 和 statusHistory 的状态分离**：当前架构两边各自累积状态（eventFold 维护 finalText/items，statusHistory 维护渲染条目）。Phase 3 不要打破这个分离：text.delta 要在两边都处理，但用各自的语义。

---

## 6. 调试技巧

### 6.1 看流式是否真的流起来了

在浏览器 DevTools Network 面板找 `/api/agent/runs/{run_id}/events` SSE 连接，应该能看到 `data: {"event_type":"text.delta",...}` 事件按顺序到达。如果只看到一个大 chunk，说明流式没生效。

### 6.2 看 agentscope runtime 是不是真的 stream=True 在调 model-call

在 agentscope runtime 日志里搜：

```
POST /api/agent/service/runs/{run_id}/model-call
```

应该看到 header `Accept: text/event-stream`。

### 6.3 手动测 `/model-call` SSE

```bash
curl -N -X POST http://127.0.0.1:8080/api/agent/service/runs/$RUN_ID/model-call \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "X-Agent-Idempotency-Key: model:leader:test:1" \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "model:leader:test:1",
    "run_id": "'$RUN_ID'",
    "participant_id": "leader",
    "model_call_id": "model-call-test",
    "model": "model-a",
    "messages": [{"role":"user","content":"hello"}],
    "stream": true,
    "params": {}
  }'
```

应该看到 SSE 流：`data: {"choices":[{"delta":{"content":"..."}}]}` 一行行过来，最后是 `data: {"type":"stream_end",...}`。

### 6.4 单独验证 idempotency 修复

跑 `test_agent_service_idempotency.py`（Phase 1 加的），如果 Task（subagent）功能在 dev server 上还报 409，说明 Phase 1 的逻辑哪里漏了 —— 不大可能，因为 Phase 1 已经合并并通过了 614 个测试。

---

## 7. 文件清单总结

### 已 commit

- Phase 1（`391a891cf`）：
  - `backend/open_webui/models/agent_runs.py` (find_operation_by_idempotency_key)
  - `backend/open_webui/routers/agent_service.py` (\_append_agent_event_with_operation 放宽)
  - `services/agentscope-runtime/agentscope_runtime/openwebui_client.py` (append_event 409 当成功)
  - `backend/open_webui/test/agent/test_agent_service_idempotency.py` (新增)
  - `backend/open_webui/test/agent/test_agent_run_routes_db_store.py` (扩展)
  - `services/agentscope-runtime/tests/test_openwebui_client.py` (扩展)
- Phase 2.1（`6e3e644a1`）：
  - `backend/open_webui/agent/protocol.py` (TEXT_DELTA enum)
  - `backend/open_webui/agent/events.py` (TextDeltaAppend, append_text_delta)
  - `backend/open_webui/models/agent_runs.py`
  - `backend/open_webui/routers/agent_service.py` (/text-delta endpoint)
  - `backend/open_webui/test/agent/test_events.py` (扩展)

### 待 commit (Phase 2.2)

- `backend/open_webui/agent/model_authority.py` (stream_model_call)
- `backend/open_webui/routers/agent_service.py` (/model-call SSE branch)
- `services/agentscope-runtime/agentscope_runtime/agentscope_bridge.py`
- `services/agentscope-runtime/agentscope_runtime/app.py`
- `services/agentscope-runtime/agentscope_runtime/openwebui_client.py`
- `services/agentscope-runtime/tests/test_agentscope_bridge.py`
- `services/agentscope-runtime/tests/test_app.py`

### Phase 3 待新建/修改

- `src/lib/components/chat/AgentEvents/types.ts`（修改）
- `src/lib/components/chat/AgentEvents/agentStatusAdapter.ts`（修改）
- `src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts`（扩展）
- `src/lib/components/chat/AgentEvents/eventFold.ts`（修改：text.delta 处理）
- `src/lib/components/chat/AgentEvents/AgentRunStatusBridge.svelte`（可能不用改，看方案）
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`（修改）
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/TextStatusRow.svelte`（新增）

---

## 8. 工作量评估

- Phase 2.2 commit + 跑回归：30 min
- Phase 3 实施：1.5 - 2 天（adapter + svelte + 测试 + 调试 layout）
- E2E 验证：0.5 天
- Handoff 更新 + push + PR：30 min

**总计：约 3 天**

---

## 9. 联系上下文

- 完整实施 plan：`/Users/liusihang/.claude/plans/joyful-pondering-sifakis.md`
- 上一阶段 handoff：`docs/handoff-pr7-agent-mode-status-history-upgrade-2026-06-20.md`
- 上一阶段 handoff（旧版本）：`docs/handoff-agent-runtime-streaming-text-2026-06-20.md`
- agentscope 相关参考实现：`services/agentscope-runtime/.venv/lib/python3.12/site-packages/agentscope/model/_ollama/_model.py`（看 streaming `_call_api` 模式）
- agentscope ReAct 循环源码：`services/agentscope-runtime/.venv/lib/python3.12/site-packages/agentscope/agent/_agent.py:755-880`（`_reasoning_impl`）
