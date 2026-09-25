#!/bin/bash
# Wave 2 probe: does the @goal path (the route the composer middleware calls) work?
# The panel token stays in a shell variable and is never printed.
set -u
AUDIT_HOME="$HOME/.hermes/godsv/audit-home"
SERVE_PID=$(pgrep -f 'hermes serve' | head -1)
[ -n "${SERVE_PID:-}" ] || { echo "no serve process"; exit 2; }

TOKEN=$(tr '\0' '\n' < "/proc/$SERVE_PID/environ" 2>/dev/null \
  | grep -E 'HERMES_(DASHBOARD_)?SESSION_TOKEN=' | head -1 | cut -d= -f2-)
[ -n "${TOKEN:-}" ] || { echo "no panel token in the backend env"; exit 4; }

# The app and its backend SHARE the Electron debug port, and parsing `ss` owner lists proved
# fragile (two wrong ports in a row). Ask the ports instead: the API port is the loopback
# listener that answers our plugin route. 9333 is the debug port, never the API.
PORT=""
for candidate in $(ss -ltn 2>/dev/null | grep '127.0.0.1:' | awk '{print $4}' \
                   | sed -E 's/.*127\.0\.0\.1:([0-9]+)$/\1/' | sort -u); do
  [ "$candidate" = "9333" ] && continue
  code=$(curl -s -o /dev/null -m 2 -w '%{http_code}' -H "X-Hermes-Session-Token: $TOKEN" \
         "http://127.0.0.1:$candidate/api/plugins/godsv/health" || true)
  case "$code" in 200|401|403) PORT="$candidate"; break ;; esac
done

echo "serve pid : $SERVE_PID"
echo "port      : ${PORT:-unknown}"
echo "token     : present (value never printed)"
[ -n "${PORT:-}" ] || { echo "no API port answered"; exit 3; }

BASE="http://127.0.0.1:$PORT/api/plugins/godsv"
AUTH="X-Hermes-Session-Token: $TOKEN"

echo
echo "--- GET  /health"
curl -s -H "$AUTH" "$BASE/health" | head -c 200; echo
echo "--- GET  /completion (before: goal state the UI polls)"
curl -s -H "$AUTH" "$BASE/completion" | head -c 300; echo
echo "--- POST /completion/criteria (the route the @goal middleware calls)"
curl -s -o /tmp/gv_w2_criteria.json -w '  status=%{http_code}\n' -X POST \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"goal":"Wave 2 probe: state the current git short SHA of the gods-voice repository","criteria":["the reported SHA equals the repository HEAD"]}' \
  "$BASE/completion/criteria"
head -c 400 /tmp/gv_w2_criteria.json; echo
echo "--- GET  /completion (after: contract + revision?)"
curl -s -H "$AUTH" "$BASE/completion" | head -c 600; echo
echo "--- events im Runtime"
python3 - "$AUDIT_HOME/godsv/events.db" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    print("  total:", con.execute("select count(*) from events").fetchone()[0])
    for kind, n in con.execute("select kind, count(*) from events group by kind order by 2 desc limit 10"):
        print(f"   {n:4d}  {kind}")
except Exception as exc:
    print("  db error:", exc)
PY
