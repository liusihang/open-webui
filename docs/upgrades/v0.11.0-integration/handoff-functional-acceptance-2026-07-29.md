# v0.11 functional acceptance handoff

## Goal

Verify the isolated v0.11 test stack end to end after the frontend hot-patches, covering the integrated backend, runtime, terminal/retrieval/tool, frontend, and preserved custom AgentScope surfaces. Do not infer whole-product health from the landing page alone.

## Truth surface

- Source: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base` at committed HEAD.
- Runtime: `aiserver` container `open-webui-pr7` on port 18085 and its exact image, health, API responses, and logs.
- Browser: authenticated Playwright contexts against `http://192.168.2.238:18085/`; the user's Edge profile remains the authority when profile-persisted behavior differs.
- Formal live: container `open-webui` is read-only and must remain on its existing image with no restart.

## Execution

- Owner: root thread.
- Started: 2026-07-29 09:51 Asia/Shanghai.
- Current checkpoint: complete. Source, production build, four-worker runtime, authenticated browser routes, real temporary Chat, real temporary Agent, default-model behavior, persistence boundaries, and final logs are green.
- Stop condition: any application console error, failed required request, wrong runtime image/version, unhealthy/restarted container, formal-live drift, or a core flow that cannot complete.
- Runtime rollback anchor: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-e3a9c97dd059/rollback-open-webui-pr7.sh`.
- Test-only default-model rollback: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-default-model-e3a9c97dd059-retry3/rollback-test-default-model.sh`.

## Hard contracts

- Preserve custom AgentScope authority, approvals, user input, cancellation/reconnect, artifacts, citations, tool timeline, and subagent attribution.
- Official duplicate Sub-agents runtime/UI and `list_chat_files`, `grep_chat_files`, `query_chat_files` must remain absent.
- Avoid destructive or broad state changes. Prefer temporary chats and read-only route/API checks; record any test data before creating it.

## Acceptance matrix

| Area | Required evidence | Status |
| --- | --- | --- |
| Runtime identity and health | Exact image/version, `/health`, `/health/db`, model catalog, restart/OOM/log audit | passed |
| Authentication and navigation | Authenticated home, expanded/collapsed sidebar, search, Notes, Workspace, settings/admin routes | passed |
| Chat and provider path | Temporary chat, real streamed inference, model/reasoning controls, no console/request failure | passed |
| Custom AgentScope path | Agent mode entry plus preserved runtime/event behavior using existing acceptance probes | passed |
| Sidebar/channels/folders/history | Chat list/action menu, channels, folders, pagination and OpenClaw entry without runtime error | passed |
| Notes/workspace/admin | Route rendering and representative API data for models, knowledge, prompts, tools, functions, skills | passed |
| Terminal/retrieval/tool contracts | Existing focused suites plus runtime catalog/permission probes; exclusions remain absent | passed |
| Security/config/migrations | Existing focused suites, migration head/runtime DB checks, config/auth endpoints | passed |
| Regression and exclusions | Focused/full automated tests, production-source exclusion scan, browser console/log audit | passed |

## Final accepted source and runtime

- Source commits introduced during the browser acceptance loop:
  - `17cf77c906d2`: recognize v0.11 temporary chat IDs.
  - `93032060d9d59170b9f9c5dbb13e43c929eab9c6`: restore socket event dispatch, including the missing event `type` binding and guarded reload/list behavior.
  - `e3a9c97dd059aa814ea4d34bf1aca910923cf2e8`: isolate stored mode-profile drafts so a Chat revision cannot be submitted as an Agent revision.
- Final test image: `open-webui:v011-hotfix-e3a9c97dd059`, image ID `sha256:5a541612b86655ac1423b5e88109c47ff818819d99315cf7e51fa9a764e9ac05`, source label `e3a9c97dd059aa814ea4d34bf1aca910923cf2e8`.
- Final test container: `8716ccc26419e06660d2a4b431d32675909d5c8eabe9e290eb95d7f46776fe03`, healthy, restart count 0.
- Formal-live container remained read-only throughout: `ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255`, image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count 0.
- Runtime database head remained `a11c0d3f0bd0`.

## Final browser acceptance

- Authenticated route/interaction matrix passed for home with expanded/collapsed sidebar, search, More menu, Chat/Agent toggle, Notes, Workspace models/knowledge/prompts/skills/tools, Calendar, Automations, admin users/evaluations/functions, all five admin settings sections, and user general settings. Required content rendered with no redirect, 404, console error, page error, HTTP error, or request failure.
- Temporary Chat used `bifrostapi.Cliproxy/gpt-5.5`, returned HTTP 200 with `temporary:ks3Wdl34i7uZyK0uAAAD`, and rendered `V011_CHAT_UI_OK_93032060` with zero browser errors.
- Temporary Agent deliberately started with an existing Chat draft. The request correctly replaced Chat revision `ecbc1341-534d-4630-b69b-78a98a5032af` with configured Agent revision `3118a971-b710-4845-b9d1-9c807e15bb16`; run `e7409ac2-b4cb-44b1-860e-557991279872` completed and rendered `V011_AGENT_UI_OK_93032060`. It created exactly the four required Agent events and no Chat row.
- Final UI evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-ui-functional-e3a9c97dd059-20260729-114700`; manifest SHA-256 `7807a5b2bc1bea3f40a4bf006d7d2093c6e986c30b92463831dbcc9dcc908452`.
- Screenshots: `output/playwright/v011-functional-acceptance-20260729/final-home-expanded-e3a9c97d.png`, `final-home-collapsed-e3a9c97d.png`, and `default-model-temporary-chat-success-e3a9c97d.png`.

## Default-model acceptance

- The test user's saved model preference was absent and the global default was empty, so the UI selected the first catalog entry, `bifrostapi.Cliproxy/gpt-5-codex-mini`; that catalog entry is not routable by the provider and returned 502.
- Only the isolated test stack's global default was set to the known-good `bifrostapi.Cliproxy/gpt-5.5`; all other model configuration was preserved and no container was rebuilt or restarted.
- Twenty independent authenticated `/api/config` connections all returned the same default. The unauthenticated response intentionally omits this field; an earlier unauthenticated validation was therefore not a cache-invalidation failure.
- A fresh Playwright context loaded an auth state containing only the test token, cleared session storage, confirmed `selectedModels` was absent, and did not write it. The actual completion request automatically used `bifrostapi.Cliproxy/gpt-5.5`, returned HTTP 200 with `temporary:D8V8PeJdhBxKOfLTAAAT`, and rendered `V011_DEFAULT_MODEL_UI_OK_E3A9C97D`; console, page, HTTP, and request-failure arrays were all empty.
- Post-E2E database counts matched the completed four-worker acceptance exactly: Chat `337`, Agent runs `460`, Agent events `4031`. This proves the temporary default-model Chat did not persist product data.
- Default-model evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-default-model-e3a9c97dd059-20260729-121500`; manifest SHA-256 `07f6bd9213eb9ce536121b86c109ec435ccda5455ea99b5578e4489615d7017a`.
- Temporary browser authentication files were removed locally and remotely after the run.

## Validation results

- Historical preflight runtime image: `sha256:38a2aa1b41fd1107254ca8ff36f0d1059ff4d3d0d79bf6b480a2c610520cbe6f`, frontend source `98ae1cd6071d5434f080c98e8195884b391af124`, healthy, restart count 0.
- Formal-live anchor: image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count 0.
- Final full frontend suite under Node `v22.22.0`: `35/35` files and `392/392` tests passed; production build completed after all fixes.
- Production-source exclusion scan: no `delegate_task`, official Sub-agents config/runtime symbols, or `list_chat_files`, `grep_chat_files`, `query_chat_files` matches.
- Fresh isolated SQLite upgraded from empty to the unique merge head `a11c0d3f0bd0`; `alembic current` and `alembic heads` agree.
- Full backend suite on that migrated database: `1256/1256` tests passed with `72` warnings in `30.25s`.
- An initial unmigrated-SQLite run produced the expected schema errors; no product change was retained, and the documented migrated-database procedure passed.
- Earlier source-specific remote evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-functional-98ae1cd6071d-20260729-101400` with acceptance SHA-256 `498862bc31aa75a5b4c4cbaf4341b40bcc76ff344f93a295f0ecdc1096d7f6d9`.
- Four pinned workers (`11`, `12`, `13`, `14`) exposed the same 35-model catalog and accepted Chat/Agent profile revisions; required custom resources `terminals` and `sub_agent` were present.
- Real streamed Chat completed with 13 content deltas and the unique marker. Real AgentScope run `2f34ee50-e66f-4986-a170-e2f4fdd46ba4` completed with exactly one each of `run.running`, `final.started`, `final.delta`, and `run.completed`.
- The acceptance created exactly one Agent run and four matching events while leaving chat count unchanged. Admin detail/event reads returned 200 and an unrelated ordinary user received 404.
- Runtime authenticated surfaces returned representative data: 35 provider models, 60 chats on the page, 5 notes, 15 knowledge bases, 9 prompts, 14 tools, 13 functions, 9 skills, 1 terminal, and 8 workspace models; the three forbidden chat-file tool IDs were absent.
- Runtime database remained at `a11c0d3f0bd0` with all five target columns, all three target indexes, and zero normalized-email duplicates. Test and formal containers were byte-for-byte identical before/after by container/image/status/health/restart anchor; both remained healthy with restart count 0.
- Two pre-write probe attempts stopped before inference because unknown API paths fall through to the SPA with HTTP 200 and OpenAPI is disabled. The final probe correctly distinguishes the HTML fallback from an API route; the production-source exclusion scan remains the route authority.
- Final four-worker evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-functional-e3a9c97dd059-20260729-115100`; acceptance SHA-256 `cc302cc84ab9f3a896a9ec42da8170c1e11f7fa667c64a2b5a319ee0260e696c`.
- Final Agent acceptance run `bf174266-220e-4882-b131-f90d4f72ee44` completed with exactly one each of `run.running`, `final.started`, `final.delta`, and `run.completed`.
- Final API counts were 35 provider models, 60 chats on the page, 5 notes, 15 knowledge bases, 9 prompts, 14 tools, 13 functions, 9 skills, 1 terminal, and 8 workspace models. Required `terminals` and `sub_agent` resources were present and all three forbidden official chat-file tools remained absent.
- The broad final log audit had no traceback, worker exit, OOM, unhandled task exception, or HTTP 5xx. One pre-existing dynamic custom function, `async_context_compression`, logged an optional frontend-language lookup type error and used its documented fallback; Chat and Agent inference still completed. The narrower default-model acceptance window had zero suspicious lines. This custom function was not mutated because it is outside the v0.11 core integration scope.

## Disposition

Functional acceptance of the isolated test stack is complete. No known v0.11 core blocker remains on the tested surfaces. Formal-live promotion is a separate explicitly authorized operation and has not been performed.
