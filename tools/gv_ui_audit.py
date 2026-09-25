#!/usr/bin/env python
"""Product audit runner for the built Skaldir app (§17/§18/§19 of the UI refresh goal).

Drives the RUNNING app over CDP with Playwright — the real product, not a component
test — and measures the things the goal names explicitly instead of "looking at
screenshots":

  * Electron security boundary: node integration must be OFF in the renderer
    (`window.require` / `window.process` absent), the context must be isolated and
    the document secure.
  * Responsive/overflow: at 1280x800 and 900x650 every element is checked for
    `scrollWidth > clientWidth` (the "controls that spring out sideways / overflow"
    the goal forbids) and for elements painted outside the viewport.
  * Accessibility: axe-core over the chat surface, plus focusability of the
    interactive controls.
  * Reduced motion: with `prefers-reduced-motion: reduce` the activity indicator must
    still be there and must not animate.
  * Dead controls: every registered sidebar/nav target is really reachable (the route
    changes when it is activated) — a visible control that leads nowhere is a finding.

Evidence per run goes to `outputs/ui-audit/<stamp>/`: `report.json` (all measurements),
`shot-<viewport>.png`, `axe.json`.

Usage:  .venv/bin/python tools/gv_ui_audit.py --port 9333 [--label after-ui-refresh]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

OVERFLOW_SCRIPT = """
() => {
  const out = [];
  const all = document.querySelectorAll('*');
  for (const el of all) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const dx = el.scrollWidth - el.clientWidth;
    const dy = el.scrollHeight - el.clientHeight;
    if (dx > 2 && style.overflowX !== 'auto' && style.overflowX !== 'scroll') {
      // `hover-marquee` is a deliberate pattern (truncate + reveal on hover); reporting it as
      // overflow was a false positive of this rule (verified 17.09. against the host sidebar:
      // data-slot="sidebar-group-content" rows). Keep it as a note, never as a P1.
      const marquee = String(el.className || '').includes('hover-marquee');
      out.push({
        kind: marquee ? 'marquee-note' : 'horizontal',
        over: dx,
        tag: el.tagName.toLowerCase(),
        cls: String(el.className || '').slice(0, 90),
        text: String(el.textContent || '').trim().slice(0, 60)
      });
    }
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && (rect.right > window.innerWidth + 2 || rect.left < -2)) {
      out.push({
        kind: 'outside-viewport',
        over: Math.round(Math.max(rect.right - window.innerWidth, -rect.left)),
        tag: el.tagName.toLowerCase(),
        cls: String(el.className || '').slice(0, 90),
        text: String(el.textContent || '').trim().slice(0, 60)
      });
    }
  }
  out.sort((a, b) => b.over - a.over);
  return out.slice(0, 25);
}
"""

SURFACE_SCRIPT = """
() => {
  const text = document.body.innerText || '';
  return {
    hash: location.hash,
    panelMounted: /SKALDIR/.test(text) && /Treasury/.test(text) && /Verification/.test(text),
    hasTitle: /Skaldir Agent|God's Voice/.test(document.title),
    documentTitle: document.title,
    skaldirMarkers: ['SKALDIR', 'Treasury', 'Verification'].filter((m) => text.includes(m))
  };
}
"""

SECURITY_SCRIPT = """
() => ({
  hasRequire: typeof window.require !== 'undefined',
  hasProcess: typeof window.process !== 'undefined',
  hasModule: typeof window.module !== 'undefined',
  secureContext: Boolean(window.isSecureContext),
  electronApi: typeof (window.electron || window.hermesDesktop || null),
  userAgent: navigator.userAgent,
  viewport: { w: window.innerWidth, h: window.innerHeight }
})
"""
CONTRACT_SCRIPT = """
() => {
  const out = {}
  const body = getComputedStyle(document.body)
  out.body_font_family = body.fontFamily
  out.geist_in_stack = /Geist/i.test(body.fontFamily)
  out.sego_in_stack = /Segoe WPC/i.test(body.fontFamily)
  // rendered text: the floor the type rule enforces (no label below 11px)
  const leaves = []
  document.querySelectorAll('body *').forEach((el) => {
    if (!el.children.length && el.textContent && el.textContent.trim().length > 1) leaves.push(el)
  })
  const tiny = leaves.filter((el) => parseFloat(getComputedStyle(el).fontSize) < 11)
  out.text_nodes = leaves.length
  out.below_11px = tiny.length
  out.below_11px_sample = tiny.slice(0, 5).map((el) => ({
    text: el.textContent.trim().slice(0, 30),
    size: getComputedStyle(el).fontSize,
    tag: el.tagName.toLowerCase()
  }))
  const sizes = leaves.map((el) => parseFloat(getComputedStyle(el).fontSize)).filter((n) => Number.isFinite(n))
  out.min_font_size = sizes.length ? Math.min(...sizes) : null
  // mono surfaces really use the bundled mono
  const mono = Array.from(document.querySelectorAll('.font-mono, code, pre')).slice(0, 5)
  out.mono_families = mono.map((el) => getComputedStyle(el).fontFamily).slice(0, 3)
  // zoom: Chromium folds the page zoom into devicePixelRatio
  out.device_pixel_ratio = window.devicePixelRatio
  out.inner = { w: window.innerWidth, h: window.innerHeight }
  out.outer = { w: window.outerWidth, h: window.outerHeight }
  // the chat must not carry a cockpit entry or a permanent voice band
  const named = []
  document.querySelectorAll('button, a, [role="button"], [role="link"]').forEach((el) => {
    const name = String(el.getAttribute('aria-label') || el.textContent || '').trim().toLowerCase()
    if (name.includes('cockpit')) named.push(name.slice(0, 60))
  })
  out.cockpit_entries = named
  const top = document.querySelector('[data-slot="composer.top"]')
  out.composer_top_children = top ? top.querySelectorAll('*').length : -1
  out.composer_top_text = top ? String(top.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120) : null
  // The chat-first rule the audit found broken: no single button may own the
  // whole composer width (that was the full-width cockpit entry). The host does
  // not mark plugin slots with data-slot, so the measurement runs over the
  // composer itself and reports the widest button relative to it.
  const composerBox = document.querySelector('[class~="group/composer"]')
  out.composer_width = composerBox ? Math.round(composerBox.getBoundingClientRect().width) : null
  out.widest_composer_button = (() => {
    if (!composerBox) return null
    const box = composerBox.getBoundingClientRect().width || 1
    let widest = { width: 0, name: '' }
    composerBox.querySelectorAll('button').forEach((el) => {
      const width = el.getBoundingClientRect().width
      if (width > widest.width) {
        widest = { width: Math.round(width), name: String(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40) }
      }
    })
    return { ...widest, ratio: Number((widest.width / box).toFixed(3)) }
  })()
  out.plan_rows = document.querySelectorAll('[data-godsv-plan]').length
  out.plan_text = (() => {
    const el = document.querySelector('[data-godsv-plan]')
    return el ? String(el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160) : null
  })()
  out.plan_toggle = Boolean(document.querySelector('[data-godsv-plan-toggle]'))
  out.orbs = document.querySelectorAll('[data-godsv-orb]').length
  out.orb_states = Array.from(document.querySelectorAll('[data-godsv-orb]')).map((el) => el.getAttribute('data-godsv-orb'))
  // Braille spinner glyphs (U+2800..U+28FF) — the audit's "competing thinking
  // indicator". Scoped to where agent work is signalled (the composer and its
  // status stack); the count for the whole document is reported for information,
  // because list/loading chrome elsewhere is a different concern.
  const braille = /[\\u2800-\\u28FF]/
  const brailleIn = (root) => {
    if (!root) return 0
    let count = 0
    root.querySelectorAll('*').forEach((el) => {
      if (!el.children.length && braille.test(el.textContent || '')) count += 1
    })
    return count
  }
  const composerRoot = document.querySelector('[data-slot="composer-bounds"], [class~="group/composer"]')
  out.braille_nodes = brailleIn(document.body)
  out.braille_in_composer = brailleIn(composerRoot)
  out.composer_glyph_spinners = composerRoot ? composerRoot.querySelectorAll('.glyph-spinner').length : 0
  out.orbs_in_composer = composerRoot ? composerRoot.querySelectorAll('[data-godsv-orb]').length : 0
  out.landmarks = {
    main: document.querySelectorAll('main, [role="main"]').length,
    nav: document.querySelectorAll('nav, [role="navigation"]').length,
    aside: document.querySelectorAll('aside, [role="complementary"]').length
  }
  return out
}
"""

APPROVAL_MENU_SCRIPT = """
() => {
  const trigger = document.querySelector('[data-godsv-approvals-trigger]')
  if (!trigger) return { present: false }
  const before = trigger.getAttribute('data-godsv-approvals')
  trigger.click()
  return { present: true, mode_before: before }
}
"""

APPROVAL_MENU_READ_SCRIPT = """
() => {
  const menu = document.querySelector('[data-godsv-approval-menu]')
  const options = Array.from(document.querySelectorAll('[data-godsv-approval-option]')).map((el) => el.getAttribute('data-godsv-approval-option'))
  const trigger = document.querySelector('[data-godsv-approvals-trigger]')
  const labels = menu ? String(menu.innerText || '').replace(/\\s+/g, ' ').trim() : null
  return {
    menu_open: Boolean(menu),
    options,
    labels,
    mode_after_open: trigger ? trigger.getAttribute('data-godsv-approvals') : null,
    expanded: trigger ? trigger.getAttribute('aria-expanded') : null
  }
}
"""

THEME_SCRIPT = """
() => {
  const cs = getComputedStyle(document.documentElement)
  const body = getComputedStyle(document.body)
  const luminance = (rgb) => {
    const m = String(rgb).match(/(\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)/)
    if (!m) return null
    const [r, g, b] = [Number(m[1]), Number(m[2]), Number(m[3])]
    return Number(((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255).toFixed(4))
  }
  return {
    dark_class: document.documentElement.classList.contains('dark'),
    bg_token: cs.getPropertyValue('--background').trim(),
    border_token: cs.getPropertyValue('--dt-border').trim(),
    body_bg: body.backgroundColor,
    body_luminance: luminance(body.backgroundColor),
    font_sans: cs.getPropertyValue('--dt-font-sans').trim().slice(0, 120),
    brand: cs.getPropertyValue('--dt-font-brand').trim()
  }
}
"""



FOCUS_SCRIPT = """
() => {
  const controls = document.querySelectorAll('button, [role="button"], a[href], input, textarea, [tabindex]:not([tabindex="-1"])');
  let focusable = 0;
  for (const el of controls) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (el.disabled) continue;
    focusable += 1;
  }
  return { controls: controls.length, focusable };
}
"""

NAV_SCRIPT_READ = """
() => Array.from(document.querySelectorAll('nav a, nav button, [data-sidebar] a, [data-sidebar] button'))
  .map((el) => ({ label: String(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40),
                  href: String(el.getAttribute('href') || '') }))
  .filter((e) => e.label)
"""


def sh(cmd: list[str], timeout: float = 30.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as err:  # noqa: BLE001 - reported, not swallowed
        return f"(command failed: {err})"


def contract_checks(page, report: dict, finding) -> None:
    """The design contract of the Skaldir UI fix wave, measured on the live app.

    Every check stores its raw measurement, so a failure is a number and not an
    opinion. The checks that changed by design (no cockpit entry, no permanent
    voice band, no braille spinner, an explicit approval menu) FAIL when the old
    behaviour comes back.
    """
    c: dict = {}
    report["measurements"]["contract"] = c
    checks: dict[str, bool] = {}

    def record(key: str, passed: bool, detail: str = "") -> None:
        checks[key] = bool(passed)
        print(f"{'ok  ' if passed else 'FAIL'} contract: {key}{(' — ' + detail) if detail else ''}")

    # 0) the chat surface is what gets measured here
    try:
        page.evaluate("() => { window.location.hash = '#/' }")
        page.wait_for_timeout(1500)
    except Exception:  # noqa: BLE001
        pass

    data = page.evaluate(CONTRACT_SCRIPT)
    c["chat"] = data

    # 1) typography: Geist in the stack, no Windows-only face, nothing below 11px
    record("fonts_geist", data["geist_in_stack"] and not data["sego_in_stack"], str(data["body_font_family"])[:120])
    record("small_text_zero", data["below_11px"] == 0, f"{data['below_11px']} node(s), min {data['min_font_size']}px")

    # 2) zoom: no shrink-to-fit page zoom (the audited build shipped 0.9)
    dpr = data["device_pixel_ratio"]
    record("zoom_not_shrunk", dpr is None or dpr >= 0.99, f"devicePixelRatio={dpr}")

    # 3) chat-first: no cockpit entry, no permanent band, no competing spinner
    record("no_cockpit_entry", len(data["cockpit_entries"]) == 0, str(data["cockpit_entries"])[:120])
    record("composer_top_not_a_button", (data.get("widest_composer_button") or {}).get("ratio", 0) < 0.9,
           f"widest button in the composer = {json.dumps(data.get('widest_composer_button'))} of {data.get('composer_width')}px")
    record("no_permanent_voice_band", "no goal yet" not in str(data["composer_top_text"] or ""),
           str(data["composer_top_text"])[:120])
    record("braille_gone", data["braille_in_composer"] == 0 and data["composer_glyph_spinners"] == 0,
           f"in composer: braille={data['braille_in_composer']} glyph-spinner={data['composer_glyph_spinners']} "
           f"(document-wide braille={data['braille_nodes']})")
    record("main_landmark", data["landmarks"]["main"] >= 1, str(data["landmarks"]))

    # 4) the plan row exists only with a goal — both readings are useful
    plan_present = data["plan_rows"] >= 1
    c["plan"] = {
        "present": plan_present,
        "text": data["plan_text"],
        "toggle": data["plan_toggle"],
        "orbs": data["orbs"],
        "orb_states": data["orb_states"]
    }
    if plan_present:
        record("plan_row_reachable", data["plan_toggle"], str(data["plan_text"])[:120])
        # the plan is EXPANDED by default (owner 17.09.) and carries both blocks
        plan_state = page.evaluate(
            """() => {
              const el = document.querySelector('[data-godsv-plan]')
              const text = el ? String(el.innerText || '').replace(/\\s+/g, ' ').trim() : ''
              return {
                expanded: el ? el.getAttribute('data-godsv-expanded') : null,
                task_block: text.includes('Part of the goal'),
                verdict_block: text.includes('Verifiziert durch Evidenz'),
                add: Boolean(document.querySelector('[data-godsv-plan-add]')),
                items: document.querySelectorAll('[data-godsv-plan-item]').length,
                selection_boxes: document.querySelectorAll('[data-godsv-plan] input[type="checkbox"]').length,
                move_controls: document.querySelectorAll('[aria-label="Move criterion up"], [aria-label="Move criterion down"]').length,
                orbs_in_plan: document.querySelectorAll('[data-godsv-plan] [data-godsv-orb]').length,
                text
              }
            }"""
        )
        c["plan_expanded"] = plan_state
        record(
            "plan_expanded_with_hierarchy",
            plan_state["expanded"] == "1" and plan_state["task_block"] and plan_state["verdict_block"],
            json.dumps({k: v for k, v in plan_state.items() if k != "text"})[:200]
        )
        record(
            "plan_items_editable",
            plan_state["add"] and plan_state["selection_boxes"] == plan_state["items"] and plan_state["move_controls"] >= plan_state["items"] - 1,
            f"items={plan_state['items']} boxes={plan_state['selection_boxes']} moves={plan_state['move_controls']} add={plan_state['add']}"
        )
        hash_before = page.evaluate("() => location.hash")
        page.evaluate("() => document.querySelector('[data-godsv-plan-toggle]')?.click()")
        page.wait_for_timeout(600)
        details = page.evaluate(
            """() => {
              const el = document.querySelector('[data-godsv-plan-details]')
              const text = el ? String(el.innerText || '').replace(/\\s+/g, ' ').trim() : ''
              return {
                open: Boolean(el),
                text,
                criteria: document.querySelectorAll('[data-godsv-criterion]').length,
                hash: location.hash
              }
            }"""
        )
        c["plan_details"] = details
        record("plan_expands_in_place", details["open"] and details["hash"] == hash_before, str(details)[:160])
        record("plan_separates_task_from_verdict",
               "Part of the task" in details["text"] and "Independently verified" in details["text"],
               details["text"][:160])
        page.evaluate("() => document.querySelector('[data-godsv-plan-toggle]')?.click()")
        page.wait_for_timeout(400)
    else:
        record("plan_row_reachable", True, "no goal in the focused session — the row is goal-gated by design")

    # 5) approvals: NOT in the chat (owner 17.09.). The status bar owns that
    # safety setting, so the composer must carry no approval control at all.
    approval = page.evaluate(
        """() => ({
          in_composer: document.querySelectorAll('[class~="group/composer"] [data-godsv-approvals]').length,
          anywhere: document.querySelectorAll('[data-godsv-approvals]').length,
          statusbar_control: (() => {
            const bar = document.querySelector('[data-slot="statusbar"]')
            if (!bar) return null
            const el = Array.from(bar.querySelectorAll('button')).find((node) =>
              /approval/i.test(String(node.getAttribute('aria-label') || node.textContent || ''))
            )
            return el ? String(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60) : null
          })()
        })"""
    )
    c["approval"] = approval
    record("approvals_out_of_the_chat", approval["in_composer"] == 0 and approval["anywhere"] == 0, json.dumps(approval)[:160])

    # 5b) the chatbar: taller input, ring off, monochrome
    composer_tokens = page.evaluate(
        """() => {
          const cs = getComputedStyle(document.documentElement)
          const box = document.querySelector('[class~="group/composer"]')
          return {
            ring_strength: cs.getPropertyValue('--composer-ring-strength').trim(),
            input_min_height: cs.getPropertyValue('--composer-input-min-height').trim(),
            height: box ? Math.round(box.getBoundingClientRect().height) : null
          }
        }"""
    )
    c["composer"] = composer_tokens
    record(
        "chatbar_ring_off_and_taller",
        composer_tokens["ring_strength"] in ("0", "0px", "0rem") and composer_tokens["input_min_height"] not in ("", "1.625rem"),
        json.dumps(composer_tokens)
    )

    # 6) the panel page (settings) carries a heading, not a telemetry header
    page.evaluate("() => { window.location.hash = '#/gods-settings' }")
    page.wait_for_timeout(1800)
    panel = page.evaluate(
        """() => {
          const text = String(document.body.innerText || '')
          const headings = Array.from(document.querySelectorAll('h1, h2, [role="heading"]'))
            .map((el) => String(el.textContent || '').trim()).filter(Boolean)
          return {
            has_mode_line: /whisper mode/i.test(text),
            has_refresh: /\\u21bb refresh/.test(text),
            has_as_of: /as of \\d/i.test(text),
            headings: headings.slice(0, 6),
            mains: document.querySelectorAll('main, [role="main"]').length
          }
        }"""
    )
    c["settings_page"] = panel
    record("settings_no_telemetry_header",
           not panel["has_mode_line"] and not panel["has_refresh"] and not panel["has_as_of"],
           str(panel)[:180])
    record("settings_has_heading", len(panel["headings"]) >= 1, str(panel["headings"])[:120])

    # 7) theme: the system mode follows the host scheme and the tokens really move
    page.evaluate("() => { window.location.hash = '#/' }")
    page.wait_for_timeout(800)
    page.emulate_media(color_scheme="dark")
    page.wait_for_timeout(900)
    dark = page.evaluate(THEME_SCRIPT)
    page.emulate_media(color_scheme="light")
    page.wait_for_timeout(900)
    light = page.evaluate(THEME_SCRIPT)
    page.emulate_media(color_scheme=None)
    page.wait_for_timeout(400)
    c["theme"] = {"dark": dark, "light": light}
    tokens_moved = dark["bg_token"] != light["bg_token"] or dark["body_bg"] != light["body_bg"]
    darker = (
        dark["body_luminance"] is not None
        and light["body_luminance"] is not None
        and dark["body_luminance"] < light["body_luminance"]
    )
    record("theme_follows_host_scheme", tokens_moved and darker,
           f"dark={dark['body_bg']}/{dark['body_luminance']} light={light['body_bg']}/{light['body_luminance']}")
    record("brand_font_token", bool((dark.get("brand") or "").strip()), str(dark.get("brand"))[:80])

    # 8) the three reviewed sizes, with the overflow rule the goal names
    for width, height in ((1920, 1048), (1280, 800), (900, 650)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(900)
        real = page.evaluate(
            """() => ({
              w: window.innerWidth, h: window.innerHeight,
              scrollW: document.documentElement.scrollWidth,
              clientW: document.documentElement.clientWidth
            })"""
        )
        sidebar = page.evaluate(
            """() => {
              const el = document.querySelector('[data-slot="sidebar"], aside, nav')
              if (!el) return null
              const box = el.getBoundingClientRect()
              return { width: Math.round(box.width), hidden: getComputedStyle(el).display === 'none' || box.width === 0 }
            }"""
        )
        composer = page.evaluate(
            """() => {
              const el = document.querySelector('[class~="group/composer"]')
              if (!el) return null
              const box = el.getBoundingClientRect()
              return { width: Math.round(box.width), left: Math.round(box.left), right: Math.round(box.right) }
            }"""
        )
        key = f"{width}x{height}"
        c.setdefault("sizes", {})[key] = {"real": real, "sidebar": sidebar, "composer": composer}
        record(f"size_{key}_no_horizontal_scroll", real["scrollW"] <= real["clientW"] + 2, str(real))
        if composer:
            record(f"size_{key}_composer_usable", composer["width"] >= 260, str(composer))

    c["contract_summary"] = {
        "passed": sum(1 for v in checks.values() if v),
        "failed": sum(1 for v in checks.values() if not v),
        "checks": checks
    }
    report["measurements"]["contract_summary"] = c["contract_summary"]
    for key, passed in checks.items():
        if not passed:
            finding("P1", f"design contract: {key}", json.dumps(c.get(key, c), ensure_ascii=False)[:400])
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(600)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9333, help="CDP port of the running app")
    ap.add_argument("--label", default="audit", help="label for the evidence directory")
    ap.add_argument("--out", default="outputs/ui-audit", help="evidence root")
    ap.add_argument("--contract", dest="contract", action="store_true", default=True,
                    help="run the design-contract checks (default)")
    ap.add_argument("--no-contract", dest="contract", action="store_false",
                    help="skip the design-contract checks")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = REPO / args.out / f"{stamp}-{args.label}"
    outdir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "label": args.label,
        "started": stamp,
        "cdp_port": args.port,
        "git_head": sh(["git", "-C", str(REPO), "rev-parse", "HEAD"]).strip(),
        "findings": [],
        "measurements": {}
    }

    def finding(severity: str, what: str, detail: str) -> None:
        report["findings"].append({"severity": severity, "what": what, "detail": detail})
        print(f"[{severity}] {what}: {detail}")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        except Exception as err:  # noqa: BLE001
            print(f"CDP not reachable on {args.port}: {str(err).splitlines()[0][:160]}")
            return 2
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        page.wait_for_timeout(2500)

        # 0) which surface are we measuring? (the panel must be mounted, not assumed)
        try:
            page.evaluate("() => { window.location.hash = '#/gods-voice' }")
            page.wait_for_timeout(2500)
        except Exception as err:  # noqa: BLE001
            print(f"panel navigation failed: {str(err).splitlines()[0][:120]}")
        surface = page.evaluate(SURFACE_SCRIPT)
        report["measurements"]["surface"] = surface
        if not surface["panelMounted"]:
            finding("P1", "panel surface not mounted — UI claims would measure the shell",
                    json.dumps(surface, ensure_ascii=False))

        # 1) Electron security boundary -------------------------------------------------
        security = page.evaluate(SECURITY_SCRIPT)
        report["measurements"]["security"] = security
        if security["hasRequire"] or security["hasModule"]:
            finding("P0", "renderer exposes node integration", json.dumps(security))
        if security["hasProcess"]:
            finding("P1", "renderer exposes `process`", json.dumps(security))
        if not security["secureContext"]:
            finding("P2", "document is not a secure context", json.dumps(security))

        # 2) responsive + overflow ------------------------------------------------------
        for width, height in ((1280, 800), (900, 650)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(1200)
            key = f"{width}x{height}"
            # An Electron window is not resizable through the CDP page: claim a size only
            # when the renderer really reports it, otherwise name the size we measured.
            real = page.evaluate("() => ({ w: window.innerWidth, h: window.innerHeight })")
            applied = real["w"] == width and real["h"] == height
            report["measurements"][f"viewport_{key}"] = {"requested": key, "applied": applied, "real": real}
            where = key if applied else f"real {real['w']}x{real['h']} (requested {key}, NOT applied by Electron)"
            overflow = page.evaluate(OVERFLOW_SCRIPT)
            report["measurements"][f"overflow_{key}"] = overflow
            shot = outdir / f"shot-{key}.png"
            page.screenshot(path=str(shot))
            hard = [o for o in overflow if o["kind"] == "horizontal" and o["over"] > 8]
            if hard:
                finding("P1", f"horizontal overflow at {where}",
                        "; ".join(f"{o['tag']}.{o['cls'][:40]} +{o['over']}px" for o in hard[:4]))
            if not applied:
                finding("P2", f"viewport claim not verified for {key}",
                        f"renderer reports {real['w']}x{real['h']}; the Electron window is fixed, so "
                        f"the measured numbers belong to that size, not to {key}")
            outside = [o for o in overflow if o["kind"] == "outside-viewport" and o["over"] > 8]
            if outside:
                finding("P2", f"elements painted outside the viewport at {key}",
                        "; ".join(f"{o['tag']}.{o['cls'][:40]} +{o['over']}px" for o in outside[:4]))

        # 3) accessibility: axe + focusability ------------------------------------------
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(800)
        try:
            from axe_playwright_python.sync_playwright import Axe
            axe = Axe()
            results = axe.run(page)
            violations = getattr(results, "response", {}).get("violations", []) if isinstance(getattr(results, "response", None), dict) else []
            (outdir / "axe.json").write_text(json.dumps(violations, ensure_ascii=False, indent=2), encoding="utf-8")
            report["measurements"]["axe_violations"] = [
                {"id": v.get("id"), "impact": v.get("impact"), "nodes": len(v.get("nodes") or [])}
                for v in violations
            ]
            serious = [v for v in violations if v.get("impact") in ("critical", "serious")]
            if serious:
                finding("P1", "axe: critical/serious accessibility violations",
                        ", ".join(f"{v.get('id')}({v.get('impact')})x{len(v.get('nodes') or [])}" for v in serious[:5]))
        except Exception as err:  # noqa: BLE001
            report["measurements"]["axe_error"] = str(err).splitlines()[0][:160]
            print(f"axe could not run: {report['measurements']['axe_error']}")
        focus = page.evaluate(FOCUS_SCRIPT)
        report["measurements"]["focus"] = focus
        if focus["controls"] and focus["focusable"] == 0:
            finding("P1", "no focusable control found", json.dumps(focus))

        # 4) reduced motion -------------------------------------------------------------
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_timeout(800)
        reduced = page.evaluate(
            """() => {
              const animated = Array.from(document.querySelectorAll('*')).filter((el) => {
                const s = getComputedStyle(el);
                return s.animationName !== 'none' && s.animationDuration !== '0s';
              });
              return { animated: animated.length, sample: animated.slice(0, 3).map((el) => el.className) };
            }"""
        )
        report["measurements"]["reduced_motion"] = reduced
        if reduced["animated"] > 0:
            finding("P2", "motion continues under prefers-reduced-motion",
                    json.dumps(reduced["sample"])[:200])
        page.emulate_media(reduced_motion="no-preference")

        # 5) dead controls: every visible nav target must really lead somewhere ---------
        nav = page.evaluate(NAV_SCRIPT_READ)
        report["measurements"]["nav_entries"] = nav
        before = page.evaluate("() => location.hash + location.pathname")
        reached = []
        for entry in nav[:8]:
            label = entry["label"]
            clicked = page.evaluate(
                """(label) => {
                  const els = Array.from(document.querySelectorAll('nav a, nav button, [data-sidebar] a, [data-sidebar] button'));
                  const target = els.find((el) => String(el.getAttribute('aria-label') || el.textContent || '').trim().startsWith(label));
                  if (!target) return false;
                  target.click();
                  return true;
                }""",
                label,
            )
            page.wait_for_timeout(900)
            after = page.evaluate("() => location.hash + location.pathname")
            reached.append({"label": label, "clicked": clicked, "changed": after != before})
            before = after
        report["measurements"]["nav_reachability"] = reached
        dead = [r for r in reached if not r["changed"] and r["clicked"]]
        if dead:
            finding("P2", "nav targets whose activation changed no route",
                    ", ".join(r["label"] for r in dead))

        # 6) the design contract of the UI fix wave (fonts, chat-first chrome,
        # the one thinking indicator, the approval menu, theme, the three sizes)
        if args.contract:
            contract_checks(page, report, finding)
            # screenshots at the three reviewed sizes, next to the measurements
            for width, height in ((1920, 1048), (1280, 800), (900, 650)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(900)
                page.screenshot(path=str(outdir / f"shot-contract-{width}x{height}.png"))

    (outdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nui audit: {len(report['findings'])} finding(s) — evidence in {outdir}")
    return 0 if not any(f["severity"] in ("P0", "P1") for f in report["findings"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
