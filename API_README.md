# API_README

本文档说明本分支新增/增强的 API 端点，并给出 `/docs` 与 `/openapi.json` 的查看方式。

## 1. 如何查看 API `/docs`

本项目在 `ENV=dev` 时才默认暴露 OpenAPI 文档：

- Swagger UI: `/docs`
- OpenAPI JSON: `/openapi.json`

代码位置：`backend/open_webui/main.py`

```python
docs_url="/docs" if ENV == "dev" else None
openapi_url="/openapi.json" if ENV == "dev" else None
```

如果你当前环境不是 `dev`，接口仍可用，但看不到 Swagger 页面。

## 2. 鉴权说明

- 需要登录用户的接口：`Authorization: Bearer <token>`
- 需要管理员的接口：同样使用 Bearer Token，但账号必须是 admin

## 3. 新增模型管理端点

路由前缀：`/api/v1/models`  
代码位置：`backend/open_webui/routers/models.py`

这些接口均要求：

- `get_verified_user`
- 对目标模型有写权限（owner / admin / access grant write）

### 3.1 PATCH meta

- `POST /api/v1/models/model/meta/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "meta": {
    "tags": [
      { "name": "prod" }
    ],
    "profile_image_url": "https://example.com/icon.png"
  }
}
```

### 3.2 PATCH params

- `POST /api/v1/models/model/params/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "params": {
    "temperature": 0.2,
    "top_p": 0.9
  }
}
```

### 3.3 更新模型图标

- `POST /api/v1/models/model/icon/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "profile_image_url": "https://example.com/icon.png"
}
```

说明：空字符串会回退为 `/static/favicon.png`。

### 3.4 更新系统提示词

- `POST /api/v1/models/model/prompt/system/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "system": "You are a strict code reviewer."
}
```

说明：空值会清理 `params.system`。

### 3.5 更新建议提示词

- `POST /api/v1/models/model/prompts/suggestions/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "suggestion_prompts": [
    {
      "title": ["生成日报", "Report"],
      "content": "Summarize today's commits."
    }
  ]
}
```

### 3.6 更新 capabilities

- `POST /api/v1/models/model/capabilities/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "capabilities": {
    "web_search": true,
    "code_interpreter": false,
    "deep_research": true
  }
}
```

### 3.7 更新激活状态

- `POST /api/v1/models/model/active/update`

请求体：

```json
{
  "id": "openai.gpt-4o-mini",
  "is_active": true
}
```

### 3.8 返回值

以上模型更新接口返回 `ModelModel`（完整模型对象）或 `null`（按声明为 Optional）。

## 4. Deep Research 配置端点

路由前缀：`/api/v1/configs`  
代码位置：`backend/open_webui/routers/configs.py`

权限：`get_admin_user`

### 4.1 读取配置

- `GET /api/v1/configs/deep_research`

### 4.2 更新配置

- `POST /api/v1/configs/deep_research`

请求体：

```json
{
  "ENABLE_DEEP_RESEARCH": true,
  "DEERFLOW_BASE_URL": "http://127.0.0.1:2026",
  "DEERFLOW_API_KEY": "",
  "DEERFLOW_MODEL": "gpt-4o-mini",
  "DEERFLOW_CONNECT_TIMEOUT_SECS": 10,
  "DEERFLOW_REQUEST_TIMEOUT_SECS": 900,
  "DEERFLOW_REUSE_THREADS": true
}
```

服务端会做下限保护：

- `DEERFLOW_CONNECT_TIMEOUT_SECS >= 1`
- `DEERFLOW_REQUEST_TIMEOUT_SECS >= 5`

## 5. RAG 自适应文件上下文（通过既有配置接口扩展）

路由前缀：`/api/v1/retrieval`  
代码位置：`backend/open_webui/routers/retrieval.py`

权限：`get_admin_user`

### 5.1 读取 RAG 配置

- `GET /api/v1/retrieval/config`

新增返回字段：

- `ADAPTIVE_FILE_CONTEXT_ENABLED`
- `ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE`
- `ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE`
- `ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST`
- `ADAPTIVE_FILE_CONTEXT_DEBUG`

### 5.2 更新 RAG 配置

- `POST /api/v1/retrieval/config/update`

最小示例：

```json
{
  "ADAPTIVE_FILE_CONTEXT_ENABLED": true,
  "ADAPTIVE_FILE_CONTEXT_DEFAULT_MODE": "retrieval",
  "ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_FILE": 8000,
  "ADAPTIVE_FILE_CONTEXT_MAX_TOKENS_PER_REQUEST": 32000,
  "ADAPTIVE_FILE_CONTEXT_DEBUG": false
}
```

## 6. Memory 编排参数（通过 admin config 接口扩展）

路由前缀：`/api/v1/auths`  
代码位置：`backend/open_webui/routers/auths.py`

权限：`get_admin_user`

### 6.1 读取 admin config

- `GET /api/v1/auths/admin/config`

新增字段（节选）：

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

### 6.2 更新 admin config

- `POST /api/v1/auths/admin/config`

最小示例（只展示新增项）：

```json
{
  "SHOW_ADMIN_DETAILS": true,
  "WEBUI_URL": "http://localhost:3000",
  "ENABLE_SIGNUP": true,
  "ENABLE_API_KEYS": true,
  "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS": false,
  "API_KEYS_ALLOWED_ENDPOINTS": "",
  "DEFAULT_USER_ROLE": "user",
  "DEFAULT_GROUP_ID": "",
  "JWT_EXPIRES_IN": "-1",
  "ENABLE_COMMUNITY_SHARING": true,
  "ENABLE_MESSAGE_RATING": true,
  "ENABLE_FOLDERS": true,
  "ENABLE_CHANNELS": true,
  "ENABLE_MEMORIES": true,
  "ENABLE_NOTES": true,
  "ENABLE_USER_WEBHOOKS": false,
  "ENABLE_USER_STATUS": true,
  "MEMORY_RETRIEVAL_MODE": "balanced",
  "MEMORY_RETRIEVAL_QUERY_K": 8,
  "MEMORY_NEED_STRONG_THRESHOLD": 0.7,
  "MEMORY_NEED_SOFT_THRESHOLD": 0.45,
  "MEMORY_MIN_TOP1_SIMILARITY": 0.35,
  "MEMORY_INJECTION_STRONG_TOP_N": 2,
  "MEMORY_INJECTION_SOFT_TOP_N": 1,
  "MEMORY_MAX_CONTEXT_CHARS": 1400,
  "MEMORY_NEED_INTENT_WEIGHT": 0.45,
  "MEMORY_NEED_RELEVANCE_WEIGHT": 0.45,
  "MEMORY_NEED_CONTINUITY_WEIGHT": 0.1,
  "MEMORY_STATELESS_PENALTY": 0.15
}
```

服务端约束（自动校正）：

- `MEMORY_RETRIEVAL_MODE ∈ {aggressive, balanced, conservative}`
- 阈值区间自动限制为 `[0, 1]`
- `strong_threshold > soft_threshold`（若不满足会自动修正）
- `QUERY_K >= 1`
- `TOP_N >= 1`
- `MAX_CONTEXT_CHARS >= 300`

## 7. 用 `/openapi.json` 快速核对端点

```bash
curl -s http://localhost:8080/openapi.json | jq '.paths | keys[]' | rg "deep_research|model/meta/update|model/params/update|model/icon/update|model/prompt/system/update|model/prompts/suggestions/update|model/capabilities/update|model/active/update|/retrieval/config|/auths/admin/config"
```

如果输出为空，先检查：

1. 服务是否已启动
2. 是否在 `ENV=dev`（否则 `/openapi.json` 默认关闭）

