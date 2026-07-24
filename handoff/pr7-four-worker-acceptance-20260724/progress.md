# PR7 四 Worker 验收进度

## 2026-07-24

### C0 本地真值建立

- 已确认当前工作目录：`/Users/liusihang/.codex/worktrees/d790/openwebui`。
- 已确认起始 HEAD：`ccde1d134de3e82101461d6c73c6d11d7ce66498`。
- 已确认指定分支：`codex/pr7-live-compatible-20260722`，分支 ref 与 HEAD 相同。
- 已确认指定分支 worktree：`/Users/liusihang/openwebui/.worktrees/pr7-live-compatible-20260722`，初始状态干净。
- 下一步：在 `aiserver` 上执行隔离与正式 live 的只读 preflight。

### C0 远端只读 preflight 完成

- 时间：`2026-07-24 21:30 Asia/Shanghai`。
- 隔离 WebUI：container `df1ba2b48e5397bd...`，image `sha256:fd6145b041f28269...`，healthy，restart=0，当前 `UVICORN_WORKERS=1`。
- 隔离 runtime：container `739472bd32748c19...`，image `sha256:f7396ba23e49f934...`，healthy，restart=0。
- 正式 live：container `78faa81d479d8c5e...`，image `sha256:7ec820b71fa94205...`，healthy，restart=0，当前 `UVICORN_WORKERS=4`。
- 两套 DB/Redis 位于不同 Compose project/network；连接变量已设置但未打印敏感值。
- 结论：目标正确，正式 live 未被修改；C0 complete，进入 C1。
- 下一步：只在隔离 Compose 增加可逆 worker override，保存 resolved config 和回退命令后重建隔离 WebUI。

### C1 4-worker 启用与 worker PID 覆盖完成

- 创建并使用临时 override `compose.webui-4-workers-acceptance-20260724.yaml`，只覆盖隔离 WebUI `UVICORN_WORKERS=4`。
- 隔离 WebUI 重建后：container `fa8da867458d49d9...`，原镜像 ID 不变，healthy，restart=0；runtime 与正式 live 未重建。
- 4 个 worker child 已由 `docker top`/容器 `/proc` 读到；32 个保持连接的真实 `/health` 请求返回 200，并由 socket inode 映射到 4 个 worker：`11:1,12:2,13:4,14:25`。
- 冷启动观察：约 3 分钟后 health/ready/health-db 全部 200；启动日志证明一次性依赖/工具/terminal 预热由一个 worker 执行，其他 worker 跳过。
- C1 complete，下一步是认证 API 下的 config/model/function/tool/module/content cache invalidation。

### C2 跨 worker 缓存失效完成

- 时间：`2026-07-24 21:45-21:55 Asia/Shanghai`。
- 真实保持连接探针覆盖容器 worker PID `11,12,13,14`；探针为 4 条并行 keepalive 连接，避免串行请求触发 Uvicorn keepalive timeout。
- Config：临时切换 `ENABLE_BASE_MODELS_CACHE` 后四 worker 均读到 `true`，随后恢复原值 `false`；`ENABLE_DIRECT_CONNECTIONS` 始终为 `true`。
- Function/model：临时 function 的 valves schema `v1 -> v2` 四 worker 一致；创建后 `/api/models` 四 worker 均可见，删除后四 worker 均不可见。
- Tool：临时 tool 的 valves schema `t1 -> t2` 四 worker 一致，删除后四 worker 的 valves spec 均为 404。
- Redis：version namespace key count `114`；已观察 `config/functions/models/tools` 四个 namespace。
- 清理：临时 function/tool DB 行为空，config 恢复，WebUI/runtime healthy，restart=0，镜像未变。
- 结论：C2 complete；已证明版本化/Redis invalidation 对 config、function、model、tool 及 module/content cache 生效，但 Agent run-scoped callable registry 仍需单独验收。

### C3 启动单例完成

- 4 个 `Started server process`（worker `11,12,13,14`）；依赖安装 1 次、其余 3 worker skip。
- startup singleton skip 3 次；tool server 初始化 1 次；terminal server 初始化 1 次；未观察 respawn loop。
- runtime 仍为单 worker，健康且 restart=0；精确时间窗日志没有 duplicate scheduler/reconcile/cleanup 或 runtime finalization ReadTimeout。
- 冷启动从 recreate 到 healthy 约 3 分钟，原因是一次性 tool/terminal prewarm；该时延是 live 4-worker 风险，但不是启动锁死。

### C4 Agent/SSE 当前失败与定位

- 当前 4-worker 运行 native phase 到达了真实 `commentary -> tool.requested -> tool.completed -> commentary -> tool.requested`，随后 `run.failed`。
- 运行 ID：`094453f0-3230-433f-8708-8895ac30bb54`；审计文件：`/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-native-phase-20260724-220927.json`。
- WebUI 精确日志显示 callback `POST /api/agent/service/runs/.../tool-call` 返回 403；runtime 精确日志为 `tool_not_allowed: Tool is not available for this run`，随后 `ToolOutcomeIndeterminate`。
- DB snapshot 已保存本地 Tool 的 opaque IDs，但 `get_agent_tool_authority()` 的 rebuild 只覆盖 builtin/terminal/external，不覆盖 snapshot type `openwebui`；启动 run 的 worker 内存 registry 不会跨 worker 共享。
- 另一个 2-delta fixture 在当前配置 `CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE=7` 下合并为 1 个 delta，属于探针片段数不足造成的配置预期，不作为代码缺陷；需用真实 native/final 流验证多 delta。

### C6 失败测试、最小修复与回归

- RED：新增 `test_rebuild_agent_tool_registry_rebuilds_openwebui_tools`，在修复前按预期因 503 registry rebuild failed 失败。
- GREEN：新增 `_openwebui_tool_source_id_from_snapshot()` 和 `_rebuild_openwebui_tools()`，复用现有 `get_tools()` 权限检查、DB 内容和模块加载；不引入 fallback registry 或跨进程内存共享。
- 回归：`test_agent_service_rebuild.py` + `test_tool_authority.py`：`39 passed, 18 warnings`；`test_cache_invalidation.py` + `test_startup_singleton.py`：`10 passed, 1 warning`。
- 下一步：提交本地修复，构建新的隔离 WebUI 镜像，只重建隔离 WebUI，重跑 native/final/cancel/恢复和并发验收。

### C4/C6 第二类跨 worker 缺陷：stale model cache miss

- 修复镜像 `open-webui:agentmode-v0102-b1a2ac825-slim` 部署后，local Tool registry 的 403 已消失；一次 native phase 完整事件顺序为 `run.running -> commentary -> tool.requested/completed -> commentary -> tool.requested/completed -> final.started -> 5 final.delta -> run.completed`。
- 同一栈的第二次完整运行在第二个 model call 落到另一 worker 时失败：`model_not_allowed: Model is not available for this run: bifrostapi.Cliproxy/gpt-5.5`。该 worker 的 `app.state.MODELS` 非空但过期，原实现仅在整个 cache 为空时调用 model_loader。
- RED：新增 `test_model_authority_refreshes_nonempty_stale_model_cache_on_miss`，修复前因 `ModelNotAllowed` 失败。
- GREEN：`_resolve_authorized_model()` 在非空 cache miss 时重新调用一次 model_loader 并重读 `app.state.MODELS`；不改变正常命中路径，也不增加无界重试。
- model authority 回归：`27 passed, 7 warnings`。
- 真实 Bifrost 记录中 provider 实际为 `Cliproxy`、model `gpt-5.5`；前一次 native 探针只因 harness 使用 `openai` provider 过滤了 Bifrost 关联，不能把该 harness 失败误报为 Agent 失败。
- 下一步：构建基于已验收镜像的最小 overlay，只覆盖两份后端文件；隔离重建后重复 native 至少两次，确认 Tool 和 model 两类跨 worker miss 均稳定。

### Checkpoint 记录格式

每个 checkpoint 追加：命令/时间、结果、证据文件、决策、下一步和回退状态。

## C4 修复后 Agent/SSE、取消与刷新恢复（2026-07-24 23:42-23:47 Asia/Shanghai）

- 第二个最小 overlay 镜像：`open-webui:agentmode-v0102-f2ab0434d-overlay`，image ID `sha256:851d543359ce53717ffe9ee597b321dae62998415ff3b8722af38516da8a558b`；只覆盖 `agent_service.py` 和 `model_authority.py`，隔离 WebUI 为 4 workers，runtime 未重建。
- Native run 1：run `1b5269d5-6477-470c-8863-cb3c844a4442`，审计 `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-native-phase-20260724-234229.json`，Bifrost request `994ace5d-3ac0-432a-a97e-9ebd0a3bc36b`。
- Native run 2：run `de4dc315-5fe1-46ee-95e7-218e396dca9d`，审计 `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-native-phase-20260724-234343.json`，Bifrost request `88d72c12-f8d2-41ef-b78d-cfe6b0161f59`。
- 两次都精确得到：`run.running -> commentary -> tool.requested -> tool.completed -> commentary -> tool.requested -> tool.completed -> final.started -> final.delta x5 -> run.completed`；Bifrost 中 `user/commentary/call/output/commentary/call/output` 为连续的 0..6 事件，`final_delta_count=5`，临时 Tool 已删除。
- 取消 run：`6189705e-a21f-483c-ac62-b7b69649d3aa`，审计 `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-cancellation-20260724-234514.json`；最终事件为 `run.cancelled`，runtime state `cancelled`，无 error，临时 Tool 已删除。
- 刷新恢复探针使用 run `1b5269d5-6477-470c-8863-cb3c844a4442`，连续 5 轮每个 worker 均读到 14 个事件、5 个 final delta，`consistent=true`；证明刷新后跨 worker 恢复读回一致。
- approval/user-input 没有在本轮 live 环境中执行确定性流程；现有代码和单测支持不等同于真实验收，最终结论保留此缺口。
- 旧 local-final-stream 两片段合并为一个 delta 是 `CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE=7` 与 fixture 片段数不匹配，不作为代码缺陷或验收失败。

## C5 受控并发验收（2026-07-24 23:48:47-23:49:02 Asia/Shanghai）

- 探针：`pr7_four_worker_concurrency_probe.py`；请求窗口为 `2026-07-24T15:48:47Z` 至 `2026-07-24T15:49:02Z`，仅使用隔离栈和安全的已有资源读请求。
- batches：models `8/8`（concurrency 8，p50 2876.69 ms，p95 3874.91 ms，max 4457.99 ms）；knowledge list `4/4`（concurrency 4，p50 225.12 ms）；knowledge search `4/4`（p50 86.51 ms）；files list `4/4`（p50 1049.09 ms）；files count `4/4`（p50 25.32 ms）；chat SSE `2/2`（concurrency 2，p50 2941.08 ms，max 3214.23 ms，均收到 `sse_done`，每个 6 条 SSE data lines）。
- 总计 26 个非破坏 API 请求和 2 个 SSE 请求，HTTP 错误率 0%，没有 `ReadTimeout`、Traceback 或 respawn loop；结束时 established WebUI connections 为 0。
- docker stats：开始 CPU 15.81%、内存 1.646 GiB/31.3 GiB，结束 CPU 14.34%、内存 1.774 GiB/31.3 GiB，BlockIO 约 77.8 kB/164 MB。
- 精确时间窗内有 2 条相同 `frontend language` Redis `DataError` warning，源于探针未提供前端语言，不影响请求状态；无对应 HTTP 失败。

## C7 恢复与最终锚点（2026-07-24 23:54-23:56 Asia/Shanghai）

- 使用原始合并 Compose（`compose.yaml`、`compose.webui-rebuild-eaff69b0d317.yaml`、`compose.webui-eaff69-no-migrations.yaml`、`compose.webui-4a4e43e206.yaml`、`compose.agent-runtime-742f686182.yaml`）仅重建隔离 `open-webui-pr7`；没有重建 runtime、DB、Redis 或正式 live。
- 恢复后隔离 WebUI：container `1cd01a28b9d785ee11c6580308bcfc22f27306c73f42dd75af1939ed9f0e109c`，image `sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4`，healthy，`UVICORN_WORKERS=1`，restart=0，started `2026-07-24T15:54:21.075863088Z`。
- 隔离 runtime 恢复核对：container `739472bd32748c196b44a643c352311788ff32ed13d1e2a9a5ab3a225f7f03e3`，image `sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9`，healthy，`UVICORN_WORKERS=1`，restart=0，started 未变。
- resolved config 恢复为原始 WebUI image `open-webui:agentmode-v0102-4a4e43e206-slim`、runtime image `open-webui-pr7-agentscope-runtime:742f686182-true-final-stream`、两者 `UVICORN_WORKERS=1`；临时两个 4-worker override 已删除。
- 正式 live 后核对仍为 container `78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e`，image `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`，healthy，`UVICORN_WORKERS=4`，restart=0，started `2026-07-07T03:53:51.178582025Z`，与 preflight 完全一致。
- 回退状态：完成；远端隔离临时 override 已清理，正式 live 未执行写操作、重启、重建或切换。

## 2026-07-25 C8 发布门槛续验启动

- 当前本地 truth surface：`/Users/liusihang/openwebui/.worktrees/pr7-live-compatible-20260722`，branch `codex/pr7-live-compatible-20260722`，HEAD `b55669d9a2d500aa66918c7144aa9d78eccdc43e`，工作树干净。
- 已重新读取上一轮 handoff；确认唯一明确缺口是 approval/user-input 尚未在修复后的真实 4-worker 栈执行。
- 历史证据仅用于定位探针：approval 的可靠成功签名是 destructive tool 请求后进入 `approval.requested` / `waiting_approval`，而 user-input 必须走 runtime-native `request_user_input` 与 `user_input.*` 终态；本轮仍需按当前隔离栈重新验证。
- 下一步：只读核对远端当前锚点和可复用验收脚本，再准备可逆 4-worker override。

### C8 当前远端真值

- 隔离 WebUI 仍为 container `1cd01a28...`、image `sha256:fd6145b...`、healthy、restart=0、`UVICORN_WORKERS=1`；runtime 仍为 container `739472bd...`、image `sha256:f7396ba...`、healthy、restart=0。
- 正式 live 仍为 container `78faa81d...`、image `sha256:7ec820b...`、healthy、restart=0、`UVICORN_WORKERS=4`，与上一轮最终锚点一致。
- 修复 overlay `open-webui:agentmode-v0102-f2ab0434d-overlay` 仍存在，image ID `sha256:851d543359ce53717ffe9ee597b321dae62998415ff3b8722af38516da8a558b`，build version `b1a2ac8252-multiworker-overlay`。
- 本地当前实现同时包含 approval 与 runtime-native user-input 协调器、状态与事件类型；下一步需定位当前 API/探针契约并在 overlay 上做真实生命周期验收。

## C9 第一轮真实 4-worker 交互验收

- 时间窗：`2026-07-25 00:24-00:26 Asia/Shanghai`；审计文件：`/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-four-worker-interactions-20260725-002416.json`。
- 探针将 4 条 keep-alive 连接按容器 worker PID 固定到 `11,12,13,14`，每个等待态和终态都由四 worker 并行读回一致；临时 Tool 只模拟写操作、不写文件，结束后已删除。
- Approval approved：run `8043f33c-6fb7-4f12-9ac9-e295cf651b0d`；worker 11 启动、13 批准、14 做重复决定；事件为 `run.running -> text.delta -> tool.requested -> approval.requested -> approval.completed -> tool.completed -> final.started -> final.delta -> run.completed`，重复决定为 `historical_completed`。
- Approval rejected：run `3257cb24-a563-4b59-b46e-76f0687a7306`；worker 12 启动、14 拒绝、11 重放；终态 `approval.completed -> run.failed`，没有 `tool.completed` 或 final，重复决定为 `historical_completed`。
- User-input accepted：run `620c2cbf-9c89-4221-afcc-85fb3b60d740`；worker 13 启动、11 提交回答、12 重放；事件包含 `user_input.requested -> user_input.completed -> commentary -> tool.requested/completed -> final -> run.completed`，最终答案包含精确回答，重复决定为 `historical_completed`。
- User-input cancelled：run `716dc0a3-5d29-4adf-9e44-24128bd11763`；worker 14 启动、12 取消、13 重放；事件为 `user_input.requested -> user_input.cancelled -> final -> run.completed`，没有 run/tool failure，重复决定为 `historical_completed`。
- 第一轮结果 `ok=true`，总耗时 113.779 秒。下一步：在 waiting 状态关闭原连接，重新绑定四个 worker 后完成批准/回答，以证明刷新恢复；随后按上述四个 run ID 精确核对 DB 与 runtime/WebUI 日志。

### Rejection 语义追踪与探针修正

- 精确 DB 核对显示第一轮 rejected run 的受保护 Tool 没有执行，decision execution `status=succeeded`、`attempt_count=1`、`backend_committed=true`；但 run error 为 `runtime_finalization_failed / empty_model_response`，并出现第二次 model call。
- 根因不在 decision dispatcher：当前 durable runtime 的明确设计是把 rejected approval 注入为 `ToolResultState.DENIED`，设置 `continuation_pending=true`，再让模型生成拒绝后的用户答复。初版探针却要求“拒绝后不要 final”，导致模型在恢复轮返回空公共响应。
- 初版模型还选择了系统 terminal 的 `write_file`，而非同名临时 Tool；批准 case 在 terminal 中写入了一个 28-byte 临时文件。探针已改为唯一方法名 `protected_release_action(operation='write')`，由参数触发审批分类且实现完全无副作用，避免同名路由歧义。
- 修正后的 rejection 验收标准：`approval.completed -> final -> run.completed`，无 `tool.completed`、无 `run.failed`，最终答案包含 marker 和 `rejected`。需重新实跑确认假设。

### 第二轮探针连接诊断

- Approval approved 修正版 run `235e80d9-83dd-4649-80ac-185bacffc741` 已通过：无副作用临时 Tool、生效的审批、tool execution、final 和 run.completed 均正确。
- 该 run 在 `waiting_approval` 时关闭原四连接并重新绑定四个新 local ports；新连接在 PID `11,12,13,14` 均读回 `waiting_approval` 和相同 4 个事件，随后从另一 worker 批准并完成，证明审批等待态可刷新恢复。
- 第一个 case 后探针某 keep-alive 在读下一响应头时被服务端关闭；因异常缺少 method/path 标签，不能定位具体请求。临时 Tool `pr7_interaction_gate_e72ef9adf68a` 已通过新短连接删除，DB row count=0。
- 探针已改为每个 case 开始重新绑定四连接，并在连接异常中记录 method/path/PID/local port；将重新执行四个 case。

### 第三轮交互与清理状态

- 修正探针完整通过，审计 `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-four-worker-interactions-20260725-003503.json`；四个 case 全部 `completed`、error=null，decision executions 均 `succeeded`、attempt_count=1、backend_committed=true。
- Approval approved 和 user-input accepted 均在 waiting 状态关闭旧连接并重新绑定四个新 local ports；四 worker 分别一致恢复 `waiting_approval` / `waiting_user_input` 后，由另一 PID 提交决定并完成。
- 修正版 rejection run `b2e779d9-0bfa-414f-a001-c7be9edb1a9a` 正常产生 `approval.completed -> final -> run.completed`，无 `tool.completed`、无 run error，确认初版异常是探针指令问题而非 durable rejection 缺陷。
- 第一次遗留文件清理 run `5075cd9f-486c-47d2-b9ca-4da922307d58` 明确返回当前 terminal 没有 `delete_file`；其 Tool snapshot 显示 `run_command` 可用。改为精确 `rm -- /tmp/APPROVAL-APPROVED-4d789291d3.txt` 后重新清理。

### C9 完成证据

- 权威交互审计：`/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-four-worker-interactions-20260725-003503.json`，`ok=true`、worker PIDs `11,12,13,14`、临时 Tool 已删除。
- 最终四个 run：approved `c14ac229-7231-485d-bfb3-0c86b0374093`；rejected `b2e779d9-0bfa-414f-a001-c7be9edb1a9a`；user-input accepted `ebbe07fc-1e2f-402e-bcdc-745a8d7fba29`；user-input cancelled `ced89943-2dad-4039-a788-bb47b8cdba5a`。
- DB：四 run 均 `state=completed`、`error=null`、pending_user_input_id=null；四条 decision execution 均 `status=succeeded`、attempt_count=1、backend_committed=true；临时 Tool row count=0。
- 精确 `2026-07-25 00:35-00:37` 窗口的 WebUI/runtime 日志没有 ReadTimeout、Traceback、ERROR、Exception、runtime_finalization_failed 或 empty_model_response。
- 初版审批误写文件已通过 run `17d73a06-18a3-4549-b5a5-9bccf23c4eef` 精确清理：terminal `run_command` 执行 `rm -- /tmp/APPROVAL-APPROVED-4d789291d3.txt`，exit_code=0、status=done；审计 `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/e2e-agentmode-terminal-cleanup-20260725-010219.json`，trigger Tool 已删除。
- C9 complete。下一步：发布级测试、提交当前证据、从该干净 commit 构建完整 external-services slim 候选镜像；overlay 不能作为最终发布制品。
