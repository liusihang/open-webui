"""Conversation-level routing contract for ordinary Chat and Agent Mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ConversationMode(StrEnum):
    CHAT = 'chat'
    AGENT = 'agent'


class ConversationModeError(ValueError):
    code = 'conversation_mode_error'


class InvalidConversationModeError(ConversationModeError):
    code = 'invalid_conversation_mode'

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__(f'Unsupported conversation mode: {value!r}')


class ConversationModeMismatchError(ConversationModeError):
    code = 'conversation_mode_mismatch'

    def __init__(
        self,
        *,
        requested: ConversationMode,
        persisted: ConversationMode,
    ) -> None:
        self.requested = requested
        self.persisted = persisted
        super().__init__(
            f'Conversation mode is {persisted.value!r}, not {requested.value!r}'
        )


@dataclass(frozen=True)
class ConversationModeResolution:
    mode: ConversationMode
    should_persist: bool


def normalize_conversation_mode(value: Any) -> ConversationMode:
    if isinstance(value, ConversationMode):
        return value
    if isinstance(value, str):
        try:
            return ConversationMode(value)
        except ValueError:
            pass
    raise InvalidConversationModeError(value)


def chat_has_agent_mode_evidence(chat: dict[str, Any] | None) -> bool:
    if not isinstance(chat, dict):
        return False

    history = chat.get('history')
    if not isinstance(history, dict):
        return False

    messages = history.get('messages')
    if isinstance(messages, dict):
        candidates = messages.values()
    elif isinstance(messages, list):
        candidates = messages
    else:
        return False

    return any(
        isinstance(message, dict) and bool(message.get('agent_run_id'))
        for message in candidates
    )


def resolve_conversation_mode(
    *,
    requested: Any,
    persisted: Any,
    is_new: bool,
    has_agent_run: bool = False,
) -> ConversationModeResolution:
    if is_new:
        mode = (
            ConversationMode.CHAT
            if requested is None
            else normalize_conversation_mode(requested)
        )
        return ConversationModeResolution(mode=mode, should_persist=True)

    should_persist = persisted is None
    canonical = (
        ConversationMode.AGENT
        if persisted is None and has_agent_run
        else ConversationMode.CHAT
        if persisted is None
        else normalize_conversation_mode(persisted)
    )

    if requested is not None:
        requested_mode = normalize_conversation_mode(requested)
        if requested_mode is not canonical:
            raise ConversationModeMismatchError(
                requested=requested_mode,
                persisted=canonical,
            )

    return ConversationModeResolution(
        mode=canonical,
        should_persist=should_persist,
    )


def normalize_new_conversation_chat(chat: dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_conversation_mode(
        requested=chat.get('mode'),
        persisted=None,
        is_new=True,
    )
    return {**chat, 'mode': resolution.mode.value}
