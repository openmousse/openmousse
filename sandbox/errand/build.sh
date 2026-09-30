#!/usr/bin/env bash
# Builds the errand Agent's two sandbox images (see README in this folder):
#   openclaw-sandbox:bookworm-slim          OpenClaw's default sandbox image (skipped when present, --force rebuilds)
#   openclaw-sandbox-browser:bookworm-slim  OpenClaw's browser image, from the OpenClaw source matching the installed version
#   mousse-errand:bookworm-slim             shell sandbox + Doorman's CA as the only trusted root
#   mousse-errand-browser:bookworm-slim     browser + Chromium forced through Doorman
# Usage: build.sh <sentinel dir with ca.pem and ca.spki> [--force]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SENT="${1:?sentinel dir (ca.pem, ca.spki)}"
FORCE="${2:-}"
[ -s "$SENT/ca.pem" ] && [ -s "$SENT/ca.spki" ] || { echo "no ca.pem / ca.spki in $SENT (start Doorman once first)" >&2; exit 1; }
have() { docker image inspect "$1" >/dev/null 2>&1; }
CTX="$(mktemp -d)"; trap 'rm -rf "$CTX"' EXIT

if [ "$FORCE" = --force ] || ! have openclaw-sandbox:bookworm-slim; then
  docker build -q -t openclaw-sandbox:bookworm-slim -f "$HERE/Dockerfile.base" "$HERE"
fi

if [ "$FORCE" = --force ] || ! have openclaw-sandbox-browser:bookworm-slim; then
  VER="$(openclaw --version | sed -E 's/^OpenClaw ([0-9.]+).*/\1/')"
  RAW="https://raw.githubusercontent.com/openclaw/openclaw/v$VER"
  mkdir -p "$CTX/oc/scripts/docker/sandbox"
  curl -fsSL "$RAW/scripts/docker/sandbox/Dockerfile.browser" -o "$CTX/oc/scripts/docker/sandbox/Dockerfile.browser"
  curl -fsSL "$RAW/scripts/sandbox-browser-entrypoint.sh" -o "$CTX/oc/scripts/sandbox-browser-entrypoint.sh"
  docker build -q -t openclaw-sandbox-browser:bookworm-slim -f "$CTX/oc/scripts/docker/sandbox/Dockerfile.browser" "$CTX/oc"
fi

mkdir -p "$CTX/errand" && cp -r "$HERE/." "$CTX/errand/"
cp "$SENT/ca.pem" "$CTX/errand/ca.pem"
SPKI="$(tr -d '\n' < "$SENT/ca.spki")"
docker build -q -t mousse-errand:bookworm-slim --label "org.openmousse.errand.ca-spki=$SPKI" -f "$CTX/errand/Dockerfile" "$CTX/errand"
docker build -q -t mousse-errand-browser:bookworm-slim --build-arg "CA_SPKI=$SPKI" --label "org.openmousse.errand.ca-spki=$SPKI" \
  -f "$CTX/errand/browser/Dockerfile" "$CTX/errand"
docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'openclaw-sandbox|mousse-errand'
