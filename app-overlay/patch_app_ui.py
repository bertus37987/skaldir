"""Skaldir UI correction overlay — fonts, type floor, zoom, accessibility names,
and the chat's one thinking indicator.

`patch_app.py` carries the IDENTITY overlay (name, mark, theme, panel). This
module carries the corrections the independent UI audit found against the product
design (`Documents/Design_Docs/Agent_Audits/agent_3/GUI_UX_AUDIT.md` and
`TOP_UI_GAPS.md`); `patch_app.py` imports and extends both lists, so a build
applies them through one mechanism with the same guarantees: every anchor must
hit exactly once or the build fails loudly (a Hermes update that moves a line
fails the build instead of shipping a half-fixed UI), and nothing here touches
runtime data, sessions, provider auth or the engine.

What each patch is for (audit finding → design line):

  * fonts + type floor  — 01_PRODUCT_UX_DESIGN.md:46 (Geist / Space Grotesk /
    Geist Mono, no 7-10px labels). P1 #2 in the audit; the shipped build used a
    Windows-only face and 9px tab labels.
  * default zoom 100%   — 01_PRODUCT_UX_DESIGN.md:15 (the UI must not be shrunk
    to fit more chrome). The shipped default was a 90% page zoom.
  * statusbar aria-label / composer textbox semantics — the audit's axe findings
    (`button-name` critical, missing multiline semantics).
  * the chat's working indicator uses thinking-orbs — THINKING_ORBS prompt:
    "keine separate eigene Thinking-Animation" (the braille spinner went).

Anchors verified against the Hermes checkout (`apps/desktop`) on 17.09.2026;
`tools/check_ui_overlay_anchors.py` re-checks them without building.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 1) The faces — Geist (UI), Space Grotesk (brand), Geist Mono (code)          #
# --------------------------------------------------------------------------- #

FONT_FACES_CSS = """\
/* Skaldir typography (01_PRODUCT_UX_DESIGN.md §3): Geist for the interface,
   Space Grotesk for the wordmark, Geist Mono for code. Vendored with the app
   (src/gv-fonts/, OFL-1.1) — no CDN, no network fetch, and no Windows-only
   face in the stack. The --dt-font-* tokens below decide where each lands. */
@font-face {
  font-family: 'Geist';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url('./gv-fonts/GeistSans-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'Geist';
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url('./gv-fonts/GeistSans-Medium.woff2') format('woff2');
}
@font-face {
  font-family: 'Geist';
  font-style: normal;
  font-weight: 600;
  font-display: swap;
  src: url('./gv-fonts/GeistSans-SemiBold.woff2') format('woff2');
}
@font-face {
  font-family: 'Geist Mono';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url('./gv-fonts/GeistMono-Regular.woff2') format('woff2');
}
@font-face {
  font-family: 'Geist Mono';
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url('./gv-fonts/GeistMono-Medium.woff2') format('woff2');
}
@font-face {
  font-family: 'Space Grotesk';
  font-style: normal;
  font-weight: 500;
  font-display: swap;
  src: url('./gv-fonts/SpaceGrotesk-Medium.woff2') format('woff2');
}
@font-face {
  font-family: 'Space Grotesk';
  font-style: normal;
  font-weight: 700;
  font-display: swap;
  src: url('./gv-fonts/SpaceGrotesk-Bold.woff2') format('woff2');
}

"""

#: The shipped UI stack. 'Segoe WPC' is a Windows-only VS Code face: on Linux it
#: resolved to nothing and the first REAL face was the system UI font, while the
#: design names Geist. Geist first, the platform faces as fallback, emoji last.
FONT_SANS_OLD = """\
    --dt-font-sans:
      'Segoe WPC', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif,
      'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji', emoji;
"""

FONT_SANS_NEW = """\
    --dt-font-sans:
      'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif,
      'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji', emoji;
    /* The brand face: wordmark only, never body text. Consumers fall back to the
       UI face when the overlay's faces are absent (a stock Hermes tree). */
    --dt-font-brand: 'Space Grotesk', 'Geist', ui-sans-serif, system-ui, sans-serif;
"""

FONT_MONO_OLD = """\
    --dt-font-mono:
      Menlo, Monaco, 'SF Mono', 'Courier Prime', monospace, 'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol',
      'Noto Color Emoji', emoji;
"""

FONT_MONO_NEW = """\
    --dt-font-mono:
      'Geist Mono', 'JetBrains Mono', Menlo, Monaco, 'SF Mono', 'Courier Prime', monospace, 'Apple Color Emoji',
      'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji', emoji;
"""

#: `src/styles.css` is only the CSS default. The RUNNING stack comes from the
#: active theme: `themes/context.tsx` publishes the theme's own `fontSans` onto
#: `--dt-font-sans` at every theme application, so patching styles.css alone left
#: "Segoe WPC" on screen (measured 17.09. in the built app, dpr 1, type floor
#: active). SYSTEM_SANS/SYSTEM_MONO are what the builtin presets and the God's
#: Voice theme carry — patch them at the source, in one place.
THEME_SANS_OLD = """\
const SYSTEM_SANS =
  '"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", system-ui, sans-serif, ' +
  EMOJI_FALLBACK

const SYSTEM_MONO = 'Menlo, Monaco, "SF Mono", "Courier Prime", monospace, ' + EMOJI_FALLBACK"""

THEME_SANS_NEW = """\
// Skaldir typography (01_PRODUCT_UX_DESIGN.md §3): Geist is the interface face,
// Geist Mono the code face. Both ship with the app (src/gv-fonts, OFL-1.1) and
// are resolved by the same token path as before — no Windows-only face, no theme
// that silently keeps the old stack.
const SYSTEM_SANS =
  '"Geist", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif, ' + EMOJI_FALLBACK

const SYSTEM_MONO = '"Geist Mono", "JetBrains Mono", Menlo, Monaco, "SF Mono", "Courier Prime", monospace, ' + EMOJI_FALLBACK"""


# --------------------------------------------------------------------------- #
# 2) The type floor and the focus ring                                         #
# --------------------------------------------------------------------------- #

#: Tailwind's arbitrary-value utilities compile to plain classes, so the
#: "no 7/9/10px labels" rule is ONE unlayered rule instead of 154 edits — and it
#: only raises what was below it (0.4375rem…0.68rem = 7px…10.88px). Unlayered CSS
#: wins over Tailwind's `@layer utilities`, so this holds without `!important`.
#: The focus ring is the audit's "no visible focus indicator" finding: token
#: coloured, on every interactive role, keyboard-only (`:focus-visible`).
TYPE_FLOOR_CSS = """\
/* Skaldir type floor + keyboard focus (01_PRODUCT_UX_DESIGN.md:33-46, audit
   P1 #6). One token-level rule, not 154 sprinkled sizes: no UI label renders
   below 11px. */
.text-\\[0\\.4375rem\\], .text-\\[0\\.5rem\\], .text-\\[0\\.56rem\\], .text-\\[0\\.5625rem\\],
.text-\\[0\\.58rem\\], .text-\\[0\\.6rem\\], .text-\\[0\\.62rem\\], .text-\\[0\\.625rem\\],
.text-\\[0\\.64rem\\], .text-\\[0\\.65rem\\], .text-\\[0\\.66rem\\], .text-\\[0\\.67rem\\],
.text-\\[0\\.68rem\\], .text-\\[7px\\], .text-\\[9px\\], .text-\\[10px\\] {
  font-size: 0.6875rem;
}

/* Skaldir type LADDER (AUFBAU/09 §a K1, AUFBAU/03 §c E1) — the same mechanism
   one step up: 17 arbitrary sizes / 194 finds sit BETWEEN 11.04px and 16.32px,
   i.e. legal but off the ladder. One rule block instead of 194 single edits.
   Snap = nearest step, ties round UP (never below the metadata step); the
   mapping is measured, not guessed:
     11px  0.6875rem <- 0.69 (1) | 0.7 (69) | 0.71 (5) | 0.72 (24) | 0.73 (5) | 0.74 (1)
     13px  0.8125rem <- 0.75 (40) | 0.78 (9) | 0.8 (14) | 0.82 (5) | 0.85 (3) | 12px (3)
     15px  0.9375rem <- 0.875 (2) | 0.9 (1) | 0.95 (6)
     16px  1rem      <- 1.02 (4)          — the intro surface, see the exception note
   `text-[1rem]` (2 finds, 16px) is already on the ladder and stays untouched.
   The class list is DELIBERATELY DISJOINT from the 16 floor classes above: a
   class listed twice would be overwritten by the later rule while the floor pin
   stays green (AUFBAU/09 §e nr. 1). Only `font-size` — never an outline — so
   the composer's editor exception below stays the only ring rule.

   THE LADDER IS 11 | 13 | 15 — WITH EXACTLY ONE EXCEPTION (decision of 18.09.,
   AUFBAU/14 E7 + the K13 conflict, see the K13 anchor below): **16px is allowed
   for DIALOG TITLES only**. Reason: the update dialog's title had to come down
   from 20px against an 11px label (E3 wants <= 1.5), and 15px would have
   collided with the `text-sm` step that the scale-class rule below snaps onto
   15px — the two would then describe different things. 16/11 = 1.45 keeps the
   dialog legal and leaves 15px unambiguous as "the read step". Measured sites
   at 16px: the dialog title (`text-base`, K13) and the two intro-reveal sites
   (`text-[1.02rem]`, 4 finds). The intro one is an OVERLAY surface (one-time
   intro, no dialog) and is the single documented exception to "16px = dialog
   titles" — named here instead of being silently smoothed over (it is also
   §6 nr. 6 of the wave report: an open one-line decision, not a claim). */
.text-\\[0\\.69rem\\], .text-\\[0\\.7rem\\], .text-\\[0\\.71rem\\], .text-\\[0\\.72rem\\],
.text-\\[0\\.73rem\\], .text-\\[0\\.74rem\\] {
  font-size: 0.6875rem;
}

.text-\\[0\\.75rem\\], .text-\\[0\\.78rem\\], .text-\\[0\\.8rem\\], .text-\\[0\\.82rem\\],
.text-\\[0\\.85rem\\], .text-\\[12px\\] {
  font-size: 0.8125rem;
}

.text-\\[0\\.875rem\\], .text-\\[0\\.9rem\\], .text-\\[0\\.95rem\\] {
  font-size: 0.9375rem;
}

.text-\\[1\\.02rem\\] {
  font-size: 1rem;
}

/* Skaldir type ladder, second half — the SCALE CLASSES (AUFBAU/09 §a K2, the
   rest of it): `text-xs` (12px, 355 finds in 147 files) and `text-sm` (14px,
   109 finds) are Tailwind scale steps that sit NEXT TO the ladder, not under
   the floor — the floor never reached them, so every `text-xs` label rendered
   8 % too small. Same mechanism as above (unlayered beats `@layer utilities`),
   `font-size` ONLY: the utility's own line-height token stays (0.75rem/1rem =
   1.23 at 13px and 0.875rem/1.25rem = 1.33 at 15px), exactly the reasoning the
   K3 caption token documents. Together with the rules above the whole tree is
   on {11,13,15,16}; the measured census before this rule was 83.7 % on the
   ladder with all 14 deviants at 12px (wave A report §4).
   NOT listed here on purpose: `text-base` (16px, the dialog-title exception),
   `text-lg`/`text-xl`+ (dialog titles, K13 territory) and `text-[1rem]`. */
.text-xs {
  font-size: 0.8125rem;
}

.text-sm {
  font-size: 0.9375rem;
}

:where(a, button, summary, [role='button'], [role='menuitem'], [role='menuitemradio'], [role='tab'], [tabindex]):focus-visible,
:where(input, select, textarea):focus-visible {
  outline: 2px solid var(--ring, currentColor);
  outline-offset: 2px;
}

/* Text editors keep the CARET as their focus affordance. The chat composer is a
   contenteditable with border-radius 0 inside a 22px-rounded pill, so the ring
   above drew a square dark frame inside the round bar (measured 17.09. on a real
   click: `outline: color(srgb .09 .09 .09 / .94) solid 2px`, offset 2px, radius 0)
   — the owner reported it as "a black bar appears". No ring on editors. */
[contenteditable='true']:focus-visible {
  outline: none;
}

"""

# --------------------------------------------------------------------------- #
# 3) Zoom: the shipped 90% default shrinks the UI to fit more chrome           #
# --------------------------------------------------------------------------- #

ZOOM_OLD = "export const DEFAULT_ZOOM_LEVEL = Math.log(0.9) / Math.log(ZOOM_FACTOR_BASE)"
ZOOM_NEW = """\
// UI audit P1 #2: the UI must not be scaled down to fit more chrome
// (01_PRODUCT_UX_DESIGN.md:15), and a 0.9 page zoom silently shrank every label
// the type scale sets (13px body text measured as 11.7px on screen). 100% is the
// baseline; the Appearance control still offers every other step.
export const DEFAULT_ZOOM_LEVEL = Math.log(1) / Math.log(ZOOM_FACTOR_BASE)"""

ZOOM_DOC_OLD = "/** Appearance 90% preset. Fresh installs + Actual Size / Ctrl+0. */"
ZOOM_DOC_NEW = "/** Appearance 100% (actual size). Fresh installs + Actual Size / Ctrl+0. */"

#: A persisted 90% from the pre-fix build is not a user choice — it is the OLD
#: default. Treating exactly that value as "unset" lets the fix reach existing
#: installs while honouring anyone who really picked a scale.
ZOOM_MIGRATE_OLD = """\
    const level = Number(raw?.zoomLevel)

    return Number.isFinite(level) ? level : null"""

ZOOM_MIGRATE_NEW = """\
    const level = Number(raw?.zoomLevel)

    // UI fix (audit P1 #2): 90% was the shipped default, not a choice. Only that
    // exact level migrates to the 100% baseline; any other stored value is the
    // user's and is returned untouched.
    const legacyDefault = Math.log(0.9) / Math.log(1.2)

    if (Number.isFinite(level) && Math.abs(level - legacyDefault) < 1e-9) {
      return null
    }

    return Number.isFinite(level) ? level : null"""


# --------------------------------------------------------------------------- #
# 4) Accessibility names the audit's axe run reported (P1 #6)                  #
# --------------------------------------------------------------------------- #

#: axe `button-name` (critical): the status bar renders icon-only buttons with a
#: tooltip and no accessible name — a screen reader announced only "button". The
#: item's own `title`/`toggleLabel` IS its name (both plain text), so the fix
#: needs no new strings and cannot drift from the tooltip.
A11Y_STATUSBAR_OLD = """\
        className={cn(STATUSBAR_ACTION_CLASS, item.className)}
        disabled={item.disabled}
        onClick={event => {"""

A11Y_STATUSBAR_NEW = """\
        aria-label={typeof item.title === 'string' ? item.title : item.toggleLabel}
        className={cn(STATUSBAR_ACTION_CLASS, item.className)}
        disabled={item.disabled}
        onClick={event => {"""

#: The composer editor is a contentEditable div. What is genuinely missing is the
#: MULTILINE semantic; `role="textbox"` is ALREADY in the stock source (the same
#: attribute block, right above `spellCheck`). The first version of this patch
#: added a second `role="textbox"` on top of it — invalid ARIA and a hard
#: TypeScript error, measured 18.09. on the built tree:
#:   * stock `~/.hermes/hermes-agent/.../composer/index.tsx`: 1x role="textbox"
#:   * mirror `~/.hermes/godsv/app-src/.../composer/index.tsx`: 2x role="textbox"
#:   * `tsc -p tsconfig.json --noEmit`: `index.tsx(1172,9): error TS17001: JSX
#:     elements cannot have multiple attributes with the same name`
#:   * axe: `nested-interactive` on 48 nodes
#: So the patch now adds exactly the missing attribute and touches nothing else —
#: one `role="textbox"` (the stock one), one `aria-multiline="true"`.
#:
#: Split anchor (pattern C): two adjacent stock lines with the insertion BETWEEN
#: them, so the pair is consumed by one run and a second run over the same tree
#: fails loudly (count 0 != 1) instead of adding a second `aria-multiline`.
#: The old anchor was prefix-style (`old in new`) and would have doubled silently.
A11Y_COMPOSER_OLD = """\
        contentEditable={!inputDisabled}
        data-placeholder={placeholder}"""

A11Y_COMPOSER_NEW = """\
        contentEditable={!inputDisabled}
        aria-multiline="true"
        data-placeholder={placeholder}"""

#: axe `landmark-one-main` + `region` (moderate): the chat surface is the page's
#: primary content but carried no landmark, so screen-reader users could not jump
#: to it. `role="main"` on the focused chat surface — the unfocused tiles of a
#: split view must NOT each claim to be main, which is why it is tied to the same
#: flag that already marks focus.
A11Y_CHAT_MAIN_OLD = """\
      data-chat-unfocused={surfaceFocused ? undefined : ''}"""
A11Y_CHAT_MAIN_NEW = """\
      data-chat-unfocused={surfaceFocused ? undefined : ''}
      role={surfaceFocused ? 'main' : undefined}"""


# --------------------------------------------------------------------------- #
# 5) One thinking indicator: the chat uses the SAME thinking-orbs              #
# --------------------------------------------------------------------------- #

GLYPH_IMPORT_OLD = "import { GlyphSpinner } from '@/components/ui/glyph-spinner'"
GLYPH_IMPORT_NEW = "import { GvOrb } from '@/components/ui/gv-orb'"

#: Each of these five sites animated the braille spinner for exactly the fact the
#: panel shows as an orb (the audit's "konkurrierende Thinking-Indikatoren"). The
#: replacement keeps the surrounding markup, the label and the class and swaps
#: only the indicator: `working` for a running row, `weaving` for subagent work
#: (the design's own mapping: Subagent-Aggregation -> weaving). The orb itself is
#: the vendored library, published by the plugin (`publishOrbRuntime`) and read by
#: the overlay's `components/ui/gv-orb.tsx` — never a second animation.
ORB_SWAPS: list[tuple[str, str, str]] = [
    (
        "src/app/chat/composer/status-stack/status-row.tsx",
        """\
    return (
      <GlyphSpinner
        ariaLabel={s.running}
        className="text-[0.85rem] leading-none text-emerald-500/80"
        spinner="braille"
      />""",
        """\
    return (
      <GvOrb
        ariaLabel={s.running}
        className="text-[0.85rem] leading-none text-emerald-500/80"
        state="working"
      />""",
    ),
    (
        "src/app/chat/composer/status-stack/status-row.tsx",
        """\
    return (
      <GlyphSpinner
        ariaLabel={s.running}
        className="text-[0.85rem] leading-none text-muted-foreground/80"
        spinner="braille"
      />""",
        """\
    return (
      <GvOrb
        ariaLabel={s.running}
        className="text-[0.85rem] leading-none text-muted-foreground/80"
        state="working"
      />""",
    ),
]

ORB_SWAPS.extend([
    (
        "src/app/chat/composer/status-stack/index.tsx",
        """\
              <GlyphSpinner
                ariaLabel={t.statusStack.running}
                className="text-[0.8rem] leading-none text-muted-foreground/80"
                spinner="braille"
              />""",
        """\
              <GvOrb
                ariaLabel={t.statusStack.running}
                className="text-[0.8rem] leading-none text-muted-foreground/80"
                state="working"
              />""",
    ),
    (
        "src/app/chat/composer/status-stack/subagent-section.tsx",
        """\
      <GlyphSpinner
        ariaLabel={item.status === 'queued' ? t.agents.queued : t.agents.running}
        className="mt-0.5 shrink-0 text-(--ui-purple)"
        spinner="braille"
      />""",
        """\
      <GvOrb
        ariaLabel={item.status === 'queued' ? t.agents.queued : t.agents.running}
        className="mt-0.5 shrink-0 text-(--ui-purple)"
        state={item.status === 'queued' ? 'breathing' : 'working'}
      />""",
    ),
    (
        "src/app/chat/composer/status-stack/subagent-section.tsx",
        """\
          <GlyphSpinner
            ariaLabel={live.some(item => item.status === 'running') ? t.agents.running : t.agents.queued}
            className="text-(--ui-purple)"
            spinner="braille"
          />""",
        """\
          <GvOrb
            ariaLabel={live.some(item => item.status === 'running') ? t.agents.running : t.agents.queued}
            className="text-(--ui-purple)"
            state="weaving"
          />""",
    ),
    # The composer's profile-swap overlay and its hydration pill: both are
    # "connecting to the backend" — the design's mapping for connecting/API/MCP
    # /Provider is the `connecting` orb, and both used to tick braille.
    (
        "src/app/chat/chat-swap-overlay.tsx",
        """\
        <GlyphSpinner className="w-3 justify-start text-(--ui-accent)" paused={!profile} spinner="braille" />""",
        """\
        <GvOrb className="w-3 justify-start text-(--ui-accent)" paused={!profile} state="connecting" />""",
    ),
    (
        "src/app/chat/chat-swap-overlay.tsx",
        """\
      <GlyphSpinner className="w-3 justify-start text-(--ui-accent)" spinner="braille" />""",
        """\
      <GvOrb className="w-3 justify-start text-(--ui-accent)" state="connecting" />""",
    ),
    (
        "src/components/assistant-ui/tool/fallback.tsx",
        """\
      <GlyphSpinner
        ariaLabel={copy.statusRunning}
        className="size-3.5 shrink-0 text-[0.95rem] text-(--ui-text-tertiary)"
        spinner="breathe"
      />""",
        """\
      <GvOrb
        ariaLabel={copy.statusRunning}
        className="size-3.5 shrink-0 text-(--ui-text-tertiary)"
        size={16}
        state="working"
      />""",
    ),
    # Tool rows and delegated work: the same orb, `working` / `weaving` — the
    # design's mapping for tool execution and subagent aggregation. This is what
    # makes "every streaming action and tool use shows an orb" true for the CHAT
    # rows too: one implementation, panel and host alike.
    (
        "src/components/assistant-ui/tool/delegate.tsx",
        """\
      <GlyphSpinner ariaLabel={label} className="size-3.5 text-[0.95rem] text-(--ui-text-tertiary)" spinner="breathe" />""",
        """\
      <GvOrb ariaLabel={label} className="size-3.5 text-(--ui-text-tertiary)" size={16} state="weaving" />""",
    ),
])


#: The composer's peel-off ("popout") is a real feature, but a PERSISTED float is a
#: trap: the app restores a narrow 19.5rem pill anchored to a stored bottom/right
#: inset, and docking back is a drag gesture with no visible affordance. Measured
#: on 17.09. (live app): `poppedOut:true, right:893.6` ⇒ composer ink centre 873
#: while the conversation column is centred at 1090 — the owner read that as "the
#: UI is shifted". The Skaldir contract puts the composer in the chat column
#: (01_PRODUCT_UX_DESIGN.md §4), so the overlay restores DOCKED and leaves peeling
#: off available in-session. Both sites matter: the per-zone state and the
#: pre-zone legacy seed.
POPOUT_LOAD_OLD = (
    "        out[id] = { poppedOut: gesturesEnabledAtLoad && zone.poppedOut, position: { ...zone.position } }"
)

POPOUT_LOAD_NEW = """\
        // UI fix: never restore a float. A persisted peel-off renders a narrow pill
        // at a stale inset and there is no visible way back (docking is a drag
        // gesture only), so the surface looks shifted after every restart.
        out[id] = { poppedOut: false, position: { ...zone.position } }"""

POPOUT_LEGACY_OLD = """\
let legacySeed: PopoutZoneState | null =
  gesturesEnabledAtLoad && storedString(LEGACY_ENABLED_KEY) === 'true'
    ? { poppedOut: true, position: legacyPosition() }
    : null"""

POPOUT_LEGACY_NEW = """\
let legacySeed: PopoutZoneState | null =
  // UI fix: the pre-zone float flag is not restored as a float either — same reason
  // as load(). The stored position is kept so the docked composer starts where the
  // user last had it.
  { poppedOut: false, position: legacyPosition() }"""

POPOUT_SWAPS: list[tuple[str, str, str]] = [
    ("src/store/composer-popout.ts", POPOUT_LOAD_OLD, POPOUT_LOAD_NEW),
    ("src/store/composer-popout.ts", POPOUT_LEGACY_OLD, POPOUT_LEGACY_NEW),
]

# --------------------------------------------------------------------------- #
# 6) Goal bar in the chat bar (Welle 1)                                        #
# --------------------------------------------------------------------------- #

#: The goal bar is an in-flow child of the composer DOCK, mounted between the
#: micro-action strip and the status stack (AUFBAU/02 §c.1). That is the one slot
#: in the lane the micro-actions may never be covered from: they stay the
#: top-most thing (`status-stack/index.tsx:286-289`).
#:
#: Both anchors are SPLIT anchors (pattern C, §1.4): the anchor is two adjacent
#: stock lines and the insertion lands BETWEEN them, so the anchor is gone after
#: one run. A prefix-style anchor would still hit once on the patched tree and a
#: second run would add a second import / a second element silently.
GOALBAR_IMPORT_OLD = """\
import { Button } from '@/components/ui/button'
import { Slot as ContribSlot } from '@/contrib/react/slot'
"""

GOALBAR_IMPORT_NEW = """\
import { Button } from '@/components/ui/button'
import { GvGoalBar } from '@/components/ui/gv-goal-bar'
import { Slot as ContribSlot } from '@/contrib/react/slot'
"""

#: `</div>` alone occurs 11x in the file — the pair is what makes this unique
#: (measured: 1 hit for the pair, 1 hit for the comment line).
GOALBAR_JSX_OLD = """\
          </div>
          {/* Session-scoped status stack (todos, subagents, background tasks,
"""

GOALBAR_JSX_NEW = """\
          </div>
          {/* The goal bar: read-only view of the session's runtime goal, an
              in-flow dock child like the stack below it. */}
          <GvGoalBar sessionId={statusSessionId} />
          {/* Session-scoped status stack (todos, subagents, background tasks,
"""


# --------------------------------------------------------------------------- #
# 7) Entstopf-Welle A — the type ladder's remaining edges and four primitives  #
# --------------------------------------------------------------------------- #

#: K3 (AUFBAU/09 §d rank 2, 159 uses): the conversation caption token was the one
#: of the three chat type tokens that sat off the ladder — `text` is 0.8125rem
#: (13px) and `tool` is 0.6875rem (11px) since the floor. The token drives every
#: `text-[length:var(--conversation-caption-font-size)]` site (159 finds, e.g.
#: artifacts/index.tsx, command-center/index.tsx, composer/context-menu.tsx).
#: `--conversation-caption-line-height` stays 1rem (16px): 13px on a 16px line is
#: 1.23, so no clamp or table row height moves with the change (checked, §e nr. 5).
#: Plain value swap (pattern A) — the old line is gone after one run, so a second
#: run fails loudly instead of writing anything twice.
CAPTION_TOKEN_OLD = "    --conversation-caption-font-size: 0.75rem;"
CAPTION_TOKEN_NEW = "    --conversation-caption-font-size: 0.8125rem;"

#: K4 (AUFBAU/09 §c nr. 1, the single visible defect of the type work): the
#: titlebar count badge was 8x8px with `text-[7px]` — since the type floor it
#: rendered 11px type inside an 8px box, the only place in the tree where the
#: glyph is LARGER than its container. E7 wants >= 16px height at 11px type:
#: `h-4 min-w-4 rounded-[4px] px-1 text-[0.6875rem]`.
#: The absolute offsets in `app/shell/titlebar-controls.tsx:103` are NOT moved:
#: `-top-2.5` is exactly what puts the badge's top edge on the titlebar's top
#: edge (icon box top = 10px, badge top = -10px => 0). A 16px box at any smaller
#: offset would stick out above the 34px titlebar; growing down/left keeps it
#: inside the bar. Measured in the built window: see the wave report (badge rect
#: inside the titlebar rect, overlap with the glyph's top edge 6px).
BADGE_OVERLAY_OLD = "        overlay: 'h-2 min-w-2 justify-center rounded-[2px] px-px text-[7px] font-semibold tabular-nums'"
BADGE_OVERLAY_NEW = "        overlay: 'h-4 min-w-4 justify-center rounded-[4px] px-1 text-[0.6875rem] font-semibold tabular-nums'"

#: K5 (AUFBAU/09 §c nr. 3): the profile initial in the rail's 16x16px glyph box.
#: E7 is satisfied by the 16px box; what is left is E1/E2 in the source — 8px type
#: becomes the metadata step. `size-4` stays, so no geometry moves.
PROFILE_GLYPH_OLD = "        'grid size-4 shrink-0 place-items-center rounded-[3px] text-[0.5rem] font-semibold uppercase leading-none',"
PROFILE_GLYPH_NEW = "        'grid size-4 shrink-0 place-items-center rounded-[3px] text-[0.6875rem] font-semibold uppercase leading-none',"

#: K6 (AUFBAU/09 §c nr. 2): the handoff avatar in the session row — same 8px type
#: in a 16px box, once per handed-off session. The inner svg grows 10px -> 12px
#: (`[&_svg]:size-3`) so the platform mark stays proportionate to the new label
#: size; the row geometry itself is untouched (size-4, -mt-px stay).
HANDOFF_BADGE_OLD = '                    className="-mt-px size-4 shrink-0 rounded-[4px] text-[0.5rem] [&_svg]:size-2.5"'
HANDOFF_BADGE_NEW = '                    className="-mt-px size-4 shrink-0 rounded-[4px] text-[0.6875rem] [&_svg]:size-3"'

#: K13 (AUFBAU/09 §a rank 10): the update dialog's own surface had a 1.82x size
#: jump — 20px title against the 10px group label (floored to 11px). E3 allows
#: <= 1.5 within a surface, so the title comes down: 20 -> 16px (`text-base`, the
#: ladder's reading-text step) gives 16/11 = 1.45. 15px would also fit (15/11 =
#: 1.36) but would collide with the description as soon as K2 snaps `text-sm`
#: (14px) to 15px in a later wave, so 16px is the stable choice. This is the ONE
#: dialog whose factor is over the limit; the other six titles in the file sit at
#: 20px against a 14px description (1.43) and are left alone. The anchor is the
#: whole line on purpose — the short anchor `text-center text-xl` hits 9x.
UPDATE_TITLE_OLD = '        <DialogTitle className="text-center text-xl">{title}</DialogTitle>'
UPDATE_TITLE_NEW = '        <DialogTitle className="text-center text-base">{title}</DialogTitle>'

#: K14 (AUFBAU/09 §a rank 14): two bare `font-size` values in styles.css that no
#: class rule reaches. `[data-slot='aui_intro'] p:last-child` is the empty-chat
#: intro paragraph (14px -> 13px, the secondary step); `.aui-error-action` is the
#: failed-turn pill (11.52px -> 11px, the metadata step). Both anchors are
#: three-line blocks so a shifted sibling rule cannot produce a false hit; both
#: are value swaps (pattern A), so one run consumes them.
INTRO_PARA_OLD = """\
  font-size: 0.875rem;
  line-height: 1.45;"""
INTRO_PARA_NEW = """\
  font-size: 0.8125rem;
  line-height: 1.45;"""

ERROR_ACTION_OLD = """\
  font-size: 0.72rem;
  line-height: 1.1rem;"""
ERROR_ACTION_NEW = """\
  font-size: 0.6875rem;
  line-height: 1.1rem;"""


# --------------------------------------------------------------------------- #
# 8) Welle 2 — the colour-free state axis (AUFBAU/12, AUFBAU/14 E1/E2)         #
# --------------------------------------------------------------------------- #
#: The gateway axis carried TWO state channels: the word (`gatewayDetail`, which
#: already names all 7 states) and a colour class (amber/red). Measured on the
#: stock `fb56a7e06d`: 5 colour classes in this one file, and 3 different glyphs
#: for 7 states — over the 6 not-ready states only 2. Colour therefore repeated
#: what the word said, and the owner's contract is accents black/white
#: (AUFBAU/14 E1). Welle 2 drops the class channel entirely and widens the glyph
#: ladder to 7 of 7 distinguishable signs.
#:
#: GROUP W2-A is INDIVISIBLE (AUFBAU/10 §4 R-A): dropping the colour while the
#: glyph ladder still shows one sign per state would leave the user with the same
#: single `AlertCircle` for four states and no colour to tell them apart. P1…P7
#: and B5 always go together.
#:
#: P1 — the class channel. The anchor spans the WHOLE declaration on purpose: the
#: variable then does not exist any more, so any forgotten reader (the consumer
#: :461, the dependency list :599) fails LOUDLY in `tsc` instead of silently
#: keeping a second state channel.
GATEWAY_CLASS_OLD = """\
  const gatewayClassName = inferenceReady
    ? undefined
    : gatewayDegraded
      ? 'text-amber-600 hover:text-amber-600'
      : 'text-destructive hover:text-destructive'
"""

GATEWAY_CLASS_NEW = """\
  // Skaldir-Regel (AUFBAU/10 §1): den Zustand traegt Symbol + Wort, nie eine Farbe.
  // Der Klassenkanal entfaellt ganz; der zweite Leser `gatewayDegraded` (:325) faellt mit ihm
  // (P2), ebenso der Verbraucher :461 (P3) und die Abhaengigkeit :599 (P4).
"""

#: P2 — the one reader of the colour flag went with P1, so the flag itself goes.
#: Measuring note: `grep -c 'gatewayDegraded'` finds 1 AFTER the run — that is the
#: comment above, not code (AUFBAU/12 §1.5 "Messfalle"). The proof is
#: `grep -c 'const gatewayDegraded'` = 0 plus the `tsc` run.
GATEWAY_DEGRADED_OLD = """\
  const gatewayDegraded = gatewayOpen || gatewayConnecting
"""

GATEWAY_DEGRADED_NEW = ""

#: P3 — the only site that hung the class on the element. With it gone the
#: gateway row carries NO `className` at all: no second state channel.
GATEWAY_ITEM_CLASS_OLD = """\
        className: gatewayRestarting ? undefined : gatewayClassName,
"""

GATEWAY_ITEM_CLASS_NEW = ""

#: P4 — `gatewayClassName` out of the `useMemo` dependency list (:585-611). The
#: anchor takes the two neighbouring lines so it hits exactly once
#: (`tests/test_ui_overlay.py:53`: every UI anchor is expected exactly once).
GATEWAY_DEPS_OLD = """\
      gatewayMenuContent,
      gatewayClassName,
      gatewayDetail,
"""

GATEWAY_DEPS_NEW = """\
      gatewayMenuContent,
      gatewayDetail,
"""

#: P5/P6 — "update available" loses the primary colour on both rows (client and
#: backend). The two `className:` lines are BYTE-IDENTICAL in the stock, so each
#: anchor carries its own second line (`detail:` vs `hidden:`) to stay unique.
#: No new word is added (AUFBAU/10 §1.2): the message already sits in `(+3)`,
#: ` (update)`, ` · restart` and the tooltip (`lib/version-status.ts`).
VERSION_CLIENT_CLASS_OLD = """\
      className: status.hasUpdate ? 'text-primary hover:text-primary' : undefined,
      detail: status.detail,
"""

VERSION_CLIENT_CLASS_NEW = """\
      detail: status.detail,
"""

VERSION_BACKEND_CLASS_OLD = """\
      className: status.hasUpdate ? 'text-primary hover:text-primary' : undefined,
      hidden: status.unknown,
"""

VERSION_BACKEND_CLASS_NEW = """\
      hidden: status.unknown,
"""

#: P7 — "N failed" loses the state red. The word carries the statement, the sign
#: (`AlertCircle`, :555) the urgency; glyph and words of the agent row are
#: otherwise untouched. The call collapses onto ONE expression so `cn(...)` stays
#: used (`grep -c 'cn('` = 1 before AND after) — no dead import.
AGENTS_FAILED_CLASS_OLD = """\
      {
        className: cn(
          agentsOpen && 'bg-accent/55 text-foreground',
          subagentsFailed > 0 && 'text-destructive hover:text-destructive'
        ),
"""

AGENTS_FAILED_CLASS_NEW = """\
      {
        // Skaldir-Regel (AUFBAU/10 §1): kein Zustandsrot — "N failed" traegt das Wort,
        // das Zeichen die Dringlichkeit.
        className: cn(agentsOpen && 'bg-accent/55 text-foreground'),
"""

#: W2-B — the GLYPH LADDER (1 sign per state, AUFBAU/12 §1.3). `AlertTriangle`
#: and `Power` already exist in the curated single source
#: (`src/lib/icons.ts`, 141 files import from it, only 3 from the package
#: directly), so only `Hourglass` and `Plug` are added there — never as a direct
#: import in the hook. Both signs ship with the installed
#: `@tabler/icons-react` 3.44.0 (`declare const IconHourglass:`/`IconPlug:` = 1
#: each).
#: B1/B2 are import-list insertions, B3/B4 the matching export-list insertions;
#: all four are SPLIT anchors (pattern C): the insertion lands BETWEEN the two
#: anchor lines, so the pair is consumed by one run and a second run hits 0.
ICON_IMPORT_HOURGLASS_OLD = """\
  IconInfoCircle as Info,
  IconKeyboard as Keyboard,
"""

ICON_IMPORT_HOURGLASS_NEW = """\
  IconInfoCircle as Info,
  IconHourglass as Hourglass,
  IconKeyboard as Keyboard,
"""

ICON_IMPORT_PLUG_OLD = """\
  IconPlus as Plus,
  IconPower as Power,
"""

ICON_IMPORT_PLUG_NEW = """\
  IconPlus as Plus,
  IconPlug as Plug,
  IconPower as Power,
"""

ICON_EXPORT_HOURGLASS_OLD = """\
  HelpCircle,
  ImageIcon,
"""

ICON_EXPORT_HOURGLASS_NEW = """\
  HelpCircle,
  Hourglass,
  ImageIcon,
"""

#: The short anchor `  Plus,\n  Power,\n` occurs ONLY in the export list — in the
#: import list the line reads `IconPlus as Plus,` (measured: 1 hit).
ICON_EXPORT_PLUG_OLD = """\
  Plus,
  Power,
"""

ICON_EXPORT_PLUG_NEW = """\
  Plus,
  Plug,
  Power,
"""

#: B6 — GAP in the patch text of AUFBAU/12, found by the type check, not by
#: reading: §1.3 B1…B4 put `Hourglass`/`Plug` into the curated single source and
#: B5 USED them in the hook — but nothing ever imported them there. `tsc` on the
#: patched mirror said it plainly:
#:   src/app/shell/hooks/use-statusbar-items.tsx(463,12): error TS2304:
#:     Cannot find name 'Hourglass'.   (same for AlertTriangle, Plug, Power)
#: `AlertTriangle` and `Power` were already exported by `src/lib/icons.ts` but
#: were not in THIS import list either. One anchor, the whole list: the insertion
#: lands between the existing lines, so the anchor is consumed by one run
#: (pattern C) and a Hermes update that changes the list fails the build loudly.
ICON_USES_OLD = """\
import {
  Activity,
  AlertCircle,
  Clock,
  Command,
  FolderOpen,
  Globe,
  Hash,
  Layers3,
  Loader2,
  Terminal,
  Zap
} from '@/lib/icons'
"""

ICON_USES_NEW = """\
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  Clock,
  Command,
  FolderOpen,
  Globe,
  Hash,
  Hourglass,
  Layers3,
  Loader2,
  Plug,
  Power,
  Terminal,
  Zap
} from '@/lib/icons'
"""

#: B5 — the sign choice. It reads `gatewayDetail` — the very word the row already
#: shows — so the ladder is a PROJECTION of the displayed state, never a fourth
#: state source (AUFBAU/10 §4 R-B: the raw state stays `runtimeReadinessDisplay`,
#: :326). Bonus: `gatewayDetail`, `copy`, `gatewayRestarting` and `inferenceReady`
#: already sit in the `useMemo` dependency list, so this needs no extra anchor.
GATEWAY_ICON_OLD = """\
        icon: gatewayRestarting ? (
          <GlyphSpinner ariaLabel={copy.gatewayRestarting} className="size-3" />
        ) : inferenceReady ? (
          <Activity className="size-3" />
        ) : (
          <AlertCircle className="size-3" />
        ),
"""

GATEWAY_ICON_NEW = """\
        icon: gatewayRestarting ? (
          <GlyphSpinner ariaLabel={copy.gatewayRestarting} className="size-3" />
        ) : inferenceReady ? (
          <Activity className="size-3" />
        ) : gatewayDetail === copy.gatewayChecking ? (
          <Hourglass className="size-3" />
        ) : gatewayDetail === copy.gatewayUnavailable ? (
          <AlertTriangle className="size-3" />
        ) : gatewayDetail === copy.gatewayConnecting ? (
          <Plug className="size-3" />
        ) : gatewayDetail === copy.gatewayNeedsSetup ? (
          <AlertCircle className="size-3" />
        ) : (
          <Power className="size-3" />
        ),
"""

#: The gateway/agent state axis ships its stock ENGLISH words again (owner directive:
#: English-only app language). W2-A/W2-B — the class drop and the 7-sign glyph ladder —
#: stay; only the W2-C VALUE swap is gone. Way "D2" (German values behind English keys)
#: is retired: no `de` locale may exist again.


#: The faces ship with the app (OFL-1.1; provenance in
#: src/skaldir/assets/fonts/MANIFEST.json) and the orb door is a new host component.
#: Both are copied into the tree like every other overlay file.
UI_FILES: list[tuple[str, str]] = [
    ("src/skaldir/assets/fonts/GeistSans-Regular.woff2", "apps/desktop/src/gv-fonts/GeistSans-Regular.woff2"),
    ("src/skaldir/assets/fonts/GeistSans-Medium.woff2", "apps/desktop/src/gv-fonts/GeistSans-Medium.woff2"),
    ("src/skaldir/assets/fonts/GeistSans-SemiBold.woff2", "apps/desktop/src/gv-fonts/GeistSans-SemiBold.woff2"),
    ("src/skaldir/assets/fonts/GeistMono-Regular.woff2", "apps/desktop/src/gv-fonts/GeistMono-Regular.woff2"),
    ("src/skaldir/assets/fonts/GeistMono-Medium.woff2", "apps/desktop/src/gv-fonts/GeistMono-Medium.woff2"),
    ("src/skaldir/assets/fonts/SpaceGrotesk-Medium.woff2", "apps/desktop/src/gv-fonts/SpaceGrotesk-Medium.woff2"),
    ("src/skaldir/assets/fonts/SpaceGrotesk-Bold.woff2", "apps/desktop/src/gv-fonts/SpaceGrotesk-Bold.woff2"),
    ("src/skaldir/assets/fonts/OFL.txt", "apps/desktop/src/gv-fonts/OFL.txt"),
    ("app-overlay/gv/gv-orb.tsx", "apps/desktop/src/components/ui/gv-orb.tsx"),
    ("app-overlay/gv/gv-goal-bar.tsx", "apps/desktop/src/components/ui/gv-goal-bar.tsx"),
    #: Welle 3 — die Subagenten-Zone (AUFBAU/15 §4.5). Quelle relativ zur Repo-Wurzel,
    #: Ziel relativ zur Baumwurzel; die Kopie wird bei jedem Build überschrieben
    #: (Finger weg von der Zieldatei im Spiegel, 01 §e Nr. 9). A1/A2 in
    #: `UI_PATCHES` hängen an DIESER Datei.
    ("app-overlay/gv/gv-subagent-zone.tsx", "apps/desktop/src/app/contrib/subagent-zone.tsx"),
]

#: `(path relative to apps/desktop, old, new, expected hits)` — the same tuple
#: shape the identity overlay uses, so `apply_patches` needs no special case.
#: The Skaldir panel is a **destination, never a resume target**. The launcher used
#: to open the app with `hermes://open/<panel>`; the app remembers the last
#: non-overlay route per profile (`hermes.desktop.lastRoute`), so `/gods-voice`
#: became the remembered route and EVERY cold start landed on the cockpit instead
#: of the last session (owner report 17.09.: "die app öffnet immer auf dem
#: ehemaligen cockpit"). Three sites are that page's whole route memory — the
#: restorable-route test, the restore `navigate`, and the persist effect — and this
#: also neutralizes an already-stored `/gods-voice`, so no storage surgery is
#: needed. `tests/test_ui_overlay.py` pins the literal against
#: `godsv.desktop.PANEL_ROUTE`.
ROUTE_RESTORABLE_OLD = """\
        const restorableNonSessionRoute =
          !!route && route !== NEW_CHAT_ROUTE && !routeSession && !isOverlayView(appViewForPath(route))"""

ROUTE_RESTORABLE_NEW = """\
        // UI fix: the panel route is not a resumable place (see ROUTE_RESTORABLE_* in
        // the Skaldir overlay) — resuming into it made every start open the cockpit.
        const restorableNonSessionRoute =
          !!route &&
          route !== NEW_CHAT_ROUTE &&
          route !== '/gods-voice' &&
          !routeSession &&
          !isOverlayView(appViewForPath(route))"""

ROUTE_RESTORE_OLD = """\
          route &&
          route !== NEW_CHAT_ROUTE &&
          !isOverlayView(appViewForPath(route)) &&"""

ROUTE_RESTORE_NEW = """\
          route &&
          route !== NEW_CHAT_ROUTE &&
          // UI fix: never navigate back into the panel on boot; a stored
          // '/gods-voice' falls through to the remembered session, else a new chat.
          route !== '/gods-voice' &&
          !isOverlayView(appViewForPath(route)) &&"""

ROUTE_PERSIST_OLD = """\
    } else if (!routedSessionId && !isOverlayView(appViewForPath(locationPathname))) {
      setRememberedRoute(locationPathname, activeProfile)
    }"""

ROUTE_PERSIST_NEW = """\
    } else if (
      !routedSessionId &&
      // UI fix: the panel page is a destination, not a place to resume — remembering
      // it here is what made the next start open the cockpit.
      locationPathname !== '/gods-voice' &&
      !isOverlayView(appViewForPath(locationPathname))
    ) {
      setRememberedRoute(locationPathname, activeProfile)
    }"""


# --------------------------------------------------------------------------- #
# Welle 3 — die Subagenten-Zone (AUFBAU/15, Owner-Vorgabe 18.09.)              #
# --------------------------------------------------------------------------- #
#: AUFBAU/15 §4.2 (A1): die Importkante der neuen Zone. Muster C — die Einfuegung landet ZWISCHEN
#: den zwei benachbarten Importzeilen, dadurch ist der Anker nach einem Lauf weg und ein zweiter
#: Lauf bricht LAUT ab (0 Treffer) statt still zu doppeln (01 §1.4, 01 §e Nr. 3).
SUBAGENT_ZONE_IMPORT_OLD = """\
import { FilesPane, LogsPane, ReviewPaneContent } from './panes'
import { ContribWiring, WiredPane } from './wiring'
"""

SUBAGENT_ZONE_IMPORT_NEW = """\
import { FilesPane, LogsPane, ReviewPaneContent } from './panes'
import { SUBAGENT_ZONE_DOCK, SUBAGENTS_PANE_ID, SUBAGENTS_ZONE_TITLE, SubagentZonePane } from './subagent-zone'
import { ContribWiring, WiredPane } from './wiring'
"""

#: AUFBAU/15 §4.3 (A2): die Kachel selbst. Der Andock-Hinweis ist der Montagepunkt
#: (dock pos:'bottom' -> model.ts:288-294 baut eine Column-Split unter der Sitzungsliste).
#: KEIN enforce: die Zone soll genau EINMAL dorthin, danach gilt die Nutzeranordnung
#: (store.ts:1644-1650 $userPlacedPanes; 04-panels.md:241).
#: width/minWidth/maxWidth sind in SUBAGENT_ZONE_DOCK eine Quelle — dieselben Zahlen wie die
#: Sitzungskachel, damit die Spaltenklammer 237–360 px (store/layout.ts:20-21) erhalten bleibt.
SUBAGENT_ZONE_PANE_OLD = """\
    render: () => idle(<FilesPane />)
  },
  {
    id: 'review',
"""

SUBAGENT_ZONE_PANE_NEW = """\
    render: () => idle(<FilesPane />)
  },
  {
    // Zone 2 der linken Spalte: Subagenten-Ueberwachung unter der Sitzungsliste
    // (Owner-Vorgabe 18.09.; HERMES-ABGLEICH/04-panels.md §D.2; AUFBAU/04 §2 Abnahme 4).
    // Angedockt und frei sind Eigenschaften DIESER Kachel — der Pin im Zonenkopf
    // schreibt in dieselbe Stelle (AUFBAU/15 §4.1), ohne zweiten Zieh-Rahmen (04:279-281).
    id: SUBAGENTS_PANE_ID,
    area: 'panes',
    title: SUBAGENTS_ZONE_TITLE,
    data: SUBAGENT_ZONE_DOCK,
    render: () => <SubagentZonePane />
  },
  {
    id: 'review',
"""


UI_PATCHES: list[tuple[str, str, str, int]] = [
    # fonts: the faces, then the two stacks that consume them
    (
        "src/styles.css",
        "/* JetBrains Mono — bundled terminal font (Apache-2.0) so bold/italic share the",
        FONT_FACES_CSS
        + "/* JetBrains Mono — bundled terminal font (Apache-2.0) so bold/italic share the",
        1,
    ),
    ("src/styles.css", FONT_SANS_OLD, FONT_SANS_NEW, 1),
    ("src/styles.css", FONT_MONO_OLD, FONT_MONO_NEW, 1),
    # the RUNNING stacks come from the theme presets (they overwrite the tokens)
    ("src/themes/presets.ts", THEME_SANS_OLD, THEME_SANS_NEW, 1),
    # type floor + focus ring — unlayered, so they beat @layer utilities
    ("src/styles.css", "@layer components {", TYPE_FLOOR_CSS + "@layer components {", 1),
    # zoom: the 100% baseline, and the old 90% migrates instead of sticking
    ("electron/zoom.ts", ZOOM_DOC_OLD, ZOOM_DOC_NEW, 1),
    ("electron/zoom.ts", ZOOM_OLD, ZOOM_NEW, 1),
    ("electron/main.ts", ZOOM_MIGRATE_OLD, ZOOM_MIGRATE_NEW, 1),
    # accessible names and semantics
    ("src/app/shell/statusbar-controls.tsx", A11Y_STATUSBAR_OLD, A11Y_STATUSBAR_NEW, 1),
    ("src/app/chat/index.tsx", A11Y_CHAT_MAIN_OLD, A11Y_CHAT_MAIN_NEW, 1),
    ("src/app/chat/composer/index.tsx", A11Y_COMPOSER_OLD, A11Y_COMPOSER_NEW, 1),
    # one indicator: the chat's status stack renders the same thinking-orbs
    ("src/app/chat/composer/status-stack/status-row.tsx", GLYPH_IMPORT_OLD, GLYPH_IMPORT_NEW, 1),
    ("src/app/chat/composer/status-stack/index.tsx", GLYPH_IMPORT_OLD, GLYPH_IMPORT_NEW, 1),
    ("src/app/chat/composer/status-stack/subagent-section.tsx", GLYPH_IMPORT_OLD, GLYPH_IMPORT_NEW, 1),
    ("src/app/chat/chat-swap-overlay.tsx", GLYPH_IMPORT_OLD, GLYPH_IMPORT_NEW, 1),
    ("src/components/assistant-ui/tool/fallback.tsx", GLYPH_IMPORT_OLD, GLYPH_IMPORT_NEW, 1),
    ("src/components/assistant-ui/tool/delegate.tsx", GLYPH_IMPORT_OLD, GLYPH_IMPORT_NEW, 1),
    # start route: the panel page is not remembered and not resumed (owner report
    # 17.09. — every start opened the old cockpit)
    (
        "src/app/contrib/hooks/use-desktop-integrations.ts",
        ROUTE_RESTORABLE_OLD,
        ROUTE_RESTORABLE_NEW,
        1,
    ),
    (
        "src/app/contrib/hooks/use-desktop-integrations.ts",
        ROUTE_RESTORE_OLD,
        ROUTE_RESTORE_NEW,
        1,
    ),
    (
        "src/app/contrib/hooks/use-desktop-integrations.ts",
        ROUTE_PERSIST_OLD,
        ROUTE_PERSIST_NEW,
        1,
    ),
    # the goal bar: read-only dock strip between the micro actions and the stack
    ("src/app/chat/composer/index.tsx", GOALBAR_IMPORT_OLD, GOALBAR_IMPORT_NEW, 1),
    ("src/app/chat/composer/index.tsx", GOALBAR_JSX_OLD, GOALBAR_JSX_NEW, 1),
    # Entstopf-Welle A: the type ladder's remaining edges (K3) and the four
    # primitive sizes (K4/K5/K6 + the two bare values K14, the one size jump K13).
    # K1 needs no entry: those selectors live inside TYPE_FLOOR_CSS above, which
    # already hangs on the `@layer components {` anchor.
    ("src/styles.css", CAPTION_TOKEN_OLD, CAPTION_TOKEN_NEW, 1),
    ("src/styles.css", INTRO_PARA_OLD, INTRO_PARA_NEW, 1),
    ("src/styles.css", ERROR_ACTION_OLD, ERROR_ACTION_NEW, 1),
    ("src/components/ui/badge.tsx", BADGE_OVERLAY_OLD, BADGE_OVERLAY_NEW, 1),
    (
        "src/components/ui/profile-glyph.tsx",
        PROFILE_GLYPH_OLD,
        PROFILE_GLYPH_NEW,
        1,
    ),
    (
        "src/app/chat/sidebar/session-row.tsx",
        HANDOFF_BADGE_OLD,
        HANDOFF_BADGE_NEW,
        1,
    ),
    ("src/app/updates-overlay.tsx", UPDATE_TITLE_OLD, UPDATE_TITLE_NEW, 1),
    # Welle 2 — the colour-free state axis (AUFBAU/12 §1, AUFBAU/14 E1).
    # W2-A (P1…P7) drops the class channel on the gateway axis and on the two
    # "update available" rows plus the agent row; W2-B (B1…B5) widens the glyph
    # ladder to 7 of 7 signs; W2-C (C1…C3) puts the German state words in behind
    # the SAME keys. A and B belong together (AUFBAU/10 §4 R-A): colour alone
    # told four states apart, so the colour may only fall once the signs do.
    # The scale-class half of K2 (`.text-xs`/`.text-sm`) needs no entry either —
    # like K1 it lives inside TYPE_FLOOR_CSS, which already hangs on its anchor.
    ("src/app/shell/hooks/use-statusbar-items.tsx", GATEWAY_CLASS_OLD, GATEWAY_CLASS_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", GATEWAY_DEGRADED_OLD, GATEWAY_DEGRADED_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", GATEWAY_ITEM_CLASS_OLD, GATEWAY_ITEM_CLASS_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", GATEWAY_DEPS_OLD, GATEWAY_DEPS_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", VERSION_CLIENT_CLASS_OLD, VERSION_CLIENT_CLASS_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", VERSION_BACKEND_CLASS_OLD, VERSION_BACKEND_CLASS_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", AGENTS_FAILED_CLASS_OLD, AGENTS_FAILED_CLASS_NEW, 1),
    ("src/lib/icons.ts", ICON_IMPORT_HOURGLASS_OLD, ICON_IMPORT_HOURGLASS_NEW, 1),
    ("src/lib/icons.ts", ICON_IMPORT_PLUG_OLD, ICON_IMPORT_PLUG_NEW, 1),
    ("src/lib/icons.ts", ICON_EXPORT_HOURGLASS_OLD, ICON_EXPORT_HOURGLASS_NEW, 1),
    ("src/lib/icons.ts", ICON_EXPORT_PLUG_OLD, ICON_EXPORT_PLUG_NEW, 1),
    # B6: the hook has to IMPORT the four signs the ladder renders (gap in
    # AUFBAU/12 §1.3 — found by the tsc run on the patched mirror).
    ("src/app/shell/hooks/use-statusbar-items.tsx", ICON_USES_OLD, ICON_USES_NEW, 1),
    ("src/app/shell/hooks/use-statusbar-items.tsx", GATEWAY_ICON_OLD, GATEWAY_ICON_NEW, 1),
    # Welle 3 — die Subagenten-Zone (AUFBAU/15 §4.2/§4.3). A1/A2 gehoeren zusammen:
    # ohne den Import waere die Kachel-ID im Contrib unaufloesbar, ohne die Kachel
    # gaebe es keine zweite Zone. Beide sind Muster C (Anker nach einem Lauf 0x).
    (
        "src/app/contrib/controller.tsx",
        SUBAGENT_ZONE_IMPORT_OLD,
        SUBAGENT_ZONE_IMPORT_NEW,
        1,
    ),
    (
        "src/app/contrib/controller.tsx",
        SUBAGENT_ZONE_PANE_OLD,
        SUBAGENT_ZONE_PANE_NEW,
        1,
    ),
] + [(path, old, new, 1) for path, old, new in ORB_SWAPS] + [
    (path, old, new, 1) for path, old, new in POPOUT_SWAPS
]

