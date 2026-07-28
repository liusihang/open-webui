# PR7 四 Worker 真实验收计划

## 目标

在不触碰正式 live 的前提下，对 `aiserver:/home/aiserver/staging/openwebui-pr7-eea11194ed-test` 的隔离 PR7 栈执行真实 4-worker 验收，证明请求命中多个 worker PID，并验证缓存失效、启动单例、Agent/SSE 和受控并发；完成后恢复隔离栈原始配置，给出 live 4-worker go/no-go。

## Truth surfaces

- 本地代码：`/Users/liusihang/openwebui/.worktrees/pr7-live-compatible-20260722`，branch `codex/pr7-live-compatible-20260722`，起始 HEAD 需记录。
- 隔离远端：`aiserver:/home/aiserver/staging/openwebui-pr7-eea11194ed-test`。
- 隔离容器：`open-webui-pr7`、`openwebui-pr7-agentscope-runtime`。
- 正式 live：`aiserver:/srv/openwebui-migration` 的 `open-webui`，只读记录前后状态。
- 验收证据：容器/镜像/健康/env/重启计数、worker PID、精确 API/SSE 响应、缓存探针、启动日志/计数、资源和错误指标。

## Checkpoints

| ID | Checkpoint | 状态 | 完成条件 |
|---|---|---|---|
| C0 | 本地与远端只读 preflight | completed | 分支、容器、镜像、健康、env、重启、compose、DB/Redis、正式 live 前状态均有证据 |
| C1 | 4-worker 可逆启用 | completed | 保存原配置与回退命令；仅隔离栈启用 4 workers |
| C2 | worker 覆盖与缓存失效 | completed | 多个 PID 命中；config/function/model/tool/module/content/version cache 跨 worker 一致 |
| C3 | 启动单例 | completed | 一次性依赖/工具/terminal 预热单例，无重复 scheduler/reconcile/respawn 证据 |
| C4 | Agent/SSE/控制流 | completed | 两次 native commentary/tool/output/final 流、5 个 final delta、取消、刷新恢复均通过；approval/user-input 未做 live 流程，作为明确遗留证据缺口记录 |
| C5 | 受控并发验收 | completed | 26 个非破坏 API 请求 + 2 个 SSE 并发完成；已记录并发度、延迟、CPU、内存、连接和精确时间窗异常 |
| C6 | 缺陷修复与回归（仅必要时） | completed | 两个跨 worker 缺陷均先 RED 后最小修复；最终聚焦回归 76 passed, 18 warnings；修复 overlay 已完成隔离真实验收 |
| C7 | 隔离栈恢复与最终结论 | completed | 隔离 WebUI 已恢复原镜像和 1 worker，临时 override 已删除；正式 live 前后锚点一致；结论见 findings |
| C8 | 发布门槛续验 preflight | completed | 当前分支、隔离栈、正式 live、修复镜像、协议/API 与回退路径均已重新读取 |
| C9 | 4-worker approval/user-input | completed | 四种生命周期、跨 PID 决定/幂等重放、waiting 全新连接恢复、DB decision execution 与精确错误窗均通过；临时资产已清理 |
| C10 | 发布级回归与完整候选复验 | completed | 完整 slim 镜像完成构建；发布级测试、4-worker 缓存、交互、原生流、取消和并发均在该镜像通过 |
| C11 | 生产快照迁移/回退演练 | completed | 完整生产 dump/files clone、f3→f8、fresh-f8 4-worker、Pipe 0.2.17、旧镜像-on-f8、forward/rollback/forward 与 API/日志均完成 |
| C12 | 切换包、最终恢复与发布决策 | completed | live 专用 compose/switch/rollback/DR 合约通过静态测试、远端真渲染、只读 preflight 和 fail-closed 守卫；隔离栈恢复，rehearsal 容器清理，正式 live 未变 |

## 回退原则

1. 所有改动只作用于隔离 compose/override 或等价运行参数，不改正式 live、生产数据库内容或用户数据。
2. 启用前保存实际 compose、env、容器 ID/镜像 ID、worker 数、挂载和连接配置。
3. 默认验收结束恢复原始 worker 数和配置；若服务异常先停止探针流量，再按保存的 compose/env 重新创建隔离容器。
4. 任何涉及代码的修改必须先有可复现失败证据，并只在本地任务分支提交，不 push。

## 停止条件

- 发现操作目标不是隔离栈；
- 无法证明数据库/Redis 连接仍指向隔离资源；
- 需要修改正式 live 或生产数据；
- 需要写入敏感 token/cookie/key 才能继续且无法安全脱敏；
- 关键失败连续三次且没有新的诊断信息。

## Errors encountered

| 时间 | 错误 | 尝试 | 处理 |
|---|---|---|---|
| 2026-07-24 | planning skill 的 `request_user_input` 在 Default mode 不可用 | 尝试调用工具确认继续 | 任务授权已在用户消息中明确，按既定范围继续，不重复调用 |
| 2026-07-25 | `docker image inspect` 使用了宿主不支持的 `hasPrefix` Go template 函数 | 读取 overlay 镜像 build env | 改用标准 `range` 输出后由 `rg` 精确筛选，已确认 image ID/build version |
| 2026-07-25 | 在隔离根目录执行 `find` 时触及 PostgreSQL/Redis 数据目录权限 | 搜索已有 approval/user-input 验收脚本 | 不提升权限、不扫描数据目录；后续限定已知 deploy/handoff 路径和文件名 |
| 2026-07-25 | 初版 rejection 探针要求模型“不要给 final”，导致 durable rejection 恢复后第二次模型调用返回空响应 | 精确 DB/event/operation 与 runtime 状态机追踪 | 代码与测试证明拒绝会注入 DENIED tool result 后继续；修正探针为拒绝后给出明确 final，并将审批 fixture 改为无副作用且无工具重名的方法 |
| 2026-07-25 | 代码检索最初误用 `services/agentscope-runtime/app.py` 等不存在路径 | 定位 runtime 实现 | 通过 `rg --files` 确认实际路径为 `services/agentscope-runtime/agentscope_runtime/*.py` |
| 2026-07-25 | 第二轮交互探针在第一个 case 完成后遇到 keep-alive 连接关闭，且初版异常缺少请求标签 | 跨四 worker 连续执行四个 case | 清理遗留 Tool；异常加入 method/path/PID/port；每个 case 开始重新绑定全新四连接，避免把正常 keep-alive 生命周期误判为产品错误 |
| 2026-07-25 | 首次遗留文件清理要求 `delete_file`，但当前 terminal envelope 不提供该工具 | 精确清理 `/tmp/APPROVAL-APPROVED-4d789291d3.txt` | 从该 run 的 `tool_access_snapshot` 确认可用 `run_command`；改为精确 `rm -- <path>`，仍走真实审批，不扫描目录 |
| 2026-07-25 | 第二次清理时模型选择了同一临时 Tool 的安全模拟方法而非 terminal `run_command` | 精确 `rm -- <path>` | 清理模式改用不具备文件能力的独立 trigger Tool，并把 run.completed 视为等待审批前的立即失败，避免无意义 300 秒等待 |
| 2026-07-25 | 完整镜像成功导入后 BuildKit local cache export 长时间停在 `preparing build cache`，目标目录仍 4 KiB | `cache-to type=local,mode=max` | 镜像内容已完成并导入；终止仅剩的 cache exporter，未提升 cache pointer；不重复构建，改以 image ID、build env、源码/镜像文件 hash 和隔离真实运行验收确认制品 |
| 2026-07-25 | 脱离 Compose 的 slim image import smoke 因缺少真实 pgvector DB 配置/连接而失败 | `docker run ... import agent_service` | 这是 external-services slim 的运行 profile 约束；不使用 dummy DB 作为通过证据，改在隔离 Compose 的真实 DB/Redis/env 上做 import、健康和功能验收 |
| 2026-07-25 | 完整候选首次缓存探针在首次并行 `/api/models` 上 `TimeoutError` | socket 与收敛窗口均为 20 秒 | 精确复测确认冷启动请求 36.238 秒后 HTTP 200、热请求低于 1.2 秒；把探针 HTTP timeout 提升为 120 秒、cache wait 提升为 180 秒，再以全新 fixture 重跑 |
| 2026-07-25 | 交互复验后第一次手工 DB 核对误用了不存在的复数表名 `agent_runs` / `agent_decision_executions` | 复用摘要里的概念名而未按 ORM 表名核对 | 从 `models/agent_runs.py` 确认真实表为 `agent_run` / `agent_run_decision_execution` 后重查，四 run 与四 execution 全部符合预期 |
| 2026-07-25 | rehearsal 预检假设远端安装了 `rsync`，链式命令在 `command -v rsync` 处退出 | 未先独立探测复制能力 | 不安装新依赖；确认同一 ext4 有 946 GiB 可用并支持 `cp --reflink=auto`，改用 coreutils 路径；错误同时记录到 `.learnings/ERRORS.md` |
| 2026-07-25 | 第一次 `docker run ... python - <<PY` 忘记 `-i`，容器 0 退出但没有执行 stdin 脚本 | 只看 exit code 会造成假通过 | 加 `-i` 后重跑，当前完整候选成功在正式 f3 DB 上只读导入 config，`agent.mode.enable` 默认与 DB 读取均正常，live anchor 未变 |
| 2026-07-25 | rehearsal runtime 裸 `docker run` 后服务正常监听 8000，但 image 本身没有 Healthcheck，脚本一直等待 `health=none` | 误以为 healthcheck 属于 image，实际由隔离 Compose 提供 | 中止仅该等待；为 rehearsal runtime 显式添加 localhost `/health` 探针和静态契约，再从 candidate 阶段重跑 |
| 2026-07-25 | rehearsal WebUI 同时接入隔离与正式网络后，通用 hostname `db` 解析到正式 DB `172.18.0.6`；无密码 URL 在认证前失败并触发 worker respawn | 两个网络都存在 `db`/`redis` DNS alias，连接串存在歧义 | 立即删除仅 rehearsal WebUI；正式 DB 未认证、未写入，live anchor 不变；连接串改用唯一容器名 `pr7-live-rehearsal-db` / `pr7-live-rehearsal-redis` 并加入静态契约 |
| 2026-07-25 | Bifrost 对比最初误用 role `postgres` 和 DB `openwebui` | 依据通用 PostgreSQL 默认值猜测 | 两次都在连接阶段失败、无 SQL 执行；改从 rehearsal 脚本和隔离 DB env 读取实际 role/DB 后完成只读比较 |
| 2026-07-25 | Pipe 精确备份用 `psql -At` 直接重定向，CLI 附加换行导致文件 md5 与 `md5(content)` 不同 | 升级前完整性断言 | 断言发生在停服务/UPDATE 前，无状态变化；改为 DB base64 编码、host 解码，精确得到旧 content hash |
| 2026-07-25 | lucen model ID 的 Bifrost 实际记录 provider 为 `Cliproxy`，探针按 `lucen` 过滤后业务已通过但日志 gate 失败 | 假设 model ID 第一段等于最终 provider 标签 | 只查询该 run 列出的 5 个精确 log ID，确认 fallback 标签；以实际 provider 重跑后严格 history/order PASS |
| 2026-07-25 | release 静态测试第一次被外层无 `set -e` 的组合命令掩盖，且 secret grep 忘记 here-string | 测试驱动脚本本身存在假绿风险 | 单独 `bash -x` 定位，修正 typo 与 stdin；之后在 `set -euo pipefail` 下独立通过，并完成远端 Compose 真渲染 |
| 2026-07-25 | frontend 首跑命中 Node 24，仓库 engines 要求 Node <=22 | 默认 PATH 指向错误 Node | 不改依赖，显式使用已有 Node 22.22.0 + pnpm 10.30.3，9 files / 133 tests 全过 |
| 2026-07-25 | 最终单 worker 进程核对先假设容器有 `ps`，随后又用多 worker 的 `multiprocessing.spawn` 判据 | 复用 4-worker 探针判据到单 worker uvicorn | 只读 `/proc` 确认单 worker 是 PID 1 直接 uvicorn；最终验证 env=1 且仅 1 个 uvicorn 进程，再执行 cleanup |

## 2026-07-25 续验边界

- 当前目标是补齐“可以发布并切换 live”的全部证据，不执行正式 live 切换本身。
- 上一轮唯一明确未通过门槛为 approval/user-input 的真实 4-worker 验收；代码/单测或旧运行记录不能代替本轮当前镜像的真实运行。
- 正式 live 继续保持只读；任何隔离重建前后都记录其 container/image/health/restart/started 锚点。

## 2026-07-25 正式切换拓扑审计

- 正式 `/srv/openwebui-migration/compose.yaml` 只有 WebUI、PostgreSQL、Redis、Bifrost、OnlyOffice；没有 AgentScope runtime 服务或状态卷，也没有 Agent runtime env。
- 正式 DB 当前 head `f3a4b5c6d7e8`、约 25.57 GB；完整候选/隔离 DB head 为 `f8a9b0c1d2e3`，runtime SQLite schema version 2。
- 因此“候选运行验收通过”仍不足以直接切换；必须完成生产快照上的 f3→f8 迁移演练、生产 secret/config/files 兼容、runtime 引入和旧镜像回退验证。
- rehearsal 只能读取正式 DB/数据目录并写入新的隔离路径/容器；不得在 `/srv/openwebui-migration` 写文件，不得执行正式 compose 或重建正式容器。
