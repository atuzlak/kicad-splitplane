#!/usr/bin/env bash
# Build the web page and (optionally) upload it to your website.
#
#   ./deploy.sh                       -> only build dist/kicad-splitplane.html
#   ./deploy.sh --serve               -> build and preview it on localhost:8000
#   ./deploy.sh USER@HOST:public_html -> build and upload to <that>/kicad-splitplane.html
#                                        (plus img/comparison.png for link previews,
#                                        and <that>/kicad-splitplane, which forwards
#                                        the short address to the .html page)
#
# The page is a single file with the images inlined. Uses scp over SSH; asks
# for your password unless you have an SSH key set up. Nothing is stored.
set -euo pipefail
cd "$(dirname "$0")"

python3 web/build.py 2>/dev/null || python web/build.py

if [ "${1:-}" = "--serve" ]; then
  echo "preview at http://127.0.0.1:8000/kicad-splitplane.html  (Ctrl+C to stop)"
  exec python3 -m http.server 8000 --bind 127.0.0.1 --directory dist
fi

if [ $# -ge 1 ]; then
  host="${1%%:*}"; path="${1#*:}"; path="${path%/}"
  ssh "$host" "mkdir -p '$path/img'"
  scp dist/kicad-splitplane.html "$host:$path/kicad-splitplane.html"
  scp web/redirect.html "$host:$path/kicad-splitplane"
  scp web/img/comparison.png "$host:$path/img/comparison.png"
  ssh "$host" "chmod a+r '$path/kicad-splitplane.html' '$path/kicad-splitplane' '$path/img/comparison.png'; chmod a+rx '$path/img'"
  echo "uploaded to $host:$path/kicad-splitplane.html"
fi
