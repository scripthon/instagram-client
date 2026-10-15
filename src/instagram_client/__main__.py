from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .client import InstagramClient
from .errors import ClientError

COMMANDS = ("profile", "post", "by-id", "oembed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instagram-client",
        description="Instagram logged-out client: profile, post, by-id, oembed.",
    )
    parser.add_argument("--proxy", default=None, help="http(s)/socks5 proxy URL")
    parser.add_argument("--timeout", type=float, default=30.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profile", help="profile + 12 latest posts, by username")
    p.add_argument("username")

    p = sub.add_parser("post", help="one post, by shortcode")
    p.add_argument("shortcode")

    p = sub.add_parser("by-id", help="profile, by numeric user id")
    p.add_argument("user_id")

    p = sub.add_parser("oembed", help="official oEmbed, by post URL")
    p.add_argument("url")
    return parser


def _run(client: InstagramClient, args: argparse.Namespace) -> Any:
    if args.command == "profile":
        return client.profile(args.username)
    if args.command == "post":
        return client.post(args.shortcode)
    if args.command == "by-id":
        return client.profile_by_id(args.user_id)
    return client.oembed(args.url)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = InstagramClient(proxy=args.proxy, timeout=args.timeout)
    try:
        result = _run(client, args)
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
