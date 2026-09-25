# CLI style guide — Skaldir (`skaldir` / `python -m skaldir`)

The CLI is **monochrome on purpose**: one ink, one paper, a grey ramp between them, and
typographic weight for meaning. The owner's words: *"polished, black and white with a bit
of art"*. A colour palette is not a style — so there is none. What carries meaning is
**structure** (bars, tables, indentation), **marks** (✓ ✗ ■ ·) and **weight** (bold, dim,
reverse), and those survive on any terminal, in any log file, in any diff.

The look is **Claude-Code-like restraint**: a compact single-line header, bars instead of
frames, indentation instead of boxes, aligned columns, one blank line between sections and
nothing decorative. Readable at **80 columns** — that is the width the CLI is written for
and the width a pipe is assumed to have.

Modules: `src/skaldir/theme.py` (the decision + the palette), `src/skaldir/render.py` (the
layout helpers), `src/skaldir/logo.py` (the art). See them live: `skaldir style preview`,
`skaldir style palette`, `skaldir style logo mark`, `python -m skaldir.logo show welcome`.

## 1. Colour is a property of the terminal, never of the message

`theme.detect(stream, no_color=..., env=...)` decides, in this order:

| # | Condition | Result |
|---|---|---|
| 1 | explicit `mode=` argument (tests, opt-ins) | that mode |
| 2 | `--no-color` | `plain` |
| 3 | `NO_COLOR` set and non-empty | `plain` |
| 4 | `SKALDIR_COLOR`/`FORCE_COLOR` truthy | `rich` |
| 5 | `stdout` is not a TTY | `plain` |
| 6 | otherwise | `rich` |

Two modes, and they are the whole vocabulary:

* **`rich`** — ANSI greys + weight, unicode glyphs (`✓ ✗ ■ · ─ │ ┌ ┐ █ ░ • › …`).
* **`plain`** — **no escape sequence at all** and **ASCII only**
  (`+ x # . - | + # - - > ...`). Not "colour off, glyphs on": a redirect, a log, a pager
  or a diff gets bytes that are stable, greppable and readable in any font.

Depth inside `rich` comes from `TERM`/`COLORTERM`: `truecolor` (`38;2;l;l;l`) → `256`
(`38;5;N`, xterm greys 232-255) → `basic` (weight only: bold = bright, dim = dark). The
ramp is *only ever greys* — `Theme.grey(level)` is the one function that turns a level
into an SGR; every SGR the CLI emits has three equal channels or none.

**The ASCII promise covers borrowed text too.** `render.truncate()` and `render.wrap()`
fold the CLI's own typography on a plain theme (`—`→`-`, `…`→`...`, `→`→`->`, `·`→`-`,
curly quotes → straight) before measuring, so a probe's reason or a stored detail cannot
break it. Unknown characters (a CJK path, say) are left alone: mangling data is worse
than one wide character in a log.

Escape hatches for humans who disagree with the detection: `SKALDIR_COLOR=1` (force on),
`SKALDIR_UNICODE=1|0` (glyphs only), `SKALDIR_WIDTH=n` / `SKALDIR_HEIGHT=n` (layout).

Size is part of the decision: `render.terminal_width()` / `render.terminal_height()` honour
`$SKALDIR_WIDTH`/`$COLUMNS` and `$SKALDIR_HEIGHT`/`$LINES` first, then the console. **A pipe
with nothing declared is 80x24**, which is why

```
skaldir ... --no-color        # on a 80x24 terminal
skaldir ... | cat             # piped, nothing declared
```

produce the *same bytes* (measured: `sha256 61c86a37fba492d2…`, 600 B for both).

## 2. Bars are the separators

```
─── COMMANDS ──────────────────────────────────────────────────────────────────
layer   │ command                │ format │ mode
────────┼────────────────────────┼────────┼──────
EXPAND  │ tools.list             │ json   │ read
················································································
  77 command(s)
```

* **`render.bar(label)`** opens a section: three strokes, the label, then fill to the
  screen width — *exactly* that width, never wrapped, never ragged. The label is
  **upper-cased by the helper**: a bar is the only ALL-CAPS text in the CLI (the logo
  caption aside), which is what makes it scannable. Two bars in one output line up.
* **`render.end_rule()`** closes a section: the dotted light rule (`·······` / `.......`).
  It is used **before a summary or footer line**, so that line reads as the end of the
  section instead of as one more row of it — never as a substitute for a blank line.
* **`render.spacer()`** is the blank line between sections. **One, never two.**

One bar per multi-section output. A single-value command prints no bar at all.

## 3. Hierarchy is three signals, and nothing else

| Signal | Drawn as | Meaning |
|---|---|---|
| the bar | `─── LAYERS ────` | a section starts here |
| the mark rhythm | `✓␣␣text` | one state per line (mark + **two spaces** + text) |
| the depth cue | 4-space indent + dim text | a detail under the line it belongs to |

Nothing else changes the look. No box around a block, no frame around a list, no second
level of rules. A long text **wraps** with a hanging indent (`render.wrap`, used
automatically by `status_line(..., columns=…)`) rather than being truncated, because the
*reason* a layer is not reachable is the most valuable thing on the screen; only table
cells and bar labels truncate (with `…`/`...`, never silently).

## 4. Tokens — say *what* it is, not how it looks

Handlers never write escapes. They name a token; the theme decides the drawing:

| token | meaning | rich | plain |
|---|---|---|---|
| `heading` | a section title, a bar label | bold + white | plain |
| `strong` | emphasis inside a line | bold | plain |
| `ok` | succeeded | ✓ + bold white | `+` |
| `fail` | failed | ✗ + **reverse video** | `x` |
| `blocked` | could not be done | ■ + dim | `#` |
| `warn` | attention, not an error | · + light grey | `.` |
| `muted` | secondary text, details | dim | plain |
| `key` | the left half of a pair | dim + light grey | plain |
| `rule` | bars, rules, table borders | dim dark grey | plain |
| `accent` | the product name, the mark | bold + white | plain |

Reverse video for `fail` is the one trick monochrome has for shouting: a black-on-white
chip is unmistakable and costs no colour.

## 5. The vocabulary of helpers (`skaldir.render`)

* **`bar(label[, columns])`** — the section separator (§2).
* **`end_rule([columns])`** — the light rule that closes a section.
* **`rule(columns[, label, weight])`** — the low-level rule: `heavy` (default, the
  `─── label ───` form) or `light` (the dotted one). Exact width in every case.
* **`heading(text[, level])`** — level 1 = title + full rule; level 2 = `bar(text)`.
* **`status_line(status, text[, indent, muted, columns])`** — `✓␣␣text`; `columns` wraps.
* **`step_line(i, n, text[, status])`** — `  2/5    ✓  build`.
* **`progress(fraction[, columns, label])`** — `[████░░░░]  62%` (`[####----]`).
* **`key_values([(k, v), …][, title])`** — one record, aligned keys, two-space gap. Use it
  instead of prose for state (`workspace`, `runtime`, `verdict`).
* **`table(headers, rows[, max_width, aligns])`** — padded columns, a header rule, never a
  silent wrap: columns shrink (widest first) until the table fits, and a cut cell is
  marked. A cell whose text is exactly a token or status name (`ok`, `blocked`) is drawn
  in that token.
* **`legend([statuses])`** — `✓ ok  ✗ fail  ■ blocked  · warn`. **Reference surfaces
  only** (`style preview`, `style palette`, a command that introduces a mark). On a
  command's normal output it is noise.
* **`bullets(items)`** — `• one` (`- one`). **`box(lines, title=…)`** — only for a note
  that must not be missed, never around a whole output.
* **`truncate(text, limit)`** / **`fit`** / **`wrap`** / **`width`** — text maths. Never
  `len()` a styled string: `width()` counts wide glyphs as two and ignores escapes.

## 6. The art

`assets/cli-logo.txt` (repo root; `logo.art_candidates()` also looks next to the package)
is the owner's logo (`Skaldir_Logo.png`, read-only source, ink box `282,213-1039,1025`)
converted **once** into a grid of hex ink levels — no image library at run time, no Pillow:

* **banner** — 46 columns x 25 lines: the full startup band.
* **mark** — 16 columns x 9 lines: the compact mark.
* **caption** — the wordmark: `S K A L D I R` (`full`) / `SKALDIR` (`short`).

Both grids are drawn with the ASCII shade ramp `" .:-=+*#%@"` — **one pixel row per
terminal line**, aspect-corrected for the ~2:1 character cell (`rows = columns * h / w /
2`). In `rich` mode each run of equal ink gets a grey foreground; in `plain` mode the very
same characters are printed with no escapes, so the art degrades to ASCII instead of to
something else. The compact mark keeps the ramp on purpose: posterizing it turned the
emblem into a blob at small sizes (measured 2026-09-18).

**The art adapts to the screen, the words never do** (`logo.art_for_screen`,
`logo.splash`, `logo.welcome`):

| Terminal rows | Startup art | Hint sections |
|---|---|---|
| ≥ 42 | banner (46x25) | both (`Get started`, `Explore`) |
| 34–41 | banner (46x25) | one |
| < 34 (an 80x24 screen, and every pipe) | mark (16x9) | one |

So the startup screen always fits the screen it is printed on: 20 lines at 80x24, 36 at
40 rows, 42 at 50 rows — measured. `logo.header(title, subtitle=…)` is the one-line
alternative (`SKALDIR · verify`) for heads that must not be tall.

The pre-rebrand gold/red art `src/skaldir/assets/logo.ansi` ("GOD'S VOICE") is **not part
of this language and is never printed** — no code path reads it any more.

Regenerate (only when the logo changes): `python -m skaldir.logo regenerate` (stdlib only,
~1 s); `python -m skaldir.logo info` shows what is checked in; `python -m skaldir.logo
show welcome` prints the startup screen without starting the CLI. The generator is
deterministic: `tests/test_logo_art.py` regenerates and compares byte-for-byte with the
checked-in file.

## 7. Writing a new command

1. `theme.for_args(args)` once, at the top of the handler — it honours `--no-color`, then
   the stream. Every helper defaults to that process theme.
2. Structure with `render.bar` / `render.end_rule` / `render.spacer`; one bar per
   multi-section output.
3. `render.*` and `theme.style(text, token)` / `theme.mark(status)` for fragments. No
   literal escapes anywhere, and no literal `·`/`—`/`→` in a string either: take them from
   `theme.glyph("sep" | "arrow" | …)` so plain mode stays ASCII.
4. English only, no emoji, no rainbow. If something needs to stand out and there is no
   token for it, add a token — do not add a colour.
5. Human text goes to **stdout** in text mode; the product contract
   (`cli_contract.run_command`) moves it to stderr in `--json` mode. The handler still
   calls `set_data(...)` + `add_evidence(...)`: a success without evidence is downgraded
   to exit 7 by `build_envelope`.
6. Prove the plain path: add the new helper to
   `tests/test_theme_render.py::_every_helper` — `test_every_helper_degrades_to_pure_ascii`
   then fails on any escape or non-ASCII byte.
7. Prove it end to end: capture the command in a PTY and through a pipe (`script` is not
   installed — use a Python `pty` helper) and check the two bodies are identical with
   `--no-color`, which is the only way to know both readers see the same thing.

## 8. Hand-over: how the `style` group gets wired

`theme.register_cli(subparsers)` is the whole contract (it is idempotent, so wiring it
twice cannot raise *conflicting subparser*). In `src/skaldir/cli.py`, directly after
`sub = parser.add_subparsers(dest="command", required=False)` — the same place the `jev`
group is wired:

```python
    # CLI look & feel (theme + logo art): the group owns its verbs and the three
    # `cli_contract.COMMANDS` entries for them — see docs/CLI-STYLE.md.
    from .theme import register_cli as _register_style_cli

    _register_style_cli(sub)
```

and in `src/skaldir/cli_contract.py`, three `COMMANDS` entries (layer `kernel`, so the
dump's layer check passes; `style.logo` is `not_found` = 5 when the asset is missing):

```python
    "style.preview": _C("kernel", (_ev("src/skaldir/theme.py"), _ev("src/skaldir/render.py"),),
                        "print every helper of the CLI's visual language"),
    "style.logo": _C("kernel", (_ev("assets/cli-logo.txt", kind="file"),
                                _ev("src/skaldir/logo.py"),),
                     "print the Skaldir CLI art (banner / mark / splash)",
                     exit_codes=(0, 1, 5, 7),
                     notes="exit 5 (not_found) when assets/cli-logo.txt is missing: the CLI "
                           "never prints a fake banner"),
    "style.palette": _C("kernel", (_ev("src/skaldir/theme.py"),),
                        "grey ramp, tokens and status marks"),
```

**Status: NOT wired yet** (measured 2026-09-18: `skaldir style` exits 1 with *invalid
choice*). Until then the group is reachable only in-process —
`theme.main(["preview", "--no-color"])` — and `python -m skaldir.logo show …` is the
supported way to look at the art.

## 9. Where this is applied today (and where it is not yet)

Converted: the **startup screen** (`skaldir` with no arguments, all of it), **`contract
list`**, **`selftest`** (bar per report, two-space marks, indented dim details, light rule
before the summary), and the whole `style` group.

Not converted yet — each still prints its own layout, so a later pass should move it to
`render.*`: `doctor` (its `✓`/`·` are hard-coded, so a piped `doctor` still emits
non-ASCII bytes — measured `non-ascii=66` in a pipe), the `--help` epilog, `contract
dump` (JSON, no chrome needed), `tools-hub`, `skills list`, `metrics`. Nothing above
should be "fixed" by adding a colour: every one of them is a `render.status_line` +
`render.bar` away from the vocabulary.

## 10. Rules of thumb

* One blank line between sections (`render.spacer()`), never two.
* Lower-case sentence case for titles and notes; the bar label and the logo caption are
  the only ALL-CAPS text.
* A bar spans exactly the screen width; a table never exceeds it; prose wraps under its
  mark. Nothing ever wraps silently.
* A number in the output is either measured or absent — never estimated.
* `--json` output has no styling at all; the envelope is data, not chrome.
