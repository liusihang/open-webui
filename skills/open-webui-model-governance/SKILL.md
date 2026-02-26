---
name: open-webui-model-governance
description: Use this skill when the user asks to batch-manage Open WebUI models: toggle capabilities, set/clear unified system prompts, switch public/private visibility, update icons in bulk, and normalize model display names with consistent rules.
---

# Open WebUI Model Governance

## Overview

Use this skill for large-scale model governance in Open WebUI.
Default execution path is script-first with `scripts/model_governance.py` to minimize token usage and avoid hand-writing repetitive API calls.

## When This Skill Should Trigger

Trigger this skill when the user asks for any of the following across one or many models:
- Turn model features on/off (`capabilities`)
- Set the same system prompt for a model group
- Switch model visibility between public/private
- Standardize model icons
- Normalize display names with one naming style

## Workflow

1. Set API env vars.
2. Use `list` + selectors to preview exact target set.
3. Run the intended operation with `--dry-run` first.
4. Execute for real after preview is correct.
5. Re-run `list` to verify result.

## Script

Path: `scripts/model_governance.py`

Required auth:
- `OPEN_WEBUI_API_TOKEN` or `--token`

Optional env:
- `OPEN_WEBUI_BASE_URL` (default: `http://localhost:8080`)

### Common selectors

- `--all`
- `--id <model-id>` (repeatable)
- `--id-file <path>`
- `--id-regex <regex>`
- `--name-regex <regex>`
- `--provider <provider>` (repeatable)
- `--contains <substring>` (repeatable)
- `--active-only` or `--inactive-only`
- `--limit <n>`

Safety rule:
- The script refuses to run without selectors. Use `--all` explicitly if you truly want all models.

## Commands

### 1) Preview models

```bash
python3 scripts/model_governance.py list --provider openai --active-only
```

### 2) Toggle capabilities

```bash
python3 scripts/model_governance.py capabilities \
  --provider qwen \
  --set web_search=true \
  --set deep_research=false \
  --remove code_interpreter \
  --dry-run
```

```bash
python3 scripts/model_governance.py capabilities \
  --provider qwen \
  --set web_search=true \
  --set deep_research=false \
  --remove code_interpreter
```

### 3) Unified system prompt

```bash
python3 scripts/model_governance.py system-prompt \
  --id-regex '^openai_native_files_manifold_pipe\\.' \
  --file /tmp/system_prompt.txt \
  --dry-run
```

Clear prompt:

```bash
python3 scripts/model_governance.py system-prompt \
  --provider openai \
  --clear
```

### 4) Public/private

Public:

```bash
python3 scripts/model_governance.py access --provider openai --public --dry-run
python3 scripts/model_governance.py access --provider openai --public
```

Private:

```bash
python3 scripts/model_governance.py access --provider openai --private
```

### 5) Unified icon

Single icon URL for all targets:

```bash
python3 scripts/model_governance.py icon \
  --id-regex '^paper_rewrite\\.' \
  --url 'https://assets.example.com/icons/paper-rewrite.png'
```

Rule-based icons (recommended):

```bash
python3 scripts/model_governance.py icon \
  --all \
  --map-file references/icon-map.example.json \
  --dry-run
```

### 6) Normalize model names

```bash
python3 scripts/model_governance.py normalize-name \
  --all \
  --template '{provider_title} · {model_title}' \
  --provider-alias-file references/provider-alias.example.json \
  --dry-run
```

Then remove `--dry-run` to apply.

Supported template keys:
- `{id}`
- `{name}`
- `{provider}`
- `{provider_title}`
- `{model}`
- `{model_title}`

## References

- API details: `references/api-contract.md`
- Icon rules example: `references/icon-map.example.json`
- Provider alias example: `references/provider-alias.example.json`

## Failure Handling

- If you see many `401/403` failures, the token lacks write access for those models.
- Fix permissions first, then rerun the same command.
- Keep command + selectors unchanged to get deterministic retries.
