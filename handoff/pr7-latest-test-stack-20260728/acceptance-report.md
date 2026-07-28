# PR7 latest-image persistent test stack — acceptance report

Date: 2026-07-28

## Result

The isolated PR7 test stack is running and remains available on host port `18085`.

- Container: `open-webui-pr7`
- Image: `open-webui:pr7-chat-agent-dual-mode-1d8dba8a7-slim`
- Image ID: `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`
- Source revision: `1d8dba8a77e6e8adc5952891bac83a2a7c5a4804`
- Database revision: `c0d3b4a5e6f7`
- WebUI workers: 4
- Host worker PIDs: `2038078 2038079 2038080 2038081`
- Container worker PIDs: `11 12 13 14`
- Health: `/health` and `/health/db` both true
- Restart count: 0

The current branch has no product-code changes after the image revision; later branch changes are deployment/handoff artifacts only. No rebuild was necessary.

## Persistent administrator defaults

| Mode | Terminal | Tools | Skills | Filter/features | System Prompt |
| --- | --- | --- | --- | --- | --- |
| Chat | disabled | empty | empty | inherit | empty |
| Agent | `terminals` | `sub_agent` | empty | inherit | empty |

Current revisions:

- Chat: `ecbc1341-534d-4630-b69b-78a98a5032af`
- Agent: `3118a971-b710-4845-b9d1-9c807e15bb16`

Pinned requests to all four workers returned the same private/public revisions and defaults. Public projections did not expose System Prompt.

## Real acceptance

- Model: `bifrostapi.Cliproxy/gpt-5.5`
- Chat provider stream: 12 content deltas, 16 SSE data lines, `[DONE]`, marker present, 2.074 seconds.
- Native Agent: `run.running -> final.started -> final.delta -> run.completed`, marker present, 21.325 seconds.
- Three pinned catalog rounds showed both available `gpt-5.5` routes on every worker.
- Startup: four server processes, one singleton owner, three singleton skips, one tool-server initialization, no worker death or respawn.
- Final anomaly window: no `runtime_finalization`, `ReadTimeout`, child death, respawn, or shutdown.

Smoke records were cleaned precisely. No test Agent run, temporary binding, or diagnostic Chat remains.

## Known environment gaps

1. The isolated database does not contain the planned Skill `get-available-resources`. No opaque content was copied from formal live; default Skills are empty.
2. `web_search_and_crawl` declares `crawl4ai`, but the slim candidate does not contain that package and this stack sets `OFFLINE_MODE=true`. It cannot be a reproducible default on this image. The running test default therefore uses only `sub_agent`.
3. To make `web_search_and_crawl` a default, rebuild the image with `crawl4ai` preinstalled; do not hot-install it into the running container.
4. A synthetic product Chat request without a real Socket session is not browser-equivalent: the event-emitter path consumes the stream. The present run proves direct provider SSE plus mode-profile convergence and native Agent events, while the prior full browser acceptance remains the UI evidence for this immutable image.

## Backup and rollback

- Backup manifest: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pr7-latest-test-stack-20260728/before-c0/manifest.env`
- Dump SHA-256: `340292829d99e9e5c7d02c7799e741ec25e131062d0c9f3213241e4a4bfb6c3f`
- Source revision: `f8a9b0c1d2e3`
- Rollback helper: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/remote-rollback.sh`

Rollback requires an exact confirmation, candidate downgrade to `f8`, and WebUI-only recreation from the five baseline Compose files. It was not invoked because acceptance passed.

## Formal live

Formal live remained read-only and unchanged:

- Container ID: `78faa81d479d8c5ef33a85277feeb3dc5de68861c3f25dcaac67285935f9c13e`
- Image ID: `sha256:7ec820b71fa94205b273cb8cd00344a130e1921ae8e643ba6192b0e58933bd45`
- Health: healthy
- Restart count: 0
- Database revision: `f3a4b5c6d7e8`
- Worker PIDs: `1007971 1011557 1011836 1034666`
