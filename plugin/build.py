#!/usr/bin/env python3
"""Build the KiCad Plugin and Content Manager package.

    python plugin/build.py   ->  build/kicad-splitplane-<version>-pcm.zip
                                 build/pcm/<identifier>/metadata.json + icon.png

The zip is what users install (Plugin and Content Manager > Install from File,
or from the official repository once listed). build/pcm/ is what goes into a
merge request to https://gitlab.com/kicad/addons/metadata: copy the
<identifier> folder to packages/ there. The metadata points to the GitHub
release asset, so upload the zip there under exactly this name (the GitHub
workflow does that when you push a tag v<version>).

The zip is reproducible (fixed timestamps and order), so the sha256 written
here matches the zip CI builds from the same sources.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORE = os.path.join(ROOT, "kicad_splitplane.py")
REPO = "https://github.com/atuzlak/kicad-splitplane"

PLUGIN_FILES = ["plugin.json", "requirements.txt", "splitplane_action.py",
                "icons/splitplane-24.png", "icons/splitplane-48.png",
                "icons/splitplane-dark-24.png", "icons/splitplane-dark-48.png"]
ICON = os.path.join(HERE, "icons", "splitplane-64.png")
EPOCH = (2026, 1, 1, 0, 0, 0)


def add(z, name, data):
    info = zipfile.ZipInfo(name, EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix, as on CI; Windows would write 0
    info.external_attr = 0o644 << 16
    z.writestr(info, data)


def read(path):
    # Normalise line endings so a Windows checkout builds the same zip as CI.
    data = open(path, "rb").read()
    return data if path.endswith(".png") else data.replace(b"\r\n", b"\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", help="download URL of the zip (default: GitHub release asset)")
    args = ap.parse_args()

    version = re.search(r'__version__ = "([^"]+)"', open(CORE, encoding="utf-8").read()).group(1)
    metadata = json.load(open(os.path.join(HERE, "metadata.template.json"), encoding="utf-8"))
    metadata["versions"][0]["version"] = version
    ident = metadata["identifier"]
    name = f"kicad-splitplane-{version}-pcm.zip"

    out_dir = os.path.join(ROOT, "build")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name)
    with zipfile.ZipFile(out, "w") as z:
        add(z, "metadata.json", json.dumps(metadata, indent=2) + "\n")
        for f in PLUGIN_FILES:
            add(z, f"plugins/{f}", read(os.path.join(HERE, f)))
        add(z, "plugins/kicad_splitplane.py", read(CORE))
        add(z, "resources/icon.png", read(ICON))
        install_size = sum(i.file_size for i in z.infolist())

    data = open(out, "rb").read()
    metadata["versions"][0].update(
        download_url=args.url or f"{REPO}/releases/download/v{version}/{name}",
        download_sha256=hashlib.sha256(data).hexdigest(),
        download_size=len(data),
        install_size=install_size)
    pcm = os.path.join(out_dir, "pcm", ident)
    os.makedirs(pcm, exist_ok=True)
    with open(os.path.join(pcm, "metadata.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(metadata, indent=2) + "\n")
    shutil.copyfile(ICON, os.path.join(pcm, "icon.png"))

    print(f"wrote {os.path.relpath(out, ROOT)}")
    print(f"wrote {os.path.relpath(pcm, ROOT)}/metadata.json and icon.png")
    print(f"  download_url    {metadata['versions'][0]['download_url']}")
    print(f"  download_sha256 {metadata['versions'][0]['download_sha256']}")
    print(f"  download_size   {len(data)}")
    print(f"  install_size    {install_size}")


if __name__ == "__main__":
    main()
