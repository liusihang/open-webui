# 2026-04-13 Review: onlyoffice phase0-1 commit 9eb0d843c

## Goal
- 对提交 `9eb0d843c` 做全面审查，重点关注功能正确性、安全性、兼容性、回退路径与可维护性。

## Checkpoints
- [x] C1: 定位目标提交与影响文件。
- [x] C2: 审查后端路由/鉴权/token/回调处理逻辑。
- [x] C3: 审查前端 API/Viewer/入口接入与 fallback 行为。
- [x] C4: 审查配置与部署变更（config/main/compose/env）。
- [x] C5: 汇总风险分级、定位行号、给出修复建议。

## Progress Log
- 2026-04-13 13:xx: 创建审查 handoff，确认 worktree 分支 `codex/onlyoffice-phase0-1` 与目标提交 `9eb0d843c`，拿到影响文件列表。
- 2026-04-13 13:xx: 启动 3 个子代理并行审查：后端、前端、配置/部署；主代理做代码级复核与结论去重。
- 2026-04-13 13:xx: 完成后端复核，确认 `session_proxy_token` 复用风险、callback 文件存在性探针、terminal document key 缓存陈旧风险。
- 2026-04-13 13:xx: 完成前端复核，确认 Docs API script loader 失败后不可恢复、OnlyOfficeViewer 异步竞态、terminal_server_id 回退值不稳定。
- 2026-04-13 13:xx: 完成配置复核，确认 compose 默认拓扑下 DS 回源 URL 不可靠（缺 `WEBUI_URL` 明确指引）和文档口径不一致问题。

## Final Findings (deduped)
1. P1: `session_proxy_token` 为通用 JWT，可被外层 token payload 泄露后复用于其他后端接口。
2. P1: Docs API script 首次加载失败后缓存 rejected Promise，Retry 实际不可恢复。
3. P1: OnlyOfficeViewer 缺少初始化并发/过期请求保护，快速切换文件存在竞态覆盖。
4. P1: compose 默认场景中 Document Server 回源 URL 可能不可达（需明确 `WEBUI_URL`/internal base URL）。
5. P2: callback/file 路由先查文件再鉴权，存在 `401/404` 区分导致的存在性探针。
6. P2: terminal 文档 key 不含版本信号，可能出现缓存陈旧内容。
7. P2: FileNav 的 terminal_server_id 兜底为 selectedId/url，可能稳定触发 session 失败后 fallback。
8. P2: handoff 文档中 terminal auth_type 支持范围与 compose 启停描述口径不一致。

## Notes
- 本轮为静态代码审查，未执行端到端浏览器回归。
- 子代理结果已人工复核后纳入上述 deduped 结论。
