# Open WebUI Model Governance API Contract

Base prefix: `/api/v1/models`

## 1) List Models

- `GET /list?page=<int>`
- Response: `{ "items": [...], "total": <int> }`
- This skill script paginates automatically.

## 2) Update Capabilities

- `POST /model/capabilities/update`
- Body:

```json
{
  "id": "openai.gpt-4o-mini",
  "capabilities": {
    "web_search": true,
    "code_interpreter": false,
    "deep_research": true
  }
}
```

Notes:
- Values can be `true/false`.
- Setting key to `null` removes that capability key.

## 3) Update System Prompt

- `POST /model/prompt/system/update`
- Body:

```json
{
  "id": "openai.gpt-4o-mini",
  "system": "You are a strict code reviewer."
}
```

Notes:
- `system: null` clears `params.system`.

## 4) Update Access Grants (Public/Private)

- `POST /model/access/update`
- Body:

```json
{
  "id": "openai.gpt-4o-mini",
  "access_grants": [
    {
      "id": "uuid",
      "principal_type": "user",
      "principal_id": "*",
      "permission": "read"
    }
  ]
}
```

Public rule:
- Public read is represented by `user:*:read`.

Private rule:
- Remove `user:*:read` and keep other grants.

## 5) Update Icon

- `POST /model/icon/update`
- Body:

```json
{
  "id": "openai.gpt-4o-mini",
  "profile_image_url": "https://example.com/icon.png"
}
```

Notes:
- Empty string falls back to `/static/favicon.png` on server.

## 6) Update Model Name

- `POST /model/update`
- Body must include full model form fields (`id`, `name`, `meta`, `params`, etc.).
- This skill script builds the payload from current list data.

## Auth

Use Bearer token:

```bash
Authorization: Bearer <token>
```

The script supports:
- `OPEN_WEBUI_BASE_URL` (default `http://localhost:8080`)
- `OPEN_WEBUI_API_TOKEN`
