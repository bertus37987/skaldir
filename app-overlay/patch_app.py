"""Skaldir Agent app overlay — turns a Hermes desktop checkout into the GV app.

Runs against a *copy* of the Hermes source tree (never the checkout itself):

  1. PATCHES  — exact string swaps in three or four known files (each asserts
     it hit exactly once; a Hermes update that moves the anchor fails loudly
     instead of silently shipping a half-branded app),
  2. FILES    — bundled plugin + brand assets copied into the tree,
  3. the GV theme preset + default skin are injected so the app boots in gold.

The overlay lives in-engineer terms: same design language, same components —
only identity (name, mark, accent, splash) changes. Nothing here touches
runtime data; the built app reads the same $HERMES_HOME as stock Hermes.

Usage: python3 patch_app.py <target-tree> <repo-root>
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Theme preset — injected into src/themes/presets.ts                          #
# --------------------------------------------------------------------------- #

GODS_VOICE_THEME = """
/**
 * Skaldir Agent — the GV identity: monochrome — neutral greys, no hue accent.
 * Same design language as every builtin theme; only the palette is ours.
 */
export const godsVoiceTheme: DesktopTheme = {
  name: 'gods-voice',
  label: "Skaldir Agent",
  description: "Skaldir Agent — logo gold on warm charcoal",
  colors: {
    background: '#FAFAFA',
    foreground: '#171717',
    card: '#FFFFFF',
    cardForeground: '#171717',
    muted: '#F5F5F5',
    mutedForeground: '#595959',
    popover: '#FFFFFF',
    popoverForeground: '#171717',
    primary: '#171717',
    primaryForeground: '#FAFAFA',
    secondary: '#F5F5F5',
    secondaryForeground: '#171717',
    accent: '#EFEFEF',
    accentForeground: '#171717',
    border: '#DEDEDE',
    input: '#ffffff',
    ring: '#171717',
    midground: '#171717',
    midgroundForeground: '#FAFAFA',
    composerRing: '#171717',
    destructive: '#b32d2d',
    destructiveForeground: '#ffffff',
    sidebarBackground: '#FAFAFA',
    sidebarBorder: '#DEDEDE',
    userBubble: '#F0F0F0',
    userBubbleBorder: '#DEDEDE'
  },
  darkColors: {
    background: '#FAFAFA',
    foreground: '#171717',
    card: '#FFFFFF',
    cardForeground: '#171717',
    muted: '#F5F5F5',
    mutedForeground: '#595959',
    popover: '#FFFFFF',
    popoverForeground: '#171717',
    primary: '#171717',
    primaryForeground: '#FAFAFA',
    secondary: '#F5F5F5',
    secondaryForeground: '#171717',
    accent: '#EFEFEF',
    accentForeground: '#171717',
    border: '#DEDEDE',
    input: '#1c1f24',
    ring: '#171717',
    midground: '#171717',
    midgroundForeground: '#FAFAFA',
    composerRing: '#171717',
    destructive: '#ff6b7d',
    destructiveForeground: '#1a1500',
    sidebarBackground: '#FAFAFA',
    sidebarBorder: '#DEDEDE',
    userBubble: '#F0F0F0',
    userBubbleBorder: '#DEDEDE'
  },
  typography: {
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`
  }
}

"""

MARK_INJECT = """        <img
          alt=""
          aria-hidden="true"
          className="mx-auto mb-3 h-9 w-9 select-none opacity-90"
          draggable={false}
          src={`${import.meta.env.BASE_URL}gods-voice-mark.png`}
        />
        <p className="text-xs font-medium uppercase tracking-[0.3em] text-muted-foreground">{WORDMARK}</p>"""

# --------------------------------------------------------------------------- #
# The patches                                                                  #
# --------------------------------------------------------------------------- #

PATCHES: list[tuple[str, str, str, int]] = [
    # ---- theme: gold identity, gold default ---------------------------------
    (
        "src/themes/presets.ts",
        "export const BUILTIN_THEMES: Record<string, DesktopTheme> = {",
        GODS_VOICE_THEME
        + "export const BUILTIN_THEMES: Record<string, DesktopTheme> = {",
        1,
    ),
    (
        "src/themes/presets.ts",
        "  cyberpunk: cyberpunkTheme\n}",
        "  cyberpunk: cyberpunkTheme,\n  'gods-voice': godsVoiceTheme\n}",
        1,
    ),
    (
        "src/themes/presets.ts",
        "export const DEFAULT_SKIN_NAME = 'nous'",
        "export const DEFAULT_SKIN_NAME = 'gods-voice'",
        1,
    ),
    (
        "src/themes/use-skin-command.ts",
        "  default: 'nous',",
        "  default: 'gods-voice',",
        1,
    ),
    (
        "src/themes/use-skin-command.ts",
        "  gold: 'nous',",
        "  gold: 'gods-voice',",
        1,
    ),
    # ---- F15: the session-open swap overlay must never stay latched -------
    # SET for every awaitHydration open, but the finally only cleared when
    # `generation === openSessionGeneration` — a superseded open stepped past
    # the clear and "Waking up default…" could stay forever. Owner token:
    # whoever set it last owns clearing it; superseded opens step aside.
    (
        "src/sdk/index.ts",
        "let openSessionGeneration = 0\n",
        "let openSessionGeneration = 0\n"
        "\n"
        "// F15: owner token for the session-open swap overlay. The clear in"
        " the\n"
        "// finally must follow ownership, never a generation compare — a"
        " superseded\n"
        "// open would otherwise leave the overlay latched forever.\n"
        "let swapOverlayOwner: symbol | null = null\n"
        "let swapOverlayDeadline: number | null = null\n"
        "let swapOverlayFirstArmed: number | null = null\n",
        1,
    ),
    (
        "src/sdk/index.ts",
        "    const generation = ++openSessionGeneration\n",
        "    const generation = ++openSessionGeneration\n"
        "    const swapToken = Symbol('session-open')\n",
        1,
    ),
    (
        "src/sdk/index.ts",
        "      if (options.awaitHydration) {\n"
        "        // Keep the target-specific overlay visible through"
        " transcript hydration,\n"
        "        // not merely through the gateway/profile activation that"
        " precedes it.\n"
        "        $gatewaySwapTarget.set(targetProfile)\n"
        "      }\n",
        "      if (options.awaitHydration) {\n"
        "        // Keep the target-specific overlay visible through"
        " transcript hydration,\n"
        "        // not merely through the gateway/profile activation that"
        " precedes it.\n"
        "        // Ownership: the newest open owns the overlay; a"
        " superseded one only\n"
        "        // clears when nothing newer has claimed it (F15).\n"
        "        swapOverlayOwner = swapToken\n"
        "        $gatewaySwapTarget.set(targetProfile)\n"
        "        // F15c: ABSOLUTE Frist ab dem ERSTEN Arming — ein re-armender\n"
        "        // (superseded) Open darf die Schranke nicht neu aufziehen, sonst\n"
        "        // entwaffnet ein Resume-Retry-Zyklus die Deadline dauerhaft.\n"
        "        if (swapOverlayFirstArmed === null) {\n"
        "          swapOverlayFirstArmed = Date.now()\n"
        "          console.info('[f15] swap overlay deadline armed (sdk, absolute)')\n"
        "          swapOverlayDeadline = window.setTimeout(() => {\n"
        "            const shown = $gatewaySwapTarget.get()\n"
        "            console.info(`[f15] sdk deadline fired: shown=${String(shown)}`)\n"
        "            swapOverlayOwner = null\n"
        "            swapOverlayDeadline = null\n"
        "            swapOverlayFirstArmed = null\n"
        "          console.warn('[bot-wake] swap overlay deadline expired;"
        " surfacing recovery')\n"
        "          setResumeExhaustedSessionId(storedSessionId)\n"
        "          $gatewaySwapTarget.set(null)\n"
        "          }, hydrationTimeoutMs * 3 + 30_000)\n"
        "        } else {\n"
        "          console.info('[f15] swap overlay re-armed; absolute deadline unchanged')\n"
        "        }\n"
        "      }\n",
        1,
    ),
    (
        "src/sdk/index.ts",
        "    } finally {\n"
        "      if (options.awaitHydration && generation ==="
        " openSessionGeneration) {\n"
        "        $gatewaySwapTarget.set(null)\n"
        "      }\n"
        "    }\n",
        "    } finally {\n"
        "      if (options.awaitHydration && swapOverlayOwner === swapToken)"
        " {\n"
        "        swapOverlayOwner = null\n"
        "        $gatewaySwapTarget.set(null)\n"
        "        if (swapOverlayDeadline !== null) {\n"
        "          window.clearTimeout(swapOverlayDeadline)\n"
        "          swapOverlayDeadline = null\n"
        "        }\n"
        "        swapOverlayFirstArmed = null\n"
        "      }\n"
        "    }\n",
        1,
    ),
    # ---- splash: the Skaldir Agent wordmark + mark, not Hermes' ---------------
    (
        "src/components/chat/intro.tsx",
        "const WORDMARK = 'HERMES AGENT'",
        'const WORDMARK = "SKALDIR AGENT"',
        1,
    ),
    (
        "src/components/chat/intro.tsx",
        '        <Wordmark className="mb-1" text={WORDMARK} />',
        MARK_INJECT,
        1,
    ),
    # ---- brand badge: our mark replaces the nous-girl tile -------------------
    (
        "src/components/brand-mark.tsx",
        "// Brand badge: nous-girl mark on a white tile, identical in light/dark.",
        "// Brand badge: the Skaldir Agent mark on its charcoal tile.",
        1,
    ),
    (
        "src/components/brand-mark.tsx",
        "'inline-flex size-14 shrink-0 items-center justify-center overflow-hidden rounded-md bg-white',",
        "'inline-flex size-14 shrink-0 items-center justify-center overflow-hidden"
        " rounded-md bg-[#111111]',",
        1,
    ),
    (
        "src/components/brand-mark.tsx",
        "src={assetPath('nous-girl.jpg')}",
        "src={assetPath('gods-voice-mark.png')}",
        1,
    ),
    # ---- app identity: name, id, executable ----------------------------------
    (
        "package.json",
        '"appId": "com.nousresearch.hermes",',
        '"appId": "dev.godsvoice.app",',
        1,
    ),
    (
        "package.json",
        '"executableName": "Hermes",',
        '"executableName": "gods-voice",',
        1,
    ),
    (
        "package.json",
        '"productName": "Hermes",',
        '"productName": "Skaldir Agent",',
        2,
    ),
    # ---- window/document titles ----------------------------------------------
    (
        "index.html",
        "<title>Hermes</title>",
        "<title>Skaldir Agent</title>",
        1,
    ),
    (
        "src/main.tsx",
        "document.title = 'Hermes HUD'",
        'document.title = "Skaldir Agent HUD"',
        1,
    ),
    # The engine grew a SECOND update dialog (fb56a7e); each is anchored with its
    # own context so a future move still fails loudly instead of hitting the wrong one.
    (
        "electron/main.ts",
        "        title: 'Hermes update',\n        message: 'The update finished, but needs one more step',",
        '        title: "Skaldir Agent update",\n        message: \'The update finished, but needs one more step\',',
        1,
    ),
    (
        "electron/main.ts",
        "          title: 'Hermes update',\n          message: \"Hermes couldn't finish updating\",",
        '          title: "Skaldir Agent update",\n          message: \"Hermes couldn\'t finish updating\",',
        1,
    ),
    # ---- nous-portal wording out of the UI (links stay functional) -----------
    (
        "src/i18n/en.ts",
        "      portal: 'Nous Portal Support',",
        "      portal: 'Status & Support',",
        1,
    ),
    (
        "src/i18n/en.ts",
        "        linkText: 'Nous portal',",
        "        linkText: 'portal',",
        1,
    ),
    (
        "src/i18n/en.ts",
        "open the Nous Portal (instance status and controls)",
        "open the status page (instance status and controls)",
        1,
    ),
    (
        "src/i18n/en.ts",
        "      cloudDownCheckPortal: 'Check Portal status',",
        "      cloudDownCheckPortal: 'Check status',",
        1,
    ),
    (
        "src/i18n/en.ts",
        "      nousIncluded: 'Included with a Nous subscription — sign in with your Nous account to activate.',",
        "      nousIncluded: 'Included with your subscription — sign in to activate.',",
        1,
    ),
    (
        "src/i18n/en.ts",
        "      nousAuthNeededTitle: 'Sign in with your Nous account',",
        "      nousAuthNeededTitle: 'Sign in to activate',",
        1,
    ),
    (
        "src/i18n/en.ts",
        "will only work once you sign in with your Nous account.`,",
        "will only work once you sign in.`,",
        1,
    ),
    (
        "src/i18n/en.ts",
        "      nousAuthDoneTitle: 'Nous account connected',",
        "      nousAuthDoneTitle: 'Connected',",
        1,
    ),
    # ---- empty chat: a short question, not a feature catalogue (UI audit) -----
    (
        "src/components/chat/intro-copy.jsonl",
        '{"personality":"none","headline":"What are we building today?","body":"Describe the task in your own words. I\'ll pick the right tools, explain my plan, and check in before risky steps."}',
        '{"personality":"none","headline":"What should get done?","body":"Describe it in your own words — I verify before anything counts as complete."}',
        1,
    ),
    # ---- F15c: 404-Tile-Reconcile-Schleife stoppen (Audit 13.09., T8) -------
    # reconcileTileTranscripts las alle ~10 s jedes Tile neu und schluckte den
    # 404 einer im Backend geloeschten Session voellig still -> Endlos-Loop.
    (
        "src/app/contrib/hooks/use-background-sync.ts",
        "  $sessionTiles,\n"
        "  publishSessionState,",
        "  $sessionTiles,\n"
        "  closeSessionTile,\n"
        "  publishSessionState,",
        1,
    ),
    (
        "src/app/contrib/hooks/use-background-sync.ts",
        "    } catch {\n"
        "      // Non-fatal: the next change event retries.\n"
        "    }",
        "    } catch (err) {\n"
        "      // F15c: a tile whose stored session no longer exists in the\n"
        "      // backend must be closed, not retried forever: the sessions tick\n"
        "      // re-reads every tile every ~10 s, so a stale localStorage tile\n"
        "      // caused an endless 'Session not found' 404 loop (audit 13.09.).\n"
        "      const detail = String((err as Error | null)?.message ?? err ?? '')\n"
        "      if (detail.includes('404') || detail.toLowerCase().includes('session not found')) {\n"
        "        signatureRef.current.delete(signatureKey)\n"
        "        console.warn(`[f15] transcript 404 for tile '${storedSessionId}'; closing tile`)\n"
        "        try {\n"
        "          closeSessionTile(storedSessionId)\n"
        "        } catch {\n"
        "          /* tile may already be gone */\n"
        "        }\n"
        "        continue\n"
        "      }\n"
        "      // Non-fatal otherwise: the next change event retries.\n"
        "    }",
        1,
    ),
    # ---- v6: build the app identity so the integrated-nav UA gate matches ----
    # plugin.js's INTEGRATED branch (6 native sidebar areas, aside off) keys off
    # the UA product token; without HERMES_DESKTOP_APP_NAME the default 'Hermes'
    # never matches /SkaldirAgent/, so manual/debug starts silently lost the nav
    # (audit T3, 13.09.). Baking the default fixes every start path.
    (
        "electron/main.ts",
        "const APP_NAME = process.env.HERMES_DESKTOP_APP_NAME || 'Hermes'",
        "const APP_NAME = process.env.HERMES_DESKTOP_APP_NAME || \"Skaldir Agent\"",
        1,
    ),
]

# ---- files copied in (src relative to the repo, dst relative to the tree) ----
FILES: list[tuple[str, str]] = [
    # the panel — bundled plugin, registered at boot, no install step
    (
        "integrations/hermes/godsv_plugin/desktop/plugin.js",
        "apps/desktop/src/plugins/godsv/plugin.js",
    ),
    # the mark — splash + brand badge
    ("src/skaldir/assets/logo-mark.png", "apps/desktop/public/gods-voice-mark.png"),
    # the app icon (linux uses icon.png; icns/ico stay stock for non-linux packs)
    ("src/skaldir/assets/icon.png", "apps/desktop/assets/icon.png"),
]

# files removed from the built tree (unreferenced after patching)
REMOVE: list[str] = [
    "apps/desktop/public/nous-girl.jpg",
]


# --------------------------------------------------------------------------- #
# machinery                                                                    #
# --------------------------------------------------------------------------- #


def _write_atomic(path: Path, content: str) -> None:
    """Write via temp + os.replace — breaks hardlinks so the source tree is safe."""
    tmp = path.with_name(path.name + ".gv-tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".gv-tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


def apply_patches(tree: Path, repo: Path) -> dict:
    """Apply every patch + file copy to *tree*. Raises on a missed anchor."""
    report: dict = {"patched": {}, "copied": [], "removed": [], "errors": []}
    for rel, old, new, expected in PATCHES:
        path = tree / "apps" / "desktop" / rel
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            report["errors"].append(f"{rel}: file missing (upstream layout changed?)")
            continue
        count = text.count(old)
        if count != expected:
            report["errors"].append(f"{rel}: anchor found {count}x, expected {expected}x: {old[:60]!r}")
            continue
        _write_atomic(path, text.replace(old, new))
        report["patched"][rel] = report["patched"].get(rel, 0) + count
    for src_rel, dst_rel in FILES:
        src = repo / src_rel
        dst = tree / dst_rel
        if not src.exists():
            report["errors"].append(f"missing source file: {src_rel}")
            continue
        _copy_atomic(src, dst)
        report["copied"].append(dst_rel)
    for rel in REMOVE:
        target = tree / rel
        if target.exists():
            target.unlink()
            report["removed"].append(rel)
    if report["errors"]:
        raise RuntimeError("Skaldir Agent overlay failed:\n  " + "\n  ".join(report["errors"]))
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    tree, repo = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    try:
        report = apply_patches(tree, repo)
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return 1
    for rel, n in sorted(report["patched"].items()):
        print(f"✓ patched {rel} ({n})")
    for rel in report["copied"]:
        print(f"✓ copied  {rel}")
    for rel in report["removed"]:
        print(f"✓ removed {rel}")
    print("Skaldir Agent overlay applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))


# F15 defense-in-depth: bounded swap overlay (owner-checked deadline).
# __F15B_EXTEND__
import pathlib as _pathlib  # noqa: E402
import sys as _sys  # noqa: E402

_sys.path.insert(0, str(_pathlib.Path(__file__).parent))

from patch_app_f15b import PATCHES as _F15B_PATCHES  # noqa: E402

PATCHES.extend(_F15B_PATCHES)

# UI correction overlay (fonts, type floor, zoom, a11y names, one thinking
# indicator) — see patch_app_ui.py for the audit finding behind each anchor.
from patch_app_ui import UI_FILES as _UI_FILES  # noqa: E402
from patch_app_ui import UI_PATCHES as _UI_PATCHES  # noqa: E402

PATCHES.extend(_UI_PATCHES)
FILES.extend(_UI_FILES)
