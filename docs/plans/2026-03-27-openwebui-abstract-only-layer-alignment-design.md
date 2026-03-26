# OpenWebUI Abstract-Only Layer Alignment Design

## Goal
将当前分支重新对齐到 `9e89a7a107d63ae26db60aa7032bb0b206706a50` 的“只保留 `abstract` layer”语义，同时保留对旧 `key_findings` / `key_data` 数据与配置的最小兼容，避免现有知识库和旧镜像升级后直接失效。

## Approved Approach
采用方案 A：
- 前端 UI、管理员配置、用户可见 layer 选择全部只暴露 `abstract`
- 后端内部保留最小兼容：旧 `key_findings` / `key_data` 输入、旧 transformation 配置、旧 layer 行可被读取/映射，但不再作为一等能力暴露

## Desired Behavior
- 知识库详情页只显示 `abstract`
- Layer regenerate/backfill 只对 `abstract` 生效
- Admin -> Settings -> Documents -> Layer Generation 只显示：
  - Open Notebook Base URL
  - Open Notebook API Password
  - Open Notebook Timeout Seconds
  - Transformation Abstract
- `key_findings` / `key_data` 不再在 UI 中出现，也不再要求管理员配置
- 若历史数据里存在 `key_findings` / `key_data`，系统仍能兼容读取或在必要时归并为 `abstract`，但不再主动生成它们

## Compatibility Strategy
- 保留后端兼容别名：旧 layer 名称进入核心逻辑时映射到 `abstract`
- 保留读取旧 transformation 配置的兼容路径，但 UI 不再展示这些字段
- 保留旧测试中必要的兼容覆盖，新增明确的 abstract-only 回归测试

## Out of Scope
- 不做数据库迁移批量删除旧 layer 行
- 不重建知识库数据
- 不改变 full-text retrieval

## Testing Strategy
- 先写 failing tests：
  - 前端仅显示 `abstract`
  - Documents 只展示 abstract 配置项
  - 后端 layer alias 映射到 `abstract`
- 再做最小实现
- 最后跑 focused backend/frontend regression
