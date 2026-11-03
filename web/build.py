#!/usr/bin/env python3
"""Build the web page as one self-contained file (images inlined).

    python web/build.py   ->  dist/kicad-splitplane.html
"""
import base64
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def inline(match):
    path = os.path.join(HERE, match.group(1))
    data = base64.b64encode(open(path, "rb").read()).decode("ascii")
    return f'src="data:image/png;base64,{data}"'


def main():
    html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
    html = re.sub(r'src="(img/[^"]+\.png)"', inline, html)
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "kicad-splitplane.html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"wrote {os.path.relpath(out, ROOT)} ({len(html) // 1024} KiB)")


if __name__ == "__main__":
    main()
