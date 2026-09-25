# godsv-cu-x11 — the isolated Computer-Use display the ContainerRunner expects
#
# Why this file exists: `src/skaldir/computer_use.py` (`ContainerRunner`, the
# `--runner container-gui` bridge) targets the local image `localhost/godsv-cu-x11:latest` and
# runs inside it:
#
#     podman run -d --rm --network none --security-opt no-new-privileges <image> sleep 3600
#     podman exec <container> sh -c "(Xvfb :90 -screen 0 800x600x24 -nolisten tcp &); sleep 2; echo XVFB_READY"
#
# and then drives it with `xdotool` (mousemove / key / type), reads window names back
# (`xdotool search --name . getwindowname %@`) and captures the screen with `xwd`.
# Without this image `skaldir act check` reports the `container-gui` bridge as unavailable
# ("image missing") and no real GUI step can be executed — the anchors stay declared data.
#
# Build (rootless podman, no host display is ever touched):
#     podman build -t localhost/godsv-cu-x11:latest -f docker/cu-x11.Containerfile docker/
#
# The container is disposable (`--rm`), has no network and no new privileges; it only ever gets
# a virtual display, never the host's. `ContainerRunner.close()` removes exactly the container it
# started (by its own name) and reports whether it is really gone — `act run` and `compose run`
# publish that as `cleanup`, so a leaked container is visible instead of silent.
FROM docker.io/library/alpine:latest

# Xvfb = the virtual display, xdotool = the input driver the runner speaks, xmessage/xterm = a real
# window to observe (so observation reads actual application state), xwd = a real screen capture
# (the `observe.screenshot` perception channel), base64 = how the dump travels out of the
# container as text on stdout, ttf-dejavu = fonts, otherwise windows render as empty boxes and
# title reads get unreliable.
#
# xdg-open: the builtin `open_browser` sub-tool launches via `xdg-open about:blank` (report 53).
# Without a resolver the launch is a silent no-op and nothing ever appears on :90. Firefox itself
# does not ship an xdg-open, so the image provides the 2-line shim at /usr/local/bin/xdg-open —
# on PATH ahead of /usr/bin, so `xdg-open` resolves without dragging in the whole xdg-utils
# desktop stack (which needs a real session anyway).
#
# Why xwd and not import/scrot/gnome-screenshot: this image deliberately stays small. `xwd` is a
# real screen dump, and the XWD bytes are decoded into a PNG **on the host, by the application
# itself** (`computer_use.decode_xwd` / `xwd_to_png`, standard library only, no Pillow, no
# ImageMagick) — so the screenshot capability costs no extra package on either side (GAP-K2).
#
# NOTE: no `|| true` here on purpose. A swallowed apk failure once produced a "successfully built"
# image without xdotool/xmessage, and the only symptom was a probe that saw no windows. The package
# list is verified in the same RUN so a build that cannot do its job fails loudly instead. `base64`
# is checked too: without it a capture would look like "no screenshot tool" while xwd is installed.
RUN set -eu; \
    apk add --no-cache \
        xvfb \
        xdotool \
        xmessage \
        xterm \
        xwd \
        ttf-dejavu \
        firefox \
        bash; \
    for binary in Xvfb xdotool xmessage xterm xwd base64 firefox; do \
        command -v "$binary" >/dev/null || { echo "missing binary after install: $binary" >&2; exit 1; }; \
    done; \
    fc-cache -f >/dev/null 2>&1 || true; \
    # The builtin `open_browser` sub-tool launches via `xdg-open about:blank` (report 53).
    # Firefox ships no xdg-open and the image deliberately stays small, so this 2-line shim
    # (which just runs firefox) is what makes `xdg-open` resolve inside the container.
    printf '#!/bin/sh\nexec firefox "$@"\n' > /usr/local/bin/xdg-open; \
    chmod +x /usr/local/bin/xdg-open; \
    command -v xdg-open >/dev/null || { echo "missing xdg-open shim" >&2; exit 1; }

# The runner starts the container with `sleep 3600` and starts Xvfb itself via `podman exec`,
# so the image only has to be able to idle and to provide the binaries above.
CMD ["sleep", "3600"]
