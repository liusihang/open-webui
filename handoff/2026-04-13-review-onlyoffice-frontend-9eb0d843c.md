# 2026-04-13 Frontend Review Handoff (commit 9eb0d843c)

## Goal
- 对提交 `9eb0d843c` 的前端改动做 code review，仅覆盖以下文件：
  - `src/lib/apis/onlyoffice/index.ts`
  - `src/lib/components/common/OnlyOfficeViewer.svelte`
  - `src/lib/components/common/FileItemModal.svelte`
  - `src/lib/components/chat/FileNav.svelte`
  - `src/lib/components/chat/FileNav/FilePreview.svelte`
- 审查重点：bug / 行为回归 / 可用性与错误处理。

## Checkpoints
1. [done] 定位目标提交与目标文件范围。
2. [done] 提取五个文件在目标提交的完整内容与行号。
3. [done] 对照提交 diff，确认新增逻辑链路（OnlyOffice session、viewer、fallback、retry、terminal server_id）。
4. [done] 对关键路径进行失败场景推演：脚本加载失败、快速切换文件、terminal server_id 缺失/异常。
5. [done] 形成分级问题清单与修复建议（P0-P3）。

## Findings Snapshot
- 高优先级：
  - `OnlyOfficeViewer.svelte` 脚本加载失败后 Promise 缓存为 rejected，重试无效（需清理缓存条目）。
  - `OnlyOfficeViewer.svelte` 初始化链路缺少“过期请求防护”，快速切换文件时可能被旧请求回写，出现预览错位/闪回。
- 中低优先级：
  - `FileNav.svelte` 中 terminal server_id 的兜底会把 URL/selectedId 当作 server_id 传给后端；在缺失 server_id 的用户 terminal 配置上会稳定失败，导致重复 fallback 并增加无效请求。

## Status
- review 已完成，待向用户输出正式报告。
