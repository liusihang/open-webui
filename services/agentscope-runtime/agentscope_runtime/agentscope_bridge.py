from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from agentscope.app import SubAgentTemplate
from agentscope.credential import CredentialBase
from agentscope.message import Msg, TextBlock, ToolResultState
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


OPENWEBUI_SUBAGENT_SYSTEM_PROMPT = """You are {member_name}, an OpenWebUI-governed \
subagent in team '{team_name}' led by {leader_name}.

Team purpose: {team_description}

Your role: {member_description}

Use only OpenWebUI-governed model and tool callbacks supplied by the runtime. \
Do not expect direct provider credentials, user JWTs, terminal keys, MCP \
secrets, or raw tool server credentials inside this AgentScope runtime."""


class OpenWebUIBridgeCallbacks(Protocol):
    async def call_model(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        model_call_id: str,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool,
        params: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    async def call_tool(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        participant_id: str,
        tool_call_id: str,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class AgentScopeAPISurfaces:
    subagent_template_cls: type[SubAgentTemplate]
    chat_model_base_cls: type[ChatModelBase]
    chat_response_cls: type[ChatResponse]
    tool_base_cls: type[ToolBase]
    tool_chunk_cls: type[ToolChunk]


def verify_agentscope_runtime_apis() -> AgentScopeAPISurfaces:
    required_template_fields = {"type", "description", "system_prompt_template"}
    missing_fields = required_template_fields - set(SubAgentTemplate.model_fields)
    if missing_fields:
        raise RuntimeError(
            "AgentScope SubAgentTemplate API drifted; missing fields: "
            + ", ".join(sorted(missing_fields))
        )

    if not hasattr(ChatModelBase, "_call_api"):
        raise RuntimeError("AgentScope ChatModelBase API drifted; _call_api missing")
    if not hasattr(ToolBase, "check_permissions"):
        raise RuntimeError("AgentScope ToolBase API drifted; check_permissions missing")

    return AgentScopeAPISurfaces(
        subagent_template_cls=SubAgentTemplate,
        chat_model_base_cls=ChatModelBase,
        chat_response_cls=ChatResponse,
        tool_base_cls=ToolBase,
        tool_chunk_cls=ToolChunk,
    )


class OpenWebUICallbackCredential(CredentialBase):
    name: str = "OpenWebUI callback authority"

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        return OpenWebUIAgentScopeModel


class OpenWebUIAgentScopeModel(ChatModelBase):
    class Parameters(BaseModel):
        pass

    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        participant_id: str,
        model_id: str,
        callback_client: OpenWebUIBridgeCallbacks,
    ) -> None:
        super().__init__(
            credential=OpenWebUICallbackCredential(),
            model=model_id,
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.participant_id = participant_id
        self.callback_client = callback_client
        self._next_model_call_index = 1

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        model_call_id = self._allocate_model_call_id()
        params = dict(kwargs)
        if tools is not None:
            params["tools"] = tools
        if tool_choice is not None:
            params["tool_choice"] = _jsonable(tool_choice)

        response = await self.callback_client.call_model(
            run_id=self.run_id,
            idempotency_key=f"model:{self.participant_id}:{model_call_id}:1",
            participant_id=self.participant_id,
            model_call_id=model_call_id,
            model=model_name,
            messages=[message.model_dump(mode="json") for message in messages],
            stream=False,
            params=params,
            metadata={
                "runtime_session_id": self.runtime_session_id,
                "agentscope_bridge": True,
            },
        )
        return ChatResponse(
            content=[TextBlock(text=_extract_model_text(response))],
            is_last=True,
            metadata={
                "openwebui_response": response.get("metadata", {}),
                "participant_id": self.participant_id,
                "model_call_id": model_call_id,
            },
        )

    def _allocate_model_call_id(self) -> str:
        model_call_id = f"model-call-{self._next_model_call_index}"
        self._next_model_call_index += 1
        return model_call_id


class OpenWebUIToolProxy(ToolBase):
    is_concurrency_safe = False
    is_read_only = False

    def __init__(
        self,
        *,
        run_id: str,
        participant_id: str,
        tool_id: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        callback_client: OpenWebUIBridgeCallbacks,
    ) -> None:
        self.run_id = run_id
        self.participant_id = participant_id
        self.tool_id = tool_id
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.callback_client = callback_client
        self._next_tool_call_index = 1

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="OpenWebUI tool authority will enforce permissions.",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        tool_call_id = self._allocate_tool_call_id()
        response = await self.callback_client.call_tool(
            run_id=self.run_id,
            idempotency_key=f"tool:{self.participant_id}:{tool_call_id}:1",
            participant_id=self.participant_id,
            tool_call_id=tool_call_id,
            tool_id=self.tool_id,
            arguments=kwargs,
        )
        return ToolChunk(
            content=[TextBlock(text=str(response.get("content") or ""))],
            state=_tool_result_state(response),
            metadata={
                "openwebui_response": response,
                "participant_id": self.participant_id,
                "tool_call_id": tool_call_id,
                "tool_id": self.tool_id,
            },
        )

    def _allocate_tool_call_id(self) -> str:
        tool_call_id = f"tool-call-{self._next_tool_call_index}"
        self._next_tool_call_index += 1
        return tool_call_id


class AgentScopeRuntimeBridge:
    def __init__(
        self,
        *,
        run_id: str,
        runtime_session_id: str,
        callback_client: OpenWebUIBridgeCallbacks,
    ) -> None:
        verify_agentscope_runtime_apis()
        self.run_id = run_id
        self.runtime_session_id = runtime_session_id
        self.callback_client = callback_client

    def build_subagent_template(
        self,
        *,
        template_type: str,
        description: str,
        system_prompt_template: str = OPENWEBUI_SUBAGENT_SYSTEM_PROMPT,
    ) -> SubAgentTemplate:
        return SubAgentTemplate(
            type=template_type,
            description=description,
            system_prompt_template=system_prompt_template,
        )

    def build_model(
        self,
        *,
        participant_id: str,
        model_id: str,
    ) -> OpenWebUIAgentScopeModel:
        return OpenWebUIAgentScopeModel(
            run_id=self.run_id,
            runtime_session_id=self.runtime_session_id,
            participant_id=participant_id,
            model_id=model_id,
            callback_client=self.callback_client,
        )

    def build_tool_proxy(
        self,
        *,
        participant_id: str,
        tool_id: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> OpenWebUIToolProxy:
        return OpenWebUIToolProxy(
            run_id=self.run_id,
            participant_id=participant_id,
            tool_id=tool_id,
            name=name,
            description=description,
            input_schema=input_schema,
            callback_client=self.callback_client,
        )


def _extract_model_text(response: dict[str, Any]) -> str:
    payload = response.get("response", response)
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(_content_item_text(item) for item in content)
    content = response.get("content")
    return content if isinstance(content, str) else str(payload)


def _content_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _tool_result_state(response: dict[str, Any]) -> ToolResultState:
    status = response.get("status")
    if status == "success":
        return ToolResultState.SUCCESS
    if status == "cancelled":
        return ToolResultState.INTERRUPTED
    if status == "approval_rejected":
        return ToolResultState.DENIED
    return ToolResultState.ERROR


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
