# OpenWebUI ChatGPT-like answer typography handoff

## Goal

- Remove the compact official v0.11 assistant typography from this fork.
- Use current public ChatGPT rendered CSS as the typography reference without copying proprietary source.
- Keep the local OpenWebUI answer column aligned with the existing composer instead of mechanically copying ChatGPT's narrower column.
- Preserve fork-specific Agent/commentary behavior and all non-presentation functionality.

## Truth surfaces

- Code worktree: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`
- Branch: `codex/v011-upstream-integration-base`
- Final source commit: `4934cdf59bbf2d7661d138d7dc7959bd83e93dfb`
- Test runtime: `http://192.168.2.238:18085/`
- Final test image: `open-webui:v011-hotfix-4934cdf59bbf`
- Final test image ID: `sha256:13d2290cbd506929155f2435c94850f716c7bd66b47710c3c5ba7937789209a3`
- Formal runtime: read-only throughout this task.
- Reference UI: the user's ChatGPT screenshot plus public `chatgpt.com` DOM/CSSOM.

## Scope and boundaries

- Owned code: assistant Markdown typography, its renderer wrapper, and answer-column width.
- Browser credentials, cookies, local storage, password stores, and private ChatGPT source were not inspected.
- Observable ChatGPT CSS was reimplemented with original OpenWebUI utilities.
- `.playwright-cli/`, `output/`, and unrelated concurrent handoff/worktree files remain uncommitted user/test artifacts.
- Formal live was not changed.

## Final implementation

- `src/app.css`
  - Answer body: `16px / 24px`.
  - Base paragraph margins: `4px`; adjacent paragraphs: `16px`.
  - H1/H2/H3: `24/20/18px`, semibold, with `32/28/28px` line heights.
  - Strong text: semibold.
  - Lists/list items: zero vertical margins.
  - ChatGPT-like blockquote, rule, image, and code rhythm.
- `src/lib/components/chat/Messages/ContentRenderer.svelte`
  - Restored the official v0.11.0 `.markdown-prose` wrapper that had been lost during fork migration.
  - Preserved fork-added citation props.
- `src/lib/components/chat/Messages/Message.svelte`
  - Final default width is `58rem`, matching `MessageInput.svelte`.
  - A temporary 48rem implementation was rejected after the user correctly identified that it was narrower than the composer.
- `AnswerTypography.presentation.test.ts`
  - Covers size/line height, paragraph/heading/list/emphasis rhythm, renderer wiring, and answer/composer width alignment.

## Important visual decision

- The official compact `15px` presentation and its tighter leading are removed.
- The old pre-v0.11 `16px / 28px` line height was not restored literally because current ChatGPT measures `16px / 24px`; the final target follows ChatGPT for typography.
- ChatGPT's common 48rem column was not retained because this fork's composer is 58rem. Local structural alignment takes priority: answer and composer both measure 928px at the 1280px acceptance viewport.

## Commits

- `d3d05066b497d622ac6cedd63fe845cf2631e686` — initial ChatGPT-like typography CSS and presentation tests.
- `aaeb9fb5736acf09f452c80f6f0f9ff0c3211c47` — restore the missing ordinary assistant Markdown typography wrapper.
- `4934cdf59bbf2d7661d138d7dc7959bd83e93dfb` — align answer width with the 58rem composer.

## Verification

- Focused Vitest: 37/37 passed.
- Prettier on Svelte/TypeScript task files: passed (inherited `pluginSearchDirs` warnings only).
- Python fixture/probe compilation and Shell syntax checks: passed.
- `git diff --check`: passed.
- Production frontend build: passed under Node 22 with an 8GB V8 heap; 6408 modules transformed and static adapter output written.
- Browser E2E on a temporary authenticated test user:
  - Previous-chat detail page rendered with spinner count `0`.
  - Answer column: width/max-width `928px`.
  - Composer: width/max-width `928px`.
  - Paragraph: `16px / 24px`.
  - H1/H2/H3: `24/20/18px`, semibold.
  - Adjacent paragraph margins: `16px`; list-item vertical margins: `0`.
- Successful real-browser answer screenshot:
  - `output/playwright/v011-chatgpt-answer-typography-20260729/browser-answer-4934cdf59bbf.png`
- Computed-style evidence:
  - `output/playwright/v011-chatgpt-answer-typography-20260729/browser-computed-styles-4934cdf59bbf.json`

## Runtime acceptance

- Test container: healthy, restart count `0`, OOM killed `false`.
- Test frontend source: `4934cdf59bbf2d7661d138d7dc7959bd83e93dfb`.
- Database revision unchanged: `a11c0d3f0bd0`.
- Functional API probe: passed.
- Persisted chat probe: 60 chats visible; selected Agent chat had two readable messages.
- Recent fatal/HTTP 5xx scan: zero matches.
- Formal container remained on image `sha256:ab6d8f1816a40750a98bdcb18e5a7bd419869c43825a66631acc7f718e6f469b`, healthy, restart count `0`.
- Final evidence directory:
  - `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/evidence/v011-chatgpt-answer-typography-4934cdf59bbf-20260729-153703`
- Evidence manifest SHA-256:
  - `827b14909c053ee12f9b34e3e5a362ccbdbdb36c7201c8b97acfa781adc2b9dc`
- Rollback script:
  - `/home/aiserver/staging/openwebui-pr7-eea11194ed-test/backups/pre-hotpatch-4934cdf59bbf/rollback-open-webui-pr7.sh`

## Cleanup

- The temporary browser-only test user and its chat/share records were deleted successfully.
- Both local temporary credential/fixture files were unlinked and verified absent.
- The Playwright CLI browser and Codex in-app browser test tabs were closed.

## Current status

- Complete on the isolated test stack.
- Ready for user visual review at `http://192.168.2.238:18085/` after a hard refresh.
- No formal-live cutover was performed or authorized.
