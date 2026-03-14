# Terminals Upstream Network Compatibility Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge local terminals behavior back onto upstream `open-webui/terminals` while preserving only the Docker bridge access needed for newly created containers when the `terminals` service runs inside Docker.

**Architecture:** Start from a fresh upstream `origin/main` working tree, keep upstream lifecycle, reconciliation, and DB-init behavior, and add one explicit `docker_connect_mode` compatibility layer in the Docker backend. Run the new tests with ephemeral test dependencies, then copy only the merged source files into the deployable local build tree and set `TERMINALS_DOCKER_CONNECT_MODE=bridge` in the wrapper script.

**Tech Stack:** Python, FastAPI, aiodocker, httpx, Docker, pytest, uv

---

### Task 1: Create the upstream-first working tree

**Files:**
- Create: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream`
- Verify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/terminals/backends/docker.py`
- Verify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`

**Step 1: Clone or refresh the upstream source**

Run: `if [ -d /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/.git ]; then git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream fetch origin; else git clone https://github.com/open-webui/terminals.git /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream; fi`  
Expected: a usable Git repo exists at `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream`.

**Step 2: Create the isolated branch**

Run: `git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream switch -C codex/terminals-network-compat origin/main`  
Expected: branch `codex/terminals-network-compat` points at upstream `origin/main`.

**Step 3: Install Python dependencies**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv sync`  
Expected: the working tree has a runnable local environment without source changes.

**Step 4: Snapshot the local compatibility target**

Run: `diff -u /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/terminals/backends/docker.py /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/terminals/backends/docker.py | sed -n '1,200p'`  
Expected: the old local bridge-IP logic is visible and can be selectively reintroduced.

**Step 5: Verify the baseline is clean**

Run: `git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream status --short`  
Expected: no feature changes yet.

### Task 2: Lock the connection-mode contract with failing tests

**Files:**
- Create: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/tests/test_docker_backend_connect_mode.py`

**Step 1: Write failing tests for connection target resolution**

```python
def test_resolve_connection_target_uses_container_name_on_custom_network(monkeypatch):
    backend = DockerBackend()
    monkeypatch.setattr(docker_module.settings, "network", "terminals-net")
    monkeypatch.setattr(docker_module.settings, "docker_connect_mode", "host")
    host, port = backend._resolve_connection_target(SAMPLE_INFO, "terminals-default-user")
    assert (host, port) == ("terminals-default-user", 8000)


def test_resolve_connection_target_uses_bridge_ip_in_bridge_mode(monkeypatch):
    backend = DockerBackend()
    monkeypatch.setattr(docker_module.settings, "network", "")
    monkeypatch.setattr(docker_module.settings, "docker_connect_mode", "bridge")
    host, port = backend._resolve_connection_target(SAMPLE_INFO, "terminals-default-user")
    assert (host, port) == ("172.17.0.23", 8000)


def test_resolve_connection_target_uses_host_port_in_host_mode(monkeypatch):
    backend = DockerBackend()
    monkeypatch.setattr(docker_module.settings, "network", "")
    monkeypatch.setattr(docker_module.settings, "docker_connect_mode", "host")
    monkeypatch.setattr(docker_module.settings, "docker_host", "host.docker.internal")
    host, port = backend._resolve_connection_target(SAMPLE_INFO, "terminals-default-user")
    assert (host, port) == ("host.docker.internal", 49152)
```

**Step 2: Add one explicit failure-path test**

```python
def test_bridge_mode_requires_a_bridge_ip(monkeypatch):
    backend = DockerBackend()
    monkeypatch.setattr(docker_module.settings, "network", "")
    monkeypatch.setattr(docker_module.settings, "docker_connect_mode", "bridge")
    with pytest.raises(RuntimeError, match="bridge IP"):
        backend._resolve_connection_target(INFO_WITHOUT_BRIDGE_IP, "terminals-default-user")
```

**Step 3: Run the targeted tests**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv run --with pytest --with pytest-asyncio pytest tests/test_docker_backend_connect_mode.py -q`  
Expected: FAIL because the helper and the config field do not exist yet.

**Step 4: Re-run the targeted tests to confirm deterministic failure**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv run --with pytest --with pytest-asyncio pytest tests/test_docker_backend_connect_mode.py -q`  
Expected: still FAIL, but now due only to missing implementation.

### Task 3: Add the explicit Docker connection mode to upstream config

**Files:**
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/terminals/config.py`
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/README.md`

**Step 1: Add the new config field**

```python
class Settings(BaseSettings):
    ...
    docker_host: str = "127.0.0.1"
    docker_connect_mode: str = "host"  # host or bridge
    data_dir: str = "./data/terminals"
```

**Step 2: Document the mode in the README**

Add a short note that:

- `host` keeps upstream behavior
- `bridge` is for a `terminals` container orchestrating sibling Docker containers through the Docker socket

**Step 3: Run a narrow import check**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv run python -c "from terminals.config import settings; print(settings.docker_connect_mode)"`  
Expected: prints `host`.

**Step 4: Commit the config contract**

```bash
git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream add terminals/config.py README.md tests/test_docker_backend_connect_mode.py
git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream commit -m "feat: add docker connection mode contract"
```

### Task 4: Implement the upstream-first connection resolver

**Files:**
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/terminals/backends/docker.py`
- Test: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/tests/test_docker_backend_connect_mode.py`

**Step 1: Add a focused helper for connection resolution**

```python
def _resolve_connection_target(self, info: dict, instance_name: str) -> tuple[str, int]:
    if settings.network:
        return instance_name, 8000

    if settings.docker_connect_mode == "bridge":
        bridge = info.get("NetworkSettings", {}).get("Networks", {}).get("bridge", {})
        ip_addr = bridge.get("IPAddress")
        if not ip_addr:
            raise RuntimeError("bridge IP unavailable for docker_connect_mode=bridge")
        return ip_addr, 8000

    port_bindings = info.get("NetworkSettings", {}).get("Ports", {}).get("8000/tcp", [])
    if not port_bindings:
        raise RuntimeError("published port unavailable for docker_connect_mode=host")
    return settings.docker_host, int(port_bindings[0]["HostPort"])
```

**Step 2: Route `_extract_instance_info()` through the helper**

```python
host, port = self._resolve_connection_target(info, instance_name)
return {
    "instance_id": instance_id,
    "instance_name": instance_name,
    "api_key": api_key,
    "host": host,
    "port": port,
}
```

**Step 3: Keep upstream behavior untouched elsewhere**

Do not change:

- `create_or_replace`
- label creation
- `reconcile()`
- readiness polling
- retry-on-`409`

**Step 4: Run the targeted unit tests**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv run --with pytest --with pytest-asyncio pytest tests/test_docker_backend_connect_mode.py -q`  
Expected: PASS.

**Step 5: Run one broader backend smoke test**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv run python -m compileall terminals`  
Expected: PASS with no syntax errors.

**Step 6: Commit the backend change**

```bash
git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream add terminals/backends/docker.py tests/test_docker_backend_connect_mode.py
git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream commit -m "feat: support bridge-mode docker terminal routing"
```

### Task 5: Mirror the merged upstream code into the deploy tree without deleting deployment-only files

**Files:**
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/terminals/config.py`
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/terminals/backends/docker.py`
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/README.md`
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/pyproject.toml`
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/uv.lock`
- Verify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/Dockerfile.codex`
- Verify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`

**Step 1: Confirm the upstream branch only contains the intended feature**

Run: `git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream diff --name-only origin/main..HEAD`  
Expected: only source, README, and test files related to the connection-mode feature are listed.

**Step 2: Re-run the targeted test before copying code**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream && uv run --with pytest --with pytest-asyncio pytest tests/test_docker_backend_connect_mode.py -q`  
Expected: PASS.

**Step 3: Copy only the merged source files into the local deploy tree**

Run: `rsync -a /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/terminals/ /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/terminals/ && cp /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/README.md /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/pyproject.toml /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/uv.lock /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/`  
Expected: the deployable local tree reflects the merged upstream code while preserving deployment-only artifacts such as `Dockerfile.codex` and the wrapper script.

**Step 4: Verify the mirrored files**

Run: `diff -u /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream/terminals/backends/docker.py /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/terminals/backends/docker.py && test -f /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/Dockerfile.codex && test -f /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`  
Expected: no diff for the backend file, and the deployment-only wrapper files still exist.

**Step 5: Verify the upstream branch history**

Run: `git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream log --oneline origin/main..HEAD`  
Expected: the branch shows the config-contract commit and the backend-routing commit, with no unrelated changes.

### Task 6: Make bridge mode explicit in the deployment wrapper

**Files:**
- Modify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`

**Step 1: Add the environment variable to the wrapper**

```sh
  -e TERMINALS_BACKEND=docker \
  -e TERMINALS_DOCKER_CONNECT_MODE=bridge \
  -e TERMINALS_OPEN_WEBUI_URL=http://192.168.2.238 \
```

**Step 2: Verify the wrapper still parses**

Run: `sh -n /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`  
Expected: PASS.

**Step 3: Show the effective diff**

Run: `git -C /Users/liusihang/openwebui/.tmp/open-webui-terminals-upstream diff --stat origin/main..HEAD`  
Expected: only the connection-mode feature remains in the upstream branch.

### Task 7: Verify the deployable image and runtime path

**Files:**
- Verify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/Dockerfile.codex`
- Verify: `/Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc/open-webui-terminals-start.sh`

**Step 1: Build the image from the deploy tree**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc && docker build -f Dockerfile.codex -t open-webui-terminals:network-compat .`  
Expected: image builds successfully.

**Step 2: Start the service with the wrapper**

Run: `cd /Users/liusihang/openwebui/.tmp/open-webui-terminals-buildsrc && ./open-webui-terminals-start.sh open-webui-terminals 3000 unless-stopped`  
Expected: the service container starts with `TERMINALS_DOCKER_CONNECT_MODE=bridge`.

**Step 3: Verify service health**

Run: `curl -fsS http://127.0.0.1:3000/health`  
Expected: returns `{"status":true}` or equivalent healthy JSON.

**Step 4: Manual smoke-check a new terminal**

Action: from Open WebUI, create a brand-new terminal session after the new service is running.  
Expected: the session opens successfully and the proxy reaches the newly created `open-terminal` container without host-port routing failures.

**Step 5: Record the rollout state**

Run: `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -E 'open-webui-terminals|terminals-'`  
Expected: the service container is running and at least one freshly created `terminals-*` container can be observed during the smoke test.
