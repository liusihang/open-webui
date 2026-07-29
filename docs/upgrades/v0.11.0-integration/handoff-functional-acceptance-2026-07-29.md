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
- Current checkpoint: complete after reopening. The named persisted historical chat now renders in both a fresh authenticated Playwright context and the user's existing Edge profile; the final browser/network/runtime audit is green.
- Stop condition: any application console error, failed required request, wrong runtime image/version, unhealthy/restarted container, formal-live drift, or a core flow that cannot complete.
- Runtime rollback anchor: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-0bb586c8699d/rollback-open-webui-pr7.sh`.
- Test-only default-model rollback: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-default-model-e3a9c97dd059-retry3/rollback-test-default-model.sh`.

## Persisted historical-chat regression

- User reproduction: `/c/05464d8b-52ed-447c-8717-5ff18dc27efb` renders the authenticated sidebar but leaves the main chat pane on a permanent spinner.
- Fresh authenticated Playwright reproduction matched the user-visible state and reported `ReferenceError: getConversationModeDraftCapabilitySnapshot is not defined` from the chat bundle. No `/api/v1/chats/{id}` request was issued, so the failure is before the API/database boundary.
- Root cause: commit `e3a9c97dd059` replaced the imported draft-snapshot helper with `getConversationModeDraftCapabilitySnapshotForMode`, but the persisted-chat `navigateHandler` retained one unconditional call to the removed identifier. New and temporary conversations never execute that path, which is why the previous acceptance missed it.
- TDD red: the new persisted-chat guardrail produced exactly 1 expected failure while the other 30 v0.11 integration tests passed.
- Minimal source fix: load the chat first so its canonical mode is known, then resolve its stored capability snapshot with `getConversationModeDraftCapabilitySnapshotForMode(..., conversationMode, { existingChat: true })`; legacy capability restoration is also mode-gated while ordinary prompt/files restoration remains unchanged.
- TDD green: `v011Integration.presentation`, `ConversationMode.presentation`, and `conversationModeProfiles` now pass 103/103.
- First persisted-chat thin image: `open-webui:v011-hotfix-c764e6ec2c01`, image ID `sha256:c7970f571cdd6b6556403bee0b403f822e73ce8160d8c64b3953a8f97246598a`, source `c764e6ec2c01db17e2f6897e3201cacec59e2fd6`. It replaced only `/app/build`; test container `3b9bdd3db68e...` became healthy with restart count 0, DB head remained `a11c0d3f0bd0`, and formal live remained byte-for-byte unchanged.
- First post-fix E2E proved the original blocker resolved: the named chat and task endpoints both returned 200, the exact URL remained selected, the historical title rendered, and two persisted messages appeared. This surfaced a second issue rather than the spinner: the chat is read-only for the test user, but the frontend still called the owner-only tags endpoint and logged its expected 401.
- Second TDD red/green: a new guardrail first failed because tags were unconditional; the minimal fix loads tags only when `loadedChat.user_id === $user?.id`, otherwise it uses `[]`. Focused tests now pass 104/104 and the full Node 22 frontend suite passes 394/394.
- Final persisted-chat image: `open-webui:v011-hotfix-0bb586c8699d`, image ID `sha256:bdbd84db321857ee8a8cd29326dd000b4c51d77256c2805fe2e817b987ffa63a`, source `0bb586c8699d34c211a5a3686ab61bfe10f2ac90`. Test container `74eff7a80690...` is healthy with restart count 0 and `OOMKilled=false`.
- Final fresh-context E2E: frontend version matched `0bb586c8699d...`; chat detail and task endpoints returned 200; exact URL/title and two persisted messages rendered; the minted test identity correctly saw the foreign chat as read-only; owner-only tag requests, console errors/warnings, page errors, failed responses, and request failures were all zero. Screenshot: `output/playwright/v011-persisted-chat-spinner-20260729/persisted-chat-success-0bb586c8699d.png`.
- Exact user Edge profile: before refresh it still displayed the cached spinner; a normal refresh loaded the historical title, both messages, chat actions, model controls, and owned-chat composer at the same URL. No login or site data was cleared.
- Final runtime evidence: `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-persisted-chat-0bb586c8699d-20260729-132800`; manifest SHA-256 `88a3b85588af6cd4808cd31c6ccb64961d20d85ea305ac933bac175c914ee30b`. Health/DB/version passed, DB head remained `a11c0d3f0bd0`, and the post-deploy log window contained zero fatal, HTTP 5xx, or suspicious error lines.
- Current checkpoint: complete. Formal-live promotion remains a separate authorization and was not performed.

## Hard contracts

- Preserve custom AgentScope authority, approvals, user input, cancellation/reconnect, artifacts, citations, tool timeline, and subagent attribution.
- Official duplicate Sub-agents runtime/UI and `list_chat_files`, `grep_chat_files`, `query_chat_files` must remain absent.
- Avoid destructive or broad state changes. Prefer temporary chats and read-only route/API checks; record any test data before creating it.

## Acceptance matrix

| Area                              | Required evidence                                                                                                                                   | Status |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| Runtime identity and health       | Exact image/version, `/health`, `/health/db`, model catalog, restart/OOM/log audit                                                                  | passed |
| Authentication and navigation     | Authenticated home, expanded/collapsed sidebar, search, Notes, Workspace, settings/admin routes                                                     | passed |
| Chat and provider path            | Temporary chat, real streamed inference, model/reasoning controls, no console/request failure                                                       | passed |
| Custom AgentScope path            | Agent mode entry plus preserved runtime/event behavior using existing acceptance probes                                                             | passed |
| Sidebar/channels/folders/history  | Chat list/action menu, channels, folders, pagination, and persisted historical-chat detail loading in fresh Playwright plus the user's Edge profile | passed |
| Notes/workspace/admin             | Route rendering and representative API data for models, knowledge, prompts, tools, functions, skills                                                | passed |
| Terminal/retrieval/tool contracts | Existing focused suites plus runtime catalog/permission probes; exclusions remain absent                                                            | passed |
| Security/config/migrations        | Existing focused suites, migration head/runtime DB checks, config/auth endpoints                                                                    | passed |
| Regression and exclusions         | Focused/full automated tests, production-source exclusion scan, browser console/log audit                                                           | passed |

## Final accepted source and runtime

- Source commits introduced during the browser acceptance loop:
  - `17cf77c906d2`: recognize v0.11 temporary chat IDs.
  - `93032060d9d59170b9f9c5dbb13e43c929eab9c6`: restore socket event dispatch, including the missing event `type` binding and guarded reload/list behavior.
  - `e3a9c97dd059aa814ea4d34bf1aca910923cf2e8`: isolate stored mode-profile drafts so a Chat revision cannot be submitted as an Agent revision.
  - `c764e6ec2c01db17e2f6897e3201cacec59e2fd6`: restore persisted chats with the mode-aware draft helper after loading their canonical mode.
  - `0bb586c8699d34c211a5a3686ab61bfe10f2ac90`: skip owner-only tag reads for authorized read-only chats.
- Final test image: `open-webui:v011-hotfix-0bb586c8699d`, image ID `sha256:bdbd84db321857ee8a8cd29326dd000b4c51d77256c2805fe2e817b987ffa63a`, source label `0bb586c8699d34c211a5a3686ab61bfe10f2ac90`.
- Final test container: `74eff7a80690af0e1335e168a0dd0aeccfce2d584a99bf25549058f2697e26ef`, healthy, restart count 0, `OOMKilled=false`.
- Formal-live container remained read-only throughout: `ae1b858332b7bbe252359d46e610a7b595fa6bad36b459187955737cb386e255`, image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count 0.
- Runtime database head remained `a11c0d3f0bd0`.

## Final browser acceptance

- Authenticated route/interaction matrix passed for home with expanded/collapsed sidebar, search, More menu, Chat/Agent toggle, Notes, Workspace models/knowledge/prompts/skills/tools, Calendar, Automations, admin users/evaluations/functions, all five admin settings sections, and user general settings. Required content rendered with no redirect, 404, console error, page error, HTTP error, or request failure.
- Temporary Chat used `bifrostapi.Cliproxy/gpt-5.5`, returned HTTP 200 with `temporary:ks3Wdl34i7uZyK0uAAAD`, and rendered `V011_CHAT_UI_OK_93032060` with zero browser errors.
- Persisted historical-chat acceptance passed at `/c/05464d8b-52ed-447c-8717-5ff18dc27efb`: chat/task 200, exact title and two messages rendered, correct editable/read-only behavior by identity, no tag-access error, and zero browser/network failures. The user's existing Edge tab also rendered the conversation after a normal refresh.
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
- Final full frontend suite under Node `v22.22.0`: `35/35` files and `394/394` tests passed; the final production build completed from `0bb586c8699d...` after all fixes.
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

Functional acceptance of the isolated test stack is complete, including the previously missing persisted historical-chat detail path. The named chat passed fresh-browser E2E and the user's existing Edge profile now renders it after refresh. Formal-live promotion remains separately authorized and has not been performed.
