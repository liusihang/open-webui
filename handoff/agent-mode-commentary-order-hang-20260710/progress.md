# Native phase progress

## 2026-07-10

### Discovery and design

- [x] Reproduced the semantic mismatch against exact conversation/log surfaces.
- [x] Confirmed visible `I will use ...` text is runtime-generated.
- [x] Traced the provider, Pipe, callback parser, bridge, and finalizing boundaries.
- [x] Ran a bounded raw Responses SSE capability probe.
- [x] Verified commentary and final-answer phase arrive before their first text delta.
- [x] Selected native phase passthrough; rejected the extra finalizer call.
- [x] User approved the design.
- [x] Added and committed the design document as `72cf407c0`.

### Implementation

- [ ] Pipe tests RED.
- [ ] Pipe implementation GREEN.
- [ ] Callback parser tests RED/GREEN.
- [ ] Bridge tests RED/GREEN.
- [ ] Synthetic narration removal RED/GREEN.
- [ ] Runtime/backend regression.
- [ ] Independent review and production commit.
- [ ] Image rebuild and isolated acceptance.

## Test results

| Test | Expected | Actual | Status |
|---|---|---|---|
| Raw tool-phase probe | Commentary phase before text and tool | Confirmed | pass |
| Raw final-phase probe | Final phase before first final delta | Confirmed | pass |

## Error log

| Time | Error | Resolution |
|---|---|---|
| 2026-07-10 | agent-browser socket directory unavailable | Switched to in-app browser. |
| 2026-07-10 | Initial SSH denied by sandbox | User granted permission; retry passed. |
| 2026-07-10 | Exact log returned redacted virtual key | Used installed Pipe valves without exposing the key. |
| 2026-07-10 | Wrong connection key produced provider-blocked 403 | Traced and used the actual `bifrostapi` route. |
