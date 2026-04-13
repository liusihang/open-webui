# Handoff - OpenWebUI Office 嵌入式预览能力确认

- Date: 2026-04-13
- Workspace: /Users/liusihang/openwebui
- Goal: 回答“在 OpenWebUI 中实现 Office 文件嵌入式预览”是否需要新增方案。

## Checkpoints

1. Checkpoint: 核验 Terminal FileNav 是否已支持 Office 预览
- Action: 审查 `src/lib/components/chat/FileNav.svelte` 与 `src/lib/components/chat/FileNav/FilePreview.svelte`。
- Evidence:
  - `FileNav.svelte` 定义 `OFFICE_EXTS = [docx,xlsx,pptx]` 并在打开文件时分别执行：
    - docx -> `mammoth.convertToHtml`
    - xlsx -> `xlsx` + `excelToTable`
    - pptx -> `pptxToImages`
  - `FilePreview.svelte` 对 `fileOfficeHtml` / `fileOfficeSlides` 做嵌入渲染（非下载跳转）。
- Result: Terminal 文件树内 Office 嵌入预览已具备。

2. Checkpoint: 核验聊天上传文件是否支持 Office 预览
- Action: 审查 `src/lib/components/common/FileItemModal.svelte`。
- Evidence:
  - 按 content-type/扩展名识别 `isDocx/isExcel/isPptx`；
  - `Preview` tab 中渲染 docx HTML、xlsx 表格、pptx slide 图片。
- Result: 聊天内文件卡片弹窗也已支持 Office 预览。

3. Checkpoint: 核验如何让模型操作后自动跳到预览
- Action: 审查 `backend/open_webui/utils/middleware.py` 终端事件桥接。
- Evidence:
  - `display_file` 会发 `terminal:display_file` 事件到前端并打开文件预览。
- Result: 可通过工具调用链把“编辑结果 -> 预览”串起来，无需新增重型模块。

## Current Decision

- 你要的能力“嵌入式预览”当前分支已经具备。
- 最短路径不是再接新 viewer，而是：
  1) 让 OfficeCLI 产出的目标文件位于 Terminal 可访问路径；
  2) 在工具流末尾触发 `display_file(path)` 自动打开预览。

## Notes

- 这套预览是“转换后预览”（docx->html, pptx->图片），并非 Microsoft Web 版高保真协作编辑器。
- 若目标是多人协同编辑和几乎 100% 版式一致，应考虑外接 OnlyOffice/Collabora iframe 路线（成本更高）。

4. Checkpoint: 判定 OfficeCLI 与“高保真预览”关系
- Action: 对照 OfficeCLI README 的能力边界与当前 OpenWebUI 预览实现。
- Evidence:
  - OfficeCLI 强项是结构化读写与自动化；其预览能力中最强调的是 PPTX `watch` 预览。
  - 当前 OpenWebUI 的 Office 预览链路本质是转换型预览（docx/html、xlsx/table、pptx/images）。
- Result:
  - 若目标是“跨格式稳定高保真（含复杂版式）”，OfficeCLI 不是独立解；需要接专门 office 渲染引擎。
  - OfficeCLI 适合作为“改文档的引擎”，不适合作为唯一“高保真渲染引擎”。

5. Checkpoint: 方案分层建议（按成本）
- Action: 从目标函数（保真度、改造成本、协作需求）拆分方案。
- Result:
  - 低成本只读高保真：用 LibreOffice/UNO 服务端转 PDF，再走现有 PDF Viewer。
  - 中高成本可编辑高保真：接 ONLYOFFICE Docs 或 Collabora Online（iframe/WOPI/connector）。
  - OfficeCLI 继续用于 agent 自动化编辑，与渲染层解耦。

6. Checkpoint: 启动子代理并行代码调研（in-progress）
- Action: 启动 explorer 子代理 A（前端接入点）和 B（后端/部署/安全）。
- Goal: 以最小改动面提出 OpenWebUI + ONLYOFFICE 可运行方案。
- Status: waiting subagent results; main agent 不接管其任务。

7. Checkpoint: 子代理调研完成（前端+后端）
- Action: 回收 explorer A（前端挂载点/CSP/交互链路）与 explorer B（后端接口/鉴权/部署/安全风险）结果。
- Result: 可形成最小侵入的 OpenWebUI + ONLYOFFICE 方案。

## Consolidated Minimal Solution (Draft)

- 架构定位：
  - OpenWebUI 负责会话、权限、文件元数据与回调处理；
  - ONLYOFFICE Document Server 负责高保真渲染/编辑；
  - 两者通过受控 session 接口 + callback 接口对接。

- 前端最小改动：
  1) 新建 `OnlyOfficeViewer.svelte`（复用 iframe 封装/生命周期）；
  2) 在 `FilePreview.svelte` 的 Office 分支挂载 viewer；
  3) 在 `FileItemModal.svelte` 的 Preview tab 挂载 viewer；
  4) 保持既有 `terminal:display_file` 事件链不变。

- 后端最小改动：
  1) 新增 `routers/onlyoffice.py`：
     - `POST /api/v1/onlyoffice/session`
     - `GET /api/v1/onlyoffice/files/{file_id}`（短时效 token）
     - `POST /api/v1/onlyoffice/callback/{file_id}`（JWT + doc key 校验）
  2) 在 `config.py` 增加 ONLYOFFICE 专用 `PersistentConfig`；
  3) `main.py` 注册 onlyoffice router；
  4) compose/.env 增加 onlyoffice 服务与变量。

- 风险优先级（简）：
  - P0: 禁止把长期 admin key 暴露给 DS；
  - P0: callback 必须做 JWT 与文档键校验；
  - P1: callback 下载 URL 做白名单/SSRF 防护；
  - P1: 配置 CSP `frame-src/connect-src` 放行 DS 域并收敛 CORS。

8. Checkpoint: 启动 gpt-5.4 子代理完成修改计划设计（done）
- Action: spawn gpt-5.4 subagent for implementation plan design only.
- Output: 形成 Phase0-Phase3 分阶段计划、API contract 草案、验证与回滚清单、最小改动版 vs 长期架构版对比、里程碑与待确认决策。
- Decision: 采用“最小改动面版本”作为首发主线：先只读高保真，再编辑回写，再安全强化与运维。

9. Checkpoint: 基于当前分支创建隔离 worktree（done）
- Goal: 不污染 `codex/merge-v0.8.12` 脏工作区，独立推进 Phase0+1。
- Action:
  - 验证 `.worktrees` 已被 git ignore；
  - 创建新分支与工作树：`codex/onlyoffice-phase0-1` -> `/Users/liusihang/openwebui/.worktrees/codex-onlyoffice-phase0-1`。
- Result: 隔离工作区创建成功，可独立提交 onlyoffice 方案改动。

10. Checkpoint: 子代理并行调研前后端接入点（done）
- Goal: 在不重复探索的前提下确认最小插入面与风险。
- Action:
  - 前端 explorer 输出了 `FileItemModal.svelte` 与 `FilePreview.svelte` 的 Office 渲染插入点与 fallback 策略；
  - 后端 explorer 输出了 `files/auth/config/main` 相关复用链路及安全要点（JWT、短时 token、allowlist）。
- Evidence:
  - `docs/plans/2026-04-13-onlyoffice-preview-phase0-handoff.md`
  - `docs/plans/2026-04-13-onlyoffice-phase0-1-backend-research-handoff.md`
- Result: 确认 Phase0+1 可先聚焦 `file_id` 只读链路，Terminal path 适配后置。

11. Checkpoint: Phase0 后端最小可用实现（done）
- Goal: 打通 onlyoffice 只读 session + 受控文件访问 + callback 安全骨架。
- Action:
  - 新增 `backend/open_webui/routers/onlyoffice.py`：
    - `POST /api/v1/onlyoffice/session`（仅 `view` 模式，签发短时文件 token）
    - `GET /api/v1/onlyoffice/files/{file_id}`（token 校验 + 文件读权限复核）
    - `POST /api/v1/onlyoffice/callback/{file_id}`（JWT 可选校验 + key 校验 + URL allowlist）
  - 在 `backend/open_webui/config.py` 增加 PersistentConfig：
    - `ENABLE_ONLYOFFICE_PREVIEW`
    - `ONLYOFFICE_DOCUMENT_SERVER_URL`
    - `ONLYOFFICE_JWT_SECRET`
    - `ONLYOFFICE_FILE_TOKEN_EXPIRES_IN`
    - `ONLYOFFICE_CALLBACK_ALLOWED_HOSTS`
  - 在 `backend/open_webui/main.py` 注入配置并注册 onlyoffice router。
- Result: 后端只读链路和安全基线已具备。

12. Checkpoint: Phase1 前端接入实现（done）
- Goal: 在不破坏现有 docx/xlsx/pptx 本地转换预览的前提下接入 OnlyOffice。
- Action:
  - 新增 `src/lib/apis/onlyoffice/index.ts`（创建 session API）。
  - 新增 `src/lib/components/common/OnlyOfficeViewer.svelte`（动态加载 Docs API + 只读初始化 + 失败事件）。
  - 修改 `src/lib/components/common/FileItemModal.svelte`：
    - Office 预览优先走 `OnlyOfficeViewer`；
    - 初始化失败后自动回退到原有本地转换预览（excel/docx/pptx）。
- Result: 聊天文件弹窗已有 onlyoffice 优先预览与自动 fallback。

13. Checkpoint: 部署模板补齐（done）
- Goal: 让 docker-compose 可直接启用/关闭 onlyoffice。
- Action:
  - 更新 `docker-compose.yaml`：
    - open-webui 注入 onlyoffice 环境变量；
    - 新增 `onlyoffice/documentserver` 服务（默认可通过 env 关闭 JWT）。
- Result: 本地/测试环境具备最小运行模板。

14. Checkpoint: 定向验证（done with baseline notes）
- Action:
  - `python3 -m compileall backend/open_webui/routers/onlyoffice.py backend/open_webui/main.py backend/open_webui/config.py` 通过。
  - `npm run lint:frontend` 触发仓库既有 eslint 崩溃（`@typescript-eslint/no-unused-vars` 在既有文件报 TypeError，非本次改动特有）。
  - `npm run check` 暴露仓库既有大量 TypeScript/Svelte 基线错误（非本次改动引入）。
- Result: 本次改动的 Python 语法层面通过；前端全量静态检查受仓库基线问题影响。

## Current Phase Status

- Phase0: done（后端只读 session/file/callback 骨架 + 配置 + 路由注册）。
- Phase1: done（FileItemModal 接入 OnlyOfficeViewer + fallback）。
- Deferred to next phase:
  - Terminal FileNav 路径（`FilePreview.svelte`）仍是本地转换预览，待“terminal/path -> onlyoffice 文档源适配层”落地后接入；
  - callback 真正回写（下载保存、版本冲突处理）留到后续编辑 phase。

15. Checkpoint: 补齐 Terminal FileNav -> OnlyOffice 链路（done）
- Goal: 让 `FilePreview.svelte` 路径不再只靠本地转换预览，补齐计划中的第二个前端挂载点。
- Action:
  - 修改 `src/lib/components/chat/FileNav.svelte`，传递 `selectedTerminalServerId + selectedFile(path)` 给 `FilePreview`；
  - 修改 `src/lib/components/chat/FileNav/FilePreview.svelte`，加入 `OnlyOfficeViewer` 分支与失败 fallback（退回既有 html/slides 预览）。
- Result: FileNav Office 文件支持优先走 OnlyOffice，失败自动退回原链路。

16. Checkpoint: 增加 terminal source 后端会话与取文件接口（done）
- Goal: 让 Document Server 能访问 terminal 路径文件（仅只读）。
- Action:
  - 扩展 `POST /api/v1/onlyoffice/session`：支持 `source_type=file|terminal`；
  - 新增 `GET /api/v1/onlyoffice/terminal/files?token=...`；
  - 新增 `POST /api/v1/onlyoffice/callback/terminal`；
  - 文件回调路由调整为 `POST /api/v1/onlyoffice/callback/file/{file_id}` 以避免与 terminal 固定路径冲突；
  - session 中对 terminal 连接做权限校验（复用 `has_connection_access`），并限制 phase0+1 仅支持 `auth_type=bearer|none`。
- Result: `file_id` 与 `terminal_path` 两条只读来源都可生成 onlyoffice session。

17. Checkpoint: Phase0+1 完成态核验（done）
- Action:
  - 后端：`python3 -m compileall backend/open_webui/routers/onlyoffice.py backend/open_webui/main.py backend/open_webui/config.py` 通过；
  - 前端改动文件：`OnlyOfficeViewer.svelte` 与 `apis/onlyoffice/index.ts` eslint 通过；
  - `FilePreview.svelte` 在本仓库会触发既有 eslint 插件崩溃（`@typescript-eslint/no-unused-vars` TypeError），已在主工作区对照复现，确认是基线问题。
- Result: 以改动级别看，Phase0+1 所需代码路径已全部落地。

## Updated Phase Status

- Phase0: done
  - onlyoffice 配置、路由注册、`file` + `terminal` 两类 session/取文件/callback 只读链路。
- Phase1: done
  - `FileItemModal.svelte` 与 `FilePreview.svelte` 两个前端挂载点均接入 onlyoffice，并保留 fallback。
- Next (not in phase0+1):
  - 编辑回写（callback 下载保存/冲突处理）；
  - CSP/CORS 策略收敛与生产部署硬化。

18. Checkpoint: terminal auth_type=session 支持补齐（done）
- Trigger: 用户确认主要连接方式为 `session`，需纳入 phase0+1 可用范围。
- Action:
  - 更新 `backend/open_webui/routers/onlyoffice.py` 的 terminal 连接鉴权白名单为 `bearer|none|session`；
  - 在创建 terminal onlyoffice session 时，若连接是 `session`，签发短时 `session_proxy_token`（claim 最小化：`id`,`role`，与 onlyoffice 文件 token 同 TTL）；
  - 在 `GET /api/v1/onlyoffice/terminal/files` 拉取时，`auth_type=session` 使用该短时 token 作为上游 Authorization Bearer。
- Security Note:
  - 未透传用户长期 token；仅使用短时代理 token；
 - token 仍为 JWT 明文负载，已保持最小 claim，不写入敏感凭据。
- Validation:
  - `python3 -m compileall backend/open_webui/routers/onlyoffice.py` 通过。
- Result: terminal onlyoffice 只读链路已支持 `session` 认证模式。

19. Checkpoint: UI/UX 定向修复任务对齐（done）
- Goal: 针对用户新增的 5 项前端可用性问题做最小改动修复，不改后端。
- Action:
  - 阅读并遵循 `ui-ux-pro-max` 规则，优先落实 a11y、触控可用性、移动端滚动体验；
  - 逐项核验 `FileNav/FilePreview/FileItemModal/OnlyOfficeViewer/apis/onlyoffice` 的现状与缺口。
- Result: 明确了“user terminal server_id 传空导致 onlyoffice 分支静默跳过”为 P0 前端根因之一。

20. Checkpoint: 5 项需求实现落地（done）
- Goal: 一次性修复 1-5 项并保持原 fallback 链路可用。
- Action:
  - `src/lib/components/chat/FileNav.svelte`：
    - 新增 `getTerminalServerId()`，按 `system id -> user config id/server_id -> runtime url match -> selected value` 顺序解析；
    - 传递 `onlyOfficeTerminalServerId/onlyOfficeTerminalFilePath` 给 `FilePreview`，避免 user terminal 场景静默不进 onlyoffice。
  - `src/lib/components/chat/FileNav/FilePreview.svelte` 与 `src/lib/components/common/FileItemModal.svelte`：
    - fallback 提示加入 `Retry`（就地重试，重置 onlyoffice 失败状态）；
    - 错误提示增加 `role="alert"` + `aria-live="polite"`；
    - PPT 图标翻页按钮补 `aria-label`（上一页/下一页）。
  - `src/lib/components/common/OnlyOfficeViewer.svelte`：
    - 默认容器高度改为 `h-[55dvh] md:h-[60vh]`；
    - 初始化错误提示补 `role/aria-live`。
  - `src/lib/apis/onlyoffice/index.ts`：
    - `createOnlyOfficeSession` 增加 `AbortController` 12s 超时；
    - 超时报错文案明确；
    - 非 2xx 时统一 `throw Error`，并尽量提取后端 `detail/message/error/msg`（含数组错误）。
- Result: 5 项需求均已在指定前端文件落地。

21. Checkpoint: 定向验证（done with baseline notes）
- Goal: 在仓库基线不稳定前提下，给出改动级别可验证证据。
- Action:
  - `npx prettier --write` + `npx prettier --check`（目标文件）通过；
  - `npx eslint src/lib/apis/onlyoffice/index.ts src/lib/components/common/OnlyOfficeViewer.svelte` 通过；
  - `npx eslint` 涵盖 `FilePreview.svelte` 触发既有 `@typescript-eslint/no-unused-vars` 崩溃（仓库基线问题）；
  - 将 `@typescript-eslint/no-unused-vars` 临时关闭后，`FilePreview.svelte` 可继续产出既有 lint 基线告警，确认非本次新增语法错误。
- Result: 本次新增逻辑可运行面已定向验证，剩余失败项属于仓库历史 lint 基线问题。
