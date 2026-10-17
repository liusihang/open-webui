# Isolated app bridge test contract

AppBlock accepts `title`, `html` (24 KiB), optional `css` (8 KiB), `js` (16 KiB), `height` (integer 160–900), and `stateKeys` (up to 16 unique scalar-state keys). It is a leaf element. Execution starts only after the containing assistant response is complete.

HTML is a small inert allowlist. No inline scripts, event handlers, links, images, forms, embedded frames, SVG, or external resources survive. CSS is fenced inside the opaque iframe and its resource loads are blocked by CSP. The iframe has only `allow-scripts`, never `allow-same-origin`.

JavaScript runs in a Blob worker with no window, document, parent, or DOM access. Blob-worker CSP inherits the iframe's deny-default policy; fetch, WebSocket, imports and external scripts are blocked. Arbitrary iframe JavaScript is intentionally not supported because iframe sandboxing alone does not stop self-navigation. `navigate-to` is only defense in depth; containment does not depend on that unsupported CSP directive.

The worker exposes these methods:

- `ui.getState(key)` reads the latest shared scalar value.
- `ui.setState(key, value)` writes a declared key (finite bounded number, boolean, or <=256-character string). Host context validation remains authoritative.
- `ui.onState(callback)` runs after the initial shared-state message and subsequent updates.
- `ui.on(id, 'click' | 'input' | 'change', callback)` receives a bounded string value or checkbox boolean.
- `ui.setText(id, text)` replaces text content, never HTML.
- `ui.setValue(id, value)` updates an input/select/textarea/output/progress/meter value.
- `ui.setStyle(id, property, value)` sets width/height/min/max dimensions (bounded px or percent) or color/backgroundColor/borderColor (hex only).
- `ui.draw(canvasId, commands)` draws at most 256 bounded commands per message: `['clear']`, `['rect',x,y,w,h,'#hex']`, `['circle',x,y,r,'#hex']`, `['line',x1,y1,x2,y2,'#hex',lineWidth]`, or `['text',text,x,y,'#hex',fontSize]`. Canvas backing dimensions are capped at 1200×900. The bridge accepts at most 100 worker messages/second.

The frame/host bridge checks source window, opaque origin, per-document random token, command type, declared keys, and primitive limits. Shared-state changes do not restart the worker. Regenerated app source does.

WritingBlock accepts `title`, `content` (16 KiB), and optional `format` (`text` or `markdown`). It supports edit, copy, expanded scrolling, and persistence at `writing:${__key}` through host context. Append-only streamed suffixes merge with local edits. Revisions and overflow preserve local edits and show a “Use generated text” action. Markdown uses an explicit DOMPurify tag allowlist without URL-bearing tags/attributes. No remote resources or executable markup render.

## Test commands

- `npx vitest run src/lib/utils/intelligent-ui/blocks.test.ts --pool forks --poolOptions.forks.singleFork`
- `npx vitest run --config vitest.intelligent-ui.config.ts src/lib/components/chat/Messages/IntelligentUI/isolated.browser.ts`
- `PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs CHROMIUM_PATH=/usr/bin/chromium node tests/intelligent-ui/app-sandbox.chromium.mjs`

The Chromium test checks real worker startup/CSP inheritance, rejects HTML/CSS/fetch/import network requests, checks opaque-frame identity, and exercises linked label/bar/canvas changes and state writes. A successful run is required before treating the security smoke as verified; jsdom does not execute the iframe worker.
