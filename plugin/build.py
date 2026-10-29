#!/usr/bin/env python3
"""Build the KiCad Plugin and Content Manager package.

    python plugin/build.py   ->  build/kicad-splitplane-<version>-pcm.zip

Install it in KiCad with Plugin and Content Manager > Install from File.
"""
import hashlib
import json
import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "kicad_splitplane.py")

PLUGIN_FILES = ["plugin.json", "requirements.txt", "splitplane_action.py",
                "icons/splitplane-24.png", "icons/splitplane-48.png",
                "icons/splitplane-dark-24.png", "icons/splitplane-dark-48.png"]


def main():
    version = re.search(r'__version__ = "([^"]+)"', open(CORE, encoding="utf-8").read()).group(1)
    metadata = json.load(open(os.path.join(HERE, "metadata.template.json"), encoding="utf-8"))
    metadata["versions"][0]["version"] = version

    out_dir = os.path.join(ROOT, "build")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"kicad-splitplane-{version}-pcm.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.json", json.dumps(metadata, indent=2) + "\n")
        for f in PLUGIN_FILES:
            z.write(os.path.join(HERE, f), f"plugins/{f}")
        z.write(CORE, "plugins/kicad_splitplane.py")
        z.write(os.path.join(HERE, "icons", "splitplane-64.png"), "resources/icon.png")
        install_size = sum(i.file_size for i in z.infolist())

    data = open(out, "rb").read()
    print(f"wrote {os.path.relpath(out, ROOT)}")
    print(f"  download_sha256 {hashlib.sha256(data).hexdigest()}")
    print(f"  download_size   {len(data)}")
    print(f"  install_size    {install_size}")


if __name__ == "__main__":
    main()
