from __future__ import annotations

from open_webui.agent.model_authority import AgentModelAuthority, ModelCallRequest


async def execute_agent_model_call(
    authority: AgentModelAuthority,
    request,
    call: ModelCallRequest,
) -> dict:
    return await authority.execute_model_call(request, call)
