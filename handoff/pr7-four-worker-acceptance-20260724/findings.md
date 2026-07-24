# PR7 四 Worker 验收发现

## 当前已确认

- 当前 Codex 工作区 `/Users/liusihang/.codex/worktrees/d790/openwebui` 是 detached HEAD，但 HEAD `ccde1d134de3e82101461d6c73c6d11d7ce66498` 与 `codex/pr7-live-compatible-20260722` 完全一致。
- 指定分支 worktree `/Users/liusihang/openwebui/.worktrees/pr7-live-compatible-20260722` 当前 branch 正确、起始 HEAD 相同；截至初始化时未见 dirty files。
- 以上仅为本地静态真值，不能替代远端隔离栈运行验收。

## C0 远端 preflight（2026-07-24 21:30 Asia/Shanghai）

| Surface | 隔离 PR7 | 正式 live |
|---|---|---|
| 容器 | `open-webui-pr7`=`df1ba2b48e5397bd...`；runtime=`739472bd32748c19...` | `open-webui`=`78faa81d479d8c5e...` |
| 镜像 ID | WebUI `sha256:fd6145b041f28269...`；runtime `sha256:f7396ba23e49f934...` | `sha256:7ec820b71fa94205...` |
| 状态 | 两个均 running/healthy，restart=0 | running/healthy，restart=0 |
| 启动时间 | WebUI `2026-07-21T21:12:02Z`；runtime `2026-07-21T22:06:22Z` | `2026-07-07T03:53:51Z` |
| WebUI workers | `UVICORN_WORKERS=1` | `UVICORN_WORKERS=4` |
| DB/Redis 边界 | compose project `openwebui-pr7`，network `openwebui-pr7_default`，services `db`/`redis` | compose project `openwebui-migration`，network `openwebui-migration_default`，services `db`/`redis` |
| Compose root | `/home/aiserver/staging/openwebui-pr7-eea11194ed-test` | `/srv/openwebui-migration` |
| WebUI config files | `compose.yaml` + `compose.webui-rebuild-eaff69b0d317.yaml` + `compose.webui-eaff69-no-migrations.yaml` + `compose.webui-4a4e43e206.yaml` | `/srv/openwebui-migration/compose.yaml` |
| DB migrations | `ENABLE_DB_MIGRATIONS=false` | `ENABLE_DB_MIGRATIONS=true` |

结论：目标边界正确，正式 live 只完成了只读记录；隔离栈尚未变更。当前缺口被实时确认仍是隔离 WebUI 的 4-worker 运行验收。

## C1 真实 4-worker 启动与 PID 覆盖

- 临时 override：`/home/aiserver/staging/openwebui-pr7-eea11194ed-test/compose.webui-4-workers-acceptance-20260724.yaml`，只给 `open-webui-pr7` 设置 `UVICORN_WORKERS=4`。
- 重建前容器：`df1ba2b48e5397bd...`；重建后容器：`fa8da867458d49d9...`；镜像 ID 仍为 `sha256:fd6145b041f28269...`，未 pull/build。
- runtime 容器 ID、镜像 ID、started 时间未变；正式 live 容器 ID、镜像 ID、started 时间未变。
- 4-worker 启动后 host/container 进程映射为 worker PID `11,12,13,14`（容器 PID；对应 host PID 由 `docker top` 读回）。
- 32 条保持连接的真实 `GET /health` 请求均返回 HTTP 200；按 worker 进程 fd/socket inode 统计：`11:1, 12:2, 13:4, 14:25`，`distinct_workers_with_requests=4`。
- 启动初始约 3 分钟 health 为 starting/unhealthy，原因链：4 个 worker 完成 pgvector 初始化后，由持有 `lifespan-startup-tasks` 文件锁的单一 worker 继续执行一次性 tool/terminal 预热，其余 worker 记录“already running ... skipping”；预热完成后 health/ready/health-db 全部 HTTP 200。
- 启动日志精确证据：4 个 `Started server process`；3 条 startup singleton skip；1 条 `Installing external dependencies...`；1 次 `Initialized 1 tool server(s)` 和 1 次 `Initialized 0 terminal server(s)`；无 respawn loop。
- 该启动延迟必须纳入 live 4-worker 决策，不应被误报成挂死；但当前尚未证明 cold-start 延迟是否可接受。

## 低风险回退准备

- 原始隔离 WebUI worker 数：`1`。
- 原始隔离 WebUI/runtime 容器 ID、镜像 ID、Compose config files 和 restart=0 已记录在本节。
- 默认回退：撤销临时 override，使用 Docker Compose 原始合并配置重新创建 `open-webui-pr7`；runtime 不应因 WebUI worker 验收而被重建，除非健康恢复需要。
- 正式 live 未执行 compose、restart、rebuild、switch、exec 写操作。

## 敏感信息处理

- 远端 env preflight 只确认连接变量已设置和连接边界，不在 handoff 中保存 URL 值、token、cookie、密钥或密码。
- 远端一次命令输出曾包含一个运行时服务 token；该值未写入任何文件或后续回复，后续命令将避免打印完整 env。

## 待验证

- 隔离/正式容器实时 ID、镜像、健康、环境、重启计数。
- 隔离 compose 的 worker、数据库和 Redis 连接边界。
- 多 PID 覆盖和跨 worker 缓存失效。
- 启动单例计数与 Agent/SSE/并发真实性。

## C2/C3/C4/C6 追加发现（2026-07-24）

### Cache matrix

真实 4-worker cache probe 已完成。所有结论都来自保持连接映射到容器 worker PID `11,12,13,14` 的 API 读回，不用单元测试替代：

| 范围 | 变更 | 四 worker 结果 |
|---|---|---|
| config | `ENABLE_BASE_MODELS_CACHE` 临时更新并恢复 | `true` 一致；恢复为原始 `false` |
| function valves/module content | schema `v1 -> v2` | 四 worker 均为 `v2` |
| function model cache | 临时 function create/delete | create 后四 worker 可见；delete 后四 worker 不可见 |
| tool valves/module content | schema `t1 -> t2` | 四 worker 均为 `t2` |
| tool delete | 临时 tool 删除 | 四 worker 均返回 404 |
| Redis versioning | namespace version keys | count `114`；有 `config/functions/models/tools` |

临时 DB 行已清理；探针最终恢复 config；隔离 WebUI/runtime 均 healthy、restart=0，镜像 ID 未变。

### Agent run-scoped registry defect

当前 4-worker native phase 的真实失败不是 runtime 自身随机超时，而是 WebUI callback worker 的进程内 registry 缺失：

```text
POST /api/agent/service/runs/<run_id>/tool-call -> 403
tool_not_allowed: Tool is not available for this run: tool:<local-tool>:<function>
```

`main.py` 在 run 创建时把 registry 放入 `request.app.state.AGENT_TOOL_REGISTRIES[run_id]`。`agent_service.py` 的 snapshot rebuild 已能重建 builtin、terminal、external，但本地 DB Tool 的 snapshot type 为 `openwebui`，原路径没有重建分支，所以另一 worker 永远看不到该 callable。runtime 随后报 `ToolOutcomeIndeterminate`，这正是跨 worker 运行态缺陷。

修复已按 TDD 进行：先新增失败回归，再增加本地 Tool snapshot 解析和 `get_tools()` 重建路径；没有添加重试、全局 fallback 或共享可变内存。

### Final delta 探针解释

隔离实时配置为 `CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE=7`。只产生 2 个 fixture delta 的 local-final-stream 探针被合并成 1 个 final.delta；旧单 worker 结果不能直接当作当前配置真值。后续 native/final 探针必须产生超过 chunk threshold 的真实流，或明确设置每请求 chunk 参数，才能验收“多 delta”。

### 第二类模型缓存缺陷

在 Tool registry 修复镜像上，第一次 native phase 已完成两个工具回调和 5 个 final delta；第二次运行在第二个 model call 触发：

```text
OpenWebUI model-call -> 403
model_not_allowed: Model is not available for this run: bifrostapi.Cliproxy/gpt-5.5
```

根因是 `AgentModelAuthority._resolve_authorized_model()` 只在 `app.state.MODELS` 为空时刷新。多 worker 下非空但过期的进程内模型字典会让 callback worker 拒绝一个已在其他 worker 可用的模型。修复是 miss-only 一次刷新再重读；没有引入兜底选择或无限重试。该修复已通过 27 个 model authority 测试，待隔离 overlay 重新验收。

## 证据约定

- 不记录任何 token、cookie、密码、完整 Authorization header 或敏感请求体。
- Bifrost 日志只按明确 request/session/runtime_session_id 或精确时间窗查询，不做广泛全日志扫描。
- 所有远端证据记录命令、时间、退出码和脱敏输出；大输出落文件，handoff 只保留摘要与路径。

## 最终四 Worker 验收矩阵（2026-07-24）

| 验收项 | 结果 | 真实证据与边界 |
|---|---|---|
| 多 PID 覆盖 | PASS | 32 条保持连接 `/health` 请求映射到 worker `11,12,13,14`，计数 `1,2,4,25`；另有 96 连接 sanity probe 覆盖四 PID |
| config/function/model/tool/module/content cache | PASS | config、function valves `v1->v2`、tool valves `t1->t2`、function create/delete、tool delete 均由四 worker API 读回一致；Redis version namespace 包含 `config/functions/models/tools` |
| 启动单例 | PASS | 4 server process；依赖安装 1 次 + 3 skip；startup singleton skip 3；tool init 1；terminal init 1；scheduler worker 1；无 respawn loop |
| 原生 Agent 时序与多 delta SSE | PASS | 两次真实 native run 均为 commentary/tool/output/commentary/tool/output/final.started/final.delta×5/completed；Bifrost 精确事件索引 0..6 |
| 取消 | PASS | run `6189705e-a21f-483c-ac62-b7b69649d3aa` 最终 `run.cancelled`，runtime `cancelled`，无 error |
| 刷新恢复 | PASS | 5 轮刷新读回；四 worker 每轮 14 events、5 final deltas，均 `consistent=true` |
| approval/user-input | NOT LIVE-VERIFIED | 本轮没有可重复的 live approval/user-input 流程；不能用已有单测代替该项，保留为部署前缺口 |
| 受控并发 | PASS | 26 个非破坏 API + 2 个 SSE；各 batch 0 HTTP failure，SSE 均 done；资源与精确时间窗异常已记录 |
| 隔离栈恢复 | PASS | 原 WebUI image/1 worker 恢复，runtime/DB/Redis 未重建，临时 override 删除，均 healthy/restart=0 |
| 正式 live 不变 | PASS | live container/image/started/health/restart/worker 前后完全一致；只读核对 |

## 根因、修复与提交

1. `b1a2ac82529f027cded8c82e1d9be620af491e23` — `fix(agent): rebuild local tool registry across workers`。根因是 run snapshot 中 `openwebui` 类型的本地 DB Tool 没有纳入 callback worker 的 registry rebuild；修复重建路径复用 `get_tools()` 的 DB、权限和模块加载逻辑，并由失败测试先行证明。
2. `f2ab0434d` — `fix(agent): refresh stale model cache on worker miss`。根因是非空但过期的进程内 `MODELS` cache miss 不触发 loader；修复只在 miss 时 refresh 一次，再重新授权，不引入无限重试或共享可变 fallback。
3. `d8b947b9e` — `docs(acceptance): record four-worker findings`；本轮最终 handoff 会在本次验收结束后再提交。

最终聚焦回归：

```text
76 passed, 18 warnings
```

覆盖 `test_model_authority.py`、`test_agent_service_rebuild.py`、`test_tool_authority.py`、`test_cache_invalidation.py`、`test_startup_singleton.py`。warnings 为既有测试环境/弃用提示，无新增失败。

## 最终决策

- 隔离验收结论：修复后的 PR7 代码在真实 4-worker 栈上已通过核心缓存、启动单例、Agent/SSE、取消、刷新恢复和受控并发验收，具备继续做 staged promotion 的技术条件。
- 正式 live 当前不应直接切到修复前 PR7 镜像：本次发现的两个跨 worker 缺陷在修复前都能被真实运行触发；因此“未应用两项修复的立即升级”是 **NO-GO**。
- 应用 `b1a2ac825` 与 `f2ab0434d` 后，结合正式 live 的常规灰度/回滚窗口，可进入下一步 promotion；本次没有执行 live 切换。由于 approval/user-input 尚未 live-verified，若该能力是发布硬门槛，则其完成前保持 **NO-GO**。
- 冷启动约 3 分钟，必须在 promotion 计划中保留 readiness/回滚窗口；这不是本次 4-worker 运行失败，但属于运维风险。
