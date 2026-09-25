#!/bin/sh
# Rebuild localhost/godsv-cu-x11 with firefox, smoke-test in a throwaway container,
# then retag over the old image. Never deletes the old image before the new one passes.
set -eu
cd /home/willy/projects/gods-voice

echo "== build =="
podman build --layers -t localhost/godsv-cu-x11:rc -f docker/cu-x11.Containerfile docker/ 2>&1 | tail -20

echo "== smoke test in throwaway container =="
podman run -d --rm --network none --security-opt no-new-privileges \
    --name gv-cu-smoke localhost/godsv-cu-x11:rc sleep 300
trap 'podman rm -f gv-cu-smoke >/dev/null 2>&1 || true' EXIT

podman exec gv-cu-smoke sh -c 'command -v firefox Xvfb xdotool xterm xmessage xwd base64'
podman exec gv-cu-smoke sh -c '(Xvfb :90 -screen 0 800x600x24 -nolisten tcp &); sleep 2; echo XVFB_READY'
podman exec -e DISPLAY=:90 gv-cu-smoke sh -c 'firefox --display :90 >/dev/null 2>&1 & sleep 12; xdotool search --name . getwindowname %@'
podman exec gv-cu-smoke sh -c 'DISPLAY=:90 xwd -root -silent | base64 -w0 | head -c 60; echo'
podman exec gv-cu-smoke sh -c 'DISPLAY=:90 xdotool search --name Mozilla windowfocus --sync %@ && echo FOCUS_OK'

echo "== replace: retag rc -> latest =="
podman tag localhost/godsv-cu-x11:rc localhost/godsv-cu-x11:latest
echo done
