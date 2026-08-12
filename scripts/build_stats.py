#!/usr/bin/env python3
"""Build site/stats.json for the GitHub Pages demo.

Fetches fresh download stats for the demo project list (see demo_projects.json),
appends a snapshot to each project's rolling history, and writes the result to
site/stats.json. Runs in CI (GitHub Actions) on a schedule and on push.

Usage:  python scripts/build_stats.py
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR if APP_DIR.exists() else ROOT))

from collector import COLLECTORS  # noqa: E402

SITE_DIR = ROOT / "site"
CONFIG = ROOT / "scripts" / "demo_projects.json"
MAX_HISTORY = 40


def load_existing() -> dict:
    p = SITE_DIR / "stats.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"generated_at": None, "projects": []}


async def main() -> int:
    data = load_existing()
    prev_by_id = {pr["identifier"]: pr for pr in data["projects"]}
    config = json.loads(CONFIG.read_text(encoding="utf-8"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for idx, proj in enumerate(config):
        prev = prev_by_id.get(proj["identifier"])
        entry = {
            "id": (prev or {}).get("id", idx + 1),
            "name": proj["name"],
            "type": proj["type"],
            "identifier": proj["identifier"],
            "history": list((prev or {}).get("history", [])),
        }
        try:
            stats = await COLLECTORS[proj["type"]](proj["identifier"])
            snap = {"collected_at": now, "status": "ok", **stats}
        except Exception as exc:  # noqa: BLE001 — keep last good snapshot
            snap = {
                "collected_at": now, "status": "error",
                "downloads": 0, "recent_30d": 0, "stars": 0, "forks": 0,
                "note": f"{type(exc).__name__}: {exc}"[:120],
            }
        entry["history"].append(snap)
        entry["history"] = entry["history"][-MAX_HISTORY:]
        entry["latest"] = entry["history"][-1]
        entry["delta"] = None
        ok_history = [h for h in entry["history"] if h["status"] == "ok"]
        if len(ok_history) >= 2:
            a, b = ok_history[-1], ok_history[-2]
            entry["delta"] = {
                "downloads": a["downloads"] - b["downloads"],
                "recent_30d": a["recent_30d"] - b["recent_30d"],
                "stars": a["stars"] - b["stars"],
                "forks": a["forks"] - b["forks"],
            }
        out.append(entry)
        await asyncio.sleep(2)  # be gentle with pypistats.org rate limits

    data["projects"] = out
    data["generated_at"] = now
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "stats.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    ok = sum(1 for p in out if p["latest"]["status"] == "ok")
    print(f"stats.json updated: {len(out)} projects, {ok} ok, generated {now}")
    return 0 if ok == len(out) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
