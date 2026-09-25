"""F15: store-overlay watchdog (sdk owner/deadline chains merged into patch_app.py).

Generated content — the sdk owner/deadline patches were folded into
patch_app.py as single-stage patches on 13.09. (tests/test_appbuild.py builds
all anchors up front; two-stage chains matched their anchor twice, failing the
anchor-safety tests). This file carries only the profile-store watchdog patch,
which is single-stage. The `[f15]` console.info lines remain the live
diagnostic channel (see tools/gv_cdp_console_stream.py).
"""
from __future__ import annotations

PATCHES: list[tuple[str, str, str, int]] = [
    (
        "src/store/profile.ts",
        "// Make `profile`'s backend the active gateway, lazily opening its"
        " socket if it\n",
        "// F15: age-based store-overlay watchdog (see generator"
        " docstring).\n"
        "console.info('[f15] watchdog module loaded')\n"
        "let overlayWatchValue: string | null = null\n"
        "let overlayWatchSince = 0\n"
        "window.setInterval(() => {\n"
        "  const cur = $gatewaySwapTarget.get()\n"
        "  ;(window as unknown as { __f15Watch?: unknown }).__f15Watch = {\n"
        "    value: cur === null ? null : String(cur),\n"
        "    since: overlayWatchSince,\n"
        "    t: Date.now()\n"
        "  }\n"
        "  if (cur === null) {\n"
        "    overlayWatchValue = null\n"
        "    overlayWatchSince = 0\n"
        "    return\n"
        "  }\n"
        "  // F15c: age of CONTINUOUS visibility — Wertwechsel (Re-Arms) dürfen\n"
        "  // die Uhr nicht zurücksetzen, sonst entwaffnet ein Retry-Zyklus den\n"
        "  // Watchdog dauerhaft.\n"
        "  if (overlayWatchSince === 0) {\n"
        "    overlayWatchValue = cur\n"
        "    overlayWatchSince = Date.now()\n"
        "    console.info(`[f15] overlay watchdog tracking '${cur}'`)\n"
        "  }\n"
        "  if (Date.now() - overlayWatchSince > 90_000) {\n"
        "    console.warn(`[profile] swap overlay stuck on '${cur}' for 90s;"
        " releasing the overlay`)\n"
        "    overlayWatchValue = null\n"
        "    overlayWatchSince = 0\n"
        "    $gatewaySwapTarget.set(null)\n"
        "  }\n"
        "}, 5_000)\n"
        "\n"
        "// Make `profile`'s backend the active gateway, lazily opening its"
        " socket if it\n",
        1,
    ),
]
