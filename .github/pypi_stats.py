#!/usr/bin/env python3
"""Keep an all-time record of PyPI downloads.

pypistats.org keeps only the last 180 days and does not allow a web page to
read it from the browser (no CORS header), so the clone-stats workflow runs
this daily: it merges the daily downloads (without mirrors) into pypi.json
(kept on the 'stats' branch), which the web page reads.

    curl -s https://pypistats.org/api/packages/PACKAGE/overall?mirrors=false \
        | python pypi_stats.py pypi.json
"""
import json
import sys
from datetime import datetime, timezone


def main():
    path = sys.argv[1]
    try:
        data = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        data = {"days": {}}
    for day in json.load(sys.stdin)["data"]:
        if day["category"] != "without_mirrors":
            continue
        # A day can be revised upwards while it is recent; it never shrinks.
        data["days"][day["date"]] = max(data["days"].get(day["date"], 0), day["downloads"])
    data["days"] = dict(sorted(data["days"].items()))
    data["count"] = sum(data["days"].values())
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    print(f"{path}: {data['count']} PyPI downloads over {len(data['days'])} days")


if __name__ == "__main__":
    main()
