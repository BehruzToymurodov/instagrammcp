"""Thin client for the Instagram API with Instagram Login (graph.instagram.com).

Every call uses the Instagram User access token from config. Errors from Meta
are turned into InstagramError with the API's own message, so the assistant
can explain what went wrong. The token itself never appears in errors.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from . import config


class InstagramError(RuntimeError):
    pass


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


class InstagramClient:
    def __init__(self, timeout: float = 60.0):
        self._state = config.load_token()
        self._http = httpx.Client(timeout=timeout)
        self._ig: str | None = None

    # Two kinds of token work:
    #  - Instagram Login token ("IGAA..."): graph.instagram.com, account = "me", 60-day refresh
    #  - Unified login / system user token ("EAA..."): graph.facebook.com, account = numeric
    #    Instagram user id, never expires
    @property
    def facebook_mode(self) -> bool:
        return self._state.token.startswith("EAA")

    @property
    def base(self) -> str:
        return config.GRAPH_FB if self.facebook_mode else config.GRAPH

    @property
    def ig(self) -> str:
        """Path of the Instagram account: 'me' or its numeric id."""
        if self._ig:
            return self._ig
        if not self.facebook_mode:
            self._ig = "me"
        elif config.IG_USER_ID:
            self._ig = config.IG_USER_ID
        else:
            self._ig = self._discover_ig_id()
        return self._ig

    def _discover_ig_id(self) -> str:
        tries = [
            ("me/accounts", {"fields": "instagram_business_account"}),
            ("me/instagram_accounts", {"fields": "id,username"}),
        ]
        for path, params in tries:
            try:
                for row in self.get(path, **params).get("data", []):
                    iba = row.get("instagram_business_account")
                    if iba and iba.get("id"):
                        return iba["id"]
                    if path.endswith("instagram_accounts") and row.get("id"):
                        return row["id"]
            except InstagramError:
                continue
        raise InstagramError(
            "Couldn't find the Instagram account for this token. Add IG_USER_ID=<number> "
            "to .env (see README, 'Finding IG_USER_ID')."
        )

    # ------------------------------------------------------------------ core
    @property
    def token(self) -> str:
        return self._state.token

    def _check(self, r: httpx.Response) -> Any:
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text}
        if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            err = data.get("error", data) if isinstance(data, dict) else data
            if isinstance(err, dict):
                msg = err.get("error_user_msg") or err.get("message") or str(err)
                code = err.get("code")
                sub = err.get("error_subcode")
                detail = f"{msg} (code {code}" + (f", subcode {sub}" if sub else "") + ")"
            else:
                detail = str(err)
            raise InstagramError(_scrub(f"Instagram API error {r.status_code}: {detail}", self.token))
        return data

    def get(self, path: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.token
        r = self._http.get(f"{self.base}/{path.lstrip('/')}", params=params)
        return self._check(r)

    def post(self, path: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.token
        r = self._http.post(f"{self.base}/{path.lstrip('/')}", data=params)
        return self._check(r)

    def delete(self, path: str) -> Any:
        r = self._http.delete(
            f"{self.base}/{path.lstrip('/')}", params={"access_token": self.token}
        )
        return self._check(r)

    def get_all(self, path: str, limit: int = 500, **params: Any) -> list[dict]:
        """Follow `paging.next` until `limit` items are collected."""
        out: list[dict] = []
        data = self.get(path, **params)
        while True:
            out.extend(data.get("data", []))
            nxt = data.get("paging", {}).get("next")
            if not nxt or len(out) >= limit:
                break
            data = self._check(self._http.get(nxt))
        return out[:limit]

    # ----------------------------------------------------------------- token
    def refresh_token_if_due(self, force: bool = False) -> bool:
        """Refresh the long-lived token (each refresh = 60 more days).

        Meta only allows refreshing a token that is at least 24 hours old, so a
        failed refresh on a brand-new token is ignored rather than raised.
        """
        if self.facebook_mode:
            return False  # system user tokens don't expire and can't be refreshed this way
        age = time.time() - self._state.refreshed_at
        if not force and self._state.refreshed_at and age < config.REFRESH_EVERY_SECONDS:
            return False
        r = self._http.get(
            f"{config.GRAPH_UNVERSIONED}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": self.token},
        )
        try:
            data = self._check(r)
        except InstagramError:
            if force:
                raise
            return False
        new = data.get("access_token")
        if new:
            config.save_token(new)
            self._state = config.load_token()
            return True
        return False

    # --------------------------------------------------------------- profile
    PROFILE_FIELDS = (
        "user_id,username,name,account_type,profile_picture_url,followers_count,"
        "follows_count,media_count,biography,website"
    )
    MEDIA_FIELDS = (
        "id,caption,media_type,media_product_type,permalink,timestamp,"
        "like_count,comments_count,thumbnail_url,media_url,is_shared_to_feed"
    )

    PROFILE_FIELDS_FB = (
        "id,username,name,profile_picture_url,followers_count,follows_count,"
        "media_count,biography,website"
    )

    def profile(self) -> dict:
        fields = self.PROFILE_FIELDS_FB if self.facebook_mode else self.PROFILE_FIELDS
        return self.get(self.ig, fields=fields)

    def media(self, limit: int = 50) -> list[dict]:
        return self.get_all(f"{self.ig}/media", limit=limit, fields=self.MEDIA_FIELDS)

    def media_item(self, media_id: str) -> dict:
        return self.get(media_id, fields=self.MEDIA_FIELDS)

    # -------------------------------------------------------------- insights
    REEL_METRICS = [
        "views", "reach", "likes", "comments", "shares", "saved",
        "total_interactions", "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
    ]
    POST_METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions"]

    def _insights(self, obj: str, metrics: list[str], **params: Any) -> dict:
        """Ask for all metrics at once; if Meta rejects one, fall back to one by one
        so a single unsupported metric doesn't hide the rest."""
        result: dict[str, Any] = {}
        errors: dict[str, str] = {}
        try:
            data = self.get(f"{obj}/insights", metric=",".join(metrics), **params)
            items = data.get("data", [])
        except InstagramError:
            items = []
            for m in metrics:
                try:
                    items.extend(self.get(f"{obj}/insights", metric=m, **params).get("data", []))
                except InstagramError as e:
                    errors[m] = str(e)
        for it in items:
            name = it.get("name")
            if "total_value" in it:
                tv = it["total_value"]
                result[name] = tv.get("breakdowns") or tv.get("value")
            elif "values" in it:
                vals = it["values"]
                result[name] = vals[0].get("value") if len(vals) == 1 else vals
        if errors:
            result["_unavailable"] = errors
        return result

    def media_insights(self, media_id: str, product_type: str | None = None) -> dict:
        if product_type is None:
            product_type = self.media_item(media_id).get("media_product_type", "FEED")
        metrics = self.REEL_METRICS if product_type == "REELS" else self.POST_METRICS
        return self._insights(media_id, metrics)

    def account_insights(
        self, metrics: list[str], since: int | None = None, until: int | None = None,
        period: str = "day", metric_type: str | None = "total_value",
        breakdown: str | None = None, timeframe: str | None = None,
    ) -> dict:
        return self._insights(
            self.ig, metrics, period=period, since=since, until=until,
            metric_type=metric_type, breakdown=breakdown, timeframe=timeframe,
        )

    # ------------------------------------------------------------ publishing
    def publishing_limit(self) -> dict:
        data = self.get(f"{self.ig}/content_publishing_limit", fields="quota_usage,config")
        return (data.get("data") or [{}])[0]

    def create_reel_container(
        self, caption: str = "", video_url: str | None = None, share_to_feed: bool = True,
        cover_url: str | None = None, thumb_offset_ms: int | None = None,
        is_ai_generated: bool = False, collaborators: list[str] | None = None,
        location_id: str | None = None, trial_reel: bool = False,
    ) -> str:
        params: dict[str, Any] = {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
            "cover_url": cover_url,
            "thumb_offset": thumb_offset_ms,
            "location_id": location_id,
        }
        if is_ai_generated:
            params["is_ai_generated"] = "true"
        if collaborators:
            params["collaborators"] = ",".join(collaborators)
        if trial_reel:
            params["trial_params"] = '{"graduation_strategy":"MANUAL"}'
        if video_url:
            params["video_url"] = video_url
        else:
            params["upload_type"] = "resumable"
        return self.post(f"{self.ig}/media", **params)["id"]

    def upload_video_file(self, container_id: str, path: Path) -> None:
        """Resumable upload of a local file straight to Meta (no public URL needed)."""
        size = path.stat().st_size
        with path.open("rb") as f:
            r = self._http.post(
                f"{config.RUPLOAD}/{container_id}",
                headers={
                    "Authorization": f"OAuth {self.token}",
                    "offset": "0",
                    "file_size": str(size),
                },
                content=f.read(),
                timeout=None,
            )
        self._check(r)

    def container_status(self, container_id: str) -> dict:
        return self.get(container_id, fields="status_code,status")

    def wait_until_ready(self, container_id: str, timeout_s: int = 900, every_s: int = 10) -> dict:
        start = time.time()
        while True:
            st = self.container_status(container_id)
            code = st.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return st
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"Video processing failed: {st.get('status') or code}")
            if time.time() - start > timeout_s:
                raise InstagramError(f"Still processing after {timeout_s}s (last status: {code}).")
            time.sleep(every_s)

    def publish_container(self, container_id: str) -> str:
        return self.post(f"{self.ig}/media_publish", creation_id=container_id)["id"]

    # -------------------------------------------------------------- comments
    def comments(self, media_id: str, limit: int = 100) -> list[dict]:
        return self.get_all(
            f"{media_id}/comments", limit=limit,
            fields="id,text,username,timestamp,like_count,hidden,replies{id,text,username,timestamp}",
        )

    def reply_comment(self, comment_id: str, message: str) -> str:
        return self.post(f"{comment_id}/replies", message=message)["id"]

    def hide_comment(self, comment_id: str, hide: bool = True) -> Any:
        return self.post(comment_id, hide=str(hide).lower())

    def delete_comment(self, comment_id: str) -> Any:
        return self.delete(comment_id)
