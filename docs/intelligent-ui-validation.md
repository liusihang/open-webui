# v0.12.3 + Intelligent UI validation

## Acceptance follow-up (2026-10-17)

- Fixed equality filters conflating a missing `null` cell with the literal string
  `"null"`. Regression tests cover linked tables, charts and sum/count metrics,
  while retaining non-null numeric/boolean string normalization.
- Routed native UI system copy, errors, plural row counts and accessible labels
  through the existing i18n context. Added Simplified/Traditional Chinese copy and
  retained English fallback; model-provided titles, labels and data remain unchanged.
- The follow-up passed 201 frontend tests, 20 mounted interaction tests and 269
  isolated Python tests with no skips. Chromium checks used the actual components,
  authenticated state route and SQLite in a dedicated synthetic host, including
  the null-filter regression and Chinese rendering. This is not a full-app chat
  journey, production deployment, or production PostgreSQL migration acceptance.

## Verified locally

- Official v0.12.3 is an ancestor of the integration branch through a real merge;
  the fork's original history is retained.
- The custom-feature audit covered the 83-file fork delta and the 17 files also
  changed upstream. See [upgrade notes](fork-v0123.md).
- 194 standard frontend tests passed across 11 files, including the existing
  custom-feature regressions and new parser, Markdown, SSR, persistence and
  ownership tests.
- 18 mounted Svelte/jsdom interaction tests passed against the actual json-render
  renderer. These exercise ordered text/UI blocks, partial streams, local slider
  state, explicit follow-ups, restoration, read-only behavior, linked dataset
  filters, row selection, tabs, numeric drafts and chart-cap disclosure.
- The base integration passed 231 focused offline backend tests and 32 subtests
  after formatting. The expanded prompt/state suite additionally passed 12 tests
  and 29 subtests after bounded string state was added.
  These cover retained Responses/compaction, OnlyOffice, document assets, the
  new MinerU V1 cases, prompt opt-in and atomic UI-state persistence.
- 4 portable bulk-migration tests passed. Eight filesystem-oriented cases were
  not counted as passing: this executor's enclosing `/tmp/.git` causes their
  deliberately strict repository-location guard to reject temporary fixtures.
- Static migration-graph validation found one head, no missing parents, and
  unchanged ancestry for the existing evidence migration. No live migration ran.
- Whole-repository Ruff formatting and configured F-code checks passed. The
  formatting-only cleanup of 25 retained Python files was checked for identical
  Python ASTs before and after formatting.
- Translation extraction added nine missing keys (two new UI keys and seven
  retained-fork keys), with no removal of existing keys or translation values.
- Independent [UI review](intelligent-ui-review.md) found no remaining blocking
  finding within the tested scope.

## Production build and type-check limits

The complete upstream Svelte typecheck has substantial inherited typing debt:
the pre-feature merged baseline reported 5,701 errors and 188 warnings. New
component/type errors found during implementation were corrected. The final
full check exactly matched that baseline (5,701 errors, 188 warnings in 314 files),
with no new diagnostics in the Intelligent UI implementation. Full typecheck
must not be described as green merely because focused new-code checks pass.

A local production build exhausted an explicit 4 GiB Node heap cap. A sequential retry
with a 6 GiB heap and source maps disabled transformed 6,613 modules but was
terminated by the operating system during chunk rendering (exit 137). Therefore
the local production artifact is **not verified**. The base integration commit
`3537fb38f9afe6b95fb6ee9c0698cfed302bd706` subsequently passed GitHub's actual
[production build](https://github.com/liusihang/open-webui/actions/runs/39002233669/job/117307404823),
along with unit, UI integration and Python CI. Expanded-component commits have
independent CI runs linked from [PR #11](https://github.com/liusihang/open-webui/pull/11);
check the latest head rather than treating the earlier build as proof for later changes.

## Not claimed by these tests

The tests do not establish a successful production deployment, live database
migration, full RAG/database integration, browser navigation against a running
backend, or a real model/provider's streaming output. No provider credentials or
production data were used. The model's choice of when to use UI is guided by the
opt-in prompt, not a guarantee that every model always generates a valid or
useful interface. Escaped data and ordinary Markdown remain available as fallback.

## Reproduction

Use Node 22 and the committed npm lockfile. Run:

```sh
npm ci --ignore-scripts
npx svelte-kit sync
npm run test:frontend -- --run
npm run test:intelligent-ui
python3 tests/migration/test_intelligent_ui_prompt.py
python3 tests/migration/test_intelligent_ui_state.py
```

The focused Python tests above use standard-library test doubles around the
actual prompt/state code and atomic persistence method. They do not initialize
the application or contact a database. The broader preserved-feature test suites
have their own isolated-environment prerequisites; never point them at production.
