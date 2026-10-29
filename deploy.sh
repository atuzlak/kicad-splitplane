#!/usr/bin/env bash
# Build the web page into dist/ and (optionally) upload it to your website.
#
#   ./deploy.sh                       -> only build dist/
#   ./deploy.sh --serve               -> build dist/ and preview it on localhost:8000
#   ./deploy.sh USER@HOST:public_html -> build and upload to <that>/kicad-splitplane/
#
# Uses rsync over SSH (falls back to scp). Asks for your password unless
# you have an SSH key set up. Nothing is stored.
set -euo pipefail
cd "$(dirname "$0")"

rm -rf dist && mkdir -p dist
cp -r web/index.html web/img dist/
echo "built dist/ ($(du -sh dist | cut -f1))"

if [ "${1:-}" = "--serve" ]; then
  echo "preview at http://127.0.0.1:8000/  (Ctrl+C to stop)"
  exec python3 -m http.server 8000 --bind 127.0.0.1 --directory dist
fi

if [ $# -ge 1 ]; then
  target="${1%/}/kicad-splitplane/"
  host="${1%%:*}"; path="${1#*:}"
  ssh "$host" "mkdir -p '${path%/}/kicad-splitplane'"
  if command -v rsync >/dev/null; then
    rsync -av --chmod=Du=rwx,Dgo=rx,Fu=rw,Fgo=r dist/ "$target"
  else
    scp -r dist/* "$target"
    ssh "$host" "chmod -R a+rX '${path%/}/kicad-splitplane'"
  fi
  echo "uploaded to $target"
fi
