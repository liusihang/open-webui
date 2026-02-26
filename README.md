# Open WebUI (Liusihang Enhanced Fork)

> 基于官方 [open-webui/open-webui](https://github.com/open-webui/open-webui) 的增强分支。  
> 目标是把「可用」提升到「生产可控 + 推理可视化 + 配置可调」。

![Fork Repo](https://img.shields.io/badge/fork-liusihang%2Fopen--webui-blue)
![Upstream](https://img.shields.io/badge/upstream-open--webui%2Fopen--webui-green)

## 这个仓库和官方版有什么不同？

截至 `2026-02-25`（当前工作分支相对 `upstream/main`）：

- `ahead 39`
- `behind 0`
- `54 files changed`（`+6072 / -1274`）

### 差异总览

| 模块 | 官方版本 | 本仓库增强 |
| --- | --- | --- |
| 文件上下文策略 | 主要依赖固定模式（如 full/retrieval） | 新增 **Adaptive File Context**：按 query 意图 + 文件体量 + 请求预算动态切换上下文模式，并可输出决策状态 |
| 记忆检索 | 有 memories 能力，但调参粒度有限 | 新增 **记忆检索编排器**：多维评分（意图/相关度/连续性）+ aggressive/balanced/conservative 模式 + 可视化状态回传 |
| 模型管理 API | 以整体更新为主 | 新增 **细粒度模型管理接口**（meta/params/icon/system prompt/suggestion prompts/capabilities/active 分项更新） |
| 推理与工具调用展示 | 可读性一般 | 新增 **Sequential Thinking 时间线分组渲染**、工具调用展示重构、状态历史信息密度优化 |
| 代码块体验 | 原生高亮风格 | 切换到 `svelte-highlight`，统一 VSCode 风格代码块视觉与交互按钮 |
| 主题与界面一致性 | 默认 `system` 主题 | 默认 `deerflow-light`，并对侧栏、设置页、状态卡片、代码块等统一视觉语言 |
| Deep Research | 官方无 DeerFlow 这套适配 | 新增 **DeerFlow 配置面板 + 权限位 + 适配器模块（实验性）** |

## 核心改进（带代码位置）

### 1) Adaptive File Context（文件上下文自适应）

- 核心实现：`backend/open_webui/utils/adaptive_file_context.py`
- 中间件接入：`backend/open_webui/utils/middleware.py`
- RAG 配置暴露：`backend/open_webui/routers/retrieval.py`
- 启动迁移：`backend/open_webui/utils/adaptive_file_context_migration.py`
- 回滚说明：`docs/adaptive_file_context_rollout.md`

特性要点：

- 自动估算文件 token（多来源字段兜底）
- query 意图分类（偏检索/偏总结）参与决策
- 文件级与请求级 token 预算仲裁
- 权限不通过文件自动排除（避免越权上下文注入）
- debug 状态可回传到前端 status timeline

### 2) 记忆检索编排（Memory Retrieval Orchestration）

- 核心逻辑：`backend/open_webui/utils/middleware.py`（`chat_memory_handler`）
- 配置扩展：`backend/open_webui/config.py`
- 管理后台接口：`backend/open_webui/routers/auths.py`
- 管理页调参 UI：`src/lib/components/admin/Settings/General.svelte`
- 专项文档：`MEMORY_RETRIEVAL_README.md`

特性要点：

- `intent_score + relevance_score + continuity_score` 综合计算是否注入记忆
- 支持 `aggressive / balanced / conservative` 预设
- 支持 `top_k`、阈值、权重、上下文长度等精细调参
- 全流程状态事件：检索中/命中数/注入数/错误信息

### 3) 模型管理细粒度接口

后端新增接口（`backend/open_webui/routers/models.py`）：

- `POST /models/model/meta/update`
- `POST /models/model/params/update`
- `POST /models/model/icon/update`
- `POST /models/model/prompt/system/update`
- `POST /models/model/prompts/suggestions/update`
- `POST /models/model/capabilities/update`
- `POST /models/model/active/update`

前端 SDK 对应封装：`src/lib/apis/models/index.ts`

收益：降低“整对象覆盖更新”风险，便于前端逐项配置与权限分离。

### 4) 推理与工具调用 UI 重构

- Sequential Thinking 组件：`src/lib/components/common/SequentialThinkingTimeline.svelte`
- Markdown token 聚合渲染：`src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte`
- Tool Call 展示：`src/lib/components/common/ToolCallDisplay.svelte`
- 状态历史卡片：`src/lib/components/chat/Messages/ResponseMessage/StatusHistory.svelte`
- 状态项细化：`src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`

增强点：

- 将连续 `sequential thinking` 工具调用自动聚合为时间线
- 工具入参与结果结构化展示，执行中/完成态视觉统一
- 深度研究步骤、记忆检索状态、Web 搜索状态可读性提升

### 5) DeerFlow 风格主题与 Deep Research（实验性）

- 主题与全局样式：`src/app.css`、`src/app.html`、`src/lib/stores/index.ts`
- Deep Research 设置页：`src/lib/components/admin/Settings/DeepResearch.svelte`
- 设置路由接入：`src/lib/components/admin/Settings.svelte`
- 配置 API：`backend/open_webui/routers/configs.py`
- DeerFlow 适配器：`backend/open_webui/utils/deerflow.py`

说明：

- 当前分支已提供配置面板、权限与状态渲染能力，并提供 DeerFlow SSE 适配模块。
- 该能力仍建议按“实验性/灰度”方式在生产环境启用。

## 常用新增配置项

### Adaptive File Context

- `ADAPTIVE_FILE_CONTEXT_ENABLED`
- `ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE` (`full` / `retrieval`)
- `ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE`
- `ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST`
- `ADAPTIVE_FILE_CONTEXT_DEBUG`
- `ADAPTIVE_FILE_CONTEXT_MIGRATION_VERSION`

### Memory Retrieval Orchestration

- `MEMORY_RETRIEVAL_MODE`
- `MEMORY_RETRIEVAL_QUERY_K`
- `MEMORY_NEED_STRONG_THRESHOLD`
- `MEMORY_NEED_SOFT_THRESHOLD`
- `MEMORY_MIN_TOP1_SIMILARITY`
- `MEMORY_INJECTION_STRONG_TOP_N`
- `MEMORY_INJECTION_SOFT_TOP_N`
- `MEMORY_MAX_CONTEXT_CHARS`
- `MEMORY_NEED_INTENT_WEIGHT`
- `MEMORY_NEED_RELEVANCE_WEIGHT`
- `MEMORY_NEED_CONTINUITY_WEIGHT`
- `MEMORY_STATELESS_PENALTY`

### Deep Research / DeerFlow

- `ENABLE_DEEP_RESEARCH`
- `DEERFLOW_BASE_URL`
- `DEERFLOW_API_KEY`
- `DEERFLOW_MODEL`
- `DEERFLOW_CONNECT_TIMEOUT_SECS`
- `DEERFLOW_REQUEST_TIMEOUT_SECS`
- `DEERFLOW_REUSE_THREADS`

## 快速开始

与官方 Open WebUI 安装方式兼容，推荐先用 Docker 启动：

```bash
docker run -d -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  --name open-webui \
  --restart always \
  ghcr.io/open-webui/open-webui:main
```

然后按需在管理后台开启/调整本分支新增能力（如自适应文件上下文、记忆检索编排参数等）。

官方文档入口：<https://docs.openwebui.com/>

## 与上游同步策略

- 上游仓库：<https://github.com/open-webui/open-webui>
- 当前策略：持续跟进上游主线，在此基础上叠加增强功能
- 建议升级方式：先同步上游，再验证本分支的 `Adaptive File Context` / `Memory` / `UI` 相关改动

## License

本仓库继承并遵循 Open WebUI 及其历史贡献相关许可证约束。  
详见：

- `LICENSE`
- `LICENSE_HISTORY`
