from __future__ import annotations

import pytest

from open_webui.agent.conversation_mode import (
    ConversationMode,
    ConversationModeMismatchError,
    InvalidConversationModeError,
    chat_has_agent_mode_evidence,
    resolve_conversation_mode,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("chat", ConversationMode.CHAT),
        ("agent", ConversationMode.AGENT),
        (ConversationMode.CHAT, ConversationMode.CHAT),
        (ConversationMode.AGENT, ConversationMode.AGENT),
    ],
)
def test_new_conversation_accepts_supported_modes(raw, expected) -> None:
    resolution = resolve_conversation_mode(
        requested=raw,
        persisted=None,
        is_new=True,
    )

    assert resolution.mode is expected
    assert resolution.should_persist is True


def test_new_conversation_without_mode_defaults_to_chat() -> None:
    resolution = resolve_conversation_mode(
        requested=None,
        persisted=None,
        is_new=True,
    )

    assert resolution.mode is ConversationMode.CHAT
    assert resolution.should_persist is True


@pytest.mark.parametrize("raw", ["", "work", "AGENT", 1, {}, []])
def test_invalid_requested_mode_is_rejected(raw) -> None:
    with pytest.raises(InvalidConversationModeError) as exc_info:
        resolve_conversation_mode(
            requested=raw,
            persisted=None,
            is_new=True,
        )

    assert exc_info.value.code == "invalid_conversation_mode"

def test_existing_conversation_uses_persisted_mode_when_request_omits_it() -> None:
    resolution = resolve_conversation_mode(
        requested=None,
        persisted="agent",
        is_new=False,
    )

    assert resolution.mode is ConversationMode.AGENT
    assert resolution.should_persist is False


def test_existing_conversation_accepts_matching_requested_mode() -> None:
    resolution = resolve_conversation_mode(
        requested="chat",
        persisted="chat",
        is_new=False,
    )

    assert resolution.mode is ConversationMode.CHAT
    assert resolution.should_persist is False


def test_existing_conversation_rejects_requested_mode_mismatch() -> None:
    with pytest.raises(ConversationModeMismatchError) as exc_info:
        resolve_conversation_mode(
            requested="agent",
            persisted="chat",
            is_new=False,
        )

    assert exc_info.value.code == "conversation_mode_mismatch"
    assert exc_info.value.requested is ConversationMode.AGENT
    assert exc_info.value.persisted is ConversationMode.CHAT


def test_legacy_conversation_with_agent_run_resolves_to_agent() -> None:
    resolution = resolve_conversation_mode(
        requested=None,
        persisted=None,
        is_new=False,
        has_agent_run=True,
    )

    assert resolution.mode is ConversationMode.AGENT
    assert resolution.should_persist is True


def test_legacy_conversation_with_agent_message_resolves_to_agent() -> None:
    chat = {
        "history": {
            "messages": {
                "assistant-1": {
                    "role": "assistant",
                    "agent_run_id": "run-1",
                }
            }
        }
    }

    assert chat_has_agent_mode_evidence(chat) is True

    resolution = resolve_conversation_mode(
        requested="agent",
        persisted=None,
        is_new=False,
        has_agent_run=chat_has_agent_mode_evidence(chat),
    )

    assert resolution.mode is ConversationMode.AGENT
    assert resolution.should_persist is True


def test_legacy_conversation_without_agent_evidence_resolves_to_chat() -> None:
    chat = {
        "history": {
            "messages": {
                "assistant-1": {
                    "role": "assistant",
                    "content": "ordinary response",
                }
            }
        }
    }

    assert chat_has_agent_mode_evidence(chat) is False

    resolution = resolve_conversation_mode(
        requested=None,
        persisted=None,
        is_new=False,
        has_agent_run=chat_has_agent_mode_evidence(chat),
    )

    assert resolution.mode is ConversationMode.CHAT
    assert resolution.should_persist is True


def test_legacy_agent_conversation_rejects_chat_request() -> None:
    with pytest.raises(ConversationModeMismatchError):
        resolve_conversation_mode(
            requested="chat",
            persisted=None,
            is_new=False,
            has_agent_run=True,
        )


def test_invalid_persisted_mode_is_rejected_as_corrupt_data() -> None:
    with pytest.raises(InvalidConversationModeError) as exc_info:
        resolve_conversation_mode(
            requested=None,
            persisted="work",
            is_new=False,
        )

    assert exc_info.value.code == "invalid_conversation_mode"
