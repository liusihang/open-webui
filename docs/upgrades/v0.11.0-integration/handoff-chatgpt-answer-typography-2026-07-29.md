# OpenWebUI ChatGPT-like answer typography handoff

## Goal

- Revert the official v0.11 assistant-answer font reduction from approximately 16 px to 15 px.
- Revert the official v0.11 assistant-answer line-height reduction from the former reading layout to the compact v0.11 layout.
- Measure current public ChatGPT rendered typography and use it as the visual target for OpenWebUI answer content.
- Preserve OpenWebUI functionality and fork-specific Agent/commentary behavior; this task owns presentation only.

## Truth surfaces

- Code: `/Users/liusihang/openwebui/.worktrees/v011-upstream-integration-base`
- Branch: `codex/v011-upstream-integration-base`
- Starting checkpoint: `9643bd7ad189ddc1e65fd6996d8b5c047e6e06e8`
- Test runtime: `http://192.168.2.238:18085/`
- Reference UI: the ChatGPT conversation screenshot supplied by the user plus public CSSOM from `chatgpt.com`; inspect public DOM/computed CSS only.

## Scope and boundaries

- Owned presentation surface: assistant Markdown answer typography and directly related content width.
- Do not read browser cookies, local storage, passwords, tokens, or other credentials.
- Do not copy ChatGPT proprietary source code. Reproduce observable layout values and visual behavior with original OpenWebUI CSS.
- Preserve unrelated concurrent files and changes, including `.playwright-cli/`, `output/`, tool-call-dropdown handoff/package files, and deployment hotpatch files.
- Do not touch formal live. Test-stack deployment is allowed only after focused tests and an explicit rollback anchor.

## Completed actions

- Compared pre-v0.11 fork `665221e19`, official `v0.11.0`, and current integration CSS.
- Confirmed official v0.11 introduced `prose-sm`, `!text-[0.9375rem]`, and `leading-relaxed` for `.markdown-prose`.
- Inspected ChatGPT's current public CSSOM. Relevant observable values are:
  - Base answer text: `16px / 24px` using a system-first sans-serif stack.
  - Paragraph rhythm: 4px base margins and 16px for adjacent paragraphs in the new Markdown styling.
  - H1/H2/H3: 24px/20px/18px with semibold weight.
  - Lists: zero vertical list/list-item margins in the new Markdown styling.
  - Normal conversation content width: 48rem at the relevant desktop container breakpoint.
- Added `AnswerTypography.presentation.test.ts` first and confirmed all three contract tests failed against the v0.11 styling.
- Updated `src/app.css` to an original OpenWebUI implementation of the measured typography.
- Updated `Message.svelte` default width from 58rem to 48rem; widescreen mode remains unchanged.
- Confirmed the same three tests pass after the implementation.

## Checkpoint

- Status: implementation and local build verification complete; test-stack deployment and browser E2E remain.
- Current code files:
  - `src/app.css`
  - `src/lib/components/chat/Messages/Message.svelte`
  - `src/lib/components/chat/Messages/AnswerTypography.presentation.test.ts`

## Verification results

- Node 22 focused Vitest: 36/36 tests passed across the new contract and v0.11 frontend integration suite.
- Prettier: task files passed; repository config emits inherited `pluginSearchDirs` warnings.
- `git diff --check`: passed for task files.
- Vite production build under Node 22:
  - First run reached chunk rendering but exhausted the default approximately 4GB V8 heap.
  - Second run with an 8GB V8 heap completed successfully: 6408 modules transformed, static adapter output written, exit code 0.
  - Existing repository-wide Svelte warnings remain inherited and are not caused by this task.
- Build side-effect check: no tracked build artifacts changed; only the three task code/test files plus this handoff are owned.

## Next steps

1. Commit only the three code/test files and this handoff.
2. Capture the current test-stack container/image/health/restart rollback anchor.
3. Produce and apply a rollbackable test-stack hotpatch; do not touch formal live.
4. Use a real authenticated OpenWebUI conversation to verify computed paragraph, heading, list, and width values.
5. Perform a visual comparison using a structured sample answer and verify previous conversations and Agent transcript rendering still load.

## Stop and rollback conditions

- Stop if matching the reference requires copying non-public/private application code rather than observable CSS behavior.
- Roll back the test-stack hotpatch if the container becomes unhealthy, restart count increases, or existing conversations/Agent transcript rendering regresses.
