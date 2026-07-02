# Agent Runtime Tool 合并：MCP rebuild + web_search_research + 开关迁移

## Scope

- 分支：`codex/pr7-agent-mode-status-history-upgrade`（接续上一个 PR7 UI 重构）
- 目标：为"全部 Agent 模式"愿景打通 3 项后端阻塞，**不动前端、不删 legacy 路径**
- 验收：598 个 agent+util 测试全过，6 个新测试覆盖 native FC 路径的权限边界

## 3 项改动

### Task A：修 agent runtime MCP rebuild bug

**问题**：`backend/open_webui/routers/agent_service.py:148` 的 `_rebuild_agent_tool_registry` 只处理 `type=='builtin'` 和 `type=='terminal'`，**完全没处理 `type=='external'`**（MCP/Valves/direct_tool_servers）。server 重启或 registry cache miss 后，MCP tool 在 agent runtime 里调不出来。

**改动**：
- `agent_service.py:301-320` 新增 `_external_tool_source_id_from_snapshot`，从 `tool:server:{source}:{name}` 格式的 opaque_id 提取 source tool_id（模式跟 `_terminal_id_from_snapshot_tool` 一致）
- `agent_service.py:323-389` 新增 `_rebuild_external_tools`，按 source tool_id 分组调 `get_tools`（每 server 一次，跟 run 创建路径一致），部分缺失 graceful 降级，全缺失才 503
- `agent_service.py:392-395` 新增 `_user_payload` 辅助函数
- `agent_service.py:172` 在 `_rebuild_agent_tool_registry` 里调用 `_rebuild_external_tools`
- `agent_service.py:60` import 加 `get_tools`

**测试**：`backend/open_webui/test/agent/test_agent_service_rebuild.py` 20 个测试，覆盖 source_id 解析、snapshot 边界、registry 映射、external 重建（单服务端/多函数/部分缺失/全缺失 503/损坏字段）、混合 builtin+external 端到端。

### Task B：补 web_search tool 多 query 能力

**问题**：legacy `chat_web_search_handler`（`middleware.py:1872`）用 task model 调 `generate_queries` 拆多 query 并行搜；而 `tools/builtin.py:236` 的 `search_web` tool 只支持单 query，agent runtime 用它覆盖率退化。

**改动**：
- `tools/builtin.py:275-381` 新增 `web_search_research(topic, count, __request__, __user__)` tool
  - 复用 `open_webui.routers.tasks.generate_queries` 拆多 query（lazy import 避免循环依赖）
  - `asyncio.gather` 并行调 `_search_web`
  - URL 去重，每个结果标注 source query
  - 全程 try/except，错误返回 `{"error": "..."}` 不打断 agent run
  - count 参数应用于总体结果而非每 query
- `utils/tools.py:88` import 加 `web_search_research`
- `utils/tools.py:583` `get_builtin_tools` 的 `builtin_functions.extend` 加 `web_search_research`（跟 `search_web`/`fetch_url` 共用同一特性开关和权限检查）
- `search_web` 签名保持不变（向后兼容）

**测试**：`backend/open_webui/test/util/test_builtin_search_tools.py` 6 个测试，覆盖无 request context、query 生成失败 fallback、多 query 并行搜去重、空 queries fallback、全空结果、count 截断。

### Task C：迁 features.web_search 开关语义

**问题**：`middleware.py:3021-3024` 的 `features.web_search=true` 开关在 native FC 模式下被跳过（line 3022 注释 "Skip forced RAG web search when native FC is enabled"），导致 agent runtime 路径下用户开 web_search toggle 没反应。

**改动**：
- `middleware.py:3021-3028` 在 `features.web_search=true` 分支加 `else`（native FC 路径）：设置 `extra_params['__force_web_search_tools__'] = True` 信号量，不调 legacy `chat_web_search_handler`
- `utils/tools.py:584-595` `get_builtin_tools` 加 `elif` 分支：当 `__force_web_search_tools__` 且 `features.web_search` 为 True 时，强制注入 `search_web` 和 `web_search_research`（不注入 `fetch_url`）
- **关键修复（lead review 时发现子代理漏洞）**：`elif` 分支必须同时满足 `ENABLE_WEB_SEARCH` 全局开关 + `has_user_permission('web_search')`。子代理原版绕过了这两个安全边界——`process_web_search` API（`retrieval.py:2519`）对 `ENABLE_WEB_SEARCH=False` 直接 403，native FC 路径不能比 API 路径更宽松。模型级 `builtinTools/web_search` capability gate 可以绕过（用户显式开了 toggle），但全局开关和用户权限不行。

**测试**：`backend/open_webui/test/util/test_web_search_feature_injection.py` 6 个测试：
1. native FC + 全局开 + admin → 注入成功
2. legacy 路径（非 native）→ `chat_web_search_handler` 仍被调用
3. native FC → `chat_web_search_handler` 不被调用
4. 幂等性：注入的 tool 只出现一次
5. **全局 `ENABLE_WEB_SEARCH=False` → 不注入**（安全边界）
6. **非 admin 用户 + `USER_PERMISSIONS['features.web_search']=False` → 不注入**（权限边界）

## 验收

- `pytest open_webui/test/agent/ open_webui/test/util/ --ignore=test_pgvector_search.py`：**598 passed**（含 32 个新测试）
- `pytest test_middleware_citation_map + test_builtin_skill_tools + test_terminal_session_auth`：25 passed
- 相关 imports 全部 OK

## 未做（明确范围）

- **不删** legacy `chat_web_search_handler` / `chat_completion_files_handler`（legacy 路径还在用，等 agent runtime 全量铺开后再删）
- **不动**前端 `Chat.svelte:529-546` Socket.IO status 分支 / `StatusItem.svelte` 的 `action` 分派
- **不实现** MCP/Valves 在 agent runtime 重建路径的完整 external tool 支持——本次只修 cache miss 后的 rebuild bug，不动 run 创建时的装载逻辑

## 下一步（未来 PR）

1. agent runtime 全量铺开后，删 legacy `chat_web_search_handler` / `chat_completion_files_handler`
2. 删前端 Socket.IO status 分支 + StatusItem `action` 分派
3. 评估 `web_search_research` 是否要进一步支持迭代搜索（主模型多轮调用）

## 文件清单

### 修改
- `backend/open_webui/routers/agent_service.py` — MCP rebuild
- `backend/open_webui/tools/builtin.py` — `web_search_research` tool
- `backend/open_webui/utils/middleware.py` — 开关信号量
- `backend/open_webui/utils/tools.py` — `get_builtin_tools` elif 分支 + 权限修复

### 新增
- `backend/open_webui/test/agent/test_agent_service_rebuild.py` — 20 tests
- `backend/open_webui/test/util/test_builtin_search_tools.py` — 6 tests
- `backend/open_webui/test/util/test_web_search_feature_injection.py` — 6 tests
- `docs/handoff-agent-runtime-tool-consolidation-2026-06-20.md` — 本文件
