#!/usr/bin/env python3
"""Keep an all-time record of GitHub clones.

GitHub's traffic API only returns the last 14 days and needs a token with
access to the repository, so the web page cannot ask it directly. The
clone-stats workflow runs this daily: it merges the 14 days into clones.json
(kept on the 'stats' branch), which the web page reads.

    gh api repos/OWNER/REPO/traffic/clones | python clone_stats.py clones.json
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
    for day in json.load(sys.stdin)["clones"]:
        old = data["days"].get(day["timestamp"][:10], {"count": 0, "uniques": 0})
        # Today's numbers still grow; a day never shrinks.
        data["days"][day["timestamp"][:10]] = {"count": max(old["count"], day["count"]),
                                               "uniques": max(old["uniques"], day["uniques"])}
    data["days"] = dict(sorted(data["days"].items()))
    data["count"] = sum(d["count"] for d in data["days"].values())
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1)
        f.write("\n")
    print(f"{path}: {data['count']} clones over {len(data['days'])} days")


if __name__ == "__main__":
    main()
