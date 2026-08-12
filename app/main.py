"""Project Download Tracker — homelab dashboard for PyPI & GitHub download stats.

FastAPI app: serves a Chart.js dashboard on port 8095, stores snapshots in
SQLite (/data/tracker.db), collects stats every COLLECT_INTERVAL_HOURS.
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
from collector import COLLECTORS

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "tracker.db"
SEED_PATH = DATA_DIR / "seed_projects.json"
INDEX_HTML = Path(__file__).parent / "templates" / "index.html"
INTERVAL_H = float(os.environ.get("COLLECT_INTERVAL_HOURS", "6"))

_lock = asyncio.Lock()


async def collect_project(proj: dict) -> None:
    """Collect one project and store a snapshot. Errors become error snapshots."""
    try:
        stats = await COLLECTORS[proj["type"]](proj["identifier"])
        db.insert_snapshot(proj["id"], status="ok", **stats)
    except Exception as exc:  # noqa: BLE001 — record any failure for visibility
        note = f"{type(exc).__name__}: {exc}"[:200]
        db.insert_snapshot(proj["id"], status="error", downloads=0,
                           recent_30d=0, stars=0, forks=0, note=note)


async def collect_all() -> dict:
    async with _lock:
        projects = db.list_projects()
        for proj in projects:
            await collect_project(proj)
    return {"collected": len(projects)}


async def collect_refresh_needed() -> dict:
    """Retry failed/stale projects on a fast cycle (self-healing)."""
    async with _lock:
        n = 0
        for proj in db.list_projects():
            if db.needs_refresh(proj["id"], hours=INTERVAL_H):
                await collect_project(proj)
                n += 1
    return {"collected": n}


async def _scheduler() -> None:
    await asyncio.sleep(3)  # let the server boot / volumes settle
    await collect_all()
    while True:
        await asyncio.sleep(15 * 60)  # fast retry loop for failures
        await collect_refresh_needed()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(DB_PATH)
    if db.count_projects() == 0 and SEED_PATH.exists():
        db.seed(SEED_PATH)
    task = asyncio.create_task(_scheduler())
    yield
    task.cancel()


app = FastAPI(title="Project Download Tracker", lifespan=lifespan)


class ProjectIn(BaseModel):
    name: str
    type: str
    identifier: str


def _with_latest(proj: dict) -> dict:
    snaps = db.get_latest_snapshots(proj["id"], limit=2)
    out = dict(proj)
    out["latest"] = snaps[0] if snaps else None
    out["delta"] = None
    if len(snaps) == 2 and snaps[0]["status"] == "ok":
        out["delta"] = {
            "downloads": snaps[0]["downloads"] - snaps[1]["downloads"],
            "recent_30d": snaps[0]["recent_30d"] - snaps[1]["recent_30d"],
            "stars": snaps[0]["stars"] - snaps[1]["stars"],
            "forks": snaps[0]["forks"] - snaps[1]["forks"],
        }
    return out


@app.get("/")
async def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/projects")
async def api_projects():
    return [_with_latest(p) for p in db.list_projects()]


@app.get("/api/history/{pid}")
async def api_history(pid: int):
    if not db.get_project(pid):
        raise HTTPException(404, "project not found")
    return db.get_history(pid)


@app.post("/api/projects")
async def api_add(proj: ProjectIn):
    if proj.type not in COLLECTORS:
        raise HTTPException(400, "type must be 'pypi' or 'github'")
    try:
        created = db.add_project(proj.name, proj.type, proj.identifier)
    except Exception as exc:  # noqa: BLE001 — unique constraint etc.
        raise HTTPException(409, str(exc)) from exc
    asyncio.create_task(collect_project(created))  # fetch stats right away
    return _with_latest(created)


@app.delete("/api/projects/{pid}")
async def api_delete(pid: int):
    if not db.delete_project(pid):
        raise HTTPException(404, "project not found")
    return {"ok": True}


@app.post("/api/refresh")
async def api_refresh():
    asyncio.create_task(collect_all())
    return {"ok": True, "started": True}
