# Isolated build/E2E preflight — 2026-07-27

Truth surface: `aiserver`, isolated stack `/home/aiserver/staging/openwebui-pr7-eea11194ed-test`, formal live `/srv/openwebui-migration`.

Scope boundary: candidate build/deploy may target only `open-webui-pr7`. Formal `open-webui` is read-only and must retain its before/after anchors. Credentials and connection strings are not recorded.

## Before anchors — 2026-07-27T14:54:04+08:00

| Object | Container ID | Configured image | Image ID | Status | Health | Restarts | Workers |
|---|---|---|---|---|---|---:|---:|
| isolated WebUI | `764e0020e9e55e35bc8378e19ed6f59f94dd68c1c9d2bd4da784fbf76ee02618` | `open-webui:agentmode-v0102-4a4e43e206-slim` | `sha256:fd6145b041f28269a0766e8f0f1ab91653a998745290041c43ef314c2456c8c4` | running | healthy | 0 | 1 |
| isolated AgentScope runtime | `739472bd32748c196b44a643c352311788ff32ed13d1e2a9a5ab3a225f7f03e3` | `open-webui-pr7-agentscope-runtime:742f686182-true-final-stream` | `sha256:f7396ba23e49f934216ba8fc4b38c695b7f639722d852b44234769c66ca7f6e9` | running | healthy | 0 | 1 |
| formal live WebUI | `78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e` | `open-webui:live-f8106c651-to-v0102-pr7-b3-7-onlyoffice-mergefix-slim-20260707013738` | `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45` | running | healthy | 0 | 4 |

Isolated WebUI publishes host port `18085`; formal live publishes host port `80`. Both WebUI containers have database and Redis connections configured. No connection values were emitted. Baseline memory: isolated WebUI 317.9 MiB, runtime 48.42 MiB, formal live 6.617 GiB.

## Authoritative isolated compose set

Docker labels prove the running isolated project uses these files, in order:

1. `compose.yaml`
2. `compose.webui-rebuild-eaff69b0d317.yaml`
3. `compose.webui-eaff69-no-migrations.yaml`
4. `compose.webui-4a4e43e206.yaml`
5. `compose.agent-runtime-742f686182.yaml`

The resolved service images and worker counts match the running containers: WebUI `agentmode-v0102-4a4e43e206-slim`, one worker; AgentScope runtime `742f686182-true-final-stream`, one worker. The base compose alone is not the runtime truth.

Formal live uses only `/srv/openwebui-migration/compose.yaml`.

## Rollback plan before any candidate swap

- Build from a clean committed SHA into a new immutable candidate tag.
- Add one new candidate override that changes only the isolated `open-webui-pr7` image; retain all five authoritative baseline files and do not recreate dependencies.
- Candidate swap: `docker compose <baseline -f list> -f <candidate override> up -d --no-deps --force-recreate open-webui-pr7`.
- Rollback: rerun the same command with only the five baseline files. This restores image `open-webui:agentmode-v0102-4a4e43e206-slim` and `UVICORN_WORKERS=1`.
- After candidate E2E, rollback is mandatory by default and must be followed by container/image/health/restart verification.

No container, image, compose file, database, Redis key, or formal-live state was changed during this preflight.
