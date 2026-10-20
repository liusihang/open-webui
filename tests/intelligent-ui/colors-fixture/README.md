# Native colors: rendered fixture and Chromium checks

This standalone Svelte page imports the production `IntelligentUIBlock` parser/catalog and the application's actual Tailwind stylesheet. The surrounding header and page chrome are fixture-only. The charts, table, cards, text, flow, and controls are the real components. All data is synthetic, and there is no application backend or account dependency.

## Run

From the repository root, after `npm ci`:

```sh
npx vite --config tests/intelligent-ui/colors-fixture/vite.config.ts
```

Open `http://127.0.0.1:4174/`. Add `?theme=dark` for dark mode or `?fixture=contrast` for the adversarial custom-surface test case. The fixture also has a theme toggle.

Use an official Playwright installation with Chromium available:

```sh
PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs \
CHROMIUM_PATH=/absolute/path/to/chromium \
node tests/intelligent-ui/colors-fixture/capture.mjs
```

If `playwright` and its matching Chromium are installed normally, both environment variables may be omitted. `FIXTURE_URL` overrides the fixture URL; `ARTIFACT_DIR` overrides the default `artifacts/intelligent-ui-colors` output directory. The server must remain running while the capture script runs. In a background shell, redirect stdin from `/dev/null` to prevent terminal job-control suspension.

The capture script:

- Renders the complete production catalog fixture at 1280 and 390 CSS pixels, in light and dark modes
- Exercises chart keyboard focus/tooltips, native button and slider updates to colored text bindings, and stable table styles after sorting and searching
- Confirms the authored checkbox accent uses its native checked appearance
- Fails on parser errors, browser errors, or document-level horizontal overflow
- Computes text contrast from actual browser colors, ancestor backgrounds, and opacity; checks table cell text and adversarial inherited headings, input/checkbox labels, colored spans, and descriptions (minimum 4.5:1), plus chart marks (minimum 3:1)
- Captures genuine browser PNGs, restores the default UI state before capture, and writes a machine-readable `verification.json`

The default output includes four review screenshots (`native-colors-{light,dark}-{1280,390}.png`) and four contrast diagnostic screenshots. Generated artifacts are not source files and should not be committed.

## Tests and build

```sh
npm run test:intelligent-ui -- src/lib/components/chat/Messages/IntelligentUI/colors.browser.ts
npx vite build --config tests/intelligent-ui/colors-fixture/vite.config.ts
```

The mounted tests cover parser acceptance, colored/bold bound spans, stable keyed row/cell precedence in a non-selectable table, and explicit series colors after hiding/restoring a peer. The fixture build uses the same production components without replacing them with test mocks.
