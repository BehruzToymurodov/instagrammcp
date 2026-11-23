"""Lyric Noir Instagram MCP server.

Run:  python -m instagram_mcp.server   (stdio transport, for Claude Desktop / Claude Code)
"""
from __future__ import annotations

from typing import Literal

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import core, store
from .api import InstagramClient

mcp = _Server(
    "instagram",
    instructions=(
        "Tools for one Instagram professional account (Lyric Noir). Reading and "
        "analysing are always fine. Anything that becomes public or changes the "
        "account (publishing, scheduling, replying to / hiding / deleting comments) "
        "must first be shown to the user in chat (file, caption, options, time) and "
        "done only after they clearly say yes. Publishing is two steps on purpose: "
        "prepare_reel uploads privately; publish_prepared_reel makes it public."
    ),
)

_client: InstagramClient | None = None


def client() -> InstagramClient:
    global _client
    if _client is None:
        _client = InstagramClient()
        _client.refresh_token_if_due()
    return _client


# =================================================================== profile
@mcp.tool()
def get_profile() -> dict:
    """Live profile: username, name, bio, website, followers, following, post count."""
    return client().profile()


@mcp.tool()
def sync_now(max_posts: int = 100) -> dict:
    """Save a fresh snapshot (followers + every recent post's metrics) to local history.
    History has no time limit; the more often this runs, the better the trend charts."""
    return core.take_snapshot(client(), max_posts=max_posts)


@mcp.tool()
def get_overview() -> dict:
    """One-call health check: followers and growth (7/30 days), median and average
    views, engagement rate, top and weakest posts. Syncs first if the data is
    older than 20 hours."""
    synced = core.snapshot_if_stale(client())
    out = core.overview()
    if synced:
        out["just_synced"] = synced
    return out


@mcp.tool()
def get_follower_history(days: int = 90) -> list[dict]:
    """Follower count over time from local history (one row per snapshot)."""
    rows = store.follower_history(days)
    for r in rows:
        r["date"] = core.fmt_ts(r.pop("ts"))
    return rows


# ================================================================= analytics
@mcp.tool()
def list_posts(
    sort_by: Literal["views", "reach", "likes", "comments", "shares", "saves",
                     "engagement_rate_pct", "date"] = "views",
    limit: int = 50,
) -> list[dict]:
    """All tracked posts with their latest metrics (from the last sync), sorted."""
    return core.posts_table(sort_by=sort_by, limit=limit)


@mcp.tool()
def get_post_insights(media_id: str) -> dict:
    """Live metrics for one post (views, reach, likes, comments, shares, saves,
    average watch time for Reels) plus how they grew across past snapshots."""
    c = client()
    item = c.media_item(media_id)
    live = c.media_insights(media_id, item.get("media_product_type"))
    history = store.media_history(media_id)
    for h in history:
        h["date"] = core.fmt_ts(h.pop("ts"))
    return {"post": item, "live": live, "history": history}


@mcp.tool()
def get_account_insights(
    days: int = 30,
    metrics: list[str] | None = None,
) -> dict:
    """Account-level totals for the last `days` (max 30 per call, Meta's rule):
    reach, views, accounts engaged, interactions, likes, comments, shares, saves,
    follows/unfollows and profile link taps."""
    days = max(1, min(days, 30))
    since, until = core.utc_day_range(days)
    metrics = metrics or [
        "reach", "views", "accounts_engaged", "total_interactions", "likes",
        "comments", "shares", "saves", "follows_and_unfollows", "profile_links_taps",
    ]
    return {"days": days, **client().account_insights(metrics, since=since, until=until)}


@mcp.tool()
def get_audience(
    breakdown: Literal["age", "gender", "country", "city"] = "country",
    who: Literal["followers", "engaged"] = "followers",
) -> dict:
    """Audience demographics. Meta only returns these once the account has at
    least 100 followers (or 100 engaged accounts)."""
    metric = "follower_demographics" if who == "followers" else "engaged_audience_demographics"
    return client().account_insights(
        [metric], period="lifetime", metric_type="total_value",
        breakdown=breakdown, timeframe="this_month",
    )


@mcp.tool()
def best_posting_times() -> dict:
    """Which weekdays and hours (Tashkent time) your posts got the most views,
    based on your own history. Run sync_now first for fresh numbers."""
    return core.best_posting_times()


# ================================================================ publishing
@mcp.tool()
def get_publishing_limit() -> dict:
    """How many API posts were used in the last 24 hours and the daily cap."""
    return client().publishing_limit()


@mcp.tool()
def prepare_reel(
    video_path: str,
    caption: str,
    is_ai_generated: bool = True,
    share_to_feed: bool = True,
    cover_url: str | None = None,
    thumb_offset_ms: int | None = None,
    collaborators: list[str] | None = None,
    trial_reel: bool = False,
) -> dict:
    """Step 1 of 2: upload a local .mp4/.mov and let Instagram process it.
    NOTHING is published. Returns a container_id that stays valid for 24 hours.

    - is_ai_generated: adds Instagram's "AI info" label (on by default for Lyric Noir).
    - cover_url: public image URL for the cover; otherwise thumb_offset_ms picks a frame.
    - trial_reel: show only to non-followers first (Instagram trial reels).
    Before calling publish_prepared_reel, show the user the file, caption and
    options and wait for a clear yes."""
    return core.prepare_reel(
        client(), video_path, caption,
        is_ai_generated=is_ai_generated, share_to_feed=share_to_feed,
        cover_url=cover_url, thumb_offset_ms=thumb_offset_ms,
        collaborators=collaborators, trial_reel=trial_reel,
    )


@mcp.tool()
def publish_prepared_reel(container_id: str) -> dict:
    """Step 2 of 2: make a prepared Reel PUBLIC. Only call after the user has
    approved this exact video and caption in chat. Cannot be undone from here."""
    return core.publish(client(), container_id)


@mcp.tool()
def schedule_reel(
    video_path: str,
    caption: str,
    publish_at: str,
    is_ai_generated: bool = True,
    share_to_feed: bool = True,
    cover_url: str | None = None,
    thumb_offset_ms: int | None = None,
) -> dict:
    """Queue a Reel to publish at a local time ('YYYY-MM-DD HH:MM', Tashkent).
    The video is uploaded at that moment by the scheduler (see README: the
    scheduler must be running on the PC). Confirm with the user first."""
    core.check_video(video_path)
    ts = core.parse_local_time(publish_at)
    opts = {"is_ai_generated": is_ai_generated, "share_to_feed": share_to_feed,
            "cover_url": cover_url, "thumb_offset_ms": thumb_offset_ms}
    item_id = store.add_scheduled(video_path, caption, opts, ts)
    return {"id": item_id, "publish_at": core.fmt_ts(ts), "status": "queued"}


@mcp.tool()
def list_scheduled(include_done: bool = False) -> list[dict]:
    """Queued Reels (and, if asked, already published / failed / cancelled ones)."""
    rows = store.list_scheduled(include_done)
    for r in rows:
        r["publish_at"] = core.fmt_ts(r["publish_at"])
        r["created_at"] = core.fmt_ts(r["created_at"])
    return rows


@mcp.tool()
def cancel_scheduled(item_id: int) -> dict:
    """Cancel a queued Reel before it goes out."""
    items = {s["id"]: s for s in store.list_scheduled()}
    if item_id not in items or items[item_id]["status"] != "queued":
        return {"ok": False, "reason": "No queued item with that id."}
    store.set_schedule_status(item_id, "cancelled")
    return {"ok": True, "id": item_id, "status": "cancelled"}


@mcp.tool()
def run_scheduled_now() -> list[dict]:
    """Publish any queued Reels whose time has already passed."""
    return core.run_due_scheduled(client())


# ================================================================== comments
@mcp.tool()
def list_comments(media_id: str, limit: int = 100) -> list[dict]:
    """Comments (with replies) on one post."""
    return client().comments(media_id, limit)


@mcp.tool()
def reply_to_comment(comment_id: str, message: str) -> dict:
    """Post a public reply as the account. Show the reply text to the user first."""
    return {"reply_id": client().reply_comment(comment_id, message)}


@mcp.tool()
def hide_comment(comment_id: str, hide: bool = True) -> dict:
    """Hide (or unhide) a comment. Hidden comments can be unhidden later."""
    client().hide_comment(comment_id, hide)
    return {"comment_id": comment_id, "hidden": hide}


@mcp.tool()
def delete_comment(comment_id: str) -> dict:
    """Permanently delete a comment. Prefer hide_comment; confirm with the user first."""
    client().delete_comment(comment_id)
    return {"comment_id": comment_id, "deleted": True}


# ===================================================================== token
@mcp.tool()
def refresh_access_token() -> dict:
    """Renew the access token for another 60 days (done automatically weekly)."""
    refreshed = client().refresh_token_if_due(force=True)
    return {"refreshed": refreshed}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
