# 2026-04-13 Fix Task: review findings for commit 9eb0d843c (exclude session_proxy_token issue)

## Goal
- 修复 review 中除“session_proxy_token 可被解码并复用为通用登录 JWT”外的所有问题。

## Scope
- In scope:
  - OnlyOfficeViewer 脚本失败重试不可恢复
  - OnlyOfficeViewer 初始化竞态
  - compose 默认拓扑下 DS 回源 URL 不稳定
  - callback/file 存在性探针
  - terminal 文档 key 缓存陈旧
  - terminal_server_id 前端兜底导致稳定失败
- Out of scope:
  - session_proxy_token 可复用问题（按用户要求暂不处理）

## Checkpoints
- [x] C1: 创建修复任务 handoff 与执行计划。
- [x] C2: 前端修复完成。
- [x] C3: 后端与部署修复完成。
- [x] C4: 验证通过并汇总结果。

## Changes
- Frontend:
  - `src/lib/components/common/OnlyOfficeViewer.svelte`
    - 修复 script loader 失败后 rejected Promise 缓存导致 Retry 失效；失败时删除缓存并移除失败 script 节点。
    - 增加初始化请求版本号（requestId）防竞态；旧请求不再覆盖新预览状态。
  - `src/lib/components/chat/FileNav.svelte`
    - 删除 `selectedId/url` 兜底，仅在拿到明确 `id/server_id` 时传递 `terminal_server_id` 给 OnlyOffice。
- Backend/Deploy:
  - `backend/open_webui/routers/onlyoffice.py`
    - 新增 `ONLYOFFICE_PUBLIC_BASE_URL` 优先解析逻辑，作为 document/callback URL 的基础地址。
    - `callback/file` 调整为先鉴权后查文件，并在 JWT 启用时统一 not-found 语义以降低存在性探针。
    - terminal 文档 key 引入会话信号 `session_signal`，降低陈旧缓存命中。
  - `backend/open_webui/config.py`
    - 新增 `ONLYOFFICE_PUBLIC_BASE_URL` 配置项。
  - `backend/open_webui/main.py`
    - 注入 `ONLYOFFICE_PUBLIC_BASE_URL` 到 app.state.config。
  - `.env.example` 与 `docker-compose.yaml`
    - 增加 `ONLYOFFICE_PUBLIC_BASE_URL` 示例与 compose 默认值（`http://open-webui:8080`）。

## Verification
- `python3 -m compileall -f backend/open_webui/routers/onlyoffice.py backend/open_webui/config.py backend/open_webui/main.py` 通过。
- `npx eslint src/lib/components/common/OnlyOfficeViewer.svelte` 通过。
- `npx eslint src/lib/components/common/OnlyOfficeViewer.svelte src/lib/components/chat/FileNav.svelte` 在 `FileNav.svelte` 报仓库既有 lint 问题（与本次改动无直接对应新增）。
- `git diff --check` 通过。

## Progress Log
- 2026-04-13: 新建修复任务 handoff，拆分前端/后端并行子代理执行。
- 2026-04-13: 子代理A完成前端三项修复（Retry 缓存、初始化竞态、terminal_server_id 兜底）。
- 2026-04-13: 子代理B完成后端与部署三项修复（public base URL、callback 探针、terminal key 版本信号）。
- 2026-04-13: 主代理复核 diff 与定向验证，确认本轮范围内目标已完成（按用户要求未处理 session_proxy_token 问题）。
- 2026-04-13: 用户要求针对 Finding（terminal_server_id 兜底）提交修复；准备仅提交 `FileNav.svelte` 相关改动与本 handoff 更新。
- 2026-04-13: 用户确认将剩余已审查改动一并提交；执行打包提交（不包含此前已提交的 c47b1a3b9 内容重复）。
- 2026-04-13: 依据用户“最终版本便于主线合并”要求，创建 `codex/onlyoffice-phase0-1-final`，将 `9eb0d843c + c47b1a3b9 + f6db051a7` squash 为单提交交付版本。
