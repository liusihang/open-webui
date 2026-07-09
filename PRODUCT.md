# Product

## Register

product

## Users

OpenWebUI serves people who want a self-hosted AI workspace, from everyday chat users to technical operators running long tool-assisted tasks. In Agent Mode they are focused on an outcome, but they also need to understand what the agent is doing, recognize when it needs help, and recover confidently from errors or interruptions.

## Product Purpose

Agent Mode should make autonomous work legible without turning the conversation into an operations dashboard. The primary experience remains the answer and the user's task. Process narration, tool activity, approvals, user questions, artifacts, connection state, and failures appear with the minimum prominence needed for trust and control.

## Brand Personality

Calm, capable, transparent. The interface should feel composed during long runs, precise when it asks for action, and familiar to users of mature products such as Claude and ChatGPT while remaining recognizably OpenWebUI.

## Anti-references

- A dense status dashboard embedded inside every assistant message.
- Nested cards, decorative glass surfaces, neon gradients, or attention-seeking animation.
- Raw protocol names, internal runtime terminology, private reasoning fields, or debug payloads in the default scan path.
- Interfaces that hide approvals, user questions, failures, or reconnecting state behind an unopened disclosure.
- A visual clone of Claude or ChatGPT that ignores OpenWebUI's existing typography, themes, message layout, and component vocabulary.

## Design Principles

1. Keep the answer primary. Process context supports the answer and never competes with it.
2. Quiet by default, explicit on demand. Completed routine work recedes; pending actions and failures become unmistakable.
3. Progressive disclosure preserves trust. Show a concise human-readable summary first and reveal technical detail intentionally.
4. State must remain truthful across streaming, reconnect, refresh, approval, cancellation, and completion.
5. Reuse OpenWebUI primitives and language before introducing new interaction patterns or dependencies.

## Accessibility & Inclusion

Target WCAG 2.1 AA for contrast and keyboard operation. Focus states must be visible, interactive rows must use semantic controls, state cannot rely on color alone, live updates should avoid noisy announcements, and motion must respect reduced-motion preferences. Layout and copy must remain usable with text scaling, narrow screens, localization, and long user-generated content.
