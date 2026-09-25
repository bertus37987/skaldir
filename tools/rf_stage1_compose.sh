#!/usr/bin/env bash
# Stage 1 of docs/RF-LONGRUN-FREIGABE.md: build the composed engine copy (artifact — the live
# engine tree stays untouched), apply every RF overlay in order, and prove the whole stack with
# the probes + the engine regression probe. No model calls, no cost.
#
#   bash tools/rf_stage1_compose.sh [tree]
#
# Default tree lives under $HOME, not /tmp: /tmp is a 16G tmpfs with ~1M inodes and an engine
# copy alone needs ~150k of them, so composing in /tmp fails with "Disk quota exceeded"
# part-way through the copy and leaves a tree that silently reads the live engine.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TREE="${1:-$HOME/gv-trees/secfull}"
cd "$REPO" || exit 2

echo "== compose: $TREE"
rm -rf "$TREE"
cp -a "$HOME/.hermes/hermes-agent" "$TREE" || { echo "copy failed"; exit 2; }

# TRAP (bites silently): the engine venv is a PEP 660 editable install whose finder module
# bakes ABSOLUTE paths into MAPPING. A plain copy therefore keeps importing the LIVE engine
# for every package it maps, so a probe run against the copy can read the wrong tree and still
# print a confident result. Repoint every mapping at the copy before trusting one number.
LIVE="$HOME/.hermes/hermes-agent"
FINDER=$(ls "$TREE"/venv/lib/python*/site-packages/__editable___hermes_agent*finder.py 2>/dev/null | head -1)
if [ -n "$FINDER" ]; then
  python3 - "$FINDER" "$LIVE" "$TREE" <<'PY'
import sys, pathlib
path, live, tree = (pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3])
text = path.read_text()
patched = text.replace(f"'{live}/", f"'{tree}/").replace(f'"{live}/', f'"{tree}/')
path.write_text(patched)
print(f"repointed editable finder: {patched.count(tree)} mapping(s) -> {tree}")
PY
else
  echo "WARNING: no editable finder found — verify module resolution before trusting results"
fi

# Hard gate: prove the copy really resolves its own modules before any probe runs.
(cd "$TREE" && "$TREE/venv/bin/python" -c "
import inspect, sys
bad = []
for name in ('agent.turn_finalizer', 'tools.registry', 'hermes_cli.commands'):
    try:
        mod = __import__(name, fromlist=['x'])
    except Exception as exc:
        print('WARN cannot import', name, exc); continue
    where = inspect.getfile(mod)
    if not where.startswith('$TREE'):
        bad.append((name, where))
if bad:
    print('FATAL imported from outside the tree:', bad); sys.exit(1)
print('resolution gate OK: agent/tools/hermes_cli load from', '$TREE')
") || { echo "resolution gate FAILED — refusing to run probes"; exit 1; }

for patch in \
  host-overlay/patch_rf02_request_deadline.py \
  host-overlay/patch_rf03_monotonic_stream_clock.py \
  host-overlay/patch_rf04_retry_budget.py \
  host-overlay/patch_rf05_fallback_fitness.py \
  host-overlay/patch_rf06_compression_preserve.py \
  host-overlay/patch_rf07_interim_report.py \
  host-overlay/patch_rf08_segment_continuation.py \
  host-overlay/patch_rf09_crash_windows.py \
  host-overlay/patch_rf10_stop_responsiveness.py
do
  python3 "$patch" "$TREE" || { echo "PATCH FAILED: $patch"; exit 1; }
done

ENG="$TREE/venv/bin/python"
echo "== probes on the composed tree"
failed=0
for probe in \
  host_rf04_retry_budget_probe \
  host_rf05_fallback_probe \
  host_rf07_interim_report_probe \
  host_rf08_segment_probe \
  host_rf09_crash_probe \
  host_rf10_stop_probe \
  host_rf02_deadline_probe \
  host_rf_monotonic_probe
do
  [ -f "tools/$probe.py" ] || { echo "skip (absent): $probe"; continue; }
  timeout 300 "$ENG" "tools/$probe.py" "$TREE" > "/tmp/st1_$probe.txt" 2>&1
  rc=$?
  echo "$probe RC=$rc  $(tail -1 /tmp/st1_$probe.txt | head -c 90)"
  [ "$rc" -eq 0 ] || failed=1
done

echo "== engine regression probe (13 expected)"
timeout 420 "$ENG" tools/host_engine_regression_probe.py "$TREE" \
  tests/agent/test_stream_wait_notice.py tests/agent/test_nonstream_wait_notice.py 2>&1 | tail -2
[ "${PIPESTATUS[0]}" -eq 0 ] || failed=1

echo "== RF-01 fixture / raw-data layer (no model, no cost)"
mkdir -p outputs/rf01
timeout 420 "$ENG" tools/rf_measure.py --out outputs/rf01 2>&1 | tail -6
[ "${PIPESTATUS[0]}" -eq 0 ] || failed=1

echo "== stage 1 done (failed=$failed) tree=$TREE"
exit "$failed"
