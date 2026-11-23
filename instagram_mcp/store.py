"""Local SQLite history, so analytics are never limited to a 30-day window.

Every snapshot stores followers and each post's metrics at that moment.
Over time this gives growth curves Instagram itself doesn't keep for you.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_snapshots (
    ts INTEGER NOT NULL,
    username TEXT,
    followers INTEGER,
    follows INTEGER,
    media_count INTEGER
);
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    caption TEXT,
    media_type TEXT,
    product_type TEXT,
    permalink TEXT,
    posted_at TEXT
);
CREATE TABLE IF NOT EXISTS media_snapshots (
    ts INTEGER NOT NULL,
    media_id TEXT NOT NULL,
    views INTEGER, reach INTEGER, likes INTEGER, comments INTEGER,
    shares INTEGER, saved INTEGER, total_interactions INTEGER,
    avg_watch_ms INTEGER,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_media_snap ON media_snapshots(media_id, ts);
CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_path TEXT NOT NULL,
    caption TEXT,
    options TEXT,
    publish_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | publishing | published | failed | cancelled
    result TEXT,
    created_at INTEGER NOT NULL
);
"""


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(config.DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def save_profile(p: dict, ts: int | None = None) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO profile_snapshots VALUES (?,?,?,?,?)",
            (ts or int(time.time()), p.get("username"), p.get("followers_count"),
             p.get("follows_count"), p.get("media_count")),
        )


def save_media(m: dict) -> None:
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?)",
            (m["id"], m.get("caption"), m.get("media_type"), m.get("media_product_type"),
             m.get("permalink"), m.get("timestamp")),
        )


def save_media_snapshot(media_id: str, ins: dict, ts: int | None = None) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO media_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts or int(time.time()), media_id, _int(ins.get("views")), _int(ins.get("reach")),
             _int(ins.get("likes")), _int(ins.get("comments")), _int(ins.get("shares")),
             _int(ins.get("saved")), _int(ins.get("total_interactions")),
             _int(ins.get("ig_reels_avg_watch_time")), json.dumps(ins)),
        )


def follower_history(days: int = 365) -> list[dict]:
    since = int(time.time()) - days * 86400
    with db() as con:
        rows = con.execute(
            "SELECT ts, followers, follows, media_count FROM profile_snapshots "
            "WHERE ts >= ? ORDER BY ts", (since,),
        ).fetchall()
    return [dict(r) for r in rows]


def media_history(media_id: str) -> list[dict]:
    with db() as con:
        rows = con.execute(
            "SELECT ts, views, reach, likes, comments, shares, saved, total_interactions, "
            "avg_watch_ms FROM media_snapshots WHERE media_id = ? ORDER BY ts", (media_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_media_metrics() -> list[dict]:
    """Newest snapshot per post, joined with the post's details."""
    with db() as con:
        rows = con.execute(
            """
            SELECT m.id, m.caption, m.product_type, m.permalink, m.posted_at,
                   s.ts, s.views, s.reach, s.likes, s.comments, s.shares, s.saved,
                   s.total_interactions, s.avg_watch_ms
            FROM media m
            JOIN media_snapshots s ON s.media_id = m.id
            WHERE s.ts = (SELECT MAX(ts) FROM media_snapshots WHERE media_id = m.id)
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ schedule
def add_scheduled(video_path: str, caption: str, options: dict, publish_at: int) -> int:
    with db() as con:
        cur = con.execute(
            "INSERT INTO schedule (video_path, caption, options, publish_at, created_at) "
            "VALUES (?,?,?,?,?)",
            (video_path, caption, json.dumps(options), publish_at, int(time.time())),
        )
        return int(cur.lastrowid)


def list_scheduled(include_done: bool = False) -> list[dict]:
    q = "SELECT * FROM schedule"
    if not include_done:
        q += " WHERE status IN ('queued','publishing')"
    q += " ORDER BY publish_at"
    with db() as con:
        rows = con.execute(q).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d["options"] or "{}")
        out.append(d)
    return out


def due_scheduled(now: int | None = None) -> list[dict]:
    now = now or int(time.time())
    return [s for s in list_scheduled() if s["status"] == "queued" and s["publish_at"] <= now]


def set_schedule_status(item_id: int, status: str, result: str | None = None) -> bool:
    with db() as con:
        cur = con.execute(
            "UPDATE schedule SET status = ?, result = ? WHERE id = ?", (status, result, item_id)
        )
        return cur.rowcount > 0


def claim_scheduled(item_id: int) -> bool:
    """Atomically move queued -> publishing so two runners can't post the same video."""
    with db() as con:
        cur = con.execute(
            "UPDATE schedule SET status = 'publishing' WHERE id = ? AND status = 'queued'",
            (item_id,),
        )
        return cur.rowcount == 1
