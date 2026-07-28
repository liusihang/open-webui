import json
from typing import Any

from sqlalchemy import text

from open_webui.internal.db import engine


CHAT_ID = "f251c1ab-4ee1-4142-99c4-279220d4379b"
NEEDLES = (
    "write complete text content to a file",
    "creating a new text file",
    "prefer apply_patch",
    "overwrite defaults to false",
)


def compact_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": payload}

    result: dict[str, Any] = {"keys": sorted(payload.keys())}
    for key in (
        "text",
        "delta",
        "content",
        "block_kind",
        "content_kind",
        "auxiliary_type",
        "tool_id",
        "tool_name",
        "tool_call_id",
        "status",
        "error",
        "arguments",
    ):
        if key in payload:
            value = payload[key]
            if isinstance(value, str) and len(value) > 600:
                value = value[:600] + "..."
            result[key] = value
    return result


with engine.connect() as connection:
    runs = connection.execute(
        text(
            """
            SELECT id, state, runtime_session_id, user_message_id,
                   assistant_message_id, created_at, updated_at, final_text
            FROM agent_run
            WHERE chat_id = :chat_id
            ORDER BY created_at
            """
        ),
        {"chat_id": CHAT_ID},
    ).mappings().all()

    print(json.dumps({"chat_id": CHAT_ID, "run_count": len(runs)}, ensure_ascii=False))
    for run in runs:
        print(
            json.dumps(
                {
                    "run": {
                        "id": run["id"],
                        "state": run["state"],
                        "runtime_session_id": run["runtime_session_id"],
                        "user_message_id": run["user_message_id"],
                        "assistant_message_id": run["assistant_message_id"],
                        "created_at": run["created_at"],
                        "updated_at": run["updated_at"],
                        "final_text_prefix": (run["final_text"] or "")[:300],
                    }
                },
                ensure_ascii=False,
            )
        )

        events = connection.execute(
            text(
                """
                SELECT seq, event_type, phase, summary, payload, created_at
                FROM agent_run_event
                WHERE run_id = :run_id
                ORDER BY seq
                """
            ),
            {"run_id": run["id"]},
        ).mappings().all()

        type_counts: dict[str, int] = {}
        matches: list[int] = []
        timeline: list[dict[str, Any]] = []
        for event in events:
            event_type = event["event_type"]
            type_counts[event_type] = type_counts.get(event_type, 0) + 1
            serialized = json.dumps(
                {
                    "summary": event["summary"],
                    "payload": event["payload"],
                },
                ensure_ascii=False,
            ).lower()
            if any(needle in serialized for needle in NEEDLES):
                matches.append(event["seq"])

            if (
                event_type.startswith("text.")
                or event_type.startswith("final.")
                or event_type.startswith("tool.")
                or event_type.startswith("approval.")
                or event_type.startswith("model_call.")
            ):
                timeline.append(
                    {
                        "seq": event["seq"],
                        "type": event_type,
                        "phase": event["phase"],
                        "summary": event["summary"],
                        "payload": compact_payload(event["payload"]),
                    }
                )

        print(
            json.dumps(
                {
                    "run_id": run["id"],
                    "event_count": len(events),
                    "event_type_counts": type_counts,
                    "schema_description_match_seqs": matches,
                    "timeline": timeline,
                },
                ensure_ascii=False,
            )
        )
