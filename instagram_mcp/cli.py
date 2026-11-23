"""Command line helpers (run from the repo folder):

  python -m instagram_mcp.cli check            # test the token, show the account
  python -m instagram_mcp.cli snapshot         # save today's stats to history
  python -m instagram_mcp.cli scheduler        # publish queued Reels that are due (run once)
  python -m instagram_mcp.cli scheduler --loop # keep running, check every 5 minutes
  python -m instagram_mcp.cli refresh-token    # renew the token for 60 more days
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import core
from .api import InstagramClient, InstagramError


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="instagram_mcp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("snapshot")
    sch = sub.add_parser("scheduler")
    sch.add_argument("--loop", action="store_true", help="keep running")
    sch.add_argument("--every", type=int, default=300, help="seconds between checks")
    sub.add_parser("refresh-token")
    args = ap.parse_args(argv)

    try:
        c = InstagramClient()
        if args.cmd == "check":
            p = c.profile()
            _print({
                "ok": True,
                "username": p.get("username"),
                "account_type": p.get("account_type"),
                "followers": p.get("followers_count"),
                "posts": p.get("media_count"),
                "publishing_limit": c.publishing_limit(),
            })
        elif args.cmd == "snapshot":
            c.refresh_token_if_due()
            _print(core.take_snapshot(c))
        elif args.cmd == "scheduler":
            while True:
                c.refresh_token_if_due()
                done = core.run_due_scheduled(c)
                if done:
                    _print(done)
                core.snapshot_if_stale(c)  # daily history comes for free while it runs
                if not args.loop:
                    break
                time.sleep(args.every)
        elif args.cmd == "refresh-token":
            _print({"refreshed": c.refresh_token_if_due(force=True)})
    except (InstagramError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
