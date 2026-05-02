"""CLI entry point: submit | run | run-now | status | report | watch."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from .queue import JobQueue
from .runner import PipelineRunner
from .scheduler import PipelineScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def _build_objects(args: argparse.Namespace):
    db_path = Path(getattr(args, "db", "pipeline_jobs.db"))
    queue = JobQueue(db_path)
    runner = PipelineRunner(
        queue=queue,
        output_base=Path(getattr(args, "output", "output")),
        failed_base=Path(getattr(args, "failed", "failed")),
        pexels_api_key=os.environ.get("PEXELS_API_KEY"),
        pixabay_api_key=os.environ.get("PIXABAY_API_KEY"),
        stock_cache_dir=getattr(args, "cache", None),
    )
    scheduler = PipelineScheduler(
        queue=queue,
        runner=runner,
        report_path=Path(getattr(args, "report", "daily_report.txt")),
    )
    return queue, runner, scheduler


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

def cmd_submit(args: argparse.Namespace) -> None:
    """Enqueue one or more content brief JSON files."""
    queue, _, _ = _build_objects(args)
    paths = [Path(p) for p in args.briefs]
    if not paths:
        print("No brief files provided.", file=sys.stderr)
        sys.exit(1)
    for p in paths:
        brief = json.loads(p.read_text(encoding="utf-8"))
        job_id = queue.enqueue(brief)
        print(f"Enqueued job {job_id} — {p}")


def cmd_status(args: argparse.Namespace) -> None:
    """Print queue stats."""
    queue, _, _ = _build_objects(args)
    stats = queue.get_stats()
    total = sum(stats.values())
    print(f"Queue stats (total={total}):")
    for status, count in sorted(stats.items()):
        print(f"  {status:10s} {count}")


def cmd_report(args: argparse.Namespace) -> None:
    """Print today's run report to stdout."""
    queue, _, _ = _build_objects(args)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    completed = queue.get_completed_today()
    failed = queue.get_failed_today()
    print(f"# Daily Pipeline Report — {today}")
    print(f"Completed: {len(completed)}  Failed: {len(failed)}\n")
    if completed:
        print("## Completed")
        for job in completed:
            print(f"  [job {job['id']}] {job.get('output_path', '?')}")
    if failed:
        print("\n## Failed")
        for job in failed:
            brief = {}
            try:
                brief = json.loads(job.get("brief_json", "{}"))
            except Exception:
                pass
            print(f"  [job {job['id']}] niche={brief.get('niche','?')} topic={brief.get('topic','?')}")


def cmd_run(args: argparse.Namespace) -> None:
    """Start the persistent scheduler (blocks until SIGINT)."""
    _, _, scheduler = _build_objects(args)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    scheduler.start()
    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
        loop.close()


def cmd_run_now(args: argparse.Namespace) -> None:
    """Drain the queue immediately (one pass, then exit)."""
    _, _, scheduler = _build_objects(args)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scheduler.drain_now())
    loop.close()


def cmd_watch(args: argparse.Namespace) -> None:
    """Watch a directory and enqueue new .json files as they arrive."""
    watch_dir = Path(args.watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)
    queue, _, _ = _build_objects(args)
    seen: set[str] = set(
        p.name for p in watch_dir.glob("*.json")
    )
    print(f"Watching {watch_dir} for new brief JSON files… (Ctrl-C to stop)")
    try:
        while True:
            for p in watch_dir.glob("*.json"):
                if p.name not in seen:
                    seen.add(p.name)
                    try:
                        brief = json.loads(p.read_text(encoding="utf-8"))
                        job_id = queue.enqueue(brief)
                        print(f"  → Enqueued job {job_id} from {p.name}")
                    except Exception as exc:
                        print(f"  ! Failed to enqueue {p.name}: {exc}", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default="pipeline_jobs.db", help="SQLite DB path")
    parser.add_argument("--output", default="output", help="Output base directory")
    parser.add_argument("--failed", default="failed", help="Failed-jobs directory")
    parser.add_argument("--report", default="daily_report.txt", help="Daily report path")
    parser.add_argument("--cache", default=None, help="Stock footage cache directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description="TikTok pipeline orchestration CLI",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # submit
    p_submit = subs.add_parser("submit", help="Enqueue content brief JSON file(s)")
    p_submit.add_argument("briefs", nargs="+", help="Paths to brief JSON files")
    _common_args(p_submit)
    p_submit.set_defaults(func=cmd_submit)

    # status
    p_status = subs.add_parser("status", help="Print queue stats")
    _common_args(p_status)
    p_status.set_defaults(func=cmd_status)

    # report
    p_report = subs.add_parser("report", help="Print today's run report")
    _common_args(p_report)
    p_report.set_defaults(func=cmd_report)

    # run
    p_run = subs.add_parser("run", help="Start the persistent scheduler")
    _common_args(p_run)
    p_run.set_defaults(func=cmd_run)

    # run-now
    p_now = subs.add_parser("run-now", help="Drain queue once then exit")
    _common_args(p_now)
    p_now.set_defaults(func=cmd_run_now)

    # watch
    p_watch = subs.add_parser("watch", help="Watch a directory for new brief JSON files")
    p_watch.add_argument("watch_dir", help="Directory to watch")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Poll interval (seconds)")
    _common_args(p_watch)
    p_watch.set_defaults(func=cmd_watch)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
