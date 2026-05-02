"""CLI entry point for the TikTok publisher."""
import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_publish(args: argparse.Namespace) -> int:
    from .uploader import VideoUploader
    from .config import load_niche_config

    token = args.access_token or os.environ.get("TIKTOK_ACCESS_TOKEN")
    if not token:
        print("ERROR: --access-token or TIKTOK_ACCESS_TOKEN required", file=sys.stderr)
        return 1

    niche_cfg = load_niche_config(args.config, args.niche)
    uploader = VideoUploader(access_token=token, log_file=args.log_file)
    result = uploader.upload(
        video_path=args.video,
        niche_name=args.niche,
        topic=args.topic,
        niche_config=niche_cfg,
        open_id=args.open_id or "",
    )
    print(f"Published: post_id={result['post_id']} url={result['url']}")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    from .scheduler import PostScheduler

    scheduler = PostScheduler(
        config_path=args.config,
        video_dir=args.video_dir,
        access_token=args.access_token or os.environ.get("TIKTOK_ACCESS_TOKEN"),
        log_file=args.log_file,
        open_id=args.open_id or "",
    )
    scheduler.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="publisher", description="TikTok content publisher")
    parser.add_argument("--config", "-c", required=True, help="Path to niches YAML config")
    parser.add_argument("--log-file", "-l", default=None, help="Path to JSON log file")
    parser.add_argument("--access-token", default=None, help="TikTok access token (overrides env var)")
    parser.add_argument("--open-id", default=None, help="TikTok open_id for post URL building")

    subs = parser.add_subparsers(dest="command", required=True)

    pub = subs.add_parser("publish", help="One-shot publish a video")
    pub.add_argument("--video", "-v", required=True, help="Path to MP4 file")
    pub.add_argument("--niche", "-n", required=True, help="Niche name from config")
    pub.add_argument("--topic", "-t", required=True, help="Topic string (used in title template)")
    pub.set_defaults(func=cmd_publish)

    sched = subs.add_parser("schedule", help="Start cron-based scheduler for all niches")
    sched.add_argument("--video-dir", "-d", required=True, help="Root directory containing niche sub-dirs with MP4s")
    sched.set_defaults(func=cmd_schedule)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
