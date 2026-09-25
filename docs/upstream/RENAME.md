# RENAME — God's Voice → **Skaldir Agent** (non-Python surfaces)

Scope of this record: the **non-Python** surfaces only — `integrations/**` (the desktop plugin
`plugin.js`, `plugin.yaml`, `manifest.json`, the Python plugin adapter, the dashboard API),
`app-overlay/**` (display strings and comments only — **not** the anchors or the anchor code),
`docs/**`, `README-CLI.md` and an append to `THIRD_PARTY_NOTICES.md`. The Python package rename
(`src/godsv/**` → `src/skaldir/**`, `pyproject.toml`) belongs to a sibling block and is **not**
described as done here.

This file is the honest half of the rename: what changed, what was deliberately **not** changed
because a running app depends on it, and what only the owner can do.

---

## 1. The naming truth table (measured in this tree, 2026-09-18)

| Thing | Value | Status |
|---|---|---|
| Product name (user-visible) | **Skaldir Agent** | renamed everywhere in scope |
| Slug / docs shorthand | `skaldir` | renamed |
| Desktop window class (`WM_CLASS`) | `SkaldirAgent` | already renamed in `src/godsv/desktop.py:27` |
| App menu / launcher name | `Skaldir` (`MENU_NAME`), icon `skaldir` | already renamed (`desktop.py:20-30`) |
| Splash wordmark | `SKALDIR AGENT` | already renamed (`app-overlay/patch_app.py:242`) |
| Electron `productName` | `Skaldir Agent` | already renamed (`patch_app.py:287`) |
| Electron `appId` | `dev.godsvoice.app` | **kept** — see §4.5 |
| Executable / console script | `godsvoice` | **kept** — owned by the Python block, §4.2 |
| Distribution + import package | `godsv` | **kept** — owned by the Python block, §4.2 |
| Plugin ID | `godsv` | **kept** — §4.1 |
| Runtime dir | `$GODSV_HOME` → `$HERMES_HOME/godsv` → `~/.hermes/godsv` | **kept** — §4.4 |
| Environment variables | `GODSV_*` | **kept** — §4.3 |
| Panel route | `/gods-voice` (+ `/gods-verification`, `/gods-activity`, `/gods-tools`, `/gods-settings`, `/gods-treasury`) | **kept** — §4.6 |
| Transcript marker | `::godsv-live{…}` | **kept** — §4.9 |

---

## 2. Mapping table

| Old | New | Status |
|---|---|---|
| `God's Voice` (prose, titles, UI strings, docstrings) | `Skaldir Agent` | **renamed** |
| `God's-Voice` (German compound, e.g. `God's-Voice-App`) | `Skaldir Agent` | **renamed** |
| `GOD'S VOICE` (headings, splash/wordmark references, HUD specs) | `SKALDIR AGENT` | **renamed** |
| `GodsVoice` (incl. the internal component name `GodsVoiceEmpty`) | `SkaldirAgent` (`SkaldirAgentEmpty`) | **renamed** |
| `”GodsVoice”`, `”God's Voice AI”` (trademark-check notes) | `”SkaldirAgent”`, `”Skaldir Agent AI”` | **renamed** |
| `--class=GodsVoice` in `docs/UI.md` | `--class=SkaldirAgent` | **renamed** (matches the code) |
| `dashboard/manifest.json` `label` | `Skaldir Agent` | **renamed** (`name` stays `godsv`) |
| `plugin.yaml` `description` | `Skaldir Agent — …` | **renamed** (`name` stays `godsv`) |
| `plugin.js` engine note, file docstring, empty-state component | `Skaldir Agent …` | **renamed** |
| `plugin_api.py` module docstring + the one panel sentence | `Skaldir Agent …` | **renamed** |
| `plugin.yaml` `homepage` → `github.com/bertus37987/gods-voice` | — | **kept** — repository rename is owner-only, §5 |
| `install.sh`'s installed menu entry, `.desktop` files, `~/.local/share/icons` | `Skaldir` / `skaldir` | **already renamed in code, live install still old** — §5 |
| `God'sVoice/<version>` (Electron UA) | kept | **kept for compatibility** — §4.10 |
| `God's Voice — Demo (…)`, `GV serve not found — is the God's Voice app running?`, `✓ God's-Voice-App-Build (nativ)`, `title:"God's Voice"`, `title is God's Voice` | kept | **kept — quoted/measured output**, §3.2 |
| Benchmark arm labels `Full God's Voice…` (paired with ids `full_godsv*`) | kept | **kept for compatibility**, §3.2 |
| `godsv*` package/module/path/label/env/plugin-id tokens | `godsv` | **kept for compatibility** — §4 |
| `godsvoice` command invocations in docs | `godsvoice` | **kept** (documenting a binary that exists) — §4.2 |
| Content of the legacy CLI art `src/godsv/assets/logo.ansi` (`"GOD'S VOICE"`) | kept | **kept** — it is a description of an existing asset, §3.2 |

---

## 3. Inventory

### 3.1 Before (this scope, `git`-clean base of this block)

Product-name occurrences (`God's Voice` / `God's-Voice` / `God'sVoice` / `Gods Voice` /
`GOD'S VOICE` / `GodsVoice` / `Gods-Voice`): **209 occurrences in 187 lines across 50 files**.
Files with the most: `docs/COMPLETE_AUDIT-2026-09-13.md` 34 lines, `docs/FEATURES.md` 10,
`docs/FEATURE-MAP.md` 9, `docs/EXPERIENCE-V6-PLAN.md` 8, `docs/LIVE-STREAM-SPEC.md` 8,
`docs/IMPLEMENTATION.md` 7, `integrations/hermes/godsv_plugin/__init__.py` 6,
`docs/APP-BUILD.md` / `docs/CORE-PLAN.md` / `docs/UI.md` / `docs/PROJECT-STATE.md` 6 each.
(`app-overlay/**` contained **no** product-name text at all — only identifiers, which is why that
directory is unchanged by this block.)

### 3.2 After — every occurrence that was deliberately **kept**, with the reason

**43 occurrences in 36 lines across 12 files remain** (measured after this block, excluding
`docs/RENAME.md` itself — this mapping document necessarily spells the old name out, 37 more
occurrences, and that is its job). Each remaining occurrence is either quoted/measured output or
an identifier:

| File:line | Why it is still there |
|---|---|
| `integrations/hermes/godsv_plugin/desktop/plugin.js:718` | UA gate `/(?:God'sVoice\|SkaldirAgent)\//` — the *older* app builds ship UA `God'sVoice/x`; dropping it would silently demote those installs to the non-integrated nav. |
| `docs/FEATURES.md:306` | A measured CDP probe row (DOM title + splash as measured on 13.09.) — evidence, not product text. |
| `docs/E2E-LIVE-PROOF-40.md:50` | Literal stderr text of `tools/gv_eval_longhorizon.py`. |
| `docs/E2E-LIVE-PROOF-40.md:102` | Measured UA `God'sVoice/0.17.1`. |
| `docs/EXPERIENCE-V6-PLAN.md:16` | UA gate literal in a "what is implemented" table. |
| `docs/PROJECT-STATE.md:545`, `:563` (+2 neighbouring lines) | Measured UA / `HERMES_DESKTOP_APP_NAME` value / the plugin's own `/GodsVoice\//` gate as it was. |
| `docs/RUNBOOK.md:403` | Literal output of `godsvoice demo`. |
| `docs/CLI-STYLE.md:107` | Describes the **content** of the legacy colour art `src/godsv/assets/logo.ansi`. |
| `docs/COMPLETE_AUDIT-2026-09-13.md` (20 occurrences, lines 2340, 2602, 2796, 3483, 4207, 4208, 4226, 4227, 4241, 4276, 4291, 4444, 4445, 4897, 4924, 5507, …) | The audit is a dated evidence document: UA strings, `/json/list` titles, `gv_eval_longhorizon` stderr, the `Gods-Voice` **test-fixture path name** of `tests/test_workflows.py` (tests are not owned by this block). |
| `docs/BENCHMARK-RUN-SCHEMA.json:30-32` | Arm labels sit next to their contract ids (`full_godsv`, `full_godsv_memory`, `full_godsv_capabilities`); renaming the label without the id would break stored run manifests. |
| `docs/audit/BENCHMARK_MANIFEST.json:61,70,79` | The frozen manifest of the same arms — evidence. |
| `docs/audit/PLATFORM-MATRIX.md:51` | Literal `desktop status` output used as proof. |
| `THIRD_PARTY_NOTICES.md:1,3,100` | This file is **append-only** for this wave; the heading/copyright line and the host-dependency row are licence facts, not product text (see §6). |

### 3.3 What was actually renamed

152 lines by the mechanical pass (`docs/**` + `integrations/**`, literal-protecting), plus 4 lines
by hand that the pass cannot see: `dashboard/manifest.json` `label`, the two prose lines of
`docs/BENCHMARK-RUN-SCHEMA.json`, and the escaped JS string in
`plugin.js` (`'God\'s Voice runs on …'`). One line was deliberately reverted to the measured
literal (`docs/COMPLETE_AUDIT-2026-09-13.md:4924`, `/json/list → Titel`).

Files touched by this block (**48**):

- `integrations/hermes/README.md` (renamed **and translated** to English — it is the plugin's
  user-facing doc; all env vars, paths and measured numbers kept verbatim),
  `integrations/hermes/godsv_plugin/plugin.yaml`, `…/dashboard/manifest.json`,
  `…/dashboard/plugin_api.py`, `…/desktop/plugin.js`, `…/__init__.py`
- `README-CLI.md` — naming block: product `Skaldir Agent`, slug `skaldir`, and an explicit
  statement that `godsvoice`/`godsv` are contracts, linking here
- `docs/BENCHMARK-RUN-SCHEMA.json` and **39** `docs/*.md` files (headers, titles, prose)
- `THIRD_PARTY_NOTICES.md` — appended rename notice only (append-only rule)
- this file (`docs/RENAME.md`, new)

---

## 4. Contracts that were **not** renamed (and the safe migration for each)

The rule applied: a name that a *running* process reads is a contract. Renaming it in a document
is free; renaming it in the tree without a migration is a broken install.

### 4.1 Plugin ID `godsv`
`plugin.yaml` `name:`, `dashboard/manifest.json` `name`, `plugin.js` `const ID = 'godsv'`,
the tool `godsv_capabilities`, the logger `godsv.panel`, the install dir `~/.hermes/plugins/godsv/`
and the HTTP mount `/api/plugins/godsv/`.
**Consequence of a blind rename:** `~/.hermes/config.yaml` (`plugins.enabled` /
`plugins.entries.godsv.…`) no longer matches, so the panel and the `godsv_capabilities` tool
disappear; the host logs `Failed to load plugin 'godsv'` (or the new id) and every panel route
404s. The tool name is what the *model* calls, so a rename also orphans prompts and tests.
**Safe migration:** install the plugin under the new id **as an additional** plugin, add it to
`plugins.enabled`, verify with `hermes plugins doctor <new-id>` + one live session, keep the old
entry until then, and only afterwards remove `~/.hermes/plugins/godsv`. Keep `godsv_capabilities`
as a deprecated alias for at least one release. `plugin.yaml` now carries a comment saying exactly
this.

### 4.2 Console script `godsvoice` and package `godsv`
Both live in `pyproject.toml` (`[project.scripts] godsvoice = "godsv.perf_lazy:main"`,
`name = "godsv"`), owned by the Python rename block.
**Consequence:** the installed binary `~/.local/bin/godsvoice` and `.venv/bin/godsvoice` exist
today; the overlay's `UI_FILES` copy from `src/godsv/assets/**`; `mcp_link` stores an absolute
command path (tests pin `…/venv/bin/godsvoice`); `python -m godsv.mcpserver` is documented as the
MCP entry point. Renaming only the docs would document a binary that is not installed.
**Safe migration:** add `skaldir` as a *second* console script next to `godsvoice` for one
release, re-install (`uv tool install`/`pipx`), re-run `hermes mcp remove` + `hermes mcp add`
(the stored command path changes), and update the overlay `UI_FILES` source paths in the same
commit. Therefore **every command example in this tree's docs still says `godsvoice`** — that is
the honest state, not an oversight.

### 4.3 `GODSV_*` environment variables
Read by the plugin (inside the running Hermes gateway) and by `src/godsv/**`:
`GODSV_HOME`, `GODSV_MODE`, `GODSV_LLM`, `GODSV_TRIAGE`, `GODSV_GUARD`, `GODSV_GUARD_FORCE`,
`GODSV_PAUSE`, `GODSV_PAUSE_WAIT`, `GODSV_PAUSE_TTL`, `GODSV_VAULT_KEY`, `GODSV_LLM_*`,
`GODSV_DEBUG_TRACE`, `GODSV_TRACE_FILE`, `GODSV_PROMPTS`, `GODSV_COLOR`, `GODSV_CHECK_*_CMD`.
**Consequence of a blind rename:** an unknown variable is silently ignored and several of these are
fail-open — `GODSV_MODE` falls back to `shadow` (no delivery), `GODSV_GUARD`/`GODSV_PAUSE` default
to `1` but `GODSV_GUARD=0`/`GODSV_PAUSE=0` would stop being honoured, and `GODSV_HOME` would point
at the default dir (a runtime that reports "no events db"). The same names are in the owner's shell
profiles, the app launcher env, and every doc.
**Safe migration:** read `SKALDIR_*` first and fall back to `GODSV_*`, log one deprecation warning
per variable, keep the alias for a release, and only then update launchers/systemd units + docs
together.

### 4.4 Runtime / state directory
Resolution order is one rule: `$GODSV_HOME` → `$HERMES_HOME/godsv` → `~/.hermes/godsv`. It holds
`events.db`, `memory.db`, `tools.db`, `pauses.db`, `treasury.db`, `runs.db`, `settings.yaml`,
`inbox.jsonl`, the installed app (`app/`, `app-src/` hardlink mirror) and the app's userData.
**Consequence:** renaming the directory without moving the data looks like a fresh install —
empty memory, no grants (the vault key is unrelated), no run history — and `memory-stats` /
`selftest` report missing DBs.
**Safe migration:** resolve new → old with a log line; `cp -a` the directory (never move it while
the app runs), verify with `godsvoice doctor`, keep a symlink at the old path for one release.

### 4.5 Desktop app identity: `appId dev.godsvoice.app`
`app-overlay/patch_app.py` keeps `appId` (and therefore the Electron userData directory
`$HERMES_HOME/godsv/desktop-userdata`) while the *visible* identity is already Skaldir
(`productName`, `executableName` handling, `Name=`/`Icon=`/`StartupWMClass=SkaldirAgent`).
**Consequence:** changing `appId` orphans the profile — single-instance lock, window state and the
localStorage keys the panel uses (`hermes.desktop.lastRoute` = `/gods-voice`,
`hermes.desktop.godsv.subagentsFloating`).
**Safe migration:** keep `appId`, or migrate the userData dir **and** rewrite the stored
localStorage keys in the same build.

### 4.6 Panel routes and the launcher URL
`PANEL_ROUTE = "gods-voice"` in `src/godsv/desktop.py:111` is turned into
`hermes://open/gods-voice` for the launcher; `plugin.js` registers `/gods-voice`,
`/gods-verification`, `/gods-activity`, `/gods-tools`, `/gods-settings`, `/gods-treasury`; the
overlay patch (anchor-bearing) neutralises a *stored* `/gods-voice`.
**Consequence:** renaming a route without the others breaks the launcher, the footer nav and the
"already stored route" neutralisation.
**Safe migration:** add the new routes in `plugin.js` **and** the overlay in one change, keep the
old route as a redirect for one release. The overlay lines that touch this are anchor code — out of
bounds for this block by rule, so they are **unchanged**.

### 4.7 Overlay anchors — verified untouched
`app-overlay/patch_app_ui.py` (55 anchors + 11 file entries) has no product-name text; only
`/gods-voice` and `src/godsv/assets/...` identifiers appear there. See §7 for the measured
before/after numbers.

### 4.8 Docker ownership labels
`godsv.owner`, `godsv.capability`, `godsv.build_id` (used by the runtime's teardown scoping, and by
the label-filter helpers described in `docs/RUNBOOK.md`).
**Consequence:** a rename makes previously labelled containers invisible to teardown (leaked
containers) and to tests.
**Safe migration:** write the new prefix, keep matching the old prefix in teardown for one release,
and never scope a sweep by a comma-joined label filter (Docker does not AND them).

### 4.9 In-tree protocol tokens
`::godsv-live{…}` transcript marker, the `[voice …]` hint prefix, the `godsv·contract:` /
`godsv·dod:` hint lines, `data-godsv-*` DOM attributes and `#godsv-chrome-styles`. Producer
(plugin / `src/godsv`) and consumer (app overlay, probes) must change in the same release; a
one-sided rename silently stops the live card from rendering.
**Also retained:** `GodsVoiceEmpty` was renamed to `SkaldirAgentEmpty` (internal to `plugin.js`,
no external reference — checked), while `GodsvSession` (imported by tests) was **not** renamed.

### 4.10 Legacy/measured literals (kept for compatibility or evidence)
- UA token `God'sVoice` in the plugin's `INTEGRATED` gate — keeps older app builds working.
- Benchmark arm labels `Full God's Voice…` — paired with the contract ids `full_godsv*`.
- Quoted output and measured values, see §3.2.

---

## 5. Things only the owner can rename

| Item | Why only the owner |
|---|---|
| GitHub repository `bertus37987/gods-voice` (and the `homepage:` field in `plugin.yaml`) | repository settings; GitHub keeps a redirect for the old URL, so links in older docs keep working |
| The installed app on this machine: `~/.hermes/godsv/app/**`, `~/.hermes/godsv/app-src/**` (hardlink mirror — never written by an agent), `~/.local/share/applications/*.desktop`, `~/.local/share/icons/hicolor/*/apps/{skaldir,gods-voice}.png`, `~/.local/bin/godsvoice` | re-install/rebuild + remove-legacy steps; needs `godsvoice desktop uninstall && … install` (or `desktop build-app --force`) on the live machine |
| `~/.hermes/plugins/godsv/` (the live plugin install) and `~/.hermes/config.yaml` (`plugins.enabled`, `mcp` entries) | host config is written through the host's own commands (`hermes plugins …`, `hermes mcp add/remove`), never hand-edited |
| The shell profile exports of `GODSV_*`, and any systemd/desktop launcher env | owner's environment |
| The distribution/console-script rename (`godsv`/`godsvoice` → `skaldir`) | `pyproject.toml` (sibling block) **plus** a re-install on the machine |

---

## 6. Append-only file

`THIRD_PARTY_NOTICES.md` was **not** rewritten: the heading (`# Third-Party Notices — God's Voice
(`godsv`)`), the copyright line and the host-dependency row are licence facts. A rename notice was
**appended** at the end instead (§"Rename notice"). Credits are unchanged by the rename.

---

## 7. Verification (measured, this block)

| Check | Before | After |
|---|---|---|
| `tools/check_ui_overlay_anchors.py` (exit code) | **0** — “all 55 anchors hit exactly once and all 11 files exist” | **55/55 anchors still hit exactly once**; the 8 `src/godsv/assets/fonts/*` file entries report missing **because a sibling block moved `src/godsv/` → `src/skaldir/` at 19:25–19:26 CEST** (`git status`: `R src/godsv/assets/fonts/… -> src/skaldir/assets/fonts/…`), not because of this block. Proof: in a scratch copy of the repo with `src/godsv/assets` restored from `src/skaldir/assets`, the same checker run on **this block's edited overlay** prints “all 55 anchors hit exactly once and all 11 files exist”, exit 0. |
| `node tools/check_desktop_plugin.mjs` (plugin load smoke, after the `plugin.js` edits) | — | `desktop panel: contract OK` |
| `ruff check` on the changed Python files (`integrations/hermes/godsv_plugin/__init__.py`, `…/dashboard/plugin_api.py`, `app-overlay/`) | clean | `All checks passed!` |
| `json.load` / `yaml.safe_load` on the changed structured files | valid | valid (`manifest.json`, `docs/BENCHMARK-RUN-SCHEMA.json`, `plugin.yaml`) |
| `pytest` subset: `test_appbuild test_desktop test_hermes_plugin test_plugin_installation_is_complete test_panel_api test_panel_actions test_panel_trust_boundary test_plugin_robustness test_plugin_incident_wiring test_plugin_self_contained_import test_ui_feed_coverage` + `test_ui_overlay test_gv_update_gate` | — | **152 passed, 3 failed**. All three failures have **one** cause that is not this block: `src/godsv/assets/**` (10 paths) was moved to `src/skaldir/assets/**` by the sibling Python block at 19:25–19:26, while the overlay's copy sources (`app-overlay/patch_app.py` / `patch_app_ui.py` `FILES`/`UI_FILES`) still name the old paths. Disk check of the 12 pinned sources: **8 MISS** (`src/godsv/assets/logo-mark.png`, `icon.png`, 6 fonts, `OFL.txt`, `…/fonts/MANIFEST.json`), **4 OK** (the three `app-overlay/gv/*.tsx` and `integrations/hermes/godsv_plugin/desktop/plugin.js` — the last one is edited by this block and present). Failing: `test_ui_overlay::test_overlay_fonts_are_vendored_with_their_licence` (`assert PosixPath('…/src/godsv/assets/fonts/MANIFEST.json').is_file()` → False), `test_ui_overlay::test_overlay_lists_are_internally_consistent`, `test_gv_update_gate::test_anchor_dryrun_runs_the_real_anchors_against_a_generated_tree` (`exit 22 == 0`). **Fix is out of this block's bounds** (overlay anchor code): either point the two `FILES`/`UI_FILES` lists at `src/skaldir/assets/**`, or leave a `src/godsv/assets` symlink next to the deprecation shim. |
| `pytest` — first attempt during the sibling's move | — | was unrunnable (`ModuleNotFoundError: No module named 'godsv.events'` while `src/godsv/` was empty); after the sibling's deprecation shim (`src/godsv/__init__.py`) it ran as above |

Backup of the pre-change state of every file this block touched:
`/home/willy/gv-backups/rename2-20260918-192544/scope.tgz` (plus `before-sha256.txt`).

---

## 8. Left outside this block (documented, not touched)

| Surface | Old-name occurrences | Owner |
|---|---|---|
| `README.md` | 0 product-name lines (`godsv`/`godsvoice` identifiers only) | — |
| `CHANGELOG.md` | 22 lines with the old product name (changelog = history) | unassigned |
| `src/godsv/**`, `pyproject.toml` | the package/module/console-script rename | Python rename block (in flight) |
| `tests/**` (12 files, e.g. `test_workflows.py:186` fixture title `God's Voice`, `test_run_supervisor.py`, `test_appbuild.py`, `test_gv_crash_recovery.py`) | docstrings + one fixture | test owner |
| `tools/check_desktop_plugin.mjs` (2), `tools/gv_update_gate.py` (3) | comments | tool owner |
| `install.sh` (4), `host-overlay/**`, `docker/**`, `prompts/**`, `scripts/**` | product name in comments/text | unassigned |
| `docs/CLI-REFERENCE.md` | 0 — but it is **generated** (`tools/gv_cli_reference.py`) and pins the executable name; regenerate with `.venv/bin/python tools/gv_cli_reference.py --out docs/CLI-REFERENCE.md` after the console-script rename | tool owner |
| German prose in legacy docs (`docs/*.md`) | the rename was applied **inside** German sentences (`God's Voice` → `Skaldir Agent`, `God's-Voice-App` → `Skaldir Agent-App`); the surrounding German is the English sweep's job, not this block's | English sweep |
| Doc file names (`docs/DESIGN-2-GODS-VOICE.md`, `docs/GODSV-COMPLETE-OVERVIEW.md`) | kept — file names are not user-visible product text, and renaming them would break inbound links for no visible gain. Their **titles** are renamed | this block's judgement |

---

## 9. Open cross-block seam (needs an owner decision, not a rename)

The Python block moved the package to `src/skaldir/**` (with a deprecation shim `src/godsv/__init__.py`),
but the overlay's copy sources are still the old paths, so the anchor gate and three tests are red
for a reason that has nothing to do with names:

- `app-overlay/patch_app.py` `FILES` and `app-overlay/patch_app_ui.py` `UI_FILES` still list
  `src/godsv/assets/logo-mark.png`, `src/godsv/assets/icon.png` and the 7 font files +
  `MANIFEST.json` / `OFL.txt`. Measured: **8 of those 12 source paths are missing on disk**;
  `tools/check_ui_overlay_anchors.py` therefore exits 1 (“8 anchor/file problem(s) — the build
  would refuse this overlay”) although **all 55 anchors still hit exactly once**.
- This is overlay *anchor code* (the task's hard rule: do not touch it), so it is left untouched
  and reported instead.
- Two valid fixes, both cheap: (a) rewrite those entries to `src/skaldir/assets/**`, or
  (b) keep a `src/godsv/assets → ../skaldir/assets` symlink next to the deprecation shim while the
  compatibility shim exists. Until then `desktop build-app` would refuse the overlay.
