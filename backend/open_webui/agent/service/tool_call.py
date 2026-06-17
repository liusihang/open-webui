from __future__ import annotations

from open_webui.agent.tool_authority import AgentToolAuthority, ToolCallRequest


async def execute_agent_tool_call(
    authority: AgentToolAuthority,
    request: ToolCallRequest,
) -> dict:
    return await authority.execute_tool_call(request)
