---
version: alpha
name: Skaldir Agent Cockpit
description: Dense developer control layer over Hermes — one accent, honest state, no plugin chrome.
colors:
  brand: "var(--ui-accent, #f5be36)"
  ok: "var(--ui-success, #56d364)"
  warn: "var(--ui-warn, #d29922)"
  bad: "var(--ui-danger, #ff6b7d)"
  idle: "var(--ui-text-tertiary, #8b8b8b)"
  surface: "var(--background)"
  card: "var(--card)"
  border: "var(--border)"
typography:
  brand-mark:
    fontFamily: ui-monospace, SFMono-Regular, Menlo, monospace
    fontSize: 11px
    fontWeight: 500
    letterSpacing: "0.22em"
  view-title:
    fontFamily: inherit
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: inherit
    fontSize: 14px
    lineHeight: 1.5
  meta:
    fontFamily: inherit
    fontSize: 12px
    lineHeight: 1.4
  data:
    fontFamily: ui-monospace, SFMono-Regular, Menlo, monospace
    fontSize: 11px
    lineHeight: 1.5
rounded:
  sm: 4px
  md: 8px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
components:
  status-strip:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: 12px
  state-chip:
    textColor: "{colors.ok}"
    rounded: "{rounded.md}"
    padding: 6px
  nav-item-active:
    backgroundColor: "var(--ui-hover)"
    textColor: "{colors.surface}"
    rounded: "{colors.brand}"
    padding: 6px
---

## Overview

Skaldir Agent owns a **Monitor** surface with an **Operate** secondary. The UI is
the control layer a developer leaves open next to the agent: it answers, at a
glance — *What is the goal? What is the agent doing? What is the Voice doing? Is
the path still right? What blocks completion? Which evidence exists?*

Design doctrine:

- **One accent.** Gold (`brand`) marks identity and selection only: the wordmark,
  the active nav entry, the focused action. Never used as decoration.
- **State over story.** Every status is a real backend value (completion state,
  gates, pauses, evidence). No demo numbers, no filler.
- **Three registers of text.** Titles (view-level), body (explanations),
  `data` (monospace, technical values, counts, ids). Monospace = machine truth.
- **Density with rhythm.** Sections breathe by spacing and weight, not by boxes
  and borders. Cards are reserved for genuinely separate concerns.
- **The loop is visible.** WORKING → VERIFYING → INCOMPLETE → VERIFIED_COMPLETE
  is the product's spine — status chip in the strip, hero in the cockpit,
  timeline in Verification.

## Colors

- **brand (gold)** — wordmark, active nav, primary button emphasis.
- **ok / warn / bad / idle** — the only status colors; always paired with a
  written state (never color alone).
- **surface / card / border** — inherited from the app theme; the cockpit adds
  no new background system.

## Typography

System sans for prose and titles; monospace for `data` register (counts, ids,
event rows, goal progress). The brand wordmark is the only letterspaced all-caps
element in the panel.

## Layout

- Sticky **status strip** (top): wordmark · state chip · mode · blockers ·
  goal · refresh. Constant across views.
- Left **nav rail** (176px, grouped: Control / Intelligence / Operations /
  System), sticky below the strip. View content is `flex-1 min-w-0`.
- Cockpit hero: goal + state chip + progress bar; then two 2-column grids
  (Blocking now | Requirements; Evidence | Voice activity); then Recent signal.
- Verification keeps the full gate/timeline depth; Activity merges live KPIs,
  event stream and pause history.

## Components

- **state-chip** — dot + label (+detail); tone determines color, label carries
  the state word.
- **progress bar** — 1.5px track, amber while blockers exist, green when clean.
- **nav-item** — quiet tertiary text; active = hover surface + medium weight.
- **buttons** — bordered, quiet; the destructive/irreversible action (override)
  is always explicit text, never an icon.

## Do's and Don'ts

- Do surface blockers and next actions above the fold in the cockpit.
- Do keep empty states honest ("no verdict yet — nothing has been rejected").
- Do keep the strip on every view — status is never a click away.
- Don't add decorative gradients, glows, or illustrative icons.
- Don't invent metrics; if a value has no source, it does not render.
- Don't clone dashboard tropes (hero stat cards, feature tiles); this is a
  monitor, not a landing page.
