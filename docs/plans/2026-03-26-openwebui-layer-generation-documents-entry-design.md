# OpenWebUI Layer Generation Documents Entry Design

## Goal
在 `Admin -> Settings -> Documents` 中增加一个 Layer Generation 配置分区，让管理员能直接管理当前 layered knowledge 使用的 Open Notebook 连接与 transformation 映射，而不必依赖环境变量或直接改后端持久配置。

## Current State
- 现有前端只在知识库详情页提供 layer 触发入口。
- 后端实际配置来源是 `PersistentConfig`：`OPEN_NOTEBOOK_BASE_URL`、`OPEN_NOTEBOOK_API_PASSWORD`、`OPEN_NOTEBOOK_TIMEOUT_SECS`，以及 transformation 映射项。
- `Documents` 页已经通过 retrieval config API 读写文档相关设置，因此最小改动路径是把 layer generation 配置并入该 config 通道，而不是新增一套路由和页面。

## Recommended Approach
采用方案 A：在 `Documents` 页增加一个独立的 `Layer Generation` 分区，并扩展 retrieval config 的返回/更新字段。

优点：
- 不新增 admin 导航和页面结构
- 与知识库/文档设置放在同一处，符合操作心智
- 前后端改动面最小，复用现有保存提示和加载流程

不做的事：
- 不新增独立 `/admin/settings/layer-generation` 路由
- 不改变知识库详情页现有 regenerate/backfill 交互
- 不在本次改动里重做 layer 数据模型或 transformation 语义

## UX Shape
在 `Documents` 页新增一个折叠或常规分区，包含：
- Open Notebook Base URL
- Open Notebook API Password
- Open Notebook Timeout Seconds
- Transformation Abstract
- Transformation Key Findings
- Transformation Key Data

行为：
- 页面加载时跟随 `getRAGConfig` 一起返回这些字段
- 点击保存时跟随 `updateRAGConfig` 一起持久化
- 不额外引入复杂联动；先保证可见、可改、可保存

## Backend Changes
- 在 retrieval config GET 响应中加入 Open Notebook layer generation 字段
- 在 retrieval config UPDATE 表单中加入对应字段
- 在 update handler 中同步写入 `request.app.state.config.*`
- 继续使用 `PersistentConfig`，避免新增存储机制

## Testing Strategy
- 先写后端 router 测试：验证 retrieval config 返回这些字段，并且 update 后写入 config
- 再写前端 API/静态 UI 测试：验证 `Documents` 页包含 Layer Generation 文案和字段绑定入口
- 最后跑相关 retrieval / knowledge / documents 测试
