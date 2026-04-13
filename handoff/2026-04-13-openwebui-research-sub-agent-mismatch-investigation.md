# Handoff - Research Sub Agent 检索结论不一致排查

- Date: 2026-04-13
- Workspace: /Users/liusihang/openwebui
- Goal: 排查会话 `928158e8-9101-40fd-8404-dd8863cc0f8d` 中“检索资料显示存在该病毒，但 subagent 给出不存在结论”的根因，判断是否为工具设计问题。

## Checkpoints

1. Checkpoint: 确认目标会话与现象
- Action: 用户提供具体 chat 链接与症状描述。
- Evidence: `https://ai.shuofang.cloud/c/928158e8-9101-40fd-8404-dd8863cc0f8d`。
- Result: 排查对象明确。

2. Checkpoint: 拉取会话原始执行轨迹
- Action: 调用 OpenWebUI Chat API 导出该 chat 的 messages/output/tool calls。
- Result: in progress。

## Next
- 对齐以下链路：用户问题 -> subagent tool call 入参 -> search_web/fetch_url 返回 -> subagent final evidence_text -> 主模型最终答复。
- 判断是“证据不可见”“证据被覆盖”“推理层忽略证据”还是“检索质量/召回问题”。

3. Checkpoint: 验证 subagent 是否可见检索结果
- Action: 解析目标 assistant 消息 `67af5bf4-fd39-4f16-8854-dc175b8b69b8` 的 `statusHistory` 与 `output.function_call_output`。
- Evidence:
  - `statusHistory` 显示 3 次 `search_web` 调用及结果片段回传：
    - Step1 query: `"Resentoviricetes" ICTV classification`
    - Step2 query: `"Resentoviricetes" virus classification ICTV`
    - Step3 query: `"Resentoviricetes" virus`
  - `function_call_output` 的 `sources` 中包含明确“存在该分类”的文档片段：
    - Wikispecies: `Classis: Resentoviricetes`
    - iNaturalist: `Class Resentoviricetes`
- Result: subagent **能看到**搜索结果。

4. Checkpoint: 验证结论与证据是否冲突
- Action: 对比同一 `function_call_output` 的 `evidence_text` 与 `sources`。
- Evidence:
  - `evidence_text` 声称：`Resentoviricetes 并非公认分类名称/不存在`。
  - 同一输出 `sources` 提供了“Resentoviricetes 作为 class 存在”的检索片段。
- Result: 出现“同一轮输出内证据-结论矛盾”。

5. Checkpoint: 排查工具链是否丢失 tool 结果注入
- Action: 审查当前线上工具源码（回读快照 `/tmp/research_sub_agent_plus.content.after_hotfix.py`）的 `run_sub_agent_loop`。
- Evidence:
  - 工具执行结果会作为 `{"role":"tool","content":...}` 追加进 `current_messages` 后继续推理。
  - 不存在“工具结果未注入模型上下文”的代码路径异常。
- Result: 不是“看不到结果”的工程 bug，更接近“模型推理层未遵循证据”的可靠性问题。

6. Checkpoint: 识别触发条件（配置侧）
- Action: 读取 live valves `/api/v1/tools/id/research_sub_agent_plus/valves`。
- Evidence:
  - `RESEARCH_MODEL=googlegemini-31-flash-lite-preview-academicsubagent`
  - `MAX_ITERATIONS=3`
  - 仅执行了 `search_web`，未执行 `fetch_url` 深读。
- Result: 当前配置更容易出现“浅检索 + 快速下结论”的错判，尤其是需要权威分类核验的问题。

## Interim Root Cause
- 这次主要是**子代理推理可靠性问题**，不是“工具看不到搜索结果”。
- 工具设计层面的薄弱点在于：
  1) 缺少“证据-结论一致性校验”护栏；
  2) 对“不存在”这类强否定结论没有“必须有权威来源/深读确认”的强制门槛；
  3) 在 `MAX_ITERATIONS=3` + 轻量模型配置下，容易跳过 fetch 深读并过早定性。

## Artifacts
- Chat snapshot: `/tmp/chat_928158e8.raw`
- Parsed chat json: `/tmp/chat_928158e8.json`
- Tool code snapshot: `/tmp/research_sub_agent_plus.content.after_hotfix.py`
- Valves snapshot: `/tmp/research_sub_agent_plus.valves.json`

7. Checkpoint: 核验模型参数是否可能影响该问题
- Action: 拉取主对话模型与子代理模型配置：
  - `GET /api/v1/models/model?id=bifrostapi.ZenMuxOAI/openai/gpt-5.4`
  - `GET /api/v1/models/model?id=googlegemini-31-flash-lite-preview-academicsubagent`
- Evidence:
  - 主模型 params: `top_p=0.75`
  - 子代理模型 params: `temperature=0.45`, `top_p=0.65`
- Result: 参数会影响稳定性与结论保守性，但不构成“看不到检索结果”的解释。

8. Checkpoint: 回答“是否可能没看到搜索结果”
- Action: 复核同一条 assistant 消息的 `statusHistory` 与 `function_call_output`。
- Evidence:
  - `statusHistory` 明确记录 3 次 `search_web` 调用和返回摘要。
  - `function_call_output` 中 `sources` 保留了搜索命中内容。
- Result: 子代理看到了结果；问题是证据约束不足导致的推理偏差。

9. Checkpoint: 设计“同证据直测模型”实验
- Action: 从目标会话中提取 subagent 实际看到的 `sources.document` 作为固定 evidence。
- Artifact: `/tmp/resento_sources.json`
- Goal: 用完全相同证据直接喂给子代理模型，排除“检索不可见”因素，观察结论稳定性。

10. Checkpoint: 同证据直测子代理模型（批量）
- Action: 使用 `googlegemini-31-flash-lite-preview-academicsubagent` 直接调用 `/api/chat/completions`，把会话中提取的同一份 evidence（`/tmp/resento_sources.json`）喂给模型做批量测试。
- Design:
  - 条件A `strict_default`：严格三行 verdict 模板（默认参数）
  - 条件B `strict_temp0`：严格模板（temperature=0.0, top_p=1.0）
  - 条件C `strict_temp09`：严格模板（temperature=0.9, top_p=1.0）
  - 条件D `loose_default`：宽松问法（默认参数）
  - 条件E `loose_temp09`：宽松问法（temperature=0.9, top_p=1.0）
  - 每条件 8 次，共 40 次。
- Evidence files:
  - 原始结果：`/tmp/resento_direct_model_experiment.json`
  - 复核重标：`/tmp/resento_direct_model_experiment_relabel.json`
- Result:
  - 复核后五个条件均为 `8/8 EXISTS`。
  - 单次直测示例也为：`verdict: EXISTS`。

11. Checkpoint: 参数影响判断
- Action: 对比不同温度/采样条件下同证据输出。
- Result:
  - 在该实验中，调参（含高温）未触发“NOT_FOUND”结论。
  - 说明“参数可影响稳定性”是成立的，但**不足以解释本次会话中的反向结论**。
  - 更可能是 subagent 多轮工具链下的综合推理偏差（迭代上下文/指令冲突/早停）而非 evidence 可见性或单次模型基线能力问题。

12. Checkpoint: 线上工具源码与配置复核（以 live 为准）
- Action:
  - `GET /api/v1/tools/id/research_sub_agent_plus` 导出 live content 到 `/tmp/research_sub_agent_plus.content.latest.py`
  - `GET /api/v1/tools/id/research_sub_agent_plus/valves` 导出 live valves 到 `/tmp/research_sub_agent_plus.valves.latest.json`
- Evidence:
  - live `run_sub_agent_loop` 仍将工具结果追加为 `{"role":"tool",...}` 后继续推理（见 `/tmp/research_sub_agent_plus.content.latest.py` around lines 1088-1155）
  - live `_run_mode_sub_agent` 的 model 选择优先级为 `RESEARCH_MODEL -> metadata.model.id -> __model__.id -> DEFAULT_MODEL`（见 `/tmp/research_sub_agent_plus.content.latest.py` around lines 1963+）
- Result:
  - 本轮排查以线上代码为基准，和本地快照关键逻辑一致。

13. Checkpoint: 线上模型可用性与 valves 对齐检查
- Action:
  - 拉取 `/api/v1/models` 并与 valves 的 `RESEARCH_MODEL` 对比。
- Evidence:
  - `RESEARCH_MODEL=googlegemini-31-flash-lite-preview-academicsubagent`
  - 在 `/api/v1/models` 中存在 **exact match**（计数=1）。
- Result:
  - 本案例并非“subagent 模型 ID 不存在/拼写不匹配”导致。

14. Checkpoint: 直接复核目标会话最新 subagent 执行轨迹
- Action:
  - `GET /api/v1/chats/928158e8-9101-40fd-8404-dd8863cc0f8d` 保存到 `/tmp/chat_928158e8.latest.json`
  - 解析最新 assistant 消息 `c2f2fa34-2353-4e12-a95e-9a4202fbcc49` 的 `statusHistory` 与 `function_call_output`。
- Evidence:
  - 子代理第一轮查询参数：`{"query":"\"Resentoviricetes\" ICTV NCBI"}`
  - 同轮 tool result 片段中出现 NCBI taxonomy 内容：`Lineage ... * Resentoviricetes ...`
  - 对应 `function_call_output` (`call_UZDv93...`) 的 `evidence_text` 却写出：`不存在`，并转而猜测 `Renoviricetes`。
- Result:
  - 已在线上实锤：**检索命中内容确实被传入了 subagent 上下文**，但 subagent 输出仍出现“证据-结论冲突”。

15. Checkpoint: 回答“为什么模型还是回答不存在”
- Action: 基于 live 轨迹进行因果归因。
- Root cause:
  - 不是结果传递丢失；是 subagent 在检索到冲突/混杂来源时，做了不可靠归纳（将 `Resentoviricetes` 误判为拼写错误并替换为 `Renoviricetes`）。
  - 当前工具缺少“结论与证据一致性约束”与“强否定结论门槛（必须权威来源显式否定）”。
- Result:
  - 问题本质仍是**推理可靠性缺少护栏**，而非“搜索结果没传过去”。

## New Artifacts (Live)
- `/tmp/research_sub_agent_plus.tool.latest.json`
- `/tmp/research_sub_agent_plus.content.latest.py`
- `/tmp/research_sub_agent_plus.valves.latest.json`
- `/tmp/openwebui_models.latest.json`
- `/tmp/chat_928158e8.latest.json`
- `/tmp/call_UZD_output_text.json`
- `/tmp/call_dGdm_output_text.json`

## Pending
- 若继续修复，优先最小改动：在 subagent system prompt/后处理增加“证据-结论一致性检查”，对“NOT EXISTS/不存在”结论增加硬性门槛（至少 1 条权威来源显式否定，且无正向命中证据）。
