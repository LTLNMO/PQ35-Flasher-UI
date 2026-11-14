#!/bin/sh
# Installs the AI command-line tools PQ35 Flasher can use with your own
# subscription instead of an API key:
#   Claude Code  (Claude Pro/Max)     @anthropic-ai/claude-code
#   Gemini CLI   (Google account)     @google/gemini-cli
#   Codex CLI    (ChatGPT account)    @openai/codex
#
# install.sh runs this; you can also run it on its own to install or update
# them. Everything goes into PQ35 Flasher's own folder: no root, nothing
# system-wide. Uses your Node.js if it's new enough, else downloads a private
# copy of the current Node.js LTS (checksum-verified).
set -eu

OS=$(uname)
if [ "$OS" = "Darwin" ]; then
    BASE="$HOME/Library/Application Support/PQ35-Flasher-UI"
else
    BASE="${XDG_DATA_HOME:-$HOME/.local/share}/pq35-flasher-ui"
fi
NODE_DIR="$BASE/node"
TOOLS_DIR="$BASE/ai-tools"
PACKAGES="@anthropic-ai/claude-code @google/gemini-cli @openai/codex"
NODE_MIN=20

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$1" >&2; }

# A JSON-capable Python: the app's own (installed by install.sh) or any python3.
PY="${PQ35_PYTHON:-}"
[ -n "$PY" ] || PY=$(command -v python3 || true)
[ -n "$PY" ] || { warn "Python 3 is needed to set up the AI tools; skipping."; exit 1; }

node_major() { "$1" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }

NODE=""
if [ -x "$NODE_DIR/bin/node" ]; then
    NODE="$NODE_DIR/bin/node"
elif command -v node >/dev/null && [ "$(node_major node)" -ge "$NODE_MIN" ]; then
    NODE=$(command -v node)
fi

if [ -z "$NODE" ]; then
    case "$OS-$(uname -m)" in
        Darwin-arm64) PLATFORM=darwin-arm64 ;;
        Darwin-x86_64) PLATFORM=darwin-x64 ;;
        Linux-x86_64) PLATFORM=linux-x64 ;;
        Linux-aarch64|Linux-arm64) PLATFORM=linux-arm64 ;;
        *) warn "No Node.js download for $OS $(uname -m); install Node.js $NODE_MIN+ and re-run."; exit 1 ;;
    esac
    step "Finding the current Node.js LTS"
    VERSION=$(curl -fsSL https://nodejs.org/dist/index.json | "$PY" -c '
import json, sys
print(next(r["version"] for r in json.load(sys.stdin) if r.get("lts")))')
    FILE="node-$VERSION-$PLATFORM.tar.gz"
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    step "Downloading Node.js $VERSION (private copy for the AI tools)"
    curl -fsSL "https://nodejs.org/dist/$VERSION/$FILE" -o "$TMP/$FILE"
    curl -fsSL "https://nodejs.org/dist/$VERSION/SHASUMS256.txt" -o "$TMP/SHASUMS256.txt"
    EXPECTED=$(grep " $FILE\$" "$TMP/SHASUMS256.txt" | cut -d' ' -f1)
    ACTUAL=$("$PY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$TMP/$FILE")
    [ -n "$EXPECTED" ] && [ "$EXPECTED" = "$ACTUAL" ] || { warn "Node.js download failed its checksum; not installing it."; exit 1; }
    rm -rf "$NODE_DIR"
    mkdir -p "$NODE_DIR"
    tar -xzf "$TMP/$FILE" -C "$NODE_DIR" --strip-components=1
    NODE="$NODE_DIR/bin/node"
fi

NODE_BIN=$(dirname "$NODE")
PATH="$NODE_BIN:$PATH"
export PATH
step "Installing/updating Claude Code, Gemini CLI and Codex CLI (Node.js $("$NODE" --version))"
mkdir -p "$TOOLS_DIR"
# shellcheck disable=SC2086  # PACKAGES is a word list on purpose
npm install --global --prefix "$TOOLS_DIR" --no-fund --no-audit --loglevel=error $PACKAGES

echo "AI tools are in $TOOLS_DIR/bin. PQ35 Flasher finds them by itself."
echo "Sign in once to the ones you'll use:"
echo "  \"$TOOLS_DIR/bin/claude\"   (Claude Pro/Max account)"
echo "  \"$TOOLS_DIR/bin/gemini\"   (choose Login with Google)"
echo "  \"$TOOLS_DIR/bin/codex\"    (choose Sign in with ChatGPT)"
echo "or use Settings → AI → Sign in… inside PQ35 Flasher."
