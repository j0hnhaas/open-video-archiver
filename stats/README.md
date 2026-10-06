# Repository statistics

This directory contains a long-term archive of aggregate GitHub repository statistics for **Open Video Archiver**.

The file `repository-stats.json` is updated daily by the GitHub Actions workflow `Archive repository statistics`.

It records:

- GitHub repository views and unique visitors by day
- repository clones and unique cloners by day
- release-asset download counters
- stars
- forks
- subscribers
- open issues

## Privacy

No third-party analytics service is used. The workflow reads aggregate statistics from GitHub's own APIs and stores the resulting counters in this repository. It does not collect IP addresses or identify individual visitors.

## Traffic API token

GitHub's Views and Clones API requires a token with read access to the repository's **Administration** permission. The workflow therefore expects a fine-grained personal access token stored as the repository Actions secret:

`TRAFFIC_TOKEN`

Recommended scope:

- Repository access: only `j0hnhaas/open-video-archiver`
- Repository permission: **Administration — Read-only**

The token is used only for the GitHub traffic endpoints. Release downloads and ordinary repository counters use the workflow's standard `GITHUB_TOKEN`.

If `TRAFFIC_TOKEN` is absent or invalid, the workflow still archives release downloads and ordinary repository counters; the snapshot records that traffic statistics were unavailable.

## Schedule

The workflow runs once per day and can also be started manually from the repository's **Actions** tab.
