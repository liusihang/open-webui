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

16. Checkpoint: 回答“单独问不出错，但 subagent 会错”的机制差异
- Action: 对比“单次直问模型（固定证据）”与“subagent 多轮工具调用”两种执行形态。
- Why single-shot usually correct:
  1) 输入更短、更干净，证据冲突少；
  2) 没有多轮工具输出噪声与长网页碎片干扰；
  3) 不受迭代结束策略（MAX_ITERATIONS）和“尽快收敛”提示影响。
- Why subagent can fail:
  1) `search_web` 返回的大段噪声文本会稀释关键证据；
  2) 同轮可能混入质量不一来源，模型做了“拼写纠正”启发式（把 Resentoviricetes 误判成 Renoviricetes）；
  3) 多轮+限步会放大“过早定性”风险；
  4) 当前工具缺少“证据-结论一致性校验”硬约束。
- Result:
  - 这是典型的“多步代理编排误差”而非“基座模型单问能力不足”。

17. Checkpoint: 明确子代理“回答前看到的原始内容”
- Action: 从 live chat `928158e8` 的 assistant 消息 `c2f2fa34...` 中提取 `function_call` 入参 + `statusHistory`，并对照 live 工具源码的消息组装逻辑。
- Raw inputs（按 run_sub_agent_loop 真实结构）:
  1) system: `RESEARCH_SYSTEM_PROMPT`（工具 UserValves 默认研究提示词）
  2) user: `run_research_sub_agent.prompt`（主模型传给子代理的任务提示）
  3) 每轮额外 system: `[Iteration i/N] ...`（含“最后一轮请直接给结论”）
  4) 若调用工具：追加
     - assistant(tool_calls + content)
     - tool(content=result原文)
- This run 的具体值（第一轮子代理）:
  - description: `核实病毒分类名Resentoviricetes是否为真实且当前有效的分类单元`
  - prompt: `请检索可靠来源，核实“Resentoviricetes”是否为真实存在的病毒分类单元...`
  - Step1 tool args: `{"query":"\"Resentoviricetes\" ICTV NCBI"}`
  - Step1 tool result 原文片段含：`Taxonomy browser (Resentoviricetes)`、`Lineage ... * Resentoviricetes ...`
- Result:
  - 子代理回答前看到的并不是“只有用户一句话”，而是“系统提示词 + 用户任务 + 迭代提示 + 已执行工具原文结果”。

18. Checkpoint: 量化“子代理最终判断前看到的 token 规模”
- Action:
  - 从消息 `c2f2fa34...` 提取第一次子代理（Resentoviricetes）的 3 次 `Step Result` 原文与 `prompt`。
  - 按 `run_sub_agent_loop` 的真实 final-call 结构重建 messages（system + user + 3轮 assistant/tool + final user instruction）。
  - 用项目 `.venv` 的 `tiktoken(cl100k_base)` 估算 token。
- Estimated tokens (cl100k_base):
  - Step1 tool result: `24021`
  - Step2 tool result: `12461`
  - Step3 tool result: `2555`
  - Tool result合计: `39037`
  - 最终判断请求（重建 payload JSON）总计: `41187`
  - 同一消息仅 content 串联估算: `39746`
- Note:
  - 这是基于 cl100k 的近似值；实际模型（Gemini 路由）侧 tokenizer 可能有偏差（通常几个百分点到十几个百分点）。

19. Checkpoint: 真实测量“最终判断前 token”而非 tiktoken 估算
- Goal: 用线上同模型真实返回的 `usage.prompt_tokens` 验证“是否因为上下文过长导致幻觉”。
- Action:
  - 读取 live chat `928158e8` 中消息 `67af5bf4...`（即出现冲突结论的那轮）。
  - 从 `statusHistory` 提取 Step1/2/3 的 `Tool calls/Args/Result`，按 `run_sub_agent_loop` 结构重建 replay payload：
    - `system(RESEARCH_SYSTEM_PROMPT)`
    - `user(prompt)`
    - 3 组 `assistant(tool_calls)` + `tool(result)`
    - 末尾 `user("Maximum tool iterations reached...")`
  - 直接调用线上 `/api/chat/completions`，模型 `googlegemini-31-flash-lite-preview-academicsubagent`。
- Evidence:
  - 重建 payload: `/tmp/subagent_67af_replay_payload.json`
  - 线上响应: `/tmp/subagent_67af_replay_resp.json`
  - 实测 `usage.prompt_tokens=1572`（input_tokens 同值）。
- Result:
  - 与先前“~4万 tokens”估算不一致；真实链路下该 replay 请求不是 4w 量级。

20. Checkpoint: 对照实验（同文本，改成普通 user 文本）
- Goal: 判断低 token 是否来自“文本本身短”，还是“tool/result 在链路中被特殊处理”。
- Action:
  - 把同样三段 Step Result 从 `tool` 角色改为普通 `user` 文本（其余保持一致）后再次请求同模型。
- Evidence:
  - payload: `/tmp/subagent_67af_replay_payload_userized.json`
  - response: `/tmp/subagent_67af_replay_resp_userized.json`
  - 实测 `usage.prompt_tokens=9945`。
- Result:
  - 同内容在普通文本路径下 token 明显更高；说明 `tool/function_call_output` 路径存在显著压缩/截断/忽略差异。

21. Checkpoint: function_call_output 长度阈值探测（真实线上）
- Goal: 验证是否存在 `tool` 输出长度阈值，超过后内容不再有效进入模型上下文。
- Action:
  - 构造固定模板请求，仅改变 `role=tool` 的 `content` 长度，逐点测 `usage.prompt_tokens`。
- Evidence:
  - 随机词文本测试：
    - 1000 chars -> `prompt_tokens=1053`
    - 1199 chars -> `prompt_tokens=1090`
    - 1200 chars -> `prompt_tokens=835`
    - 1201 chars -> `prompt_tokens=835`
  - 复测文件：`/tmp/_tool_threshold.json`, `/tmp/_tool_threshold2.json`, `/tmp/_tool_threshold3.json`
- Result:
  - 实测出现接近“硬阈值”行为：`tool content >= 1200 chars` 时 token 贡献骤降到近基线。
  - 这更像链路侧对 `function_call_output` 的截断/替换，而不是模型自然 tokenizer 结果。

22. Checkpoint: 对“是否因上下文太多导致幻觉”的结论更新
- Conclusion:
  1) 从真实 `usage.prompt_tokens` 看，这轮并非 4w token 级别上下文拥塞。
  2) 更可疑的是 `tool/function_call_output` 在链路中的长度阈值/压缩策略（约 1200 chars）导致证据未完整进入最终判断。
  3) 因此你的“上下文太多”怀疑需要改写为：
     - 不是“完整上下文过大”，而是“工具结果进入模型时被截断或压缩失真”，进而诱发错判。

## New Artifacts (2026-04-13 Real Token Tests)
- `/tmp/subagent_67af_replay_payload.json`
- `/tmp/subagent_67af_replay_resp.json`
- `/tmp/subagent_67af_replay_payload_userized.json`
- `/tmp/subagent_67af_replay_resp_userized.json`
- `/tmp/subagent_tool_single_payload.json`
- `/tmp/subagent_tool_single_resp.json`
- `/tmp/subagent_tool_single_baseline.json`
- `/tmp/subagent_tool_single_baseline_resp.json`
- `/tmp/_tool_threshold.json`
- `/tmp/_tool_threshold2.json`
- `/tmp/_tool_threshold3.json`
