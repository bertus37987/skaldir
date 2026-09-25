# CLI visual audit — Skaldir (`godsvoice`)

**What this is.** A look at the actual CLI surface, not a description of one: real
terminal captures at fixed sizes, plus real desktop screenshots taken through Computer
Use. Every claim below is either a byte count, a column number or a quoted line from a
file in `outputs/cli-visual/`.

**Who asked.** The owner: *"das CLI per computer use oder andere mcps anschauen"*. Computer
Use **was available** — `computer-use-linux` reported ready (GNOME Wayland, GNOME Shell
50.0, `gnome-shell-extension` window backend, AT-SPI up). Three of the four screenshots
below are real screen captures of a real ptyxis window, not text renders.

**Date of every measurement here:** 2026-09-18, 19:25–19:40 CEST.
**Measured against:** `/home/willy/gv-host-proof/wired` (see *Snapshot* below).

---

## 0. Snapshot discipline — read this before quoting any number

The tree changed underneath this audit, twice, while it ran:

| Time | What happened | Effect |
|---|---|---|
| 19:24 | first captures taken against the **working tree** (`src/godsv/`, 28 root / 77 leaf commands) | those captures are real but no longer reproducible |
| 19:26 | a sibling wave **renamed `src/godsv/` → `src/skaldir/`**, imports not yet updated | `python -m godsv` → *No module named godsv.__main__*; `python -m skaldir` → *ImportError: cannot import name 'buildflow' from 'godsv'*. **The product entry point is broken at this moment.** |
| 19:31 | `README.md` finished its English sweep (0 umlauts; it had German paragraphs at 19:24) | an earlier "README.md is still German" finding is **void** — do not repeat it |

Everything in this document was therefore re-measured against a **pinned copy**:
`/home/willy/gv-host-proof/wired` — the newest complete pre-rename copy of the working
tree (113 modules, `cli.py` mtime 19:06), read-only, used only as `PYTHONPATH`. It is a
sibling block's scratch copy, not the repo. It self-reports `godsvoice_version
"0.0.0+source"` because it is not an installed distribution.

**Consequence for any claim:** 29 root / 84 leaf is *that snapshot's* number. The repo's
own docs say 28 / 73. Neither is "the" number while the tree moves. See
`MVP/27-claims-audit.md` §3.

---

## 1. How to reproduce

```bash
# every capture in this document (12 surfaces, .ansi + .txt + .meta.json + .png)
python3 tools/gv_cli_visual_audit.py \
  --src <a pinned tree>/src \
  --python /home/willy/projects/gods-voice/.venv/bin/python \
  --cwd  /home/willy/gv-cli-visual-scratch/project \
  --out  outputs/cli-visual

# column-alignment check over the captured text
python3 tools/gv_cli_visual_audit.py --align outputs/cli-visual/palette-100x40.txt
```

The harness runs the CLI **inside a real pty** of an exact size (`TIOCSWINSZ`), so
TTY-only code paths (colour, the logo, argparse's wrapping) are exercised for real.
`script(1)` is not installed on this machine — a Python pty is the substitute, exactly as
`references/supervise-cli.md` records.

**Two traps this harness hit, both worth keeping:**

1. **`Path(argv).resolve()` on the venv interpreter loses the venv.** `.venv/bin/python`
   is a symlink to the uv-managed CPython; resolving it makes the child run as *bare*
   CPython, so `import yaml` dies with `ModuleNotFoundError`. Use `.absolute()`. This cost
   one debugging cycle and is now a comment in the tool.
2. **`git archive HEAD` is not the working tree.** HEAD (`eff149d`) has **no `contract`,
   `selftest`, `jev` or `repl` group at all** — those are uncommitted. A "pinned snapshot"
   taken from HEAD silently measures a different product than the one being reviewed.

**Honest limitation:** the interactive shell's `?` overlay and live tab-completion are
driven by readline key handling; fed keystrokes reach the pty but the overlay did not
render in the capture (`shell-overlay` exits `-15`, killed by the harness timeout). The
`/` palette is therefore captured **deterministically** through `slash.render()` over the
real parser tree and the real selftest report — the same string the TUI draws — and that
is labelled as a render, not a screen photograph.

---

## 2. The captured surfaces

### 2.1 `desktop/01-banner-window.png` — the bare call (real screenshot)

732×537 px ptyxis window titled `GV-CLI-AUDIT-WINDOW`. Content is centred in the upper
third: a **block-glyph logo** built from `▄`/`▀`, roughly 46 columns wide, reading as a
**U / horseshoe / upward-opening arc with a notch at the bottom centre** — at this size it
does *not* obviously read as the Skaldir ear/face from the source PNG. Colour is a
**full 24-bit warm gradient**: bright amber/orange in the middle of the strokes, darker
orange-red at the edges, dithered at the rim — the measured SGR set contains **hundreds of
distinct hued `38;2;R;G;B` / `48;2;R;G;B` codes** (`38;2;255;140;8`, `48;2;248;117;13`,
`48;2;255;222;50`, …). Under it, in orange with wide letter-spacing: `G O D ' S   V O I C E`.

Then, left-aligned on a character grid with two aligned columns (command / description):

```
  Skaldir Agent  v1.5.0 · self-learning agent harness · Hermes
  Runtime /home/willy/.hermes/godsv · directory /home/willy/gv-cli-visual-scratch
  h/project
```

**The Runtime line clips at the right edge and wraps mid-path** (`…/gv-cli-visual-scratch`
then `h/project` on the next line, no ellipsis, no shortening). The section headers
`Get started` / `Explore` are bold white (the vision pass read them as "blue/teal" — the
bytes say bold only; trust the bytes). The logo *does* read at this size; the caption does
not match the window title, which says `Skaldir Agent`.

### 2.2 `desktop/02-contract-list-window.png` — `contract list` (real screenshot)

Scrolled to the tail. **Monochrome, no colour at all** (`sgr_runs = 0`). Four implicit,
unlabelled columns, left-aligned, space-separated:

```
  COMPOSE    skills.export              json   write
  COMPOSE    skills.list                json   read
  COMPOSE    skills.mine                json   write
```

No header row, no rule, no legend. `json` and `read`/`write` are bare tokens — nothing on
screen says that `json` means "has `--json`" or that `write` mutates state. Column 1 mixes
two vocabularies: seven of the eight layer names plus `kernel` (not a layer). Column 2 is
padded to a fixed 22 chars, which holds at 80 columns (`max_line_width = 51`,
`overflow = 0` at both 80 and 100). The list ends with a bare count line, `84 command(s)`.

### 2.3 `desktop/03-error-window.png` — an error + exit code (real screenshot)

`python -m godsv frobnicate`, exit **1**. Monochrome. It is argparse's own output:
a `usage:` block, then `godsvoice: error: argument command: invalid choice: 'frobnicate'
(choose from …)` followed by the full 29-name choices list. At 80 columns the machine-
generated list **wraps inside command names** — the capture shows `choose from je` / `v,
init, pact-show, …` and `memory` / `-search, …`. Measured: **`max_line_width = 353`, 2
lines over 80**. Nothing suggests a near miss, there is no "did you mean", and no hint
line — for a CLI that prints honest hint lines everywhere else, this path is raw.

The machine path is much better: `tools show nope --json` exits **5** with the human line
on stderr and exactly one JSON envelope on stdout, including `"error": {"code":
"not_found", "hint": "`godsvoice tools list` shows the specs of the resolved runtime db",
"retryable": false}`. The 547-character single line is *correct* there (machine output),
not an overflow defect.

### 2.4 `desktop/04-shell-overlay-window.png` — the interactive shell (real screenshot)

Header block, honest and well-labelled, three indent levels (0/2/4):

```
  Skaldir v1.5.0 — interactive shell (godsvoice shell)
  runtime  ~/.hermes/godsv   · registry.default_runtime_dir
  provider none configured   · no config at ~/.hermes/godsv/config.yaml
  goal     none   · no run store at ~/.hermes/godsv/runs.db
ready: /help /status /quit /tools
flagged: /jev (ROUTE flagged by selftest)
not wired here: /provider /goal /runs /verify /act /compose
selftest: 8 layer(s), 5 not reachable
```

**And then the prompt is visibly broken.** Every other line in this surface carries real
`ESC[` bytes; the prompt line does not:

```
[38;2;255;255;255;1m❯[0m [2m~/.hermes/godsv[0m · [2mgoal:none[0m · [2mnone configured[0m /
```

Byte-verified: `grep -c ESC` on the capture gives 28 escapes in the header and **zero
before the prompt's `[38;2;255;255;255;1m`** — the SGR text is printed as literal
characters. The screen capture independently shows the same literal garbage. The code path
is `repl.py:783-787` (`prompt()`), whose fallback painter at `repl.py:236-237` *does*
emit `\033[`, so the prompt is built somewhere that drops the ESC — and it asks for the
role `"cyan"` (`repl.py:787`), a hue, in a theme whose own style guide forbids one.
Both the header and the prompt use `38;2;255;255;255` — **pure white, not a grey-ramp
token**.

Also visible: the shell calls itself `godsvoice shell`, and `godsvoice shell` **does not
exist** (`exit 1`). The header is the only part of the shell reachable from a user's
perspective.

### 2.5 `palette-100x40.txt` — the `/` palette (rendered, not photographed)

Four sections (`commands`, `session`, `runtime state`, `the eight layers`), three indent
levels (2/4/6), a name column, a description, and a right-edge state marker. Content and
honesty are genuinely good — every row is marked from real state:

```
  the eight layers
    /verify     recorded verification runs, verdict and freshness not wired here
    /jev        the Jev façade's own selftest                selftest: ROUTE not reachable
      /jev [kinds|packs|history|decide]  →  godsvoice jev selftest
      why: probe failed
    /tools      the tool registry: specs, builds, health     ready
```

**The state column is ragged**, measured: marker columns `[68, 65, 62, 62, 68, 68, 67, 68,
62, 68]` → **spread 6**. Descriptions are truncated with `…` at inconsistent widths and
then followed by a single space, not padded to a column. Three different state
vocabularies share the column (`ready` / `not wired here` / `selftest: ROUTE not
reachable`). Rows marked *not wired here* still print a target command that does not exist
(`→ godsvoice verify status` → `exit 1`; `→ godsvoice provider list` → `exit 1`).

### 2.6 `help-80x24.txt`, `selftest-100x30.txt`, `plain-pipe.txt`, `banner-80x24.txt`

| Surface | lines | max width | lines > 80 | SGR runs | pure ASCII |
|---|---|---|---|---|---|
| `banner-80x24` | 50 | 90 | 1 (the Runtime line) | **1501** | no |
| `help-80x24` | 83 | **265** | 5 | 0 | no (`—`, `·`) |
| `contract-list-80x24` | 86 | 51 | 0 | 0 | **yes** |
| `error-unknown-command` | 6 | 353 | 2 | 0 | yes |
| `error-not-found-json` | 3 | 547 | 1 (intended) | 0 | yes |
| `selftest-100x30` | 32 | 161 | 11 | 0 | no (`✓✗·—`) |
| `plain-pipe` (stdout = pipe) | 86 | 50 | 0 | 0 | yes |

`selftest`'s content is the best thing in the CLI (per-probe verdicts, honest
`blocked_reason`s, a closing `8 layer(s), 22 probe(s), 5 not reachable · 40ms`), but its
prose reasons are up to **161 chars** at a 100-column terminal → 11 lines wrap. `help`'s
`{a,b,c,…}` choices blob is a **single 265-char line**.

### 2.7 The "plain mode" contract does not hold for the banner

`docs/CLI-STYLE.md:29-32` promises: *"`plain` — **no escape sequence at all** and **ASCII
only** … This is the rule the test suite enforces across every helper"*, and *"there is no
path from a hue into the output"*. Measured on the bare call:

| Invocation | bytes | CSI escapes | non-ASCII codepoints |
|---|---|---|---|
| TTY, default | 21 699 | 1501 | 552 (`▀ ▄ · →`) |
| TTY, `NO_COLOR=1` | 20 370 | **1461** | 552 |
| TTY, `--no-color` | 20 370 | **1461** | 552 |
| stdout redirected to a file | 20 370 | **1461** | 552 |

So the guarantee holds for the `render.py` helpers (which are what the test enforces) but
**not for the banner path**: `cli.py:2988-3001` (`_logo_text` / `_print_banner`) reads
`assets/logo.ansi` and `print()`s it verbatim, and `_print_welcome` prints it the same way
— the pre-baked art bypasses `theme.detect()` entirely. Piping `godsvoice` to a file, or
setting `NO_COLOR`, does not change the bytes.

### 2.8 Head-to-head at 80×24, same harness, same machine

| CLI | `--help` lines | max width | lines > 80 | SGR runs |
|---|---|---|---|---|
| **Skaldir** `python -m godsv --help` | 83 | **265** | **5** | 0 |
| **Cline** `cline --help` | 67 | **80** | **0** | 0 |
| **Hermes** `hermes --help` | 224 | 94 | 8 | 0 |

(Captures: `outputs/cli-visual/compare-cline-help-80x24.txt`, `compare-hermes-help-80x24.txt`.)

Cline never exceeds the terminal width — every long line is re-wrapped to fit. Skaldir and
Hermes both overflow; Skaldir's worst line is 3.3× the terminal width. None of the three
colours `--help`, so "uncoloured help" is table stakes and not a Skaldir-specific defect.
Hermes's breadth (224 lines) is the scale Skaldir is aiming at and is at 83 today.

---

## 3. Verdict on visual quality

**It does not yet look like a polished competitor to Hermes or Cline.** It looks like a
CLI with one finished surface, one finished invention, and a lot of argparse.

The honest breakdown:

* **The banner is good.** Generated art, a caption, a two-column start block, a hint line
  in the owner's voice. It reads at 80×24. This is the surface a screenshot would sell.
* **The honesty layer is better than either competitor.** `selftest` printing per-probe
  verdicts and `blocked_reason`s, and the `/` palette marking every row `ready` / `not
  wired here` / `selftest: …`, is a real differentiator — neither Cline nor Hermes tells
  you which of its own subsystems is not wired. Keep this; it is the product's best idea.
* **Almost everything else is unfinished.** The prompt is broken, the tables have no
  headers, help blows past the terminal width, and there are three mutually inconsistent
  colour policies in one binary.

### The three biggest visual weaknesses

1. **Three colour policies in one product, and the "monochrome" promise is false for the
   loudest surface.** The banner is a full-colour 24-bit orange gradient (hundreds of
   distinct hues, 1501 SGR runs); `help`, `contract list` and `selftest` are completely
   uncoloured (0 SGR runs); the shell prompt is hardcoded pure white (`38;2;255;255;255`).
   `--no-color`, `NO_COLOR=1` and a pipe to a file all leave the banner's 1461 escapes
   untouched, contradicting `docs/CLI-STYLE.md:29-32`. *Fix: route the art through
   `theme.detect()` — keep the hue art only when the theme says `rich`/truecolor, and
   hand `plain` a pre-baked ASCII/1-bit variant.*

2. **The shell prompt prints raw escape fragments.** `[38;2;255;255;255;1m❯[0m
   [2m~/.hermes/godsv[0m …` is what the user sees, on the line they look at most
   (`repl.py:783-787`; byte-verified). This is the single most damaging defect because it
   is the first thing a user types into. *Fix: the same ESC-dropping path, plus a test
   that asserts the prompt string contains a real `\033`.*

3. **No layout discipline: nothing is measured against the terminal width, and no table
   has a header.** `help` emits a 265-char line and wraps *inside* command names at 80
   columns; `selftest` reasons run to 161 chars; the welcome line clips mid-path
   (`.../gv-cli-visual-scratch` + `h/project`); `contract list` prints four unlabelled
   columns; the `/` palette's state column is ragged across 6 columns. Cline, on the same
   terminal, overflows zero lines. *Fix: one width-aware layout helper
   (`GODSV_WIDTH` already exists as an escape hatch) used by every human surface, plus
   header rows for `contract list` and a padded state column for the palette.*

**Runners-up, worth one line each:** the brand splits three ways in one screen
(`GOD'S VOICE` caption vs `Skaldir Agent` title vs the `godsvoice` binary — the caption is
the pre-rename name); the palette offers "→ commands" that do not exist; and the shell's
own header advertises `godsvoice shell`, a command that exits 1.

---

## 4. Artifact index

```
outputs/cli-visual/
  desktop/01-banner-window.png        real screen capture (Computer Use, GNOME Wayland)
  desktop/02-contract-list-window.png real screen capture
  desktop/03-error-window.png         real screen capture
  desktop/04-shell-overlay-window.png real screen capture (shows the broken prompt)
  banner-80x24 / banner-100x30        .ansi + .txt + .meta.json + .png
  help-80x24                          "
  contract-list-80x24 / -100x30       "
  error-unknown-command               "
  error-not-found-json                "
  selftest-100x30                     "
  plain-pipe                          "
  shell-palette / shell-overlay        pty keystroke feed
  palette-100x40                      slash.render() over the real tree + real selftest
  compare-cline-help-80x24.{txt,ansi} head-to-head
  compare-hermes-help-80x24.{txt,ansi}
  summary.json                        every scenario's exit code + diagnostics
```

`.png` files under `outputs/cli-visual/` are ImageMagick **renders of the captured text**,
not photographs — they are labelled that way in each `.meta.json` (`png_note`). Only the
four files in `desktop/` are real screenshots.

**Note on `docs/CLI-REFERENCE.md`:** it was not regenerated for this audit (another wave's
file). Its counts are stale — see `MVP/27-claims-audit.md` §3.
