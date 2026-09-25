#!/usr/bin/env bash
# Skaldir: user-local CLI installation. No desktop application or system changes.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' "Required: uv. Install it from https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  uv venv .venv --python 3.11
fi
printf '%s\n' "Installing Skaldir CLI and API support..."
uv pip install -e '.[api,mcp]' --python .venv/bin/python
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/.venv/bin/skaldir" "$HOME/.local/bin/skaldir"
.venv/bin/skaldir --version
printf '\n%s\n' \
  "Installed: $HOME/.local/bin/skaldir" \
  "Ensure $HOME/.local/bin is on PATH." \
  "Next: skaldir setup" \
  "Then: skaldir" \
  "Optional browser support: uv pip install -e '.[browser]' --python .venv/bin/python" \
  "Download Chromium: .venv/bin/python -m playwright install chromium" \
  "Optional desktop-control libraries: uv pip install -e '.[computer-use]' --python .venv/bin/python"
