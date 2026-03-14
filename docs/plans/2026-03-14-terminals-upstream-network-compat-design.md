# Terminals Upstream Network Compatibility Design

**Date:** 2026-03-14  
**Target Base:** `open-webui/terminals` `origin/main`  
**Planned Branch:** `codex/terminals-network-compat`

## Goal

Move the local terminals deployment back onto upstream `open-webui/terminals` behavior while preserving one specific local fix: terminals created by a `terminals` service that is itself running inside Docker must remain reachable when the service provisions sibling `open-terminal` containers through `/var/run/docker.sock`.

## Confirmed Constraints

- Prefer upstream behavior over local patches.
- Keep only the Docker network access compatibility needed for newly created containers.
- Do not preserve old container naming rules.
- Do not preserve the local custom `ensure_terminal` implementation.
- Do not preserve old container reuse behavior for already running legacy terminals.
- Do not reintroduce removed frontend behavior unless it is required for the network fix.
- The deployable local source tree still lives at `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc`.

## Current State

The local deployable tree differs from upstream in a few places, but only one of them is essential for the currently observed runtime problem:

- Local `docker.py` uses the target container's Docker bridge IP when no explicit Docker network is configured.
- Upstream `docker.py` publishes container ports and resolves the terminal through `TERMINALS_DOCKER_HOST`.
- The local wrapper script does not set `TERMINALS_DOCKER_HOST`, so direct adoption of upstream behavior is likely to fail when `terminals` itself runs inside Docker.

Upstream has already improved several areas that the local patch originally worked around:

- per-user lifecycle locking
- `409` retry handling
- readiness polling
- restart reconciliation through container labels
- synchronous Alembic initialization

Those upstream improvements should be kept intact.

## Options Considered

### Option A: Upstream-first plus a narrow connection-mode compatibility layer

Add a small explicit Docker connection mode to upstream `docker.py` and keep everything else upstream.

Pros:

- preserves upstream lifecycle and future mergeability
- keeps the compatibility patch small and well isolated
- makes deployment intent explicit

Cons:

- requires one new configuration knob
- needs a small deployment wrapper update

### Option B: Configuration-only adaptation

Keep upstream code unchanged and try to solve the problem with `TERMINALS_DOCKER_HOST`, custom Docker networking, or host-gateway tricks.

Pros:

- smallest code diff

Cons:

- fragile in container-inside-Docker orchestration setups
- harder to reason about and debug
- does not guarantee the currently working path

### Option C: Reapply the old local Docker backend on top of upstream

Carry forward the local `docker.py` behavior wholesale and selectively backport upstream features.

Pros:

- closest to current behavior

Cons:

- fights upstream architecture
- larger future merge burden
- keeps unrelated local behavior alive

## Chosen Approach

Use **Option A**.

The merged result should keep upstream `Backend`, `DockerBackend`, `reconcile()`, ready checks, labels, and sync DB init. Only the connection target resolution logic will gain one explicit compatibility branch for local Docker bridge access.

## Detailed Design

### 1. Add an explicit Docker connection mode

Introduce a new setting in `terminals/config.py`:

- `docker_connect_mode: str = "host"`

Supported values:

- `host`: upstream default; use published host ports and `TERMINALS_DOCKER_HOST`
- `bridge`: local compatibility mode; use the provisioned container's Docker bridge IP and port `8000`

This keeps the default upstream behavior unchanged for everyone else.

### 2. Keep upstream network precedence

Connection target resolution will follow this order:

1. If `TERMINALS_NETWORK` is configured, use container-name routing on that Docker network.
2. Else if `TERMINALS_DOCKER_CONNECT_MODE=bridge`, resolve the target via `NetworkSettings.Networks.bridge.IPAddress` and port `8000`.
3. Else use upstream host-mode behavior: published host port plus `TERMINALS_DOCKER_HOST`.

The compatibility logic lives only in the helper that builds `host` and `port` for the proxy layer.

### 3. Fail loudly when explicit bridge mode is misconfigured

If bridge mode is selected but the container does not expose a usable bridge IP, provisioning should fail with a clear error instead of silently falling back to a different path. The point of the setting is to encode deployment intent, not to guess.

### 4. Keep upstream container management intact

The following upstream behavior stays unchanged:

- `create_or_replace`
- retry-on-`409`
- readiness polling against `/health`
- label-based reconciliation on startup
- idle reaper and instance bookkeeping in `Backend`

This avoids dragging the local custom `ensure_terminal()` path back into the merged result.

### 5. Update deployment entrypoint explicitly

The deploy wrapper script at `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh` should set:

- `TERMINALS_DOCKER_CONNECT_MODE=bridge`

No other compatibility settings are required for this round.

### 6. Keep dependency metadata upstream unless the code change truly requires it

`uv.lock` should not be manually merged just to preserve the old local snapshot. Prefer the upstream lockfile as-is. Only regenerate it if the implementation ends up changing dependency metadata rather than only runtime logic.

## File Impact

### Upstream-first source tree

- `terminals/config.py`
- `terminals/backends/docker.py`
- `README.md`
- `tests/test_docker_backend_connect_mode.py`

### Local deploy wrapper

- `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`
- `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/Dockerfile.codex`

## Testing Strategy

### Unit tests

Add targeted Docker backend tests for three cases:

- custom Docker network uses container name + `8000`
- host mode uses published port + `TERMINALS_DOCKER_HOST`
- bridge mode uses Docker bridge IP + `8000`

Also add one failure-path test:

- bridge mode without a bridge IP raises a clear error

### Build verification

- keep upstream dependency metadata unless a real dependency change is introduced
- build the image from the deployable local tree
- confirm the service still starts and reports `/health`

### Runtime smoke check

After deploying with `TERMINALS_DOCKER_CONNECT_MODE=bridge`, create a brand-new terminal session and verify that the proxy reaches the new `open-terminal` container successfully.

## Risks

- Upstream may continue changing connection resolution or container metadata assumptions.
- Bridge mode depends on the presence of the default Docker bridge network in the deployment environment.
- The local deployable tree is not itself a Git repo, so copying the merged upstream result into it must be done deliberately.
- The deploy tree still contains deployment-only artifacts (`Dockerfile.codex`, wrapper scripts, and possibly stale frontend assets) that are separate from the upstream source-of-truth.

## Out of Scope

- compatibility for old already-running terminal containers
- restoration of the removed bundled frontend
- preserving old local container names
- preserving the old local `ensure_terminal()` override
- solving deployment through host-gateway or custom DNS tricks
