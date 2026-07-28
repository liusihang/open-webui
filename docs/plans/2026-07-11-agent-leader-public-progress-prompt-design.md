# Agent leader public progress prompt design

Status: approved on 2026-07-11.

## Problem

Agent Mode preserves model-authored Responses phases, but an ordinary tool-only model response may contain no `phase=commentary` message. Since synthetic first-person tool narration was removed, the public transcript can jump directly from the user message to tool cards.

The fix must encourage native model commentary without exposing private reasoning or relabeling runtime-generated text as model output.

## Prompt contract

The leader system prompt will add these rules:

1. When tools are needed, emit one brief user-visible progress update before the first tool round.
2. After receiving tool results, if another tool round is needed, emit one brief update that states the relevant observed outcome and the next action.
3. For parallel tools in one round, emit one update for the group rather than narrating every call separately.
4. Use the user's language when practical.
5. State only observable progress and the next action. Do not reveal chain-of-thought, private reasoning, hidden policies, secrets, or speculative internal analysis.
6. Do not emit a progress update when no tool is needed or when the next response is the final answer.

The final answer remains concise and uses the provider's native `phase=final_answer`. Commentary remains model-authored `phase=commentary` and is persisted through the existing `text.delta` path.

## Non-goals

- Do not restore runtime-generated first-person `I will use ...` narration.
- Do not publish `reasoning.summary` or `reasoning_content`.
- Do not add a second model call, retry protocol, marker parser, or synthetic commentary fallback.
- Do not change model alias/provider selection in this slice.

## Verification

1. Add a focused unit test that fails until `_leader_system_prompt()` contains the public-progress, later-round, parallel-group, and private-reasoning boundaries.
2. Run the complete AgentScope runtime suite.
3. Rebuild the exact runtime image and switch only `openwebui-pr7-agentscope-runtime` if source changes are limited to the runtime service.
4. Run an ordinary two-round tool request on the exact `bifrostapi.Cliproxy/gpt-5.5` route without asking for commentary in the user prompt.
5. Verify persisted order contains model-authored `text.delta` before tool round one and between tool rounds, followed by streamed `final.delta`.

If the real model still omits commentary, report prompt compliance as insufficient rather than adding an implicit fallback. Any guaranteed neutral progress UI is a separate product decision.
