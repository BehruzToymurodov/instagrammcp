# Instagram MCP for Lyric Noir

A small MCP server that lets Claude **analyse** and **post to** the Lyric Noir Instagram account (@lyricno1r) through Meta's official Instagram API. It runs on your own PC. Your token stays on your PC. There are no monthly limits apart from Meta's own (about 50 API posts per 24 hours).

## What Claude can do with it

| Area | Tools |
|---|---|
| Profile | `get_profile`: followers, following, posts, bio |
| Overview | `get_overview`: growth over 7 and 30 days, median views, engagement rate, top and weakest posts |
| Posts | `list_posts` (sort by views, likes, saves…), `get_post_insights` (views, reach, watch time, shares, saves + history) |
| Account | `get_account_insights` (last 30 days), `get_audience` (age, gender, country, city; needs 100+ followers) |
| Timing | `best_posting_times`: which weekdays and hours (Tashkent time) got the most views |
| History | `sync_now`, `get_follower_history`: daily snapshots saved forever in `data/instagram.db` |
| Publishing | `prepare_reel` (uploads privately) → `publish_prepared_reel` (makes it public), `get_publishing_limit` |
| Scheduling | `schedule_reel`, `list_scheduled`, `cancel_scheduled`, `run_scheduled_now` |
| Comments | `list_comments`, `reply_to_comment`, `hide_comment`, `delete_comment` |

**Safety:** publishing has two steps on purpose. Claude uploads the video privately, shows you the caption and settings, and only makes it public after you say yes. Reels are labelled "AI info" by default (`is_ai_generated`); you can turn this off per video.

---

## Setup (Windows, about 15 minutes)

### 1. Get the access token (one time)

1. Go to **developers.facebook.com** → **My Apps → Create App**.
2. Use case: **Manage messaging & content on Instagram**.
3. Open the use case → **API setup with Instagram login** → **Generate access tokens** → **Add account** and log in as @lyricno1r.
4. Make sure these permissions are on: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_insights`.
5. Click **Generate token** and copy it.

> The token works like a password for the page. Never paste it in a chat, a screenshot or a commit.

### 2. Install

Open **PowerShell**:

```powershell
cd $HOME
git clone https://github.com/BehruzToymurodov/instagrammcp.git
cd instagrammcp
python -m venv .venv
.venv\Scripts\pip install -e .
copy .env.example .env
notepad .env
```

In Notepad, paste your token after `IG_ACCESS_TOKEN=`, then save and close.

### 3. Test it

```powershell
.venv\Scripts\python -m instagram_mcp.cli check
```

You should see `"username": "lyricno1r"` and your follower count. If you see an error, check the token in `.env`.

Then save the first snapshot of your stats:

```powershell
.venv\Scripts\python -m instagram_mcp.cli snapshot
```

### 4. Connect it to Claude

**Claude desktop app:** Settings → **Developer** → **Edit Config**. This opens `claude_desktop_config.json`. Add the following, replacing `YOUR_NAME` with your Windows user folder name:

```json
{
  "mcpServers": {
    "instagram": {
      "command": "C:\\Users\\YOUR_NAME\\instagrammcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "instagram_mcp.server"]
    }
  }
}
```

If the file already has other servers, add `"instagram": {...}` inside the existing `"mcpServers"` block. Then restart the Claude app fully (quit it from the system tray, then open it again).

**Claude Code (terminal):**

```powershell
claude mcp add instagram -- C:\Users\YOUR_NAME\instagrammcp\.venv\Scripts\python.exe -m instagram_mcp.server
```

Then just ask Claude things like *"How is Lyric Noir doing this week?"* or *"Post Film 03 tonight at 20:00 with this caption…"*.

### 5. (Optional) Scheduled posts and daily history

Scheduled Reels are published by a small runner on your PC, so the PC must be on and online at the scheduled time. While it runs, it also saves a daily stats snapshot automatically.

Simplest way: leave this running in a PowerShell window:

```powershell
.venv\Scripts\python -m instagram_mcp.cli scheduler --loop
```

To make it automatic, use **Windows Task Scheduler** → *Create Basic Task* → trigger *Daily*, then set it to repeat every 5 minutes:

- Program: `C:\Users\YOUR_NAME\instagrammcp\.venv\Scripts\python.exe`
- Arguments: `-m instagram_mcp.cli scheduler`
- Start in: `C:\Users\YOUR_NAME\instagrammcp`

---

## Good to know

- **The token renews itself.** Tokens last 60 days. The server refreshes it about once a week and saves the new one in `data/token.json`, which then takes priority over `.env`. If you don't use it for 60 days, generate a new token in the Meta dashboard and paste it into `.env` again. Delete `data/token.json` if it's older than the new token.
- **Video limits (Meta's Reels spec):** `.mp4` or `.mov`, **300 MB max**, 3 seconds to 15 minutes, H.264 video + AAC audio, 23–60 fps, video bitrate up to about 25 Mb/s. Horizontal 16:9 is allowed (Instagram shows bars in the Reels feed).
- **Covers:** `cover_url` must be a public image link. Otherwise use `thumb_offset_ms` to pick a frame from the video (e.g. `4000` = the frame at 4 s).
- **Insights delay:** new posts may show 0 views for a few minutes, and some metrics only appear after about 24 hours.
- **Audience demographics** need at least 100 followers.
- **What the API can't do:** add Instagram's own trending music, or post link, poll or location stickers. Do those in the app.
- **Private data:** `.env` and the `data/` folder are git-ignored, so they never go to GitHub.

## Making a video small enough

Grainy noir films can be big. This re-encodes to an Instagram-friendly file, usually well under 300 MB for a 100-second film, without visible loss:

```powershell
ffmpeg -i final.mp4 -c:v libx264 -crf 23 -preset slow -maxrate 20M -bufsize 40M -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart final_ig.mp4
```

If it's still over 300 MB, raise `-crf` to 25 or 26.

## Developer notes

- `instagram_mcp/api.py`: HTTP client for `graph.instagram.com` (Instagram API with Instagram Login), including resumable upload to `rupload.facebook.com`.
- `instagram_mcp/core.py`: snapshots, analytics, publishing and scheduler logic.
- `instagram_mcp/store.py`: SQLite history and schedule queue.
- `instagram_mcp/server.py`: the MCP tools (works with MCP Python SDK 1.x and 2.x).
- `instagram_mcp/cli.py`: `check`, `snapshot`, `scheduler`, `refresh-token`.
- Tests run offline against a fake API: `pip install pytest` then `python -m pytest -q`.
- Change the API version with `IG_API_VERSION` in `.env` if Meta deprecates the default.
