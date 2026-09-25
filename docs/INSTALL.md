# Install

Every command in this file was executed on 2026-09-18 (Fedora 44, `uv 0.11.29`, Python 3.12.13,
`skaldir 1.5.0`), each install into an isolated prefix so no real tool directory was touched. Steps
that could not be executed here are marked **NOT VERIFIED**.

Names, as of the rename that landed in this tree:

| | Value |
|---|---|
| Distribution (what you install) | `skaldir-agent` |
| Command (what you run) | `skaldir` — plus `godsvoice`, the same entry point under the deprecated name |
| Python package | `skaldir` (`src/skaldir/`); `src/godsv/` is a deprecation alias shim |
| Runtime directory | `~/.hermes/skaldir`, falling back to the pre-rename `~/.hermes/godsv` |

---

## Requirements

| | Needed for | Notes |
|---|---|---|
| **Python ≥ 3.11** | everything | verified on 3.12.13 |
| **`uv`** (or `pipx`, or plain `pip`) | installing the CLI | `uv` recommended |
| **Hermes Agent** | the desktop app, the MCP seam, the plugin that feeds SUPERVISE | optional for the plain CLI |
| **Docker** | the container half of EXPAND: mine a capability from an image, register it, health, rollback | see below |
| **bubblewrap (`bwrap`)** | computer-use sandbox isolation | |
| **podman** (rootless) | computer-use container isolation | |
| **ImageMagick (`convert`)** | generating the icon ladder for the desktop entry | |

`skaldir doctor` checks ten of these and prints a ✓/✗ line each:

```console
$ skaldir doctor
✓ Python ≥ 3.11 — 3.12.13
✓ godsv package — 1.5.0
✓ Prompts (13 required) — 13 loaded
✓ Hermes CLI in PATH — /home/willy/.local/bin/hermes
✓ Plugin deployed — /home/willy/.hermes/plugins/godsv
✓ Desktop entry
✓ Runtime — /home/willy/.hermes/godsv
✓ bwrap — /usr/bin/bwrap
✓ podman — /usr/bin/podman
✓ convert — /usr/bin/convert

Deployment OK.
```

Exit `2` = problems found, exit `7` = the check itself could not run (usually the prompts path —
see below).

> **Known gap:** on a fresh `skaldir-agent` install `doctor` prints
> `✗ godsv package — not installed` and exits 2. The check still looks up the pre-rename
> distribution name. Your install is fine; the line is wrong.

### Docker is a dependency — a declared one, not an assumed one

Docker is needed only for the **container** half of EXPAND. It is deliberately **not** in `doctor`'s
list, because the rest of the tool works without it. What the code does instead is report the
missing dependency honestly:

```console
$ skaldir tools mine --image busybox:1.36 --capability "list files in a directory" --json
  "staged": false,
  "failure_class": "docker_error",
  "reason": "the surface cannot be inspected: the probe(s) cli did not run in the image — no adapter
             is generated from an uninspected surface"
  "error": {"code": "blocked", …}
```

`docker_runtime.py` returns `CmdResult(127, "", "docker binary not found: …")` when the binary is
absent — never an exception, never a green tick. Docker 29.6.1 on this machine works **without
sudo** (the user is in the `docker` group); `docker compose` is the v5.3.1 plugin.

`skaldir cu check` reports which computer-use isolations are actually constructible:

```console
dry-run: available
sandbox (bwrap): available
container (podman): available
container GUI (godsv-cu-x11): available
```

---

## Install the CLI

### uv (verified)

```bash
uv tool install --from /path/to/gods-voice skaldir-agent
skaldir --version                       # skaldir 1.5.0
godsvoice --version                     # skaldir 1.5.0 — deprecated alias, same entry point
```

Install the **distribution** name, then run the **script** name. The other spelling fails on purpose:

```console
$ uv tool install --from . skaldir
error: Package name (`skaldir-agent`) provided with `--from` does not match install request (`skaldir`)
```

### pipx (**NOT VERIFIED** — pipx is not installed on this machine)

```bash
pipx install /path/to/gods-voice
```

### From source, into your own venv (verified)

```bash
python3 -m venv .venv
.venv/bin/pip install /path/to/gods-voice
.venv/bin/skaldir --version             # skaldir 1.5.0
```

### The repo's own installer

**Currently broken** — verified: `scripts/install-cli.sh` still installs the pre-rename
distribution name, so it fails immediately with the `--from` mismatch shown above (exit 2). Until it
is updated, use one of the three commands above. The script's intended interface is:

```bash
scripts/install-cli.sh install     # reinstall | uninstall | status | verify
```

To install into an isolated prefix instead of your tool dir — this is how every install above was
proven:

```bash
UV_TOOL_DIR=/tmp/proof/tool UV_TOOL_BIN_DIR=/tmp/proof/bin uv tool install --from /path/to/gods-voice skaldir-agent
```

### From a git URL (**NOT VERIFIED as usable**)

```bash
uv tool install --from git+https://github.com/bertus37987/gods-voice.git skaldir-agent
```

This installs, but resolves `main`, which at the time of writing is behind this tree (`godsv 1.4.0`),
and that build is dead on arrival: `--version` exits with `ModuleNotFoundError: No module named
'cryptography'` before argparse runs. `cryptography` is a declared runtime dependency from 1.5.0 on.
Verify `skaldir --version` after installing from a URL, and prefer a 1.5.0 checkout.

---

## After installing: point the prompts at the wheel

The prompts (`prompts/*.md`, 16 files, 13 required ids) are *data*; `skaldir.prompts.default_root()`
resolves `$SKALDIR_PROMPTS`, else `<repo>/prompts`. On a wheel install they ship **inside** the
package (`site-packages/skaldir/prompts`), which that resolver does not find — so one env var has to
point at them, or `doctor`, `prompts list` and everything Voice-shaped fails with
`prompts directory not found` and exit `7`:

```bash
export SKALDIR_PROMPTS="$(find "${UV_TOOL_DIR:-$HOME/.local/share/uv/tools}/skaldir-agent" -maxdepth 7 -type d -name prompts | head -1)"
skaldir prompts list                    # 16 prompt files listed; "MISSING" must not appear
```

Verified both ways: without the variable, `FileNotFoundError: prompts directory not found:
<…>/lib/python3.12/prompts` and exit 7; with it, 13/13 required prompts load and `doctor` turns
green.

---

## The runtime directory

All state lives in one directory, resolved by `src/skaldir/registry.py` (first match wins):

1. an explicit `--runtime` argument;
2. an explicit `--db` argument → runtime = that DB's directory;
3. `$SKALDIR_HOME`, fallback `$GODSV_HOME`;
4. `$HERMES_HOME/skaldir`, fallback `~/.hermes/skaldir`.

Ask what it picked and **why** — this is the single most useful diagnostic command:

```console
$ skaldir registry status --json
"runtime_dir":      "/home/willy/.hermes/godsv",
"selection_reason": "runtime default (~/.hermes/skaldir) (godsv: pre-rename directory, still in use)",
"tools_db":         "/home/willy/.hermes/godsv/tools.db"
```

The contents of that directory are listed in `ARCHITECTURE.md` → *Where state lives*.

### Migration from the pre-rename layout

The rename moved the runtime directory name from `godsv` to `skaldir`. The behaviour is deliberately
forgiving: if `~/.hermes/godsv` exists and `~/.hermes/skaldir` does **not**, the old directory keeps
being used and the selection reason says so. Nothing is copied and nothing is deleted. To move, do it
explicitly — set the variable, confirm the CLI sees the new path, then move the directory yourself:

```bash
export SKALDIR_HOME="$HOME/.hermes/skaldir"
skaldir registry status                 # confirm runtime_dir / selection_reason
# only then:  mv ~/.hermes/godsv ~/.hermes/skaldir
```

There is also a **tool-DB** migration, separate and explicit: `skaldir registry migrate`
(dry-run by default) merges legacy tool databases. It backs up source and target before any write,
surfaces name collisions instead of overwriting, journals newer legacy records to
`registry_migration.jsonl`, and never modifies or deletes the source database. A legacy
cwd-relative `.godsv/tools.db` is only ever *reported* by `registry status`, never read for
selection.

---

## Troubleshooting

### `doctor` / `prompts list` fail with `FileNotFoundError` (exit 7)

`prompts directory not found: <…>/prompts`. You are running a wheel install without
`SKALDIR_PROMPTS`. See *After installing* above.

### The CLI reports missing databases that clearly exist

A stray `HERMES_HOME`, `SKALDIR_HOME` or `GODSV_HOME` in your shell. A test, a container or a
parallel session left one behind, so the CLI is measuring a throwaway runtime and truthfully
reporting an empty one. Always pin it:

```bash
env -u PYTHONPATH -u HERMES_HOME -u SKALDIR_HOME -u GODSV_HOME skaldir registry status
```

### The installed CLI runs foreign code

`PYTHONPATH` pointing at a *sibling* checkout beats the installed package, so edits "have no effect"
and a fix looks broken (or alive) in the wrong tree. Confirm which tree you are measuring:

```bash
env -u PYTHONPATH python -c "import skaldir; print(skaldir.__file__)"
```

### `ModuleNotFoundError: No module named 'skaldir'` right after a successful install

You installed a checkout from before the packaging rename (the wheel contained only the `godsv`
alias shim). Reinstall from a current checkout; `skaldir --version` is the check.

### `uv tool install` warns "Failed to hardlink files"

Harmless: the tool dir and the uv cache are on different filesystems. Set `UV_LINK_MODE=copy` to
silence it. It is not the cause of an install failure.

### A full test run dies with `OSError(122, 'Disk quota exceeded')`

`/tmp` is a quota-limited tmpfs on this machine; parallel runs exhaust it and pytest aborts with an
`INTERNALERROR` and a storm of `E`s anywhere in the run. Point `TMPDIR` at a real disk:

```bash
TMPDIR=/home/willy/gv-tmp-verify .venv/bin/python -m pytest tests -q -o addopts=""
```

### `selftest` exits 2

By design. Exit 2 means at least one of the eight layers is honestly not reachable, and each blocked
layer names its reason. Read `layers[].blocked_reason` in `skaldir selftest --json`; `ARCHITECTURE.md`
lists which are blocked today and why.

### Icons / desktop entry missing

`convert` (ImageMagick) generates the icon ladder. `skaldir doctor` lists it;
`skaldir desktop install` creates the entry and icons user-locally. Nothing here needs sudo.
