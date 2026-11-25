# CLAUDE.md: Instagram MCP for Lyric Noir

Context for Claude Code. Read this before changing anything here.

## What this is
A local MCP server (Python) that lets Claude analyse and post to the Instagram page **@lyricno1r** ("Lyric Noir": animated noir music films; toolkit lives in the separate repo `BehruzToymurodov/lyricnoir`). Owner: Behruz (GaRRiK), Windows PC, Tashkent.

Built because third-party connectors (Metricool free: 20 posts/month, 30-day stats; Windsor.ai) were too limited. Only Meta's own limits apply here.

## Current setup (working as of 2026-11-25)
- Repo on the PC: `D:\Git Projects\instagrammcp`, venv at `.venv`.
- Registered in the Claude desktop app (`claude_desktop_config.json` → `mcpServers.instagram`, command `D:\Git Projects\instagrammcp\.venv\Scripts\python.exe -m instagram_mcp.server`). Status: Running; `get_profile` returns @lyricno1r.
- `.env` (git-ignored, never commit or print): `IG_ACCESS_TOKEN=EAA…` (system user token) and `IG_USER_ID=17841456463467622`.
- To add the same server to Claude Code:
  `claude mcp add instagram -- "D:\Git Projects\instagrammcp\.venv\Scripts\python.exe" -m instagram_mcp.server`
  (`.env` is found relative to the package, so no working dir is needed.)

## Meta side (how access was obtained; don't redo unless broken)
- Facebook account: **Bekhruz Tuymuradov** (old account with verified phone; a second renamed "Lyrc Noir" account was the cause of a "phone already added" error during developer registration).
- Meta developer app: **Lyric Noir Tools** (app id 2613691472483799), use case "Manage messaging & content on Instagram", development mode (not published; App Review not needed for our own account).
- New apps can't use "API setup with Instagram login" any more. We use **"API setup with Unified login"**, which needed a **business portfolio**: **Lyric Noir** (business id 2606071306485311). @lyricno1r was added to the portfolio; an empty ad account "Lyric Noir" (USD, Tashkent) was created as a side effect.
- The dashboard's "Generate token" button looped without showing a token, so the token was generated via **Business settings → System users → lyricnoir-mcp** (admin), assets: app Lyric Noir Tools + Instagram lyricno1r (full control), expiry **Never**, permissions `instagram_basic, instagram_content_publish, instagram_manage_comments, instagram_manage_insights`.
- Settings link: `https://business.facebook.com/latest/settings/system_users?business_id=2606071306485311`

## Code layout
- `instagram_mcp/api.py`: HTTP client. Two token modes: `EAA…` → `graph.facebook.com` + numeric IG id (current); `IGAA…` → `graph.instagram.com` + `me` with 60-day refresh. Resumable video upload to `rupload.facebook.com`. Errors scrub the token.
- `core.py`: snapshots, analytics (overview, posts table, best posting times in Asia/Tashkent), publishing, scheduler.
- `store.py`: SQLite at `data/instagram.db` (history has no time limit; schedule queue).
- `server.py`: 21 MCP tools (works with MCP Python SDK 1.x and 2.x).
- `cli.py`: `check`, `snapshot`, `scheduler [--loop]`, `refresh-token`.
- Tests: `python -m pytest -q` (offline, fake API).

## Rules
- Publishing is two steps on purpose: `prepare_reel` (private upload) → show the user file + caption + options → `publish_prepared_reel` only after an explicit yes. Same for schedule, reply, hide, delete.
- `is_ai_generated` defaults to true (Lyric Noir films are AI-assisted).
- Reels: mp4/mov, **300 MB max**, 3 s–15 min, H.264 + AAC. Film files are often bigger (grain); compress with the ffmpeg command in README.
- Meta's publishing cap is ~50 API posts per rolling 24 h.
- Captions follow the Lyric Noir brand: question hook, then `🎵 Artist – "Song"`, "Animated fan video", a few hashtags, never lyrics.

## Open items
- Optional: Windows Task Scheduler job for `cli scheduler` (daily snapshots + scheduled posts). Not set up yet.
- Display name is "lyricnoir" vs handle "lyricno1r". Suggested changing the display name to "Lyric Noir".
