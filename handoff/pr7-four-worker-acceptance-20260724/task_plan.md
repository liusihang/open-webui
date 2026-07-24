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
