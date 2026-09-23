#!/usr/bin/env python3
"""Edward 发布监测：HN 帖子 + GitHub stars + PyPI 下载量，7 天北极星。

Usage:
  python3 tools/launch_monitor.py [--hn-url HN_ITEM_URL]
每次运行追加一行 JSON 到 ~/.edward-notes/launch-metrics.jsonl
"""
import argparse
import json
import time
import urllib.request

REPO = "VeridicalTech/Edward"
PACKAGE = "edward-guard"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "edward-launch-monitor/0.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def hn_story(hn_url_or_id):
    item_id = hn_url_or_id.rstrip("/").split("item?id=")[-1]
    d = get(f"https://hn.algolia.com/api/v1/items/{item_id}")
    return {"hn_points": d.get("points"), "hn_comments": d.get("children") and
            sum(1 for _ in iter_comments(d))}


def iter_comments(node):
    yield node
    for c in node.get("children", []):
        yield from iter_comments(c)


def github():
    d = get(f"https://api.github.com/repos/{REPO}")
    return {"stars": d["stargazers_count"], "forks": d["forks_count"],
            "open_issues": d["open_issues_count"]}


def pypi_downloads():
    try:
        d = get(f"https://pypistats.org/api/packages/{PACKAGE}/recent")
        return {"pypi_downloads_recent": d["data"]["last_week"]}
    except Exception:
        return {"pypi_downloads_recent": None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hn-url", default="", help="HN item URL, e.g. https://news.ycombinator.com/item?id=...")
    a = ap.parse_args()

    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    try:
        rec.update(github())
    except Exception as e:
        rec["github_error"] = str(e)
    rec.update(pypi_downloads())
    if a.hn_url:
        try:
            rec.update(hn_story(a.hn_url))
        except Exception as e:
            rec["hn_error"] = str(e)

    import os as _os
    out = _os.environ.get("EDWARD_METRICS_FILE",
                           _os.path.expanduser("~/.edward-notes/launch-metrics.jsonl"))
    with open(out, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec, indent=2))
    print(f"\nappended -> {out}")


if __name__ == "__main__":
    main()
