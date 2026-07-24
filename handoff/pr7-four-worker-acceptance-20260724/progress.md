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

### Checkpoint 记录格式

每个 checkpoint 追加：命令/时间、结果、证据文件、决策、下一步和回退状态。
