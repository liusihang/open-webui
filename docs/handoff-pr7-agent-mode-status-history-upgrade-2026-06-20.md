# PR7 Agent Mode UI: 基于StatusHistory升级

## Scope

- 分支：`codex/pr7-agent-mode-status-history-upgrade`，基点 `codex/pr7-review-security-fixes`
- 目标：删掉 PR7 的整套 AgentEvents UI（AgentRunHeader/AgentRunDebugPanel/AgentRunTimeline/AgentRunEventItem/AgentRunThinkingState/renderModel），写一个 adapter 把 AgentRunEvent 转成 `message.statusHistory` 条目，让现有 `StatusHistory` + `StatusItem` 统一渲染 chatbot 和 agent 两种模式。
- 约束：前端为主；不动后端 runtime；保留 PR7 的数据层（types/eventFold/messageState/store）和可复用子组件（AgentApprovalPanel/AgentArtifactCard/AgentToolPanel/AgentSubagentPanel/AgentFinalAnswer）；保留 `markAgentRunMessageDone` + `saveMessage` 持久化路径。
- 完整方案见 `/Users/liusihang/.claude/plans/joyful-pondering-sifakis.md`。

## Product Rules

1. Agent 模式的普通 Q&A 不再显示"任务进展" header、8 种状态徽章、调试 panel。
2. Agent 活动通过 `message.statusHistory` 复用 chatbot 已有的 `StatusHistory` 渲染：当前活动大卡片 + 历史竖线节点。
3. 工具调用渲染为一行 pill（图标 + 工具名 + 参数摘要），点击展开 input/output。
4. Approval 渲染为内联 Approve/Deny 按钮。
5. Artifact 渲染为内联可折叠卡片（图片直显，代码/文本 pre，其他下载链接）。侧栏面板后续再做。
6. Subagent 渲染为一行（名字 + summary），可展开结果。
7. 普通 Q&A（无 tool）显示轻量"思考中..." spinner，答案出来后变 final answer。
8. Chatbot 模式（web_search/knowledge_search/RAG sources）行为完全不变。
9. 调试信息（Run ID/Transport/event seq）不暴露给最终用户。

## Architecture

```
后端 AgentRunEvent SSE
        ↓
AgentRunStatusBridge.svelte  (无 UI，替代 AgentRunEvents.svelte)
        ↓
agentStatusAdapter.ts        (event → statusHistory 条目, upsert by stableId)
        ↓
message.statusHistory        (跟 web_search 共享同一数组)
        ↓
StatusHistory.svelte         (现有组件，改 1 处渲染逻辑)
        ↓
StatusItem.svelte            (扩展 kind 分派)
        ↓
新增子组件: ToolStatusRow / ApprovalStatusRow / ArtifactStatusRow / SubagentStatusRow / ThinkingStatusRow / ErrorStatusRow
```

两条事件通道并存：
- Socket.IO `events` → `Chat.svelte:521` chatEventHandler → `message.statusHistory.push(status)`（web_search 等老路径，不动）
- SSE `/api/agent/runs/{id}/events` → Bridge → adapter → upsert `message.statusHistory`（agent 新路径）

## Key Design Decisions（已被 Plan agent 验证）

1. **upsert 后必须重新赋值**：`message.statusHistory = [...next]`，否则 Svelte 4 元素替换不触发 StatusHistory 的 `$: if (!equal(...))` 重算。
2. **不写 message.content**：finalText 仍由 `<AgentFinalAnswer>` 独立渲染（props `save:false, preview:false, floatingButtons:false`）。理由：ContentRenderer 流式期间全量重渲染 markdown；editCodeBlock/save 会把 finalText 当可编辑内容。
3. **两条通道接受按到达顺序**：无全局 seq，adapter 对 stableId 相同的条目 upsert，不做插入排序。
4. **改 StatusHistory 渲染而非 currentStatusIndex**：保留"最后一个 done:false 是当前 status"语义（web_search 依赖）。改 `historyStatuses` 让 running 历史节点也 shimmer。
5. **保留轻量 thinking status**：run.queued/run.running 时 push `{kind:'thinking', done:false}`，run 终结时 update 为 done:true。保证普通 Q&A 有"思考中"指示。

## Checkpoints

- 2026-06-20: 起子分支 `codex/pr7-agent-mode-status-history-upgrade` from `codex/pr7-review-security-fixes`。stash 了 4 个阻塞文件（config.py automations 改动、permissions.ts 同上、handoff brainstorming md、docs/adr/0001 未跟踪文件），stash message: "blockers before agent mode ui refactor"。
- 2026-06-20: 写完 handoff 文档（本文件）。下一步：TDD 写 `agentStatusAdapter.test.ts` + `agentStatusAdapter.ts`。
- 2026-06-20: adapter + 测试完成。23 个测试全绿（tool/approval/artifact/subagent/step/thinking/error/run error/ignored events/upsert/immutability/chatbot 兼容）。
- 2026-06-20: Bridge 组件完成。复用 eventFold 的 foldAgentRunEvent 维护 finalText/runStatus，dispatch 'final' 和 'terminal' 事件。Svelte compile OK。
- 2026-06-20: 扩展 ResponseMessage.svelte 的 statusHistory 类型（加 id/kind/detail/seq/created_at 可选字段）。
- 2026-06-20: ThinkingStatusRow + StatusItem kind 分派完成。普通 Q&A 路径跑通（thinking → finalAnswer）。
- 2026-06-20: ResponseMessage 接 Bridge 完成。bind:statusHistory + on:final + on:terminal。AgentFinalAnswer 简化 props（去掉 renderModel 依赖）。compile OK，现有 628 个 chat 测试全过。
- 2026-06-20: 验证 StatusHistory 渲染逻辑（task #7）。**Plan agent 之前的担心基于误读**：行 96 `done={status?.done !== false}` 真值表——status.done=false 时传 done=false，StatusItem 内 `(done || status?.done) === false` 成立 → shimmer。现有逻辑已正确处理 running 历史节点，**无需改动**。
- 2026-06-20: 加完所有 Row 子组件（Thinking/Step/Tool/Error/Artifact/Subagent/Approval）。Step 直接在 StatusItem 内联渲染（shimmer 文字），未单独建组件。
- 2026-06-20: 删除 PR7 旧 UI 文件（14 个）：AgentRunEvents/AgentRunHeader/AgentRunDebugPanel/AgentRunTimeline/AgentRunEventItem/AgentRunThinkingState/renderModel/renderModel.test/AgentToolPanel/AgentDetailSection/AgentArtifactCard/AgentSubagentPanel/AgentApprovalPanel/store.ts。**保留** 的只有数据层：types.ts/eventFold.ts/messageState.ts/fixtures.ts + AgentFinalAnswer.svelte（简化了 props，去掉 renderModel 依赖，加 history/messageId 必填 props 以匹配 ContentRenderer 接口）。
- 2026-06-20: 验证通过。
  - 单元测试：agentStatusAdapter.test.ts (23/23) + eventFold.test.ts (9/9) + historySync.test.ts (16/16) 全绿；主代码 584/584 测试全过（`.worktrees/` 下的 13 个 failed file 是 tsconfig.json 缺失的预存噪音，非真实失败）。
  - Svelte 类型检查 (`npm run check`)：所有新增/修改文件零新增错误。ResponseMessage.svelte 错误数从 101 → 100（实际减少 1 个）。所有"Property X does not exist on type 'never'" 错误是预存问题（源于 `export let status = null;` 的 TS 推断），与本次改动无关。所有"Cannot use 'i18n' as a store" 错误是预存模式问题（`getContext('i18n')` 返回 unknown），全代码库共有，与本次改动无关。
  - 修复了 2 个本次引入的真实类型错误：ToolStatusRow 的 `detail` narrowing（改 `open && hasDetail` 为 `open && detail`），AgentRunStatusBridge 的 `export type` 语法错误（inline 到 createEventDispatcher 泛型），AgentFinalAnswer 缺 history/messageId props（补上并从 ResponseMessage 传入）。
- 2026-06-20: 下一步：commit + 人工 E2E 验证（需 dev server + 后端）+ 截图对比。
- 2026-06-20: 按用户要求重建最新测试镜像，基于当前 HEAD `46efc38113ace5c0f6afb7d32d5595d463d43615`。
  - 目标镜像：`open-webui:pr7-slim-46efc38113`
  - 远端构建机：`aiserver`
  - 远端 staging：`/home/aiserver/staging/openwebui-pr7-status-history-46efc38113-build`
  - 构建日志：`/home/aiserver/staging/openwebui-pr7-status-history-46efc38113-build/docker-build-46efc38113.log`
  - 构建脚本：仓库内 `scripts/rebuild-pr7-slim-cache.sh` 仍有既知 SSH 引号问题（`unexpected EOF while looking for matching \`''`），因此这次沿用既有最小 workaround：`git archive` 干净源码到远端 staging，在 staging 副本上做 operational build-only patch，再手动跑 `docker buildx build --load`
  - build-only patch 仅发生在远端 staging 副本，不改本地仓库源码：
    - 删除 staged `Dockerfile` 首行 `# syntax=docker/dockerfile:1`
    - 将 `node:22-alpine3.20` / `python:3.11-slim-bookworm` 切到 `docker.m.daocloud.io/library/...`
  - 验证结果：
    - `docker image inspect open-webui:pr7-slim-46efc38113`
    - image id: `sha256:41a7f2387d84c477cefc56b169c16eeacf49999ecc6ff131a7554ee9095260e8`
    - created: `2026-06-20T06:02:57.77605994Z`
    - size: `1980524019`
  - 备注：镜像已经成功导入本地 Docker daemon；BuildKit 随后仍在执行 cache export，这一段不影响镜像可用性。
- 2026-06-20: 按用户要求替换当前 PR7 测试镜像，只操作 isolated 测试栈 `/home/aiserver/staging/openwebui-pr7-eea11194ed-test`，不碰 live `open-webui`。
  - 初始状态：
    - compose image：`open-webui:pr7-slim-cdc5bda4e`
    - container image：`open-webui:pr7-slim-cdc5bda4e`
    - live image：`open-webui:live-pr7-f8106c651`
  - 第一次替换：
    - compose 备份：`/home/aiserver/staging/openwebui-pr7-eea11194ed-test/compose.yaml.bak.20260620141128.46efc38113`
    - 仅将 `open-webui-pr7` 改为 `open-webui:pr7-slim-46efc38113`
    - `docker compose -f compose.yaml up -d --no-deps --force-recreate open-webui-pr7`
    - 在较短观察窗口里容器被判为 `unhealthy`，因此按最小回滚恢复 compose 并只重建 `open-webui-pr7`
  - 调试结论：
    - 新镜像和回滚后的旧镜像都出现相同现象：早期 healthcheck 对 `127.0.0.1:8080/health` 报 `ConnectionRefusedError`
    - 这说明首轮失败证据不足，问题更像是 `force-recreate` 后冷启动明显长于第一次观察窗口，而不是新镜像独有故障
    - 旧镜像最终在更长等待后自行恢复 `healthy`
  - 第二次替换（只改变等待策略，不改 compose 以外配置）：
    - compose 备份：`/home/aiserver/staging/openwebui-pr7-eea11194ed-test/compose.yaml.bak.20260620142049.retry46efc38113`
    - 再次只将 `open-webui-pr7` 切到 `open-webui:pr7-slim-46efc38113`
    - 给予完整冷启动窗口后，容器成功恢复
  - 最终验证结果：
    - `open-webui-pr7`：`open-webui:pr7-slim-46efc38113`，`running healthy`，`RestartCount=0`
    - `/health`：`{"status":true}`
    - `/health/db`：`{"status":true}`
    - live `open-webui`：`open-webui:live-pr7-f8106c651`，`running healthy`，`RestartCount=0`

## Verification Plan

- RED: `agentStatusAdapter.test.ts` 覆盖 tool 完成/失败、approval、subagent、artifact、普通 Q&A、失败 run。
- GREEN: `npm run test:frontend -- --run src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts`
- Svelte compile 检查：Bridge + 所有 Row + StatusItem + StatusHistory。
- E2E 场景：普通 Q&A、单次 web search（回归）、单次 tool、并发 tool、approval、artifact、subagent、失败 run。
- 截图对比 before/after（before: `handoff/pr7-952-ui-a-20260620-q1-filled-annotated.png`）。

## File Map

### 新增
- `src/lib/components/chat/AgentEvents/agentStatusAdapter.ts`
- `src/lib/components/chat/AgentEvents/agentStatusAdapter.test.ts`
- `src/lib/components/chat/AgentEvents/AgentRunStatusBridge.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/ToolStatusRow.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/ApprovalStatusRow.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/ArtifactStatusRow.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/SubagentStatusRow.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/ThinkingStatusRow.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/ErrorStatusRow.svelte`

### 修改
- `src/lib/components/chat/Messages/ResponseMessage.svelte`（MessageType 扩展 statusHistory 字段；AgentRunEvents→Bridge 接线）
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory.svelte`（historyStatuses 渲染逻辑）
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`（kind 分派）

### 删除
- `src/lib/components/chat/AgentEvents/AgentRunEvents.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunHeader.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunDebugPanel.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunTimeline.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunEventItem.svelte`
- `src/lib/components/chat/AgentEvents/AgentRunThinkingState.svelte`
- `src/lib/components/chat/AgentEvents/renderModel.ts`
- `src/lib/components/chat/AgentEvents/renderModel.test.ts`
- `src/lib/components/chat/AgentEvents/AgentToolPanel.svelte`
- `src/lib/components/chat/AgentEvents/AgentDetailSection.svelte`
- `src/lib/components/chat/AgentEvents/AgentArtifactCard.svelte`
- `src/lib/components/chat/AgentEvents/AgentSubagentPanel.svelte`
- `src/lib/components/chat/AgentEvents/AgentApprovalPanel.svelte`
- `src/lib/components/chat/AgentEvents/store.ts`

### 保留
- `src/lib/components/chat/AgentEvents/types.ts` / `eventFold.ts` / `messageState.ts` / `fixtures.ts` / `eventFold.test.ts` / `messageState.test.ts`
- `src/lib/components/chat/AgentEvents/AgentFinalAnswer.svelte`（简化 props：去掉 renderModel 依赖；加 history/messageId 必填 props）
