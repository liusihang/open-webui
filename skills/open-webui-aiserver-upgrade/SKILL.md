---
name: open-webui-aiserver-upgrade
description: Use when upgrading Open WebUI on the aiserver host, building a replacement image for aiserver, or switching the live aiserver Open WebUI image without changing the rest of the stack.
---

# Aiserver OpenWebUI Upgrade

## Overview

Use this skill for the `aiserver` Open WebUI deployment only.
The default rule is strict: change only the `open-webui` image in `/srv/openwebui-migration/compose.yaml` unless the user explicitly asks for topology changes.

## When to Use

Use this skill when the user asks to:
- inspect the live Open WebUI deployment on `aiserver`
- build a new `open-webui:<tag>` image on `aiserver`
- switch `aiserver` to a new Open WebUI image
- verify the live Open WebUI image, health, or compose state on `aiserver`

Do not use this skill for:
- generic Docker troubleshooting on other hosts
- Caddy, frpc, Postgres, or Redis topology changes unless explicitly requested
- non-`aiserver` Open WebUI deployments

## Live Paths

- Live stack: `/srv/openwebui-migration/compose.yaml`
- Live env: `/srv/openwebui-migration/.env`
- Standard target service: `open-webui`
- Standard live project: `openwebui-migration`

## Default Rule

Unless the user explicitly asks otherwise:
- replace only the `open-webui` `image:` line
- recreate only the `open-webui` service
- do not change ports
- do not change `caddy`
- do not change `frpc-preview`
- do not change Postgres or Redis

## Standard Build Flow

For source builds on `aiserver`, prefer this sequence:

1. Build from an explicit git ref, not from an ambiguous dirty tree.
2. Sync source into `/home/aiserver/staging/<name>`.
3. Patch the staged `Dockerfile`, not the repository copy.
4. Build on `aiserver` with `docker buildx build --load` in the background with a persistent log file.
5. Inspect the final image ID, creation time, `CMD`, and `ENTRYPOINT`.
6. Only switch the live service after the image is verified.

## Required Staged Dockerfile Patches

When building from source on `aiserver`, apply these remote-only patches unless the source already includes equivalent behavior:

- Prefer `docker buildx build --load` instead of the legacy builder.
- Set `npm` registry to `https://registry.npmmirror.com`.
- Set `pip` and `uv` indexes to `https://pypi.tuna.tsinghua.edu.cn/simple`.
- Rewrite Debian sources to `https://mirrors.tuna.tsinghua.edu.cn`.
- Set these build-time environment variables to avoid unnecessary or broken downloads:
  - `CYPRESS_INSTALL_BINARY=0`
  - `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`
  - `PUPPETEER_SKIP_DOWNLOAD=true`
  - `ONNXRUNTIME_NODE_INSTALL_CUDA=skip`

## Runtime Validation

Before calling a built image good, verify all of:

- `docker image inspect <tag>` returns an image ID
- `CMD` is `["bash","start.sh"]`
- `ENTRYPOINT` is empty or null
- a smoke container can at least reach application startup
- the live service reaches `healthy` after `docker compose up -d --no-deps --force-recreate open-webui`

If the user asks whether the image is "normal", do not answer from build logs alone.
At minimum, inspect the image config; ideally also run a smoke container or live health check.

## Tool And Terminal Pitfalls

Two recurring failure modes are easy to misdiagnose:

- `TERMINAL_SERVER_CONNECTIONS` with `auth_type=session`
  - The model can miss terminal tools even when the terminal UI itself connects.
  - Root cause: backend terminal spec loading must fetch the terminal OpenAPI spec with the user's JWT.
  - Symptom: terminal works interactively, but the model sees no terminal tools.

- stale model `filterIds`
  - Old filter IDs can survive in model metadata after the underlying function is deleted.
  - A real example from this deployment was `unified_tool_mcp_router_filter`.
  - Symptom: logs repeatedly show `Failed to load function module for <filter_id>: Function not found`.
  - Fix the data first, then re-evaluate any tool visibility issue.

## Script

Path: `skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh`

Supported commands:
- `inspect`
- `build-only`
- `switch-image`

## Quick Reference

Inspect live state:

```bash
skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh inspect
```

Build only from local `HEAD`:

```bash
skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh build-only
```

Build only from a specific ref with a proxy:

```bash
skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh build-only \
  --commit d9230f705 \
  --image-tag open-webui:d9230f705-terminal-fixes \
  --proxy-url http://192.168.2.201:7897
```

Switch the live image only:

```bash
skills/open-webui-aiserver-upgrade/scripts/upgrade_aiserver_openwebui.sh switch-image \
  --image-tag open-webui:d9230f705-terminal-fixes
```

## Common Mistakes

- Running against the wrong stack path
  - Always use `/srv/openwebui-migration/compose.yaml` for live changes.
- Editing ports during a normal upgrade
  - Do not change ports unless the user explicitly requests it.
- Rebuilding unrelated services
  - Only recreate `open-webui` during `switch-image`.
- Treating staging compose files as live
  - `/home/aiserver/staging/...` is for build prep, not the live stack.
- Trusting build success without inspecting image config
  - Always confirm `CMD` and `Entrypoint`.
- Leaving `buildx` unavailable on `aiserver`
  - Install the `docker-buildx` package first, then rebuild.
- Debugging missing terminal tools only from the frontend
  - Check terminal spec loading and stale model filter references too.
