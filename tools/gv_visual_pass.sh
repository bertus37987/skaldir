#!/usr/bin/env bash
# Skaldir (godsv) — visual pass: one runnable pass over the real installed desktop app.
#
# What it does (in order, and it does not skip steps):
#   1. reports what agent-workspace-linux can really do here (doctor + missing tools)
#   2. refuses to run when an owner instance is already up (unless explicitly allowed)
#   3. starts the app with ITS OWN user-data dir and ITS OWN CDP port — never the
#      owner's profile, never the owner's port, never the owner's window
#   4. waits for readiness (CDP up AND the panel markers really mounted)
#   5. screenshots the main surfaces (reuses tools/gv_shot.mjs)
#   6. runs the console-stream check (reuses tools/gv_console.mjs)
#   7. runs the UI audit (reuses tools/gv_ui_audit.py: overflow, axe, dead controls)
#   8. tears everything down with SIGTERM only — never kill -9
#   9. prints one numeric summary (tools/gv_visual_pass.py) and exits 0 only when
#      every numeric criterion in MVP/11-visual-pass.md holds
#
# Two display modes:
#   host     — uses the session's existing display (Xwayland :0 here). Verified working.
#   isolated — a private X11 workspace from agent-workspace-linux (needs Xvfb + a WM +
#              xdpyinfo + xclip). BLOCKED on this machine until the owner installs them;
#              `--mode isolated` prints the exact command and exits 4.
#
# Usage:
#   tools/gv_visual_pass.sh [--port 9334] [--user-data-dir PATH] [--out DIR]
#                           [--label NAME] [--surfaces core|all|a,b,c] [--mode auto|host|isolated]
#                           [--timeout SECONDS] [--allow-owner-instance] [--keep-ud]
#                           [--no-disable-gpu] [--dry-run] [--help]
#
# Exit codes: 0 pass · 1 criteria failed · 2 usage/config · 3 refused (collision) ·
#             4 blocked (no display / isolated mode unavailable)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
OWNER_UD="$HERMES_HOME_DIR/godsv/desktop-userdata"     # the owner's official profile (read-only: we never use it)
APP_BIN="$HERMES_HOME_DIR/godsv/app/linux-unpacked/gods-voice"
APP_NAME="Skaldir Agent"
WM_CLASS="SkaldirAgent"
EXACT_INSTALL_CMD="sudo dnf install -y xorg-x11-server-Xvfb xdpyinfo openbox xclip"

PORT=9334
LABEL="visual-pass"
OUT_ROOT="$REPO/outputs/visual-pass"
SURFACES="core"
MODE="auto"
READY_TIMEOUT=90
ALLOW_OWNER=0
KEEP_UD=0
DISABLE_GPU=1
SKIP_ONBOARDING=0
DRY_RUN=0
STAMP="$(date +%Y%m%d-%H%M%S)"
UD=""
OUTDIR=""
WS_ID="visualpass"
ISOLATED=0
LAUNCHED=0
TORE_DOWN=0
OWNER_PIDS_BEFORE=""

CORE_SURFACES="shell,Capabilities,Verification,Tools,Treasury,Settings"
ALL_SURFACES="shell,Capabilities,Messaging,Artifacts,Scheduled jobs,Verification,Tools,Settings,Treasury"

log()  { printf '%s\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; }
die()  { local code="$1"; shift; printf 'gv_visual_pass: %s\n' "$*" >&2; exit "$code"; }

usage() { sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# ---------------------------------------------------------------- arguments ---
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?}"; shift 2;;
    --user-data-dir) UD="${2:?}"; shift 2;;
    --out) OUT_ROOT="${2:?}"; shift 2;;
    --label) LABEL="${2:?}"; shift 2;;
    --surfaces) SURFACES="${2:?}"; shift 2;;
    --mode) MODE="${2:?}"; shift 2;;
    --timeout) READY_TIMEOUT="${2:?}"; shift 2;;
    --allow-owner-instance) ALLOW_OWNER=1; shift;;
    --keep-ud) KEEP_UD=1; shift;;
    --keep-onboarding) SKIP_ONBOARDING=1; shift;;
    --no-disable-gpu) DISABLE_GPU=0; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage; exit 0;;
    *) usage >&2; die 2 "unknown argument: $1";;
  esac
done
case "$MODE" in auto|host|isolated) ;; *) die 2 "--mode must be auto, host or isolated";; esac
case "$SURFACES" in
  core) SURFACE_LIST="$CORE_SURFACES";;
  all)  SURFACE_LIST="$ALL_SURFACES";;
  *)    SURFACE_LIST="$SURFACES";;
esac

[ -x "$APP_BIN" ] || die 2 "app binary not found/executable: $APP_BIN"
command -v curl >/dev/null || die 2 "curl is required"
NODE_BIN="$(command -v node || true)"
[ -n "$NODE_BIN" ] || die 2 "node is required (gv_shot.mjs / gv_console.mjs / gv_dom_probe.mjs)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || die 2 "no python interpreter found"
[ -f "$REPO/tools/gv_shot.mjs" ] && [ -f "$REPO/tools/gv_console.mjs" ] && [ -f "$REPO/tools/gv_dom_probe.mjs" ] \
  || die 2 "tools/gv_shot.mjs, tools/gv_console.mjs and tools/gv_dom_probe.mjs are required"
[ -f "$REPO/tools/gv_ui_audit.py" ] || die 2 "tools/gv_ui_audit.py is required"

json_get() {  # json_get <file> <dotted.path>
  "$PY" - "$1" "$2" <<'PYEOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    print(""); raise SystemExit(0)
cur = data
for part in sys.argv[2].split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        print(""); raise SystemExit(0)
print(cur if isinstance(cur, (str, int, float, bool)) else json.dumps(cur))
PYEOF
}

# --------------------------------------------------- 0) process identification
# A process is "an app process" when its cmdline names the app binary. We skip our
# own shell and our parent so a pattern can never match the harness itself.
app_procs() {  # prints "<pid>\t<cmdline>" for app-binary processes (including children)
  local p cmd
  for p in /proc/[0-9]*; do
    p="${p#/proc/}"
    [ "$p" = "$$" ] && continue
    [ "$p" = "$PPID" ] && continue
    cmd="$( { tr '\0' ' ' < "/proc/$p/cmdline"; } 2>/dev/null )" || continue
    case "$cmd" in
      *"$APP_BIN"*) printf '%s\t%s\n' "$p" "$cmd" ;;
    esac
  done
}

effective_ud() {  # effective_ud <pid> → the user-data dir that process really uses
  local pid="$1" cmd env_ud flag_ud
  cmd="$( { tr '\0' ' ' < "/proc/$pid/cmdline"; } 2>/dev/null )" || { printf ''; return; }
  flag_ud="$(printf '%s' "$cmd" | tr ' ' '\n' | sed -n 's/^--user-data-dir=//p' | head -1)"
  env_ud="$( { tr '\0' '\n' < "/proc/$pid/environ"; } 2>/dev/null | sed -n 's/^HERMES_DESKTOP_USER_DATA_DIR=//p' | head -1)"
  if [ -n "$flag_ud" ]; then printf '%s' "$flag_ud"
  elif [ -n "$env_ud" ]; then printf '%s' "$env_ud"
  else printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}/Skaldir Agent"; fi
}

pids_for_ud() {  # pids whose effective user-data dir is $1
  local want="$1" pid cmd
  app_procs | while IFS=$'\t' read -r pid cmd; do
    [ -z "$pid" ] && continue
    if [ "$(effective_ud "$pid")" = "$want" ]; then printf '%s\n' "$pid"; fi
  done
}

owner_pids() { pids_for_ud "$OWNER_UD" | tr '\n' ' '; }

port_holder() {  # "<proc>,pid=<pid>" of the listener on $1, or empty
  local port="$1"
  if command -v ss >/dev/null; then
    ss -ltnpH "sport = :$port" 2>/dev/null | sed -n 's/.*users:((\([^)]*\)).*/\1/p' | head -1
  fi
}

port_free() {  # TCP connect probe against our own loopback port
  (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1 && return 1
  return 0
}

# ------------------------------------------------------------ 1) capabilities
DOCTOR_JSON=""
CAP_REPORT=""
detect_capabilities() {
  CAP_REPORT="agent-workspace-linux capability report"
  if command -v agent-workspace-linux >/dev/null 2>&1; then
    DOCTOR_JSON="$(mktemp)"
    agent-workspace-linux doctor >"$DOCTOR_JSON" 2>/dev/null || true
    local ver awl_real awl_pkg
    awl_real="$(readlink -f "$(command -v agent-workspace-linux)" 2>/dev/null || true)"
    awl_pkg="$(printf '%s' "$awl_real" | sed -n 's#\(.*/node_modules/\(@[^/]*/\)\?[^/]*\)/.*#\1#p')"
    ver="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$awl_pkg/package.json" 2>/dev/null | head -1)"
    ready="$(json_get "$DOCTOR_JSON" ready_for_x11_workspace)"
    viewer="$(json_get "$DOCTOR_JSON" ready_for_host_viewer)"
    CAP_REPORT="$CAP_REPORT
  cli path                 : ${awl_real:-not found}
  version                  : ${ver:-unknown}
  ready_for_x11_workspace  : ${ready:-unknown}   (private Xvfb/Xephyr workspace: needs Xvfb + WM + xdpyinfo)
  ready_for_host_viewer    : ${viewer:-unknown}   (view the workspace in the host session: needs a source build, not needed here)"
    CAP_REPORT="$CAP_REPORT
  window/input tooling     : $(for t in xdotool xprop xauth import scrot; do command -v "$t" >/dev/null && printf '%s ' "$t"; done)
  screenshot tooling       : $(for t in ffmpeg import scrot; do command -v "$t" >/dev/null && printf '%s ' "$t"; done)"
  else
    CAP_REPORT="$CAP_REPORT
  NOT INSTALLED (agent-workspace-linux not on PATH)"
  fi
  MISSING_TOOLS=""
  local t
  for t in Xvfb xdpyinfo openbox xclip; do
    command -v "$t" >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS $t"
  done
  MISSING_TOOLS="${MISSING_TOOLS# }"
}

display_probe() {  # prints "<display>|<geometry>" or "<wayland>|(no X11 ...)" or empty
  local d="${DISPLAY:-}" geom=""
  if [ -n "$d" ] && command -v xdotool >/dev/null 2>&1; then
    geom="$(timeout 6 xdotool getdisplaygeometry 2>/dev/null | tr '\n ' 'xx' | sed 's/x$//')"
  fi
  if [ -n "$geom" ]; then
    printf '%s|%s' "$d" "$geom"
  elif [ -n "$d" ]; then
    printf '%s|(DISPLAY set but not answering)' "$d"
  elif [ -n "${WAYLAND_DISPLAY:-}" ]; then
    printf 'wayland|(native Wayland: %s, no X11 window tooling)' "$WAYLAND_DISPLAY"
  fi
}

# ---------------------------------------------------------------- 2) preflight
OUTDIR="$OUT_ROOT/$STAMP-$LABEL"
shots_dir="$OUTDIR/shots"
audit_dir="$OUTDIR/audit"

[ -z "$UD" ] && UD="/tmp/gv-visualpass/$STAMP/userdata"

detect_capabilities
DISP_RAW="$(display_probe)"
DISP_NAME="${DISP_RAW%%|*}"
DISP_DESC="${DISP_RAW##*|}"

log "=== Skaldir visual pass — preflight ==="
log "app binary      : $APP_BIN"
log "own profile     : $UD"
log "own CDP port    : $PORT"
log "owner profile   : $OWNER_UD  (never touched)"
log "run directory   : $OUTDIR"
log "$CAP_REPORT"
if [ -n "$MISSING_TOOLS" ]; then
  log "  MISSING for the isolated X11 workspace: ${MISSING_TOOLS// /, }"
  log "  exact owner command to unblock it:"
  log "      $EXACT_INSTALL_CMD"
fi
log "  host display    : ${DISP_NAME:-none} ${DISP_DESC:+($DISP_DESC)}"
[ -n "$DISP_DESC" ] || warn "no usable display found (DISPLAY=$DISPLAY, WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset})"

# owner / collision guard ------------------------------------------------------
OWNER_PIDS_BEFORE="$(owner_pids)"
OWNER_COUNT=$(printf '%s' "$OWNER_PIDS_BEFORE" | wc -w)
SAME_UD_PIDS="$(pids_for_ud "$UD" | tr '\n' ' ')"
SAME_UD_COUNT=$(printf '%s' "$SAME_UD_PIDS" | wc -w)
PORT_HOLDER="$(port_holder "$PORT")"
REFUSED=0
REFUSE_REASON=""

if [ "$SAME_UD_COUNT" -gt 0 ]; then
  REFUSED=1; REFUSE_REASON="our own profile is already in use by PID(s) $SAME_UD_PIDS"
fi
if [ -n "$PORT_HOLDER" ]; then
  if [ -z "$REFUSE_REASON" ]; then
    REFUSED=1; REFUSE_REASON="CDP port $PORT is already taken by $PORT_HOLDER"
  else
    REFUSE_REASON="$REFUSE_REASON; CDP port $PORT is already taken by $PORT_HOLDER"
  fi
fi
if [ "$OWNER_COUNT" -gt 0 ] && [ "$ALLOW_OWNER" -eq 0 ]; then
  if [ -z "$REFUSE_REASON" ]; then
    REFUSED=1
    REFUSE_REASON="an OWNER instance is already running (PID(s) $OWNER_PIDS_BEFORE, profile $OWNER_UD)"
  else
    REFUSE_REASON="$REFUSE_REASON; an OWNER instance is also running (PID(s) $OWNER_PIDS_BEFORE)"
  fi
fi

if [ "$OWNER_COUNT" -gt 0 ]; then
  if [ "$ALLOW_OWNER" -eq 1 ]; then
    warn "owner instance running (PID(s) $OWNER_PIDS_BEFORE) — continuing because --allow-owner-instance was given."
    warn "  isolation proof: separate --user-data-dir ($UD) ⇒ separate Electron single-instance lock;"
    warn "  separate CDP port ($PORT) ⇒ separate devtools endpoint; screenshots come from OUR renderer via CDP,"
    warn "  never from an X screen grab, so the owner's window can never appear in the evidence."
  else
    log "  owner instance  : RUNNING (PID(s) $OWNER_PIDS_BEFORE)"
  fi
else
  log "  owner instance  : not running"
fi

if [ "$REFUSED" -eq 1 ]; then
  log ""
  log "REFUSED — this pass will not run: $REFUSE_REASON"
  log ""
  log "Nothing was started; the owner's app was not touched."
  log "If the owner's instance is the only obstacle and you accept a second, fully isolated"
  log "instance on the same display (own profile + own port), re-run with:"
  log "    tools/gv_visual_pass.sh --allow-owner-instance"
  exit 3
fi

# mode resolution --------------------------------------------------------------
ISO_READY="$(json_get "$DOCTOR_JSON" ready_for_x11_workspace)"
case "$MODE" in
  auto)
    if [ "$ISO_READY" = "True" ] || [ "$ISO_READY" = "true" ]; then MODE_RESOLVED="isolated"
    elif [ -n "$DISP_NAME" ]; then MODE_RESOLVED="host"
    else MODE_RESOLVED="blocked"; fi
    ;;
  isolated) MODE_RESOLVED="isolated";;
  host)     [ -n "$DISP_NAME" ] && MODE_RESOLVED="host" || MODE_RESOLVED="blocked";;
esac

if [ "$MODE_RESOLVED" = "isolated" ] && { [ "$ISO_READY" != "True" ] && [ "$ISO_READY" != "true" ]; }; then
  MODE_RESOLVED="blocked"
fi

if [ "$MODE_RESOLVED" = "blocked" ]; then
  log ""
  log "BLOCKED — no display path is available for a visual pass."
  if [ -n "$MISSING_TOOLS" ]; then
    log ""
    log "agent-workspace-linux cannot create its isolated X11 workspace. Missing: ${MISSING_TOOLS// /, }"
    log "It reported: $(json_get "$DOCTOR_JSON" blockers)"
    log ""
    log "EXACT OWNER COMMAND (run once, then re-run this harness with --mode isolated):"
    log "    $EXACT_INSTALL_CMD"
    log ""
    log "Package names verified against the Fedora 44 repos on this machine:"
    log "    xorg-x11-server-Xvfb 21.1.24-1.fc44 · xdpyinfo 1.3.4-4.fc44 · openbox 3.6.1-30.fc44 · xclip 0.13-26.git11cba61.fc44"
    log "  (Xvfb = private X server, openbox = window manager the workspace needs for window ids/geometry,"
    log "   xdpyinfo = display readiness probe, xclip = clipboard for workspace paste)"
  fi
  if [ -z "$DISP_NAME" ]; then
    log "Also no host display is reachable (DISPLAY=${DISPLAY:-unset}), so even the fallback path is closed."
    log "If the session is running under Wayland only, log in to a GNOME session that exposes Xwayland (:0)."
  fi
  log ""
  log "Nothing was started; the owner's app was not touched."
  exit 4
fi

log "  mode resolved   : $MODE_RESOLVED"

if [ "$DRY_RUN" -eq 1 ]; then
  log ""
  log "=== dry run — nothing will be started ==="
  log "would create     : $OUTDIR"
  log "surfaces         : $SURFACE_LIST"
  if [ "$MODE_RESOLVED" = "host" ]; then
    log "would launch     : env HERMES_DESKTOP_USER_DATA_DIR=$UD HERMES_DESKTOP_APP_NAME=\"$APP_NAME\" \\"
    log "                   setsid $APP_BIN --user-data-dir=$UD --class=$WM_CLASS \\"
    log "                          --remote-debugging-port=$PORT$([ "$DISABLE_GPU" -eq 1 ] && printf ' --disable-gpu --disable-gpu-compositing')"
  else
    log "would launch     : agent-workspace-linux workspace start --id $WS_ID --ack-hidden-workspace --ack-unenforced-policy"
    log "                   agent-workspace-linux workspace launch --id $WS_ID --ack-unenforced-policy --cwd $REPO \\"
    log "                          --env HERMES_DESKTOP_USER_DATA_DIR=$UD --env HERMES_DESKTOP_APP_NAME=\"$APP_NAME\" \\"
    log "                          --wait-window --screenshot-window -- \\"
    log "                          $APP_BIN --user-data-dir=$UD --class=$WM_CLASS --remote-debugging-port=$PORT"
  fi
  log "would teardown   : SIGTERM to the launched PID(s) only (never kill -9); workspace stop for isolated mode"
  exit 0
fi

mkdir -p "$shots_dir" "$audit_dir"

# ---------------------------------------------------------------- 3) teardown
teardown() {
  [ "$TORE_DOWN" -eq 1 ] && return 0
  TORE_DOWN=1
  log ""
  log "--- teardown (SIGTERM only, never kill -9) ---"
  local pids p i left
  if [ "$ISOLATED" -eq 1 ] && command -v agent-workspace-linux >/dev/null 2>&1; then
    timeout 60 agent-workspace-linux workspace stop --id "$WS_ID" --timeout-ms 20000 2>&1 | sed 's/^/    /' || true
    timeout 60 agent-workspace-linux workspace cleanup --id "$WS_ID" 2>&1 | sed 's/^/    /' || true
  fi
  pids="$(pids_for_ud "$UD" | tr '\n' ' ')"
  for p in $pids; do kill -TERM "$p" 2>/dev/null || true; done
  for i in $(seq 1 25); do
    [ -z "$(pids_for_ud "$UD")" ] && break
    sleep 1
  done
  pids="$(pids_for_ud "$UD" | tr '\n' ' ')"
  if [ -n "$pids" ]; then
    warn "still alive after first SIGTERM: $pids — sending SIGTERM again"
    for p in $pids; do kill -TERM "$p" 2>/dev/null || true; done
    for i in $(seq 1 15); do
      [ -z "$(pids_for_ud "$UD")" ] && break
      sleep 1
    done
  fi
  left="$(pids_for_ud "$UD" | wc -l)"
  TEARDOWN_LEFTOVERS="$left"
  if [ "$left" -gt 0 ]; then
    warn "$left process(es) refused to exit on SIGTERM: $(pids_for_ud "$UD" | tr '\n' ' ')"
    warn "NOT escalating to SIGKILL (project rule). Inspect them by hand."
  else
    log "  all visual-pass processes exited on SIGTERM"
  fi
  local owner_now; owner_now="$(owner_pids)"
  if [ "$OWNER_COUNT" -gt 0 ]; then
    if [ -n "$owner_now" ]; then
      log "  owner instance untouched: yes (PID(s) $owner_now still alive)"
    else
      warn "owner instance is GONE (was $OWNER_PIDS_BEFORE) — investigate; this harness only ever killed PIDs using profile $UD"
    fi
  fi
  if [ "$KEEP_UD" -eq 0 ] && [ -n "$UD" ]; then
    case "$UD" in
      /tmp/gv-visualpass/*) rm -rf "$UD" 2>/dev/null || true; log "  removed throwaway profile $UD";;
      *) log "  kept profile outside /tmp: $UD";;
    esac
  fi
}
TEARDOWN_LEFTOVERS=""
trap 'teardown' EXIT INT TERM

# ---------------------------------------------------------------- 4) launch
log ""
log "--- starting an isolated instance (own profile + own port) ---"
: > "$OUTDIR/app.log"
if [ "$MODE_RESOLVED" = "isolated" ]; then
  ISOLATED=1
  WS_ID="visualpass-$STAMP"
  log "  starting private X11 workspace $WS_ID ..."
  timeout 120 agent-workspace-linux workspace start --id "$WS_ID" \
    --ack-hidden-workspace --ack-unenforced-policy >"$OUTDIR/workspace-start.log" 2>&1 \
    || die 4 "agent-workspace-linux workspace start failed — see $OUTDIR/workspace-start.log"
  log "  launching the app inside it ..."
  timeout 180 agent-workspace-linux workspace launch --id "$WS_ID" --ack-unenforced-policy \
    --cwd "$REPO" \
    --env "HERMES_DESKTOP_USER_DATA_DIR=$UD" --env "HERMES_DESKTOP_APP_NAME=$APP_NAME" \
    --env "GODSV_MODE=${GODSV_MODE:-whisper}" \
    --wait-window --screenshot-window -- \
    "$APP_BIN" --user-data-dir="$UD" --class="$WM_CLASS" --remote-debugging-port="$PORT" \
    $([ "$DISABLE_GPU" -eq 1 ] && printf '%s' "--disable-gpu --disable-gpu-compositing") \
    >"$OUTDIR/workspace-launch.log" 2>&1 \
    || warn "workspace launch did not confirm a window — continuing, readiness check will decide"
else
  LAUNCH_PID=""
  env HERMES_DESKTOP_USER_DATA_DIR="$UD" HERMES_DESKTOP_APP_NAME="$APP_NAME" \
      HERMES_DESKTOP_CWD="$REPO" GODSV_MODE="${GODSV_MODE:-whisper}" \
      setsid "$APP_BIN" --user-data-dir="$UD" --class="$WM_CLASS" \
        --remote-debugging-port="$PORT" $([ "$DISABLE_GPU" -eq 1 ] && printf '%s' "--disable-gpu --disable-gpu-compositing") \
      >>"$OUTDIR/app.log" 2>&1 &
  LAUNCH_PID=$!
  log "  launcher pid $LAUNCH_PID (profile $UD, port $PORT$([ "$DISABLE_GPU" -eq 1 ] && printf ', --disable-gpu --disable-gpu-compositing'))"
fi
LAUNCHED=1

# ------------------------------------------------------- 5) readiness waiting
READY_START=$(date +%s)
CDP_UP=0
for _ in $(seq 1 "$READY_TIMEOUT"); do
  if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then CDP_UP=1; break; fi
  sleep 1
done
READINESS_OK=false
READINESS_SECONDS=$(( $(date +%s) - READY_START ))
SHELL_EXPR='JSON.stringify({state:document.readyState, title:document.title, nav:(document.body.innerText||"").includes("New session")})'
if [ "$CDP_UP" -eq 1 ]; then
  log "  CDP answered on :$PORT after ${READINESS_SECONDS}s"
  for _ in $(seq 1 20); do
    out="$(timeout 45 "$NODE_BIN" "$REPO/tools/gv_dom_probe.mjs" "$PORT" "$SHELL_EXPR" 2>/dev/null || true)"
    case "$out" in *'"state":"complete"'*) READINESS_OK=true; break;; esac
    sleep 3
  done
  READINESS_SECONDS=$(( $(date +%s) - READY_START ))
else
  warn "no CDP endpoint on port $PORT after ${READY_TIMEOUT}s — see $OUTDIR/app.log"
fi
log "  readiness       : CDP=${CDP_UP} document-rendered=${READINESS_OK} in ${READINESS_SECONDS}s"

if [ "$READINESS_OK" != "true" ]; then
  log ""
  log "BLOCKED — the app never became inspectable, so there is nothing to shoot."
  log "Evidence: $OUTDIR/app.log (last lines):"
  tail -15 "$OUTDIR/app.log" 2>/dev/null | sed 's/^/    /'
  exit 4
fi

# ------------------------------------------------------------------- helpers
# CDP on this stack is occasionally slow to answer (and a renderer that is
# mid-navigation can briefly have no page target), so every DOM question is asked
# more than once and an empty answer is never treated as a fact.
probe() {  # probe <js-expression> [attempts] → last non-empty answer; rc!=0 if all empty
  local expr="$1" tries="${2:-3}" i out
  for i in $(seq 1 "$tries"); do
    out="$(timeout 25 "$NODE_BIN" "$REPO/tools/gv_dom_probe.mjs" "$PORT" "$expr" 2>/dev/null || true)"
    if [ -n "$out" ]; then printf '%s' "$out"; return 0; fi
    sleep 2
  done
  return 1
}

CLICK_LATER_EXPR='(() => {const b=[...document.querySelectorAll("button")].find(x=>/choose a provider later/i.test(x.textContent||"")); if(!b) return JSON.stringify({found:false}); b.click(); return JSON.stringify({found:true, label:(b.textContent||"").trim().slice(0,64)});})()'
SURF_EXPR='JSON.stringify({mounted:["SKALDIR","Treasury","Verification"].every(m=>(document.body.innerText||"").includes(m)), markers:["SKALDIR","Treasury","Verification"].filter(m=>(document.body.innerText||"").includes(m)), hash:location.hash})'

#: The app shows the first-run provider surface on a profile without a provider and
#: it can (re)appear after a page reload. Dismissing it is a normal user path — the
#: app's own e2e suite clicks exactly this button (e2e/onboarding-settings.spec.ts).
onboarding_dismiss() {
  local out
  out="$(probe "$CLICK_LATER_EXPR" 3 || true)"
  case "$out" in
    *'"found":true'*) ONBOARDING="dismissed ($out)"; log "  onboarding      : dismissed via the real \"choose a provider later\" button"; sleep 3; return 0;;
    *'"found":false'*) ONBOARDING="${ONBOARDING:-not present}"; log "  onboarding      : not present"; return 0;;
    *) ONBOARDING="unknown (probe gave no answer)"; warn "onboarding probe gave no answer — surface may be the first-run overlay"; return 1;;
  esac
}

panel_route() { probe '(() => { window.location.hash = "#/gods-voice"; return "ok" })()' 2 >/dev/null || true; sleep 3; }

#: Call this DIRECTLY, never inside $(...): a command substitution runs it in a subshell
#: and PANEL_MOUNTED would be lost in the parent. The probe answer travels via $PROBE_OUT.
PROBE_OUT=""
wait_panel() {  # sets PANEL_MOUNTED/PANEL_MOUNTED_AT; writes the last probe answer to $PROBE_OUT
  local i out=""
  [ -z "$PROBE_OUT" ] && PROBE_OUT="$(mktemp)"
  for i in $(seq 1 8); do
    out="$(probe "$SURF_EXPR" 1 || true)"
    case "$out" in
      *'"mounted":true'*) PANEL_MOUNTED=true; PANEL_MOUNTED_AT=$i; printf '%s' "$out" > "$PROBE_OUT"; return 0;;
    esac
    sleep 3
  done
  PANEL_MOUNTED=false
  printf '%s' "${out:-<no probe answer>}" > "$PROBE_OUT"
  return 1
}

#: Both GPU switches are needed, measured: without --disable-gpu Page.captureScreenshot
#: (raw CDP, and therefore gv_shot/gv_console) never returns; without
#: --disable-gpu-compositing Playwright's page.screenshot (gv_ui_audit) times out even
#: though it prints "fonts loaded". With both, every measured capture returned in ~2s.
#: Software rasterisation on this stack means a capture that follows a page nobody has
#: touched can stall. Ask for one cheap deliberate repaint first — no extra CDP
#: screenshot call, because that capture path is the expensive/fragile part.
REPAINT_EXPR='(() => { const b=document.body; b.style.transform="translateZ(0)"; window.dispatchEvent(new Event("resize")); requestAnimationFrame(() => { b.style.transform=""; }); return "repaint-requested" })()'
paint_nudge() {
  probe "$REPAINT_EXPR" 1 >/dev/null 2>&1 || true
  sleep 1
}

# ------------------------------------------------- 6) first-run overlay gate
# A profile without a provider shows the first-run "connect a provider" surface,
# which covers the panel. Dismissing it is a normal user path — the app's own e2e
# suite does exactly this (apps/desktop/e2e/onboarding-settings.spec.ts) — so the
# pass clicks that button and then reports WHICH surface it measured. It never
# pretends first-run numbers are panel numbers.
ONBOARDING="not checked"
PANEL_MOUNTED=false
PANEL_MOUNTED_AT=0
MEASURED_SURFACE="unknown"

log ""
log "--- surface classification (which surface are we about to measure?) ---"
if [ "$SKIP_ONBOARDING" -eq 0 ]; then onboarding_dismiss; fi
panel_route
wait_panel || true
SURF_STATE="$(cat "$PROBE_OUT" 2>/dev/null || printf '<no probe answer>')"
[ "$PANEL_MOUNTED" = true ] && MEASURED_SURFACE="panel/cockpit (+ shell chrome)" || MEASURED_SURFACE="first-run provider-connect overlay"
log "  measured surface: $MEASURED_SURFACE"
log "  surface probe   : $SURF_STATE"
if [ "$PANEL_MOUNTED" != true ]; then
  warn "panel/cockpit markers are NOT mounted after 45s: the audit numbers below describe"
  warn "the first-run overlay, not the panel (see MVP/11-visual-pass.md, 'what this cannot prove')."
fi

# ------------------------------------------------------------ 7) screenshots
log ""
log "--- surfaces (CDP screenshots of our own renderer) ---"
paint_nudge 1441 901
: > "$OUTDIR/surfaces.tsv"
SURFACES_SHOT=0
SURFACES_EXPECTED=0
idx=0
IFS=',' read -r -a surface_array <<< "$SURFACE_LIST"
for surface in "${surface_array[@]}"; do
  surface="$(printf '%s' "$surface" | sed 's/^ *//;s/ *$//')"
  [ -z "$surface" ] && continue
  idx=$((idx + 1))
  SURFACES_EXPECTED=$((SURFACES_EXPECTED + 1))
  slug="$(printf '%s' "$surface" | tr ' A-Z' '-a-z' | tr -cd 'a-z0-9-')"
  shot="$shots_dir/$(printf '%02d' "$idx")-$slug.png"
  surface_tab=""
  [ "$surface" = "shell" ] || surface_tab="$surface"
  if [ -z "$surface_tab" ]; then
    timeout 90 "$NODE_BIN" "$REPO/tools/gv_shot.mjs" "$PORT" "$shot" --size 1440x900 --wait 1500 \
      >>"$OUTDIR/shots.log" 2>&1 || true
  else
    timeout 90 "$NODE_BIN" "$REPO/tools/gv_shot.mjs" "$PORT" "$shot" --size 1440x900 --tab "$surface_tab" --wait 1800 \
      >>"$OUTDIR/shots.log" 2>&1 || true
  fi
  if [ ! -s "$shot" ]; then
    warn "$(basename "$shot"): capture produced nothing — repaint and retry once"
    paint_nudge
    if [ -z "$surface_tab" ]; then
      timeout 90 "$NODE_BIN" "$REPO/tools/gv_shot.mjs" "$PORT" "$shot" --size 1440x900 --wait 1500 \
        >>"$OUTDIR/shots.log" 2>&1 || true
    else
      timeout 90 "$NODE_BIN" "$REPO/tools/gv_shot.mjs" "$PORT" "$shot" --size 1440x900 --tab "$surface_tab" --wait 1800 \
        >>"$OUTDIR/shots.log" 2>&1 || true
    fi
  fi
  bytes=0
  [ -f "$shot" ] && bytes="$(stat -c %s "$shot")"
  printf '%s\t%s\t%s\n' "$surface" "$shot" "$bytes" >> "$OUTDIR/surfaces.tsv"
  if [ "$bytes" -gt 0 ]; then
    SURFACES_SHOT=$((SURFACES_SHOT + 1))
    info "$(printf '%-16s %8s bytes  %s' "$surface" "$bytes" "$shot")"
  else
    warn "$(printf '%-16s CAPTURE FAILED' "$surface")"
  fi
done

if [ "$ISOLATED" -eq 1 ]; then
  log "  X-level evidence from the private workspace (independent of CDP):"
  timeout 60 agent-workspace-linux workspace windows --id "$WS_ID" >"$OUTDIR/workspace-windows.json" 2>&1 || true
  timeout 60 agent-workspace-linux workspace screenshot-window --id "$WS_ID" --class "$WM_CLASS" \
    --output "$OUTDIR/workspace-window.png" >>"$OUTDIR/workspace-windows.json" 2>&1 || true
  timeout 60 agent-workspace-linux workspace screenshot --id "$WS_ID" \
    --output "$OUTDIR/workspace-display.png" >>"$OUTDIR/workspace-windows.json" 2>&1 || true
fi

# ------------------------------------------------------- 7) console + audit
log ""
log "--- renderer console stream (reload + capture) ---"
timeout 120 "$NODE_BIN" "$REPO/tools/gv_console.mjs" "$PORT" 10 >"$OUTDIR/console.log" 2>&1 || true
CONSOLE_LINES="$(grep -vc '^---' "$OUTDIR/console.log" 2>/dev/null || true)"
CONSOLE_LINES="${CONSOLE_LINES:-0}"
CONSOLE_ERRORS="$(grep -c -E '^\[(error|exception)\]' "$OUTDIR/console.log" 2>/dev/null || true)"
CONSOLE_ERRORS="${CONSOLE_ERRORS:-0}"
log "  renderer lines: $CONSOLE_LINES (errors/exceptions: $CONSOLE_ERRORS)"
if [ "$CONSOLE_ERRORS" -gt 0 ]; then
  grep -E '^\[(error|exception)\]' "$OUTDIR/console.log" | head -8 | sed 's/^/    /'
fi

# The console check reloads the renderer, and the first-run overlay comes back with
# a reload on a profile without a provider — so the surface the audit is about to
# measure is re-established (and re-reported) here, not assumed.
log ""
log "--- re-establishing the surface after the reload ---"
onboarding_dismiss || true
panel_route
wait_panel || true
SURF_STATE_2="$(cat "$PROBE_OUT" 2>/dev/null || printf '<no probe answer>')"
[ "$PANEL_MOUNTED" = true ] && MEASURED_SURFACE="panel/cockpit (+ shell chrome)" || MEASURED_SURFACE="first-run provider-connect overlay"
log "  measured surface: $MEASURED_SURFACE"
log "  surface probe   : $SURF_STATE_2"

log ""
log "--- UI audit (overflow, axe, dead controls, security boundary) ---"
paint_nudge 1442 902
run_audit() {
  timeout 900 "$PY" "$REPO/tools/gv_ui_audit.py" --port "$PORT" --label "$LABEL" --out "$audit_dir" \
    >>"$OUTDIR/audit.log" 2>&1
  AUDIT_EXIT=$?
  AUDIT_REPORT="$(ls -1t "$audit_dir"/*/report.json 2>/dev/null | head -1)"
}
run_audit; AUDIT_ATTEMPTS=1
if [ -z "$AUDIT_REPORT" ]; then
  warn "the audit wrote no report.json (exit $AUDIT_EXIT) — its frame capture timed out."
  warn "retrying once after a reload + repaint; the audit's own log is $OUTDIR/audit.log"
  probe '(() => { location.reload(); return "reloading" })()' 1 >/dev/null || true
  sleep 12
  onboarding_dismiss || true
  panel_route
  wait_panel || true
  paint_nudge 1443 903
  run_audit; AUDIT_ATTEMPTS=2
fi
log "  gv_ui_audit.py exit=$AUDIT_EXIT after $AUDIT_ATTEMPTS attempt(s) ($(grep -c '' "$OUTDIR/audit.log" 2>/dev/null || echo 0) log lines, $OUTDIR/audit.log)"
AUDIT_P0=0; AUDIT_P1=0; AUDIT_P2=0
if [ -n "$AUDIT_REPORT" ]; then
  read -r AUDIT_P0 AUDIT_P1 AUDIT_P2 < <("$PY" - "$AUDIT_REPORT" <<'PYEOF'
import json, sys
sev = {"P0": 0, "P1": 0, "P2": 0}
for f in (json.load(open(sys.argv[1])).get("findings") or []):
    sev[f.get("severity", "P2")] = sev.get(f.get("severity", "P2"), 0) + 1
print(sev["P0"], sev["P1"], sev["P2"])
PYEOF
)
  log "  audit findings : P0=$AUDIT_P0 P1=$AUDIT_P1 P2=$AUDIT_P2"
else
  warn "no audit report.json found under $audit_dir"
fi

# ------------------------------------------------------------- 8) run record
GIT_HEAD="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
{
  printf 'mode\t%s\n' "$MODE_RESOLVED"
  printf 'display\t%s\n' "${DISP_NAME:-none}"
  printf 'display_detail\t%s\n' "${DISP_DESC:-none}"
  printf 'user_data_dir\t%s\n' "$UD"
  printf 'cdp_port\t%s\n' "$PORT"
  printf 'git_head\t%s\n' "$GIT_HEAD"
  printf 'isolated_ready\t%s\n' "$ISO_READY"
  printf 'missing_tools\t%s\n' "${MISSING_TOOLS:-none}"
  printf 'surfaces_expected\t%s\n' "$SURFACES_EXPECTED"
  printf 'readiness_ok\t%s\n' "$READINESS_OK"
  printf 'onboarding\t%s\n' "$ONBOARDING"
  printf 'panel_mounted\t%s\n' "$PANEL_MOUNTED"
  printf 'measured_surface\t%s\n' "$MEASURED_SURFACE"
  printf 'readiness_seconds\t%s\n' "$READINESS_SECONDS"
  printf 'panel_mounted_at_probe\t%s\n' "$PANEL_MOUNTED_AT"
  printf 'audit_attempts\t%s\n' "$AUDIT_ATTEMPTS"
  printf 'audit_exit\t%s\n' "$AUDIT_EXIT"
  printf 'audit_report\t%s\n' "${AUDIT_REPORT:-none}"
  printf 'console_lines\t%s\n' "$CONSOLE_LINES"
  printf 'console_errors\t%s\n' "$CONSOLE_ERRORS"
  printf 'owner_pids_before\t%s\n' "${OWNER_PIDS_BEFORE:-none}"
  printf 'allow_owner_instance\t%s\n' "$ALLOW_OWNER"
} > "$OUTDIR/run.tsv"

log ""
log "--- teardown ---"
teardown
{
  printf 'teardown_leftovers\t%s\n' "${TEARDOWN_LEFTOVERS:-0}"
} >> "$OUTDIR/run.tsv"

# --------------------------------------------------------------- 9) summary
"$PY" "$REPO/tools/gv_visual_pass.py" --outdir "$OUTDIR"
SUMMARY_EXIT=$?
log ""
log "evidence: $OUTDIR"
log "summary : $OUTDIR/summary.json"
exit "$SUMMARY_EXIT"
