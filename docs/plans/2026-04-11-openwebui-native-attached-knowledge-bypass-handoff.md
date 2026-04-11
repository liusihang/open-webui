# 2026-04-11 Native Attached Knowledge Bypass Handoff

## 目标
- 修复 native attached knowledge bypass 的 3 个 review finding：
  - 混合场景（scoped knowledge + regular files）保留 regular files 的 legacy retrieval。
  - skip gate 与 native builtin knowledge tool 可用性对齐（`capabilities.builtin_tools` + `meta.builtinTools.knowledge`）。
  - 补齐针对上述风险路径的 focused tests（TDD）。

## Checkpoints
1. **测试先行（Failing tests）**
   - 更新 `test_native_attached_knowledge_bypass_gate.py`：
     - gate helper 接收 `model` 并验证 builtin tool 能力门控。
     - 新增：
       - `builtin_tools=false` 时 gate 应返回 `False`。
       - `builtinTools.knowledge=false` 时 gate 应返回 `False`。
   - 更新 `test_attached_knowledge_native_flow.py`：
     - 新增混合附件场景：legacy handler 仅接收 regular file，不接收 knowledge attachment。
     - 新增两条能力回退场景：
       - `builtin_tools=false` 时不允许 bypass legacy handler。
       - `builtinTools.knowledge=false` 时不允许 bypass legacy handler。
   - 运行（预期失败）：
     - `/Users/liusihang/openwebui/.venv/bin/python -m pytest open_webui/test/util/test_native_attached_knowledge_bypass_gate.py open_webui/test/util/test_attached_knowledge_native_flow.py -q`
   - 结果：`9 failed`（包含 gate 签名不匹配 + 两个目标回归路径断言失败）。

2. **实现最小修复**
   - 修改 `backend/open_webui/utils/middleware.py`：
     - 新增 `has_native_builtin_knowledge_tools(model)`。
     - 新增 `is_scoped_knowledge_attachment(item)` 与 `split_files_for_native_scoped_knowledge_bypass(metadata)`。
     - `should_skip_legacy_file_retrieval_for_native_scoped_knowledge(...)` 增加 `model` 参数并纳入 capability/category gate。
     - `apply_legacy_file_retrieval_if_needed(...)` 调整为：
       - bypass 命中时仅剔除 scoped knowledge 附件；
       - 如果存在 regular files，则仅对 regular files 继续执行 `chat_completion_files_handler(...)`；
       - 如果没有 regular files，则跳过 legacy retrieval。

3. **回归验证（Focused suite）**
   - 运行：
     - `/Users/liusihang/openwebui/.venv/bin/python -m pytest open_webui/test/util/test_native_attached_knowledge_bypass_gate.py open_webui/test/util/test_attached_knowledge_native_flow.py -q`
   - 结果：`14 passed`
   - 运行（扩展聚焦范围）：
     - `/Users/liusihang/openwebui/.venv/bin/python -m pytest open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py open_webui/test/util/test_attached_knowledge_tool_resolution.py open_webui/test/util/test_effective_knowledge_scope.py open_webui/test/util/test_attached_knowledge_query_tool.py open_webui/test/util/test_layered_knowledge_tools.py open_webui/test/util/test_native_attached_knowledge_bypass_gate.py open_webui/test/util/test_attached_knowledge_native_flow.py -q`
   - 结果：`29 passed`

## 当前状态
- 三个 review finding 均已按 TDD 路径修复并通过 focused backend tests。
- 本次修改未触及 admin config/UI 结构。

## 会话复检（2026-04-11）
- 复检目标：确认该 worktree 当前代码是否仍处于“已完成”状态。
- 首次执行命令（在 `/Users/liusihang/openwebui/.worktrees/native-attached-knowledge-bypass`）：
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest backend/open_webui/test/util/test_native_attached_knowledge_bypass_gate.py backend/open_webui/test/util/test_attached_knowledge_native_flow.py -q`
- 首次结果：`12 failed, 2 passed`（报错为 `middleware` 缺少两个目标函数）。
- 根因定位：
  - 该执行上下文会导入主仓库模块 `/Users/liusihang/openwebui/backend/open_webui/utils/middleware.py`，而非当前 worktree 模块。
  - 导入目标验证：
    - 在 worktree 根目录直接 `import open_webui.utils.middleware`，`__file__` 指向主仓库路径。
    - 在 `PYTHONPATH=<worktree>/backend` 或进入 `<worktree>/backend` 后，`__file__` 才指向 worktree 路径。
- 纠偏后验证（在 `/Users/liusihang/openwebui/.worktrees/native-attached-knowledge-bypass/backend`）：
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest open_webui/test/util/test_native_attached_knowledge_bypass_gate.py open_webui/test/util/test_attached_knowledge_native_flow.py -q`
  - 结果：`14 passed`
- 扩展聚焦验证（同目录）：
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest open_webui/test/apps/webui/routers/test_retrieval_config_layer_generation.py open_webui/test/util/test_attached_knowledge_tool_resolution.py open_webui/test/util/test_effective_knowledge_scope.py open_webui/test/util/test_attached_knowledge_query_tool.py open_webui/test/util/test_layered_knowledge_tools.py open_webui/test/util/test_native_attached_knowledge_bypass_gate.py open_webui/test/util/test_attached_knowledge_native_flow.py -q`
  - 结果：`29 passed`
- 会话结论（纠偏后）：
  - 当前 worktree 对应目标在 focused 范围内已完成；失败来自错误执行路径，不是功能回归。

## 提交准备（2026-04-11）
- 目标：在 `84dd9163e feat: add native attached knowledge retrieval bypass` 基础上追加修复提交。
- 复验命令（在 `/Users/liusihang/openwebui/.worktrees/native-attached-knowledge-bypass/backend`）：
  - `/Users/liusihang/openwebui/.venv/bin/python -m pytest open_webui/test/util/test_native_attached_knowledge_bypass_gate.py open_webui/test/util/test_attached_knowledge_native_flow.py -q`
- 复验结果：`14 passed`
- 提交范围（仅相关文件）：
  - `backend/open_webui/utils/middleware.py`
  - `backend/open_webui/test/util/test_native_attached_knowledge_bypass_gate.py`
  - `backend/open_webui/test/util/test_attached_knowledge_native_flow.py`
  - `docs/plans/2026-04-11-openwebui-native-attached-knowledge-bypass-handoff.md`
