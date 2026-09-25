"""UI correction overlay (app-overlay/patch_app_ui.py) — structural contract.

The overlay is how a UI fix reaches the built app: anchored string swaps on the
Hermes desktop source plus copied files. Two things must hold before a build is
worth starting, and both are cheap to test here:

  * every anchor is unique and hits exactly once in the source tree (the overlay's
    own failure mode is a silent half-patch — `apply_patches` refuses instead, but
    that is minutes into a build),
  * the patched result actually carries the fix: Geist in both font stacks, no
    Windows-only face, no braille spinner left in the chat status stack, real
    accessible names, and the 100% zoom baseline.

The source tree is the local Hermes checkout; when it is absent the tree-level
tests skip (the list-level test still runs, so a broken overlay file is caught
everywhere).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app-overlay"))

from patch_app_ui import UI_FILES, UI_PATCHES  # noqa: E402

HERMES_DESKTOP = Path.home() / ".hermes" / "hermes-agent" / "apps" / "desktop"

STATUS_STACK = "src/app/chat/composer/status-stack"


def _apply(tree: Path) -> dict[str, str]:
    """Apply the overlay in memory; raise when an anchor is not exactly there."""
    out: dict[str, str] = {}
    for rel, old, new, expected in UI_PATCHES:
        text = out.get(rel)
        if text is None:
            text = (tree / rel).read_text(encoding="utf-8")
        hits = text.count(old)
        assert hits == expected, f"{rel}: anchor found {hits}x, expected {expected}x: {old[:70]!r}"
        out[rel] = text.replace(old, new)
    return out


def test_overlay_lists_are_internally_consistent() -> None:
    """No duplicate anchors, every copied source exists, no absolute paths."""
    seen: set[tuple[str, str]] = set()
    for rel, old, _new, expected in UI_PATCHES:
        assert expected == 1, f"{rel}: every UI anchor is expected exactly once"
        assert not rel.startswith("/"), f"{rel}: paths are tree-relative"
        assert (rel, old) not in seen, f"{rel}: duplicate anchor"
        seen.add((rel, old))
    for src_rel, dst_rel in UI_FILES:
        assert (REPO / src_rel).is_file(), f"missing overlay source: {src_rel}"
        assert not dst_rel.startswith("/"), f"{dst_rel}: paths are tree-relative"
    assert len({dst for _src, dst in UI_FILES}) == len(UI_FILES)


def test_overlay_fonts_are_vendored_with_their_licence() -> None:
    """The faces ship with the app and carry their provenance."""
    manifest = REPO / "src" / "skaldir" / "assets" / "fonts" / "MANIFEST.json"
    assert manifest.is_file()
    names = {dst.split("/")[-1] for _src, dst in UI_FILES if dst.endswith(".woff2")}
    assert names == {
        "GeistSans-Regular.woff2",
        "GeistSans-Medium.woff2",
        "GeistSans-SemiBold.woff2",
        "GeistMono-Regular.woff2",
        "GeistMono-Medium.woff2",
        "SpaceGrotesk-Medium.woff2",
        "SpaceGrotesk-Bold.woff2",
    }
    assert any(dst.endswith("OFL.txt") for _src, dst in UI_FILES)


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_patch_lands_the_ui_fixes() -> None:
    patched = _apply(HERMES_DESKTOP)

    styles = patched["src/styles.css"]
    # typography: Geist for the UI, Space Grotesk for the wordmark, Geist Mono for
    # code — and the Windows-only face is gone from the stack.
    assert "--dt-font-sans:" in styles and "'Geist'" in styles
    assert "--dt-font-brand: 'Space Grotesk'" in styles
    assert "--dt-font-mono:" in styles and "'Geist Mono'" in styles
    assert "'Segoe WPC'" not in styles
    assert "@font-face {\n  font-family: 'Geist';" in styles
    # the type floor exists and covers the sub-11px utilities (7px..0.68rem)
    assert ".text-\\[0\\.625rem\\]" in styles and "font-size: 0.6875rem;" in styles
    assert ":focus-visible" in styles
    # ...but text editors get NO ring: the chat composer is a radius-0 contenteditable
    # inside a 22px pill, so the ring drew a square dark frame inside the round bar
    # (measured on a real click, old build: `outline: color(srgb .09 .09 .09 / .94)
    # solid 2px`, offset 2px). Editors keep the caret instead.
    assert "[contenteditable='true']:focus-visible {\n  outline: none;\n}" in styles
    assert "[contenteditable='true']:focus-visible,\n" not in styles
    # the control ring survives for the roles the audit asked for
    assert "[role='menuitemradio']" in styles
    assert "outline: 2px solid var(--ring, currentColor);" in styles

    # zoom: 100% baseline, older stored 90% levels migrate instead of sticking
    assert "DEFAULT_ZOOM_LEVEL = Math.log(1)" in patched["electron/zoom.ts"]
    assert "legacyDefault" in patched["electron/main.ts"]

    # accessibility: real names, real composer semantics
    assert "aria-label={typeof item.title === 'string'" in patched[
        "src/app/shell/statusbar-controls.tsx"
    ]
    composer = patched["src/app/chat/composer/index.tsx"]
    assert 'role="textbox"' in composer and 'aria-multiline="true"' in composer


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_leaves_one_thinking_indicator_in_the_chat() -> None:
    """The chat status stack renders thinking-orbs, not the braille spinner."""
    patched = _apply(HERMES_DESKTOP)
    for rel in (
        f"{STATUS_STACK}/status-row.tsx",
        f"{STATUS_STACK}/index.tsx",
        f"{STATUS_STACK}/subagent-section.tsx",
    ):
        text = patched[rel]
        assert "GlyphSpinner" not in text, f"{rel}: the braille spinner survived"
        assert "from '@/components/ui/gv-orb'" in text, f"{rel}: no orb import"
    # the mapping stays the design's: running work -> working, subagents -> weaving
    assert 'state="working"' in patched[f"{STATUS_STACK}/status-row.tsx"]
    assert 'state="weaving"' in patched[f"{STATUS_STACK}/subagent-section.tsx"]
    # other loading chrome keeps its own spinner (only the thinking paths moved)
    other = (HERMES_DESKTOP / "src/app/chat/sidebar/load-more-row.tsx").read_text(encoding="utf-8")
    assert "glyph-spinner" in other


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_keeps_the_composer_docked() -> None:
    """A persisted peel-off must not be restored: the composer belongs to the chat
    column, and a stale float renders as a narrow pill at an old inset (measured:
    ink centre 873 vs column centre 1090) with no visible way back."""
    patched = _apply(HERMES_DESKTOP)
    store = patched["src/store/composer-popout.ts"]
    assert "poppedOut: gesturesEnabledAtLoad && zone.poppedOut" not in store
    assert "out[id] = { poppedOut: false, position: { ...zone.position } }" in store
    assert "? { poppedOut: true, position: legacyPosition() }" not in store
    assert "{ poppedOut: false, position: legacyPosition() }" in store
    # peeling off inside a session stays possible — only the RESTORE is docked
    assert "persistZones()" in store


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_never_resumes_into_the_panel_route() -> None:
    """The panel is a destination, never a resume target.

    The launcher opened the app with `hermes://open/<panel>`; the app remembers
    the last non-overlay route per profile, so `/gods-voice` became the
    remembered route and every cold start landed on the cockpit (owner report
    17.09.). The overlay must (a) refuse to restore it and (b) refuse to
    remember it — (b) is what also neutralizes an already-stored value.
    """
    from skaldir.desktop import PANEL_ROUTE

    panel = "/" + PANEL_ROUTE
    assert panel == "/gods-voice", "the overlay literal and the launcher route must agree"
    src = _apply(HERMES_DESKTOP)["src/app/contrib/hooks/use-desktop-integrations.ts"]
    # the restore latch, the restore navigate, and the persist effect
    assert src.count(f"route !== '{panel}' &&") == 2  # latch + restore navigate
    assert src.count(f"locationPathname !== '{panel}' &&") == 1  # persist effect
    assert f"locationPathname !== '{panel}' &&" in src
    # the remembered route is still persisted for real pages (e.g. /skills)
    assert "setRememberedRoute(locationPathname, activeProfile)" in src


def test_overlay_mounts_the_goal_bar_between_micro_actions_and_status_stack() -> None:
    """Welle 1: the goal bar is an in-flow DOCK child in the one slot between the
    micro-action strip and the status stack (AUFBAU/02 §c.1), and both of its
    anchors are SPLIT anchors: a second run over the same tree must find them
    gone instead of silently adding a second import or a second element.
    """
    import patch_app_ui

    patched = _apply(HERMES_DESKTOP)
    composer = patched["src/app/chat/composer/index.tsx"]
    assert composer.count("import { GvGoalBar } from '@/components/ui/gv-goal-bar'") == 1
    assert composer.count("<GvGoalBar sessionId={statusSessionId} />") == 1
    # the slot: below the micro-action strip, above the status stack
    strip = composer.index("'px-[5px] pb-1.5 empty:hidden'")
    bar = composer.index("<GvGoalBar sessionId={statusSessionId} />")
    assert strip < bar < composer.index("<ComposerStatusStack")
    # pattern C — the anchor pair is consumed by the run (0x on the patched text)
    assert composer.count(patch_app_ui.GOALBAR_IMPORT_OLD) == 0
    assert composer.count(patch_app_ui.GOALBAR_JSX_OLD) == 0
    # the bar is read-only, orb-free, and carries the owner's empty-state wording
    bar_src = (REPO / "app-overlay" / "gv" / "gv-goal-bar.tsx").read_text(encoding="utf-8")
    assert "@goal define a goal and i actually get it done." in bar_src
    assert "GvOrb" not in bar_src or "no orb: `GvOrb` is deliberately not imported here." in bar_src
    assert "<GvOrb" not in bar_src and "gv-orb'" not in bar_src
    assert "runSessionControlAction" not in bar_src


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_gives_the_composer_exactly_one_textbox_role() -> None:
    """Our own defect (measured 18.09.): the A11Y anchor inserted a SECOND
    `role="textbox"` into an element that already carried one in the stock source
    (stock 1x, mirror 2x) — invalid ARIA and `tsc` TS17001
    ("JSX elements cannot have multiple attributes with the same name").

    The patched editor must therefore carry the stock attribute exactly once, the
    multiline semantic exactly once, and the pattern-C anchor pair must be gone
    from the patched text: a second run over the same tree then stops with
    `found 0x, expected 1x` instead of quietly adding a second attribute.
    """
    import patch_app_ui

    stock = (HERMES_DESKTOP / "src/app/chat/composer/index.tsx").read_text(encoding="utf-8")
    assert stock.count('role="textbox"') == 1, "the stock element changed - re-anchor first"

    patched = _apply(HERMES_DESKTOP)["src/app/chat/composer/index.tsx"]
    # element-scoped: from the editor's own aria-label to the end of its tag
    editor = patched[patched.index("aria-label={t.composer.message}") :]
    editor = editor[: editor.index("/>")]
    assert editor.count('role="textbox"') == 1, "the duplicated role is back (tsc TS17001)"
    assert editor.count('aria-multiline="true"') == 1
    assert patched.count('role="textbox"') == 1
    # pattern C — the anchor pair is consumed by the run
    assert patched.count(patch_app_ui.A11Y_COMPOSER_OLD) == 0


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_snaps_the_type_ladder_onto_11_13_15_and_16() -> None:
    """Entstopf-Welle A, K1 (AUFBAU/09 §b.1): the 17 arbitrary sizes between
    11.04px and 16.32px snap onto 11 | 13 | 15 (+16 for reading text) with ONE
    rule block inside the existing TYPE_FLOOR_CSS — no new anchor.

    The block must be DISJOINT from the 16 floor classes: a class listed twice
    would be overwritten by the later rule while this very pin stays green
    (AUFBAU/09 §e nr. 1), which is the trap the rule was written against.
    """
    styles = _apply(HERMES_DESKTOP)["src/styles.css"]
    focus_at = styles.index(":where(a, button, summary,")
    floor_block = styles[:focus_at]
    snap_block = floor_block[floor_block.index("Skaldir type LADDER") :]

    def sel(size: str) -> str:
        return r".text-\[" + size.replace(".", r"\.") + r"rem\]"

    floor_classes = (
        "0.4375 0.5 0.56 0.5625 0.58 0.6 0.62 0.625 0.64 0.65 0.66 0.67 0.68".split()
    )
    snap_11 = "0.69 0.7 0.71 0.72 0.73 0.74".split()
    snap_13 = "0.75 0.78 0.8 0.82 0.85".split()
    snap_15 = "0.875 0.9 0.95".split()

    for size in floor_classes:
        assert sel(size) in floor_block, f"the floor lost {size}"
        assert sel(size) not in snap_block, f"{size} is floored AND snapped - silent overwrite"
    for size in snap_11:
        assert sel(size) in snap_block
    for size in snap_13:
        assert sel(size) in snap_block
    for size in snap_15:
        assert sel(size) in snap_block
    assert r".text-\[12px\]" in snap_block and r".text-\[1\.02rem\]" in snap_block
    # reading text is the fourth step; text-[1rem] is already on the ladder
    assert r".text-\[1rem\]" not in snap_block.replace(r".text-\[1\.02rem\]", "")
    # the snap rules carry font-size only — no second outline DECLARATION anywhere
    assert "outline:" not in snap_block
    assert styles.count("[contenteditable='true']:focus-visible {") == 1


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_lands_the_entstopf_welle_a_primitives() -> None:
    """K3/K4/K5/K6/K13/K14 (AUFBAU/09 §a): one caption token, four primitives,
    one size jump, two bare font-size values. Every new anchor is a split or a
    value swap, so one run CONSUMES it — asserted here for all of them at once,
    because a surviving anchor is exactly the silent doubling the wave is
    supposed to end.
    """
    import patch_app_ui

    patched = _apply(HERMES_DESKTOP)
    styles = patched["src/styles.css"]

    # K3 — the caption token moves onto the ladder; its line-height stays put
    assert styles.count("--conversation-caption-font-size: 0.8125rem;") == 1
    assert styles.count("--conversation-caption-font-size:") == 1
    assert "--conversation-caption-line-height: 1rem;" in styles
    # K14 — the two bare values no class rule reaches
    assert "font-size: 0.8125rem;\n  line-height: 1.45;" in styles  # aui_intro p:last-child
    assert "font-size: 0.6875rem;\n  line-height: 1.1rem;" in styles  # .aui-error-action

    # K4 — the count badge: 8x8 -> 16x16 at 11px type (E7: >= 16px at 11px)
    badge = patched["src/components/ui/badge.tsx"]
    assert "h-4 min-w-4 justify-center rounded-[4px] px-1 text-[0.6875rem]" in badge
    assert "text-[7px]" not in badge

    # K5 — the rail's profile glyph: 16px box keeps, the type steps up
    glyph = patched["src/components/ui/profile-glyph.tsx"]
    assert "size-4 shrink-0 place-items-center rounded-[3px] text-[0.6875rem]" in glyph

    # K6 — the handoff avatar in the session row: type up, svg 10px -> 12px
    row = patched["src/app/chat/sidebar/session-row.tsx"]
    assert 'text-[0.6875rem] [&_svg]:size-3' in row

    # K13 — the ONE dialog whose factor was over E3's 1.5 (20px vs the 11px label)
    overlay = patched["src/app/updates-overlay.tsx"]
    assert '<DialogTitle className="text-center text-base">{title}</DialogTitle>' in overlay
    assert '<DialogTitle className="text-center text-xl">{title}</DialogTitle>' not in overlay

    wave_anchors = {
        patch_app_ui.A11Y_COMPOSER_OLD,
        patch_app_ui.CAPTION_TOKEN_OLD,
        patch_app_ui.INTRO_PARA_OLD,
        patch_app_ui.ERROR_ACTION_OLD,
        patch_app_ui.BADGE_OVERLAY_OLD,
        patch_app_ui.PROFILE_GLYPH_OLD,
        patch_app_ui.HANDOFF_BADGE_OLD,
        patch_app_ui.UPDATE_TITLE_OLD,
    }
    seen = 0
    for rel, old, _new, _expected in patch_app_ui.UI_PATCHES:
        if old in wave_anchors:
            seen += 1
            assert patched[rel].count(old) == 0, f"{rel}: the anchor survived one run"
    assert seen == len(wave_anchors), "a wave anchor is not registered in UI_PATCHES"


def test_plugin_publishes_the_orb_runtime() -> None:
    """The host door (`gv-orb.tsx`) needs a publisher: the plugin hands over its
    vendored thinking-orbs once, plus an event for late mounts."""
    plugin_path = REPO / "integrations" / "hermes" / "godsv_plugin" / "desktop" / "plugin.js"
    plugin = plugin_path.read_text(encoding="utf-8")
    assert "globalThis.__GV_THINKING_ORB__ = {" in plugin
    assert "document.dispatchEvent(new Event('gv-orb-ready'))" in plugin
    assert "publishOrbRuntime()" in plugin
    orb_door = (REPO / "app-overlay" / "gv" / "gv-orb.tsx").read_text(encoding="utf-8")
    assert "gv-orb-ready" in orb_door and "__GV_THINKING_ORB__" in orb_door
    # the fallback is a static dot — never a second animated indicator
    body = orb_door.split('*/', 1)[1]
    assert "animation" not in body and "keyframes" not in body


# --------------------------------------------------------------------------- #
# Welle 2 — the colour-free state axis (AUFBAU/12 §1, AUFBAU/14 E1/E2)         #
# --------------------------------------------------------------------------- #

W2_HOOK = "src/app/shell/hooks/use-statusbar-items.tsx"
W2_ICONS = "src/lib/icons.ts"
W2_EN = "src/i18n/en.ts"

#: The 12 stock anchors of Welle 2 that are still registered, by constant name on
#: `patch_app_ui`. The three W2-C word constants are gone (MVP/06 — English-only).
W2_CONSTANTS = (
    "GATEWAY_CLASS_OLD",
    "GATEWAY_DEGRADED_OLD",
    "GATEWAY_ITEM_CLASS_OLD",
    "GATEWAY_DEPS_OLD",
    "VERSION_CLIENT_CLASS_OLD",
    "VERSION_BACKEND_CLASS_OLD",
    "AGENTS_FAILED_CLASS_OLD",
    "ICON_IMPORT_HOURGLASS_OLD",
    "ICON_IMPORT_PLUG_OLD",
    "ICON_EXPORT_HOURGLASS_OLD",
    "ICON_EXPORT_PLUG_OLD",
    "ICON_USES_OLD",
    "GATEWAY_ICON_OLD",
)


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_drops_the_colour_channel_and_widens_the_glyph_ladder() -> None:
    """Welle 2, W2-A + W2-B (AUFBAU/12 §1.2/§1.3, AUFBAU/14 E1).

    The gateway axis carried two state channels: the word (already 7 of 7
    distinct) and a colour class. The colour repeats what the word says, so it
    goes — but ONLY together with the wider glyph ladder: on the stock, four
    states shared one sign, and colour was the only thing telling them apart
    (AUFBAU/10 §4 R-A). Both halves are asserted here at once for that reason.
    """
    import patch_app_ui

    patched = _apply(HERMES_DESKTOP)
    hook = patched[W2_HOOK]

    # W2-A — no state colour left in the file that carried all 5 classes
    for token in ("text-amber", "text-destructive", "text-primary"):
        assert token not in hook, f"{token} survived the colour removal"
    assert "const gatewayDegraded" not in hook
    assert "gatewayClassName" not in hook
    # ...and the code that is left still uses every import it names
    assert hook.count("cn(") == 1

    # W2-B — 7 distinct signs over the 7 states, decided on the displayed WORD
    branch = hook.index("icon: gatewayRestarting ? (")
    ladder = hook[branch : hook.index("id: 'gateway-health'", branch)]
    signs = re.findall(r"<([A-Za-z]+)\b", ladder)
    assert len(set(signs)) == len(signs) == 7, f"glyph ladder is not 1:1: {signs}"
    for sign in ("Hourglass", "Plug", "AlertTriangle", "Power"):
        assert f"<{sign} " in ladder, f"missing sign {sign}"
    # the requirement is "at least 4 distinguishable signs"; measured: 7 for 7
    # (the stock had 3 for 7 states, and only 2 over the 6 not-ready states)
    # the two NEW signs live in the curated single source, not in the hook
    icons = patched[W2_ICONS]
    assert "  IconHourglass as Hourglass," in icons and "\n  Hourglass,\n" in icons
    assert "  IconPlug as Plug," in icons and "\n  Plug,\n" in icons
    assert "@tabler/icons-react" not in hook

    # every Welle-2 stock anchor is pattern C: consumed by the run, so a second
    # run over the same tree stops loudly instead of doubling silently
    seen = 0
    for name in W2_CONSTANTS:
        old = getattr(patch_app_ui, name)
        for rel, anchor, _new, _expected in patch_app_ui.UI_PATCHES:
            if anchor == old:
                seen += 1
                assert patched[rel].count(anchor) == 0, f"{name} survived one run"
    assert seen == len(W2_CONSTANTS), "a Welle-2 anchor is not registered in UI_PATCHES"


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_keeps_the_stock_english_state_words_behind_the_same_keys() -> None:
    """Welle 2, W2-C — REVERTED by MVP/06 (owner directive: English-only app).

    W2-C swapped the VALUES of the seven gateway words and the two agent-row
    words to German behind unchanged English keys (way "D2"). That is gone: the
    axis ships its stock English words again, so the patch entry and its three
    constants were deleted. Every key keeps its name — which is what the glyph
    ladder and every caller keep reading — and the German vocabulary is retired
    because no `de` locale may exist again.
    """
    # the overlay still applies cleanly (anchor sanity) ...
    _apply(HERMES_DESKTOP)
    # ... but W2-C is unregistered, so en.ts is no longer in the patched set.
    # Read the tree file itself: that is exactly what the app now ships.
    import patch_app_ui

    assert [t for t in patch_app_ui.UI_PATCHES if t[0] == W2_EN] == []
    en = (HERMES_DESKTOP / W2_EN).read_text(encoding="utf-8")

    for key in ("gatewayReady", "gatewayNeedsSetup", "gatewayUnavailable",
                "gatewayChecking", "gatewayConnecting", "gatewayOffline",
                "gatewayRestarting"):
        assert f"{key}:" in en, f"the key {key} disappeared - callers would break"
    for word in ("gatewayReady: 'ready',", "gatewayNeedsSetup: 'needs setup',",
                 "gatewayUnavailable: 'inference unavailable',",
                 "gatewayChecking: 'checking',", "gatewayConnecting: 'connecting',",
                 "'restarting…'"):
        assert word in en, f"missing English state word {word}"
    # the German values of W2-C must not come back
    for word in ("'bereit'", "'Einrichtung fehlt'", "'Inferenz nicht erreichbar'",
                 "'prüft'", "'verbindet'", "'Neustart läuft…'"):
        assert word not in en, f"the German state word {word} is back"
    # the three Skaldir core words survive as words, not as colours
    assert en.count("'offline'") == 1
    # the agent row's two detail words: the stock English plural branch, unchanged
    assert "${count} subagent${count === 1 ? '' : 's'}" in en
    assert "${count} failed" in en
    assert "${count} Subagent${count === 1 ? '' : 'en'}" not in en
    assert "${count} fehlgeschlagen" not in en


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_snaps_the_scale_classes_and_names_the_sixteen_px_exception() -> None:
    """Welle 2, the rest of K2 (AUFBAU/09 §a K2) + the ladder decision (AUFBAU/14 E7).

    `text-xs` (12px) and `text-sm` (14px) sat NEXT TO the ladder, not under the
    floor: the floor never reached them, so every `text-xs` label rendered 8 %
    too small (measured census: 14 deviants, all 12px). Same one-rule mechanism,
    `font-size` only — the utilities' own line-height tokens stay.

    The block also has to document the ladder's ONE exception: 16px is allowed
    for DIALOG TITLES only (K13 needed 16px because 15px is the `text-sm` step).
    """
    styles = _apply(HERMES_DESKTOP)["src/styles.css"]
    focus_at = styles.index(":where(a, button, summary,")
    floor_block = styles[:focus_at]
    snap_block = floor_block[floor_block.index("Skaldir type LADDER") :]

    # K2's two scale classes are on the ladder now (13px / 15px)
    assert ".text-xs {\n  font-size: 0.8125rem;\n}" in snap_block
    assert ".text-sm {\n  font-size: 0.9375rem;\n}" in snap_block
    # ...and they are SELECTORS of their own, not a re-declaration of a floored
    # utility (a second rule on a floored class overrides the floor silently)
    assert ".text-xs {" not in floor_block.split("Skaldir type LADDER")[0]
    assert ".text-sm {" not in floor_block.split("Skaldir type LADDER")[0]
    # font-size only — never an outline (the focus ring stays the single one)
    assert "outline:" not in snap_block
    # the 16px exception is written down where the rule lives, not in a chat
    assert "16px is allowed\n   for DIALOG TITLES only" in snap_block
    # the rule block reached the patched stylesheet exactly once
    assert styles.count("Skaldir type LADDER") == 1
    assert styles.count("Skaldir type ladder, second half") == 1


W2_MOCKUP_APP = REPO / "tools" / "ui-demo" / "src" / "App.tsx"


@pytest.mark.skipif(not W2_MOCKUP_APP.is_file(), reason="no ui-demo mockup in this tree")
def test_mockup_panel_box_counts_honestly_and_lists_the_17_rows() -> None:
    """Welle 2, W2-D (AUFBAU/12 §3, AUFBAU/14 E2).

    The box's header counted 2 panels while 3 labels were rendered (the third is
    wired NEGATIVELY: `checked={!subFrei}`). The counter is now derived from both
    sources, the box carries a `data-slot` so it is measurable at all, and the 17
    status-bar rows come from ONE list — no second list in the markup.
    """
    src = W2_MOCKUP_APP.read_text(encoding="utf-8")

    assert 'data-slot="panel-manager"' in src
    assert "VON 3 AN" in src and "VON 2 AN" not in src
    assert "+ (subFrei ? 0 : 1)" in src
    # the one source: 17 rows, 10 start hidden, 3 are locked (state, not a preference)
    rows = re.findall(
        r"\{ id: '[^']+', wort: '[^']+', gesperrt: (true|false), aus: (true|false) \}",
        src,
    )
    assert len(rows) == 17, f"the row list has {len(rows)} entries"
    assert sum(1 for g, a in rows if a == "true") == 10
    assert sum(1 for g, a in rows if g == "true") == 3
    assert sum(1 for g, a in rows if a == "false") == 7  # = checked in the box
    # the header the app uses, and the counter derived from the same list
    group_head = "IN DER STATUSZEILE ZEIGEN · "
    group_head += "{STATUSBAR_ZEILEN.filter(z => !z.aus).length} VON {STATUSBAR_ZEILEN.length} AN"
    assert group_head in src
    # locked rows are visible AND disabled (the app's lockedVisible semantics)
    assert "disabled={z.gesperrt} aria-disabled={z.gesperrt || undefined}" in src
    # the box carries no state colour of its own
    assert "text-amber" not in src and "text-destructive" not in src and "text-primary" not in src


# --------------------------------------------------------------------------- #
# Welle 3 — die Subagenten-Zone (AUFBAU/15, Owner-Vorgabe 18.09.)              #
# --------------------------------------------------------------------------- #

W3_ZONE = REPO / "app-overlay" / "gv" / "gv-subagent-zone.tsx"


def test_overlay_welle3_lists_and_zone_source() -> None:
    """Welle 3, tree-free half: the two anchors are registered and pattern C, the
    copied zone exists in `UI_FILES`, and the zone itself stays read-only, orb-free
    in the empty state and free of a SECOND poller / a second drag frame.
    """
    import patch_app_ui

    assert (W3_ZONE).is_file()
    assert ("app-overlay/gv/gv-subagent-zone.tsx",
            "apps/desktop/src/app/contrib/subagent-zone.tsx") in UI_FILES

    registered = {old for _rel, old, _new, _exp in patch_app_ui.UI_PATCHES}
    assert patch_app_ui.SUBAGENT_ZONE_IMPORT_OLD in registered
    assert patch_app_ui.SUBAGENT_ZONE_PANE_OLD in registered

    src = W3_ZONE.read_text(encoding="utf-8")
    body = src.split("*/", 1)[1]  # the doc comment is prose, not code
    # the mount point is the DOCK HINT, not a preset entry and not a tab
    assert "dock: { pane: 'sessions', pos: 'bottom' }" in body
    assert "'center'" not in body
    # both the pin (floating) and the grip (startPaneDrag) come from the existing
    # mechanics — no second drag frame anywhere in the file
    assert "startPaneDrag(SUBAGENTS_PANE_ID" in body
    assert "onPointerMove" not in body and "onPointerUp" not in body
    # free position is persisted by the HOST's floating pane
    assert body.count("SUBAGENT_ZONE_DOCK") == 2 and "placement: 'floating'" in body
    # ONE data source, ONE counting rule, no second poller
    assert "$subagentsBySession" in body and "activeSubagentCount(" in body
    assert "useSubagentSnapshot" not in body and "'subagent.list'" not in body
    # no second indicator, no orb without a run, no invented row
    assert "GlyphSpinner" not in body
    assert 'no subagent open' in body
    assert "sichtbar.length === 0 &&" in body
    # read-only: no steering/abort affordance leaves the zone
    assert "runSessionControlAction" not in body and "SubagentControls" not in body
    # closing collapses instead of removing the standing surface
    assert "registerPaneCloser(SUBAGENTS_PANE_ID" in body


@pytest.mark.skipif(not HERMES_DESKTOP.is_dir(), reason="no local Hermes desktop tree")
def test_overlay_welle3_mounts_the_zone_under_the_session_list() -> None:
    """Welle 3: the zone is a SECOND pane in the left column — registered as its own
    tile with the dock hint on `sessions`, and both anchors are consumed by the run
    (pattern C: a second run must stop loudly instead of adding a second import or a
    second tile).
    """
    import patch_app_ui

    patched = _apply(HERMES_DESKTOP)["src/app/contrib/controller.tsx"]
    zone_import = ("import { SUBAGENT_ZONE_DOCK, SUBAGENTS_PANE_ID, SUBAGENTS_ZONE_TITLE, "
                   "SubagentZonePane } from './subagent-zone'")
    assert patched.count(zone_import) == 1
    assert patched.count("id: SUBAGENTS_PANE_ID,") == 1
    assert patched.count("data: SUBAGENT_ZONE_DOCK,") == 1
    # the tile sits in the pane list, between `files` and `review`
    order = [patched.index("id: 'files'"),
             patched.index("id: SUBAGENTS_PANE_ID,"),
             patched.index("id: 'review'")]
    assert order == sorted(order)
    # pattern C — both anchor pairs are gone from the patched text
    assert patched.count(patch_app_ui.SUBAGENT_ZONE_IMPORT_OLD) == 0
    assert patched.count(patch_app_ui.SUBAGENT_ZONE_PANE_OLD) == 0
    # the column's clamp stays the session tile's (237/237/360), never a flex value
    # (AUFBAU/15 §4.1 Z-R4): two different numbers would give the sash two ranges.
    zone = W3_ZONE.read_text(encoding="utf-8")
    assert "minWidth: '237px'" in zone and "maxWidth: '360px'" in zone

