"""Collectors for project download stats.

- PyPI packages  -> pypistats.org public API (recent + overall series)
- GitHub repos   -> api.github.com (repo meta + release asset download counts)
"""
import asyncio

import httpx

UA = {"User-Agent": "project-download-tracker/1.0 (https://github.com/abduznik/project-tracker)"}

PYPI_RECENT = "https://pypistats.org/api/packages/{pkg}/recent"
PYPI_OVERALL = "https://pypistats.org/api/packages/{pkg}/overall"
GH_REPO = "https://api.github.com/repos/{repo}"
GH_RELEASES = "https://api.github.com/repos/{repo}/releases?per_page=100"


async def _get_json(client: httpx.AsyncClient, url: str, attempts: int = 2) -> dict:
    for i in range(attempts):
        try:
            resp = await client.get(url, headers=UA, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (429, 502, 503, 504) and i < attempts - 1:
                await asyncio.sleep(10)
                continue
            raise


async def collect_pypi(pkg: str) -> dict:
    """All-time downloads (with_mirrors series) + last 30 days."""
    async with httpx.AsyncClient() as client:
        recent = await _get_json(client, PYPI_RECENT.format(pkg=pkg))
        await asyncio.sleep(3)  # pypistats rate limit (~1 req/s)
        overall = await _get_json(client, PYPI_OVERALL.format(pkg=pkg))

    # Sum per-category totals; prefer with_mirrors (canonical on pypistats.org).
    by_cat: dict[str, int] = {}
    for row in overall.get("data", []):
        cat = row.get("category") or "with_mirrors"
        by_cat[cat] = by_cat.get(cat, 0) + int(row.get("downloads", 0))
    total = by_cat.get("with_mirrors") or (sum(by_cat.values()) if by_cat else 0)

    return {
        "downloads": total,
        "recent_30d": int((recent.get("data") or {}).get("last_month", 0)),
        "stars": 0,
        "forks": 0,
    }


async def collect_github(repo: str) -> dict:
    """Sum of release-asset download counts + stars/forks."""
    async with httpx.AsyncClient() as client:
        info = await _get_json(client, GH_REPO.format(repo=repo))
        releases = await _get_json(client, GH_RELEASES.format(repo=repo))

    total = sum(
        int(asset.get("download_count", 0))
        for rel in releases
        for asset in rel.get("assets", [])
    )
    return {
        "downloads": total,
        "recent_30d": 0,
        "stars": int(info.get("stargazers_count", 0)),
        "forks": int(info.get("forks_count", 0)),
    }


COLLECTORS = {"pypi": collect_pypi, "github": collect_github}
