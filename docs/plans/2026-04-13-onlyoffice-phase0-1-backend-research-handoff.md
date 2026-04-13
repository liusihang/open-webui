# OnlyOffice Phase0+1 Backend Research Handoff

## Goal

在 `/Users/liusihang/openwebui/.worktrees/codex-onlyoffice-phase0-1` 完成只读调研，输出：

1. 文件访问、鉴权、文件响应相关现有路由/工具（用于 onlyoffice session/file/callback 设计）
2. `config.py` 中新增 ONLYOFFICE `PersistentConfig` 的最佳位置与命名模式
3. `main.py` 中新 router 的注册位置
4. 只读首发（Phase0+1）优先接口建议与可后置项
5. 安全必做项（JWT、短时效 token、SSRF 白名单）

## Checkpoints

### Checkpoint 1 (Done)

- 动作: 扫描仓库与历史 handoff 约定
- 证据:
  - `docs/plans/2026-03-25-openwebui-chat-performance-handoff.md`
  - `backend/open_webui/routers/`
- 结论: 使用 `docs/plans/` 记录本次新任务 handoff。

### Checkpoint 2 (Done)

- 动作: 定位主文件路由与配置注入位置
- 证据:
  - `backend/open_webui/main.py` 路由注册段（`app.include_router(...)`）
  - `backend/open_webui/main.py` `app.state.config.*` 注入段
- 结论: onlyoffice router 应遵循现有 `/api/v1/*` 注册段；config 注入建议放在与外部集成配置相邻区块。

### Checkpoint 3 (Done)

- 动作: 精读文件访问与响应链路
- 证据:
  - `backend/open_webui/routers/files.py`
  - `backend/open_webui/storage/provider.py`
  - `backend/open_webui/utils/access_control/files.py`
  - `backend/open_webui/models/access_grants.py`
- 结论: 现有文件权限和响应能力可复用（`get_verified_user` + `has_access_to_file` + `FileResponse/StreamingResponse` + `Storage.get_file`）。

### Checkpoint 4 (Done)

- 动作: 精读鉴权与 token 链路
- 证据:
  - `backend/open_webui/utils/auth.py`
  - `backend/open_webui/routers/auths.py`
  - `backend/open_webui/utils/misc.py` (`parse_duration`)
  - `backend/open_webui/main.py` API key 限制中间件
- 结论: JWT 生成/校验/吊销链路完整，可扩展 onlyoffice 专用短时 token（建议独立 claim）。

### Checkpoint 5 (Done)

- 动作: 精读 SSRF 防护与 URL 校验实践
- 证据:
  - `backend/open_webui/retrieval/web/utils.py` (`validate_url`)
  - `backend/open_webui/config.py` (`WEB_FETCH_FILTER_LIST`)
  - `backend/open_webui/routers/images.py` 对外 URL 加载前校验
  - `backend/open_webui/routers/terminals.py` 代理路径净化
- 结论: 可复用现有 URL 安全基线，但 onlyoffice callback 下载需单独强约束 allowlist。

## Current Status

- 调研完成，待输出最终结论。
- 未修改业务代码。

### Checkpoint 6 (Done)

- 动作: 对齐同日 onlyoffice 前端调研 handoff，确认后端接口边界需求
- 证据:
  - `docs/plans/2026-04-13-onlyoffice-preview-phase0-handoff.md`
- 结论: 首发后端接口需同时支持 webui `file_id` 场景与后续可扩展的 terminal/path 场景，但 Phase0+1 先聚焦 `file_id` 只读。

### Checkpoint 7 (Done)

- 动作: 核验文件授权语义（owner / grants / group）与默认私有行为
- 证据:
  - `backend/open_webui/utils/access_control/files.py`
  - `backend/open_webui/models/access_grants.py`
  - `backend/open_webui/utils/access_control/__init__.py`
- 结论: 文件访问应继续复用 `has_access_to_file(..., read|write)`；不要仅依赖 `AccessGrants.has_access`，否则会漏掉 channel/chat/model 挂载权限。
