---
name: OpenWebUI Agent Mode
description: A calm, transparent workbench for long-running AI tasks.
colors:
  surface-light: "#f9f9f9"
  surface-subtle-light: "#ececec"
  surface-dark: "#202020"
  surface-subtle-dark: "#292929"
  text-strong-light: "#333333"
  text-muted-light: "#737373"
  text-strong-dark: "#ececec"
  text-muted-dark: "#a3a3a3"
  attention: "#7c3aed"
  warning: "#d97706"
  danger: "#dc2626"
  success: "#059669"
typography:
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.35
rounded:
  control: "8px"
  panel: "12px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
components:
  agent-summary:
    textColor: "{colors.text-muted-light}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "4px 6px"
  attention-panel:
    backgroundColor: "{colors.surface-light}"
    textColor: "{colors.text-strong-light}"
    rounded: "{rounded.panel}"
    padding: "12px"
---

# Design System: OpenWebUI Agent Mode

## Overview

**Creative North Star: "The Calm Workbench"**

Agent Mode lives inside the conversation rather than beside it. Its default surface is a compact, prose-adjacent work log inspired by Claude's calm reading rhythm. When the user must decide, approve, answer, or recover, the interface adopts ChatGPT-like clarity through explicit labels, controls, and state feedback.

The design is restrained and theme-native. It inherits OpenWebUI's Inter and system font stack, neutral OKLCH scale, message width, light and dark themes, and familiar control shapes. It rejects dashboards, nested cards, decorative gradients, glass effects, protocol jargon, and animation that does not communicate state.

**Key Characteristics:**

- Answer-first hierarchy with process as supporting context.
- Compact timeline rhythm, readable summaries, and intentional disclosure.
- Clear attention states for approval, user input, failure, and reconnecting.
- Semantic controls, visible focus, reduced-motion support, and responsive copy.

## Colors

The palette is neutral-first. Accent color is reserved for focus, current action, and required attention rather than decoration.

### Primary

- **Workbench Violet** (`#7c3aed`): focused controls, selected options, and the minimum highlight needed for a user action.

### Neutral

- **Paper Gray** (`#f9f9f9`) and **Soft Divider** (`#ececec`): light-theme tonal separation.
- **Workbench Charcoal** (`#202020`) and **Raised Charcoal** (`#292929`): dark-theme tonal separation without pure black.
- **Strong Ink** (`#333333`) and **Quiet Ink** (`#737373`): primary and supporting light-theme text.
- **Light Ink** (`#ececec`) and **Muted Light Ink** (`#a3a3a3`): primary and supporting dark-theme text.
- **Signal Amber** (`#d97706`), **Failure Red** (`#dc2626`), and **Success Green** (`#059669`): semantic states used with icons and text, never alone.

**The Attention Rarity Rule.** Saturated color appears only for focus, selection, waiting user action, warning, failure, or confirmed success.

## Typography

**Display Font:** Inter with system-ui fallback

**Body Font:** Inter with system-ui fallback

**Label/Mono Font:** Existing OpenWebUI monospace stack for technical details only

**Character:** Compact and familiar. Hierarchy comes from weight, spacing, and text color instead of oversized headings or ornamental type.

### Hierarchy

- **Title** (600, `0.875rem`, 1.35): pending actions and meaningful state changes.
- **Body** (400, `0.875rem`, 1.55): commentary, answers, explanations, and prompts, capped near 70ch where practical.
- **Label** (500, `0.75rem`, 1.35): elapsed time, tool state, metadata, and disclosure labels.
- **Technical detail** (400, `0.75rem`, 1.45): paths, arguments, structured payloads, and error diagnostics inside explicit disclosure.

**The Human Language Rule.** Default text says what happened and what the user should do. Internal event names and runtime vocabulary belong only in technical details.

## Elevation

Agent Mode is flat by default. Separation comes from spacing, typography, dividers, and subtle tonal backgrounds. Shadows are reserved for existing OpenWebUI floating menus and overlays, not transcript rows.

**The Flat Transcript Rule.** Routine progress never becomes a stack of cards. A bordered or tinted panel is earned only by interaction, error recovery, or a bounded detail disclosure.

## Components

### Agent Summary

- **Shape:** compact semantic button or disclosure trigger with an 8px focus contour.
- **Content:** current human-readable state, elapsed time, optional reconnect label, and a consistent chevron icon.
- **Behavior:** expands while running; stays open for pending approval, pending user input, reconnecting, and failure; respects an explicit user collapse until a new attention state arrives.

### Timeline Rows

- **Routine commentary:** body-colored prose with a restrained marker and no card container.
- **Tools:** one concise verb plus human-readable tool label; successful details available through disclosure; failures expanded with actionable summary.
- **Subagents and artifacts:** compact rows using the same state and disclosure vocabulary as tools.

### Approval and User Input

- **Shape:** a single bounded attention panel, 12px radius, clear prompt, and standard buttons or selectable rows.
- **States:** pending, submitting, accepted, rejected, skipped, cancelled, expired, and failed are all visually and semantically distinct.
- **Controls:** keyboard reachable with visible focus; primary action is clear but never duplicated.

### Details

- **Default:** closed for successful routine work and open for failures.
- **Content:** sanitized payload, command, output, path, or error information using the existing mono stack.
- **Constraint:** private reasoning and unsafe raw fields never render.

### Motion

- Use 150 to 200ms opacity and transform transitions for disclosure and chevrons.
- Spinners communicate active work only.
- Under `prefers-reduced-motion`, remove rotation and transition choreography while preserving state text.

## Do's and Don'ts

### Do:

- **Do** keep the final answer as the strongest content in the assistant message.
- **Do** auto-surface new approvals, questions, failures, and reconnecting state.
- **Do** provide successful tool details through intentional disclosure without exposing them by default.
- **Do** use existing OpenWebUI icons, typography, theme tokens, and semantic controls.
- **Do** verify light theme, dark theme, narrow viewport, keyboard focus, text scaling, and reduced motion.

### Don't:

- **Don't** turn every event into a bordered card or dashboard metric.
- **Don't** use nested cards, decorative glass surfaces, neon gradients, or attention-seeking animation.
- **Don't** show raw protocol names, private reasoning fields, internal runtime terminology, or debug payloads in the default scan path.
- **Don't** hide approvals, user questions, failures, or reconnecting state behind an unopened disclosure.
- **Don't** clone Claude or ChatGPT at the expense of OpenWebUI's existing interaction vocabulary.
- **Don't** use color as the only state indicator.
