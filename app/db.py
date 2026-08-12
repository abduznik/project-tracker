"""SQLite storage for the project tracker."""
import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('pypi', 'github')),
    identifier TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    downloads INTEGER NOT NULL DEFAULT 0,
    recent_30d INTEGER NOT NULL DEFAULT 0,
    stars INTEGER NOT NULL DEFAULT 0,
    forks INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_project ON snapshots(project_id, collected_at);
"""

_conn: sqlite3.Connection | None = None


def init(db_path: str | Path) -> None:
    global _conn
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(_SCHEMA)
    _conn.commit()


def _c() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db not initialized")
    return _conn


def count_projects() -> int:
    return _c().execute("SELECT COUNT(*) FROM projects").fetchone()[0]


def seed(seed_path: str | Path) -> int:
    """Import projects from a JSON file if the projects table is empty."""
    items = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    n = 0
    for it in items:
        try:
            add_project(it["name"], it["type"], it["identifier"])
            n += 1
        except sqlite3.IntegrityError:
            pass  # already exists
    return n


def add_project(name: str, ptype: str, identifier: str) -> dict:
    if ptype not in ("pypi", "github"):
        raise ValueError("type must be 'pypi' or 'github'")
    cur = _c().execute(
        "INSERT INTO projects (name, type, identifier) VALUES (?, ?, ?)",
        (name.strip(), ptype, identifier.strip()),
    )
    _c().commit()
    return get_project(cur.lastrowid)


def delete_project(pid: int) -> bool:
    cur = _c().execute("DELETE FROM projects WHERE id = ?", (pid,))
    _c().commit()
    return cur.rowcount > 0


def get_project(pid: int) -> dict | None:
    row = _c().execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def list_projects() -> list[dict]:
    rows = _c().execute("SELECT * FROM projects ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def insert_snapshot(pid: int, downloads: int, recent_30d: int, stars: int, forks: int,
                    status: str = "ok", note: str | None = None) -> None:
    _c().execute(
        "INSERT INTO snapshots (project_id, downloads, recent_30d, stars, forks, status, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, int(downloads), int(recent_30d), int(stars), int(forks), status, note),
    )
    _c().commit()


def get_latest_snapshots(pid: int, limit: int = 2) -> list[dict]:
    rows = _c().execute(
        "SELECT * FROM snapshots WHERE project_id = ? ORDER BY collected_at DESC, id DESC LIMIT ?",
        (pid, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_history(pid: int) -> list[dict]:
    rows = _c().execute(
        "SELECT collected_at, downloads, recent_30d, stars, forks, status, note "
        "FROM snapshots WHERE project_id = ? ORDER BY collected_at ASC, id ASC",
        (pid,),
    ).fetchall()
    return [dict(r) for r in rows]


def needs_refresh(pid: int, hours: float) -> bool:
    """True when a project has no snapshot, a failed snapshot, or a stale one."""
    rows = _c().execute(
        "SELECT collected_at, status FROM snapshots "
        "WHERE project_id = ? ORDER BY collected_at DESC, id DESC LIMIT 1",
        (pid,),
    ).fetchall()
    if not rows:
        return True
    row = rows[0]
    if row["status"] != "ok":
        return True
    import datetime as _dt
    try:
        ts = _dt.datetime.strptime(row["collected_at"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (_dt.datetime.utcnow() - ts).total_seconds() > hours * 3600
