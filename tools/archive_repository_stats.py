#!/usr/bin/env python3
"""Archive aggregate GitHub repository statistics without third-party analytics."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "https://api.github.com"
API_VERSION = "2022-11-28"
OUT = Path("stats/repository-stats.json")


def api_get(path: str, token: str | None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "open-video-archiver-stats",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{API}{path}", headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def collect_releases(repository: str, token: str | None) -> tuple[dict, int]:
    releases: dict[str, dict[str, int]] = {}
    total = 0
    page = 1

    while True:
        items = api_get(
            f"/repos/{repository}/releases?per_page=100&page={page}", token
        )
        if not isinstance(items, list):
            raise RuntimeError("Unexpected releases API response")

        for release in items:
            if release.get("draft"):
                continue
            tag = release.get("tag_name") or f"release-{release.get('id', 'unknown')}"
            assets: dict[str, int] = {}
            for asset in release.get("assets", []):
                count = int(asset.get("download_count", 0))
                assets[str(asset.get("name", "unnamed"))] = count
                total += count
            releases[tag] = assets

        if len(items) < 100:
            break
        page += 1

    return releases, total


def merge_traffic(target: dict, payload: object, key: str) -> None:
    if not isinstance(payload, dict):
        return
    rows = payload.get(key, [])
    if not isinstance(rows, list):
        return

    for row in rows:
        timestamp = str(row.get("timestamp", ""))
        day = timestamp[:10]
        if not day:
            continue
        target[day] = {
            "count": int(row.get("count", 0)),
            "uniques": int(row.get("uniques", 0)),
        }


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "j0hnhaas/open-video-archiver")
    github_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    traffic_token = os.environ.get("TRAFFIC_TOKEN")
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        data = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        data = {
            "schema": "ova-repository-stats/1.0",
            "repository": repository,
            "updated_at": None,
            "traffic": {"views": {}, "clones": {}},
            "snapshots": [],
        }

    repo = api_get(f"/repos/{repository}", github_token)
    if not isinstance(repo, dict):
        raise RuntimeError("Unexpected repository API response")

    releases, release_downloads_total = collect_releases(repository, github_token)

    traffic_status = "unavailable"
    traffic_error = None

    if traffic_token:
        try:
            views = api_get(f"/repos/{repository}/traffic/views?per=day", traffic_token)
            clones = api_get(f"/repos/{repository}/traffic/clones?per=day", traffic_token)
            merge_traffic(data.setdefault("traffic", {}).setdefault("views", {}), views, "views")
            merge_traffic(data.setdefault("traffic", {}).setdefault("clones", {}), clones, "clones")
            traffic_status = "available"
        except HTTPError as exc:
            traffic_status = "error"
            traffic_error = f"GitHub traffic API returned HTTP {exc.code}"
    else:
        traffic_error = "TRAFFIC_TOKEN is not configured"

    snapshot = {
        "date": today,
        "stars": int(repo.get("stargazers_count", 0)),
        "forks": int(repo.get("forks_count", 0)),
        "subscribers": int(repo.get("subscribers_count", 0)),
        "open_issues": int(repo.get("open_issues_count", 0)),
        "release_downloads_total": release_downloads_total,
        "release_assets": releases,
        "traffic_status": traffic_status,
    }
    if traffic_error:
        snapshot["traffic_note"] = traffic_error

    snapshots = [
        item for item in data.get("snapshots", [])
        if item.get("date") != today
    ]
    snapshots.append(snapshot)
    snapshots.sort(key=lambda item: item.get("date", ""))

    data["schema"] = "ova-repository-stats/1.0"
    data["repository"] = repository
    data["updated_at"] = now.isoformat().replace("+00:00", "Z")
    data["snapshots"] = snapshots

    OUT.write_text(
        json.dumps(data, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    print(f"Updated {OUT}")
    print(
        f"{today}: stars={snapshot['stars']}, forks={snapshot['forks']}, "
        f"release_downloads={release_downloads_total}, traffic={traffic_status}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
