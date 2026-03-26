from urllib.parse import urlencode


def build_upstream_terminal_ws_request(
    connection: dict,
    session_id: str,
    user_id: str,
    client_token: str,
) -> tuple[str, dict | None]:
    base_url = (connection.get("url") or "").rstrip("/")
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")

    auth_type = connection.get("auth_type", "bearer")
    params = {"user_id": user_id}
    first_message = None

    if auth_type == "session" and client_token:
        params["token"] = client_token
    elif auth_type == "bearer":
        first_message = {"type": "auth", "token": connection.get("key", "")}

    upstream_url = f"{ws_base}/api/terminals/{session_id}"
    if params:
        upstream_url += f"?{urlencode(params)}"

    return upstream_url, first_message
