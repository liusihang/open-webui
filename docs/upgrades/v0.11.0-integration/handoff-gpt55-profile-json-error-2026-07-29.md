# GPT-5.5 Profile JSON Error Fix Handoff

## Goal

Resolve the `Unexpected token 'I', "Internal S"... is not valid JSON` failure on the isolated `192.168.2.238:18085` stack, including the user's correction that it occurs in a brand-new conversation. Prove the fix against the exact Redis client behavior, then deploy and validate new chat, native tool execution, and persisted-chat reopening in a real browser.

## Truth surface

- Source worktree: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`
- Branch: `codex/v011-upstream-integration-base`
- Code fix commit and deployed source revision: `b7cb48cb9ab1383791b66cc94bd40050b0af6e30`
- Live test URL and container: `http://192.168.2.238:18085`, `open-webui-pr7`
- Original failing conversation: `4ae52f3f-5a28-417c-a039-30d547c65d67`
- Formal live container: `open-webui`, inspection only; it was not modified

## Root cause

- The exact failing `/api/chat/completions` request returned HTTP 500 before model/profile processing. The traceback entered `main.py` at `if not request.app.state.MODELS`, then `RedisDict.__len__()`, Redis `HLEN`, and failed with `redis.exceptions.ConnectionError: Error 32 while writing to socket. Broken pipe`.
- The runtime uses four Uvicorn workers, Redis-backed application state, redis-py 8.0.1, `REDIS_HEALTH_CHECK_INTERVAL=30`, and TCP keepalive. redis-py's connection retry object has zero retries. Its health-check PING disconnects a stale socket but propagates the current request failure; only a subsequent request uses the new connection.
- The stale object was a pooled Redis TCP connection held by a long-running backend worker, not an old chat. A brand-new chat can be routed to that worker and encounter the stale socket on its first request.
- Starlette emitted a plain-text `Internal Server Error` body. `generateOpenAIChatCompletion()` then unconditionally called `res.json()` for non-2xx responses, replacing the server failure with the visible `Unexpected token 'I'` JSON parse error.
- All three relevant files matched official v0.11.0 before the fix, so this defect came from the adopted upstream path rather than the selective integration.

## Implemented fix

- `backend/open_webui/socket/utils.py`
  - Added one bounded retry only for Redis `ConnectionError`/`TimeoutError` on idempotent RedisDict reads.
  - Covered `hget`, `hexists`, `hlen`, `hkeys`, `hvals`, `hgetall`, and the `hkeys` read used by `set()`.
  - Left non-idempotent writes, distributed locks, and the global Redis connection factory unchanged.
- `backend/open_webui/test/socket/test_redis_dict_reconnect.py`
  - Added 13 regression cases for both transient exception types and exact one-retry behavior.
- `src/lib/apis/openai/index.ts`
  - Added content-type-aware error-body parsing with readable text/status fallback for non-JSON or malformed JSON responses.
- `src/lib/apis/openai/index.test.ts`
  - Proves plain-text HTTP 500 becomes `Internal Server Error`, not a JavaScript `SyntaxError`, while JSON `detail` behavior remains unchanged.

## Local verification

- TDD RED: backend regression failed 13/13 before the Redis change; frontend reproduced the exact `Unexpected token 'I'` failure while the JSON control passed.
- TDD GREEN: Redis regression 13/13; focused frontend error parsing 2/2.
- Related backend socket/model/cache suite: 25/25 passed, with four known dependency deprecation warnings from the reused root environment.
- Full frontend test suite: 37 files, 400/400 tests passed under Node 22.22.0.
- Ruff, Prettier, `git diff --check`, and targeted ESLint on the new test passed. The production API file retains one pre-existing unused import present at the pre-fix HEAD.
- Node 22.22.0 production build passed. Its `build/_app/version.json` was asserted to equal the full code-fix SHA before packaging.
- Full `pnpm check` remains an unrelated branch baseline failure: 8195 errors and 216 warnings across 352 legacy files.

## Test-stack deployment

- Hotpatch image: `open-webui:v011-hotfix-b7cb48cb9`
- Immutable image ID: `sha256:2eaa381ce64fbdf4ed96c53b77e1fe0b3ab83d1257f42af2ee97db204d205685`
- The image is a thin immutable layer on the previously accepted v0.11 image: it replaces the complete frontend build and the single changed backend module. No dependency rebuild was required.
- Backend module SHA-256: `31e43d8b91ba1d4d250facd9a69c3a8633d44bd5303ba6f1595ee88c56ad89c2`
- Rollback script: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-b7cb48cb9-20260730-001054/rollback-open-webui-pr7.sh`
- Deployment evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/gpt55-redis-reconnect-b7cb48cb9-20260730-001054`
- Only `open-webui-pr7` was recreated. The formal live container was kept read-only.

## Exact Redis fault injection

- Ran inside the deployed image with redis-py 8.0.1.
- Created a temporary RedisDict, established its pooled connection, killed exactly that Redis client from a second connection, and called `len(store)` once.
- The same operation recovered and returned the expected value; the log recorded the bounded transient-read retry.
- Temporary Redis data was deleted afterward.

## Real-browser E2E

- Created an ordinary temporary user and granted only temporary read access to the configured GPT-5.5 model; the grant, user, chat, and all authentication artifacts were removed afterward.
- Authenticated home page rendered completely at 1280x900 without the central spinner.
- New `openai/gpt-5.5` conversation `36a40839-cbbe-4235-ae66-e2df3f6c5620` returned the exact marker `B7_REDIS_E2E_OK`.
- Native builtin-tool round trip passed: the model called `get_current_timestamp`, OpenWebUI displayed the tool result, and the model returned `B7_TIME_TOOL_E2E_OK`.
- Navigated away to `/`, then reopened the same chat from the expanded sidebar. All user, assistant, reasoning, and tool-result content rendered; `.animate-spin` count was `0`.
- Page search found no `Unexpected token`, `Internal Server Error`, or `not valid JSON`; browser console had 0 errors and 0 warnings.
- Screenshot: `output/playwright/gpt55-redis-reconnect-b7cb48cb9/browser-e2e/gpt55-chat-tool-history-reopen-b7cb48cb9.png`

## Classified E2E observations

- Selecting the independently configured external `OfficeTools` server produced request `tool_ids: ["server:4"]`, but asking for the toolkit by its UI group name did not cause a tool call. The backend separately logged that all native builtin tools were resolved, and the subsequent explicit `get_current_timestamp` call passed. This observation is not the Redis/JSON defect and did not affect builtin tool execution; treat external OfficeTools naming/schema compatibility as a separate follow-up if required.
- The temporary model ACL caused one background title-generation `Model not found` traceback on another worker. The main chat succeeded and obtained a title. The temporary ACL was restored exactly, and final audit classified this single fixture-induced traceback; after cleanup there were zero new traceback, HTTP 5xx, or Broken Pipe lines.

## Final runtime verification

- Test container: configured image and immutable image ID match the values above; healthy, restart count 0, OOM false.
- Test source label and frontend version: full `b7cb48cb9ab1383791b66cc94bd40050b0af6e30`.
- `/health` and `/health/db`: both true.
- Deployment-period log scan: HTTP 5xx 0, Broken Pipe 0, fatal 0, unexpected traceback 0.
- Post-cleanup log scan: HTTP 5xx 0, Broken Pipe 0, traceback 0.
- Temporary E2E users, credentials, storage state, and model-access rollback artifacts: absent.
- Formal live remained unchanged at image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, source `1d8dba8a77e6e8adc5952891bac83a2a7c5a4804`, healthy, restart count 0, OOM false.

## Current status

The Redis stale-connection failure and misleading frontend JSON parse error are fixed, committed, deployed to the isolated 18085 test stack, and accepted through direct fault injection plus real-browser new-chat, native-tool, and history-reopen E2E. Formal-live promotion remains separately authorized and has not been performed.
