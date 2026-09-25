#!/usr/bin/env bash
# Skaldir CLI — install / reinstall / uninstall as a real, runnable tool.
#
#   scripts/install-cli.sh install     # uv tool (default) -> pipx -> dedicated venv
#   scripts/install-cli.sh reinstall   # same, forcing a rebuild of the local checkout
#   scripts/install-cli.sh uninstall   # remove the tool again
#   scripts/install-cli.sh status      # what is installed where + which commands answer
#   scripts/install-cli.sh verify      # run the three shipping smoke commands
#
# Environment (all optional):
#   GV_TOOL_DIR   isolate the tool prefix (sets UV_TOOL_DIR / PIPX_HOME)
#   GV_BIN_DIR    isolate the shim directory (sets UV_TOOL_BIN_DIR / PATH entry)
#   GV_PYTHON     interpreter for the venv fallback (default: python3)
#
# Notes that cost real time to learn:
#   * The distribution is named `skaldir-agent`; the *executable* is `skaldir`
#     (`[project.scripts] skaldir = "skaldir.perf_lazy:main"`). `uv tool install --from . skaldir`
#     FAILS with "Package name (`skaldir-agent`) provided with `--from` does not match install
#     request (`skaldir`)" — install the distribution name, run the script name.
#   * `godsvoice` is still installed as a deprecated alias for the same entry point: it answers
#     identically and writes one notice line to stderr, and only when stderr is a TTY.
#   * `python -m skaldir` is the module spelling of the CLI. `python -m skaldir.cli` is not an
#     entry point (the module has no `__main__` guard) and prints nothing — use `-m skaldir`.
#   * uv refuses to hardlink across filesystems and only warns; harmless.
#   * Never run sudo here. Everything is user-level.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_NAME="skaldir-agent"
SCRIPT_NAME="skaldir"
#: The deprecated alias script the same distribution also installs. `verify` proves it
#: answers identically; nothing here depends on it.
LEGACY_SCRIPT_NAME="godsvoice"
MODULE_ENTRY="skaldir"
ACTION="${1:-install}"

if [[ -n "${GV_TOOL_DIR:-}" ]]; then
  export UV_TOOL_DIR="$GV_TOOL_DIR"
  export PIPX_HOME="$GV_TOOL_DIR/pipx"
fi
if [[ -n "${GV_BIN_DIR:-}" ]]; then
  export UV_TOOL_BIN_DIR="$GV_BIN_DIR"
  export PIPX_BIN_DIR="$GV_BIN_DIR"
fi

say() { printf '%s\n' "$*"; }
die() { printf 'install-cli: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# The directory the console script lands in, so the verify step can call it by path.
bin_dir() {
  if have uv; then printf '%s' "${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"; else printf '%s' "${PIPX_BIN_DIR:-$HOME/.local/bin}"; fi
}

tool_bin() { printf '%s/%s' "$(bin_dir)" "$SCRIPT_NAME"; }

do_install() {
  local force="${1:-}"
  if have uv; then
    say "# installing with uv tool (distribution $DIST_NAME -> script $SCRIPT_NAME)"
    local args=(tool install --from "$REPO" "$DIST_NAME")
    [[ "$force" == "force" ]] && args+=(--force)
    uv "${args[@]}"
  elif have pipx; then
    say "# uv not found — installing with pipx"
    pipx install --force "$REPO"
  else
    say "# neither uv nor pipx found — installing into a dedicated venv"
    local venv="${GV_TOOL_DIR:-$HOME/.local/share/skaldir-cli}/venv"
    "${GV_PYTHON:-python3}" -m venv "$venv"
    "$venv/bin/python" -m pip install --upgrade pip
    [[ "$force" == "force" ]] && "$venv/bin/python" -m pip install --force-reinstall "$REPO" \
      || "$venv/bin/python" -m pip install "$REPO"
    mkdir -p "$(bin_dir)"
    ln -sfn "$venv/bin/$SCRIPT_NAME" "$(tool_bin)"
    say "# linked $(tool_bin) -> $venv/bin/$SCRIPT_NAME"
  fi
}

do_uninstall() {
  if have uv; then
    uv tool uninstall "$DIST_NAME" || say "# uv tool uninstall reported nothing to remove"
  elif have pipx; then
    pipx uninstall "$DIST_NAME" || say "# pipx uninstall reported nothing to remove"
  else
    local venv="${GV_TOOL_DIR:-$HOME/.local/share/skaldir-cli}/venv"
    rm -rf "$venv" "$(tool_bin)"
    say "# removed $venv and $(tool_bin)"
  fi
}

do_status() {
  say "# repo     : $REPO"
  if have uv; then
    say "# backend  : uv $(uv --version)"
    say "# tool dir : ${UV_TOOL_DIR:-$HOME/.local/share/uv/tools}"
    say "# bin dir  : $(bin_dir)"
    uv tool list || true
  elif have pipx; then
    say "# backend  : pipx"
    pipx list || true
  else
    say "# backend  : dedicated venv (${GV_TOOL_DIR:-$HOME/.local/share/skaldir-cli}/venv)"
  fi
  say "# script   : $(tool_bin)"
}

do_verify() {
  local bin; bin="$(tool_bin)"
  [[ -x "$bin" ]] || die "no executable at $bin — run '$0 install' first"
  say "== $bin --version =="
  "$bin" --version
  say "== $bin contract dump --json =="
  "$bin" contract dump --json 2>/dev/null | head -c 200; say ""
  say "== $bin selftest --json (exit 2 = a layer is honestly unreachable) =="
  set +e
  "$bin" selftest --json >/dev/null 2>&1
  local code=$?
  set -e
  say "selftest exit: $code"
  "$bin" selftest --json 2>/dev/null | head -c 200; say ""

  # The deprecated script and the module entry point must answer the same thing. Compared as
  # text, not exit codes: an alias that answers differently is a broken alias.
  local legacy; legacy="$(bin_dir)/$LEGACY_SCRIPT_NAME"
  if [[ -x "$legacy" ]]; then
    local primary_version legacy_version module_version
    primary_version="$("$bin" --version 2>/dev/null)"
    legacy_version="$("$legacy" --version 2>/dev/null)"
    module_version="$("$(dirname "$bin")/python" -m "$MODULE_ENTRY" --version 2>/dev/null)"
    say "== $legacy --version (deprecated alias) =="
    say "$legacy_version"
    [[ "$legacy_version" == "$primary_version" ]] \
      || die "the alias answers '$legacy_version', the primary script '$primary_version'"
    say "== python -m $MODULE_ENTRY --version =="
    say "$module_version"
    [[ "$module_version" == "$primary_version" ]] \
      || die "python -m $MODULE_ENTRY answers '$module_version', the script '$primary_version'"
    say "# identical: script, deprecated alias and module entry point all answer '$primary_version'"
  else
    say "# note: no deprecated alias at $legacy — the distribution installs '$SCRIPT_NAME' only"
  fi
}

case "$ACTION" in
  install)   do_install ;;
  reinstall) do_install force ;;
  uninstall) do_uninstall ;;
  status)    do_status ;;
  verify)    do_verify ;;
  *) die "unknown action '$ACTION' (install|reinstall|uninstall|status|verify)" ;;
esac

if [[ "$ACTION" == "install" || "$ACTION" == "reinstall" ]]; then
  say ""
  do_status
  say ""
  say "# next: $0 verify    (runs $SCRIPT_NAME --version / contract dump --json / selftest --json,"
  say "#                                              plus the deprecated $LEGACY_SCRIPT_NAME alias)"
  if ! printf '%s' ":$PATH:" | grep -q ":$(bin_dir):"; then
    say "# note: $(bin_dir) is not on your PATH — call $SCRIPT_NAME by full path or export PATH"
  fi
fi
