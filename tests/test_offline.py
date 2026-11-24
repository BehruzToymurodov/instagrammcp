"""Offline tests: a fake Instagram API, no network or real token needed.

Run:  python -m pytest -q    (or: python tests/test_offline.py)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["IG_DATA_DIR"] = TMP
os.environ["IG_ACCESS_TOKEN"] = "TEST_TOKEN_SECRET"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from instagram_mcp import core, store  # noqa: E402
from instagram_mcp.api import InstagramClient, InstagramError  # noqa: E402

CALLS: list[str] = []
STATUS_POLLS = {"n": 0}


def handler(req: httpx.Request) -> httpx.Response:
    url = req.url
    path = url.path
    q = dict(url.params)
    body = dict(httpx.QueryParams(req.content.decode())) if req.method == "POST" and "rupload" not in url.host else {}
    CALLS.append(f"{req.method} {url.host}{path}")

    if path.endswith("/me") and req.method == "GET":
        return httpx.Response(200, json={"user_id": "1", "username": "lyricno1r", "account_type": "MEDIA_CREATOR",
                                         "followers_count": 120, "follows_count": 3, "media_count": 3})
    if path.endswith("/me/media") and req.method == "GET":
        if q.get("after") == "P2":
            return httpx.Response(200, json={"data": [
                {"id": "m3", "caption": "Film 03", "media_product_type": "REELS", "timestamp": "2026-11-21T14:00:00+0000",
                 "permalink": "https://instagram.com/reel/c", "like_count": 9, "comments_count": 1}]})
        return httpx.Response(200, json={"data": [
            {"id": "m1", "caption": "Film 01\n#noir", "media_product_type": "REELS", "timestamp": "2026-11-10T15:00:00+0000",
             "permalink": "https://instagram.com/reel/a", "like_count": 40, "comments_count": 5},
            {"id": "m2", "caption": "Film 02", "media_product_type": "REELS", "timestamp": "2026-11-15T09:00:00+0000",
             "permalink": "https://instagram.com/reel/b", "like_count": 20, "comments_count": 2}],
            "paging": {"next": str(url.copy_with(params={**q, "after": "P2"}))}})
    if path.endswith("/insights"):
        metric = q.get("metric", "")
        mid = path.split("/")[-2]
        if "," in metric and mid == "m2":  # simulate Meta rejecting the bulk call
            return httpx.Response(400, json={"error": {"message": "metric bad", "code": 100}})
        if metric == "ig_reels_avg_watch_time" and mid == "m2":
            return httpx.Response(400, json={"error": {"message": "not available", "code": 100}})
        views = {"m1": 5000, "m2": 1200, "m3": 300}.get(mid, 0)
        vals = {"views": views, "reach": views // 2, "likes": 1, "comments": 1, "shares": 3, "saved": 4,
                "total_interactions": 50, "ig_reels_avg_watch_time": 7300, "ig_reels_video_view_total_time": 1}
        return httpx.Response(200, json={"data": [
            {"name": m, "period": "lifetime", "values": [{"value": vals.get(m, 0)}]} for m in metric.split(",")]})
    if path.endswith("/me/media") and req.method == "POST":
        assert body["media_type"] == "REELS"
        assert body.get("upload_type") == "resumable"
        assert body.get("is_ai_generated") == "true"
        return httpx.Response(200, json={"id": "c123"})
    if "rupload" in url.host:
        assert req.headers["Authorization"] == "OAuth TEST_TOKEN_SECRET"
        assert int(req.headers["file_size"]) == len(req.content)
        return httpx.Response(200, json={"success": True})
    if path.endswith("/c123"):
        STATUS_POLLS["n"] += 1
        return httpx.Response(200, json={"status_code": "FINISHED" if STATUS_POLLS["n"] > 1 else "IN_PROGRESS"})
    if path.endswith("/me/media_publish"):
        assert body["creation_id"] == "c123"
        return httpx.Response(200, json={"id": "m9"})
    if path.endswith("/m9"):
        return httpx.Response(200, json={"id": "m9", "permalink": "https://instagram.com/reel/new"})
    if path.endswith("/content_publishing_limit"):
        return httpx.Response(200, json={"data": [{"quota_usage": 1, "config": {"quota_total": 50, "quota_duration": 86400}}]})
    if path.endswith("/m1/comments"):
        return httpx.Response(200, json={"data": [{"id": "k1", "text": "wow", "username": "fan"}]})
    if path.endswith("/k1/replies"):
        return httpx.Response(200, json={"id": "k2"})
    if path.endswith("/refresh_access_token"):
        return httpx.Response(200, json={"access_token": "NEW_TOKEN", "expires_in": 5184000})
    if path.endswith("/bad"):
        return httpx.Response(400, json={"error": {"message": "Invalid token TEST_TOKEN_SECRET", "code": 190}})
    return httpx.Response(404, json={"error": {"message": f"unmocked {path}", "code": 1}})


def make_client() -> InstagramClient:
    c = InstagramClient()
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def test_all():
    c = make_client()

    # profile + paging
    assert c.profile()["username"] == "lyricno1r"
    assert [m["id"] for m in c.media()] == ["m1", "m2", "m3"]

    # insight fallback: bulk fails for m2, one-by-one keeps the rest
    ins = c.media_insights("m2", "REELS")
    assert ins["views"] == 1200 and "ig_reels_avg_watch_time" in ins["_unavailable"]

    # snapshot -> history -> analytics
    snap = core.take_snapshot(c)
    assert snap["posts_saved"] == 3 and snap["followers"] == 120
    ov = core.overview()
    assert ov["top_posts"][0]["id"] == "m1"
    assert ov["top_posts"][0]["avg_watch_s"] == 7.3
    assert ov["top_posts"][0]["posted"] == "2026-11-10 20:00"  # Tashkent = UTC+5
    assert ov["median_views_per_post"] == 1200
    bt = core.best_posting_times()
    assert bt["posts_analysed"] == 3 and bt["by_weekday"][0]["avg_views"] == 5000
    assert core.posts_table("date")[0]["id"] == "m3"

    # second snapshot later -> follower change works
    with store.db() as con:
        con.execute("UPDATE profile_snapshots SET ts = ts - 86400*3")
    core.take_snapshot(c)
    assert core.follower_change(7)["change"] == 0

    # publishing: prepare (private) then publish
    video = Path(TMP) / "film.mp4"
    video.write_bytes(b"\x00" * 2048)
    c.wait_until_ready.__func__  # exists
    orig_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        prep = core.prepare_reel(c, str(video), "Test caption", is_ai_generated=True)
        assert prep["container_id"] == "c123" and "NOT published" in prep["status"]
        assert not any("media_publish" in x for x in CALLS)
        pub = core.publish(c, "c123")
        assert pub["link"] == "https://instagram.com/reel/new"
    finally:
        time.sleep = orig_sleep

    # bad file types are refused before any upload
    for bad in ("nope.mp4", str(Path(TMP) / "x.gif")):
        if bad.endswith(".gif"):
            Path(bad).write_bytes(b"x")
        try:
            core.check_video(bad)
            raise AssertionError("should fail")
        except InstagramError:
            pass

    # scheduling
    try:
        core.parse_local_time("2020-01-01 10:00")
        raise AssertionError("past time accepted")
    except InstagramError:
        pass
    item = store.add_scheduled(str(video), "sched", {"is_ai_generated": True}, int(time.time()) - 5)
    STATUS_POLLS["n"] = 5
    res = core.run_due_scheduled(c)
    assert res[0]["status"] == "published"
    assert store.list_scheduled(include_done=True)[0]["status"] == "published"
    assert core.run_due_scheduled(c) == []  # never posts twice

    # comments, limit
    assert c.comments("m1")[0]["text"] == "wow"
    assert c.reply_comment("k1", "thanks!") == "k2"
    assert c.publishing_limit()["config"]["quota_total"] == 50

    # token never leaks into errors
    try:
        c.get("bad")
    except InstagramError as e:
        assert "TEST_TOKEN_SECRET" not in str(e) and "***" in str(e)

    # token refresh saves to data/token.json, which then wins over .env
    assert c.refresh_token_if_due(force=True)
    assert json.loads((Path(TMP) / "token.json").read_text())["token"] == "NEW_TOKEN"
    assert InstagramClient().token == "NEW_TOKEN"
    assert c.refresh_token_if_due() is False  # not due again for a week


def test_server_tools_register():
    from instagram_mcp import server
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    expected = {"get_profile", "sync_now", "get_overview", "list_posts", "get_post_insights",
                "get_account_insights", "get_audience", "best_posting_times", "prepare_reel",
                "publish_prepared_reel", "schedule_reel", "list_scheduled", "cancel_scheduled",
                "list_comments", "reply_to_comment", "hide_comment", "delete_comment",
                "get_publishing_limit", "get_follower_history", "run_scheduled_now",
                "refresh_access_token"}
    assert expected <= names, expected - names


if __name__ == "__main__":
    test_all()
    test_server_tools_register()
    print("all tests passed")


def test_facebook_mode_system_user_token(tmp_path=None):
    """Unified login gives an 'EAA...' system user token: graph.facebook.com + numeric IG id."""
    import importlib
    from instagram_mcp import config
    seen = []

    def fb(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.url.path))
        if req.url.path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [{"id": "page1", "instagram_business_account": {"id": "1784"}}]})
        if req.url.path.endswith("/1784"):
            return httpx.Response(200, json={"id": "1784", "username": "lyricno1r", "followers_count": 7})
        if req.url.path.endswith("/1784/media_publish"):
            return httpx.Response(200, json={"id": "m1"})
        return httpx.Response(404, json={"error": {"message": "x", "code": 1}})

    old = config.TOKEN_FILE
    config.TOKEN_FILE = Path(TMP) / "none.json"
    os.environ["IG_ACCESS_TOKEN"] = "EAAsystemtoken"
    try:
        c = InstagramClient()
        c._http = httpx.Client(transport=httpx.MockTransport(fb))
        assert c.facebook_mode and c.profile()["username"] == "lyricno1r"
        assert c.publish_container("c1") == "m1"
        assert all(h == "graph.facebook.com" for h, _ in seen)
        assert ("graph.facebook.com", "/v26.0/1784/media_publish") in seen
        assert c.refresh_token_if_due(force=True) is False
    finally:
        config.TOKEN_FILE = old
        os.environ["IG_ACCESS_TOKEN"] = "TEST_TOKEN_SECRET"
