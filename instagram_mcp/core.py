"""Shared workflows used by both the MCP server and the command line:
snapshots (history), analytics, and publishing a Reel from a local file.
"""
from __future__ import annotations

import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import store
from .api import InstagramClient, InstagramError

TZ = ZoneInfo(os.getenv("IG_TIMEZONE", "Asia/Tashkent"))
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
VIDEO_EXTS = {".mp4", ".mov"}
MAX_VIDEO_BYTES = 300 * 1000**2  # Meta Reels spec: 300 MB max, 3 s - 15 min, H.264/HEVC + AAC


def parse_ig_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


# ------------------------------------------------------------------ snapshots
def take_snapshot(client: InstagramClient, max_posts: int = 100) -> dict:
    """Save followers + every recent post's metrics into local history."""
    ts = int(time.time())
    profile = client.profile()
    store.save_profile(profile, ts)
    posts = client.media(limit=max_posts)
    failed = 0
    for m in posts:
        store.save_media(m)
        try:
            ins = client.media_insights(m["id"], m.get("media_product_type"))
        except InstagramError:
            failed += 1
            ins = {}
        # like/comment counts are on the media object even when insights fail
        ins.setdefault("likes", m.get("like_count"))
        ins.setdefault("comments", m.get("comments_count"))
        store.save_media_snapshot(m["id"], ins, ts)
    return {
        "saved_at": datetime.fromtimestamp(ts, TZ).isoformat(timespec="minutes"),
        "followers": profile.get("followers_count"),
        "posts_saved": len(posts),
        "posts_without_insights": failed,
    }


def snapshot_if_stale(client: InstagramClient, max_age_hours: float = 20) -> dict | None:
    hist = store.follower_history(days=2)
    if hist and time.time() - hist[-1]["ts"] < max_age_hours * 3600:
        return None
    return take_snapshot(client)


# ------------------------------------------------------------------ analytics
def _engagement(p: dict) -> float | None:
    reach = p.get("reach")
    inter = p.get("total_interactions")
    if inter is None:
        inter = sum((p.get(k) or 0) for k in ("likes", "comments", "shares", "saved"))
    return round(100 * inter / reach, 2) if reach else None


def _local_post(p: dict) -> dict:
    dt = parse_ig_time(p.get("posted_at"))
    caption = (p.get("caption") or "").strip().splitlines()
    return {
        "id": p["id"],
        "title": caption[0][:80] if caption else "",
        "type": p.get("product_type"),
        "posted": dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if dt else None,
        "views": p.get("views"),
        "reach": p.get("reach"),
        "likes": p.get("likes"),
        "comments": p.get("comments"),
        "shares": p.get("shares"),
        "saves": p.get("saved"),
        "avg_watch_s": round(p["avg_watch_ms"] / 1000, 1) if p.get("avg_watch_ms") else None,
        "engagement_rate_pct": _engagement(p),
        "link": p.get("permalink"),
    }


def follower_change(days: int) -> dict | None:
    hist = store.follower_history(days=days + 1)
    hist = [h for h in hist if h["followers"] is not None]
    if len(hist) < 2:
        return None
    first, last = hist[0], hist[-1]
    span_days = max((last["ts"] - first["ts"]) / 86400, 1 / 24)
    gained = last["followers"] - first["followers"]
    return {
        "from": first["followers"],
        "to": last["followers"],
        "change": gained,
        "measured_over_days": round(span_days, 1),
        "per_day": round(gained / span_days, 2),
    }


def overview() -> dict:
    posts = [_local_post(p) for p in store.latest_media_metrics()]
    hist = store.follower_history(days=3650)
    views = [p["views"] for p in posts if p["views"] is not None]
    eng = [p["engagement_rate_pct"] for p in posts if p["engagement_rate_pct"] is not None]
    by_views = sorted(posts, key=lambda p: p["views"] or 0, reverse=True)
    return {
        "followers_now": hist[-1]["followers"] if hist else None,
        "history_starts": (
            datetime.fromtimestamp(hist[0]["ts"], TZ).strftime("%Y-%m-%d") if hist else None
        ),
        "follower_change_7d": follower_change(7),
        "follower_change_30d": follower_change(30),
        "posts_tracked": len(posts),
        "median_views_per_post": statistics.median(views) if views else None,
        "average_views_per_post": round(statistics.mean(views)) if views else None,
        "average_engagement_rate_pct": round(statistics.mean(eng), 2) if eng else None,
        "top_posts": by_views[:5],
        "weakest_posts": by_views[-3:] if len(by_views) > 5 else [],
    }


def posts_table(sort_by: str = "views", limit: int = 50) -> list[dict]:
    posts = [_local_post(p) for p in store.latest_media_metrics()]
    key = sort_by if sort_by != "date" else "posted"
    posts.sort(key=lambda p: (p.get(key) is not None, p.get(key) or 0), reverse=True)
    return posts[:limit]


def best_posting_times() -> dict:
    """Average views by weekday and by hour of posting (local time).

    With only a few posts this is a weak signal; the result says how many
    posts each bucket is based on so the reader can judge it.
    """
    by_day: dict[str, list[int]] = defaultdict(list)
    by_hour: dict[int, list[int]] = defaultdict(list)
    for p in store.latest_media_metrics():
        dt = parse_ig_time(p.get("posted_at"))
        if not dt or p.get("views") is None:
            continue
        local = dt.astimezone(TZ)
        by_day[WEEKDAYS[local.weekday()]].append(p["views"])
        by_hour[local.hour].append(p["views"])

    def summarise(d: dict) -> list[dict]:
        rows = [
            {"when": k, "posts": len(v), "avg_views": round(statistics.mean(v))}
            for k, v in d.items()
        ]
        return sorted(rows, key=lambda r: r["avg_views"], reverse=True)

    total = sum(len(v) for v in by_day.values())
    return {
        "timezone": str(TZ),
        "posts_analysed": total,
        "by_weekday": summarise(by_day),
        "by_hour": [
            {**r, "when": f"{r['when']:02d}:00"} for r in summarise(by_hour)
        ],
        "note": (
            "Fewer than ~15 posts: treat this as a hint, not a rule."
            if total < 15 else "Based on your own post history."
        ),
    }


# ----------------------------------------------------------------- publishing
def check_video(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise InstagramError(f"Video file not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        raise InstagramError(f"Reels must be .mp4 or .mov, got {p.suffix}")
    if p.stat().st_size > MAX_VIDEO_BYTES:
        raise InstagramError(
            f"File is {p.stat().st_size / 1e6:.0f} MB; Instagram's Reels limit is 300 MB. "
            "Compress it first (see README, 'Making a video small enough'):  "
            "ffmpeg -i in.mp4 -c:v libx264 -crf 23 -preset slow -maxrate 20M -bufsize 40M "
            "-c:a aac -b:a 192k -movflags +faststart out_ig.mp4"
        )
    return p.resolve()


def prepare_reel(client: InstagramClient, video_path: str, caption: str, **options: Any) -> dict:
    """Upload + process a Reel WITHOUT publishing it. Returns a container id.

    The container stays private and expires after 24 hours if not published.
    """
    path = check_video(video_path)
    cid = client.create_reel_container(caption=caption, **options)
    client.upload_video_file(cid, path)
    client.wait_until_ready(cid)
    return {
        "container_id": cid,
        "file": str(path),
        "size_mb": round(path.stat().st_size / 1e6, 1),
        "caption": caption,
        "options": options,
        "status": "ready, NOT published yet (expires in 24 h)",
    }


def publish(client: InstagramClient, container_id: str) -> dict:
    media_id = client.publish_container(container_id)
    try:
        link = client.media_item(media_id).get("permalink")
    except InstagramError:
        link = None
    return {"media_id": media_id, "link": link, "published_at": datetime.now(TZ).isoformat(timespec="minutes")}


def run_due_scheduled(client: InstagramClient) -> list[dict]:
    """Publish every queued item whose time has come. Safe to run every few minutes."""
    results = []
    for item in store.due_scheduled():
        if not store.claim_scheduled(item["id"]):
            continue
        try:
            prep = prepare_reel(client, item["video_path"], item["caption"] or "", **item["options"])
            res = publish(client, prep["container_id"])
            store.set_schedule_status(item["id"], "published", res.get("link") or res["media_id"])
            results.append({"id": item["id"], "status": "published", **res})
        except Exception as e:  # keep going with the rest of the queue
            store.set_schedule_status(item["id"], "failed", str(e))
            results.append({"id": item["id"], "status": "failed", "error": str(e)})
    return results


def parse_local_time(when: str) -> int:
    """'2026-11-25 19:30' in IG_TIMEZONE -> unix time. Must be in the future."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(when.strip(), fmt).replace(tzinfo=TZ)
            break
        except ValueError:
            continue
    else:
        raise InstagramError("Use the format YYYY-MM-DD HH:MM, e.g. 2026-11-25 19:30")
    if dt < datetime.now(TZ) + timedelta(minutes=1):
        raise InstagramError("That time is in the past.")
    return int(dt.timestamp())


def fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")


def utc_day_range(days: int) -> tuple[int, int]:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    return int(since.timestamp()), int(until.timestamp())
