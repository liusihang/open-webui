# GPT-5.5 Profile JSON Error Diagnosis Handoff

## Goal

Determine why the `192.168.2.238:18085` test stack shows `Unexpected token 'I', "Internal S"... is not valid JSON` after asking `openai/gpt-5.5` to exercise tools. Diagnose the root cause before making changes.

## Truth surface

- Live test URL: `http://192.168.2.238:18085`
- Conversation: `4ae52f3f-5a28-417c-a039-30d547c65d67`
- Acceptance evidence: live container/logs, upstream HTTP response, and matching backend/frontend source sites

## Completed actions

- Confirmed the deployed source line is based on the v0.11 integration worktree at `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`.
- Verified the exact test container is `open-webui-pr7`, image `open-webui:v011-hotfix-7684618281df`, source revision `7684618281df7a9adbd4217d127c3abb284cc261`, healthy with zero restarts and no OOM.
- Captured the full failing request traceback from 22:49:20 local time.
- Read back Redis client/server settings and current connectivity without exposing credentials.
- Traced the frontend error text to `generateOpenAIChatCompletion()` parsing every non-2xx response with `res.json()`.
- Compared the three affected implementation files against official `v0.11.0`; they were identical before this fix, so the defect is inherited from the official v0.11 path rather than introduced by the selective integration.
- Inspected redis-py 8.0.1 inside the exact running container. Its connection retry object has `retries=0`; a failed health-check PING disconnects/reconnects the socket but still propagates the current request error.
- Added TDD regression tests for all idempotent RedisDict read operations, bounded retry behavior, plain-text HTTP errors, and preservation of JSON `detail` errors.
- Implemented one retry for RedisDict idempotent reads only. Redis locks, global connection-factory behavior, and non-idempotent Redis operations were deliberately left unchanged.
- Implemented content-type-aware error parsing for `generateOpenAIChatCompletion()`.

## Checkpoint

- Browser symptom is confirmed from the user screenshot.
- Root cause is established. No fix has been applied because the current request asks for diagnosis.

## Verification results

- The upstream Bifrost `/v1/responses` request logged immediately before the failure returned HTTP 200.
- The failing OpenWebUI request returned HTTP 500 and raised `redis.exceptions.ConnectionError: Error 32 while writing to socket. Broken pipe`.
- The exact failure site is `main.py:2528`, `if not request.app.state.MODELS`, which calls `RedisDict.__len__()` and Redis `HLEN`; this occurs before model ID lookup and before Profile/mode fields are processed.
- Redis is reachable now (`PING=True`), healthy, has zero rejected connections, zero evictions, and did not restart.
- Runtime settings are `WEBSOCKET_MANAGER=redis`, `UVICORN_WORKERS=4`, `REDIS_HEALTH_CHECK_INTERVAL=30`, `REDIS_SOCKET_KEEPALIVE=true`; redis-py is 8.0.1.
- Redis server `timeout` is 1800 seconds. The only Redis connection failure in this application container since startup is this incident (one `BrokenPipeError` plus its wrapped `ConnectionError`).
- Therefore an idle pooled Redis TCP connection in one long-running backend worker was closed by the Redis server; redis-py's health-check `PING` encountered the stale socket and propagated the connection error instead of transparently completing the request on a fresh connection. This is independent of chat age: a brand-new conversation is routed to one of the already-running workers and can hit that worker's stale pooled connection on its first message.
- Starlette returned its plain-text `Internal Server Error` body. The frontend then called `res.json()` on that body, producing `Unexpected token 'I', "Internal S"... is not valid JSON` as a secondary presentation error.
- TDD RED evidence: Redis regression suite failed 13/13 before the backend change; the frontend suite reproduced the exact `Unexpected token 'I'` error while its JSON-detail control passed.
- TDD GREEN evidence: Redis regression suite passed 13/13 and frontend OpenAI API suite passed 2/2 after the changes.
- Related backend regression: 25/25 socket, model-list, and cache-invalidation tests passed. The reused root Python environment emits four known dependency deprecation warnings.
- Full frontend regression: 37 files and 400/400 tests passed under Node 22.22.0.
- Ruff, Prettier, `git diff --check`, and the new TypeScript test's targeted ESLint check passed. The production OpenAI API file retains one pre-existing unused import that is also present at the pre-fix HEAD.
- Node 22.22.0 production build completed successfully in 1 minute 28 seconds. Existing Svelte accessibility/deprecation warnings remain.
- Full `pnpm check` is not a usable clean gate on this branch: it reports the existing baseline of 8195 errors and 216 warnings across 352 unrelated files; this task did not modify those files.

## Current status

Code fix and local verification are complete. No Redis configuration, database, formal live container, or formal live image was changed. The 18085 test stack has not yet been updated with this fix.

## Next step

Commit the scoped code/test/handoff changes, create a rollback anchor for the current 18085 image, deploy the fix to `open-webui-pr7`, run an exact redis-py 8 stale-connection fault injection, then execute authenticated browser E2E and inspect post-deploy logs/health.
