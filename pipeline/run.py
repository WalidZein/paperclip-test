"""Entry point: python -m pipeline.run --niche <niche> --topic <topic>."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .coordinator import RunCoordinator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.run",
        description="Run the full TikTok pipeline for one niche + topic.",
    )
    p.add_argument("--niche", required=True, help="Niche name (must exist in niches YAML)")
    p.add_argument("--topic", required=True, help="Video topic / title")
    p.add_argument("--config", default="configs/niches.yaml", help="Path to niches YAML")
    p.add_argument("--output", default="output", help="Output base directory")
    p.add_argument("--log-dir", default="run_logs", help="JSON run log directory")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip TikTok publish step (useful for local testing)",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    coordinator = RunCoordinator(
        config_path=args.config,
        output_base=args.output,
        log_dir=args.log_dir,
        dry_run=args.dry_run,
    )

    result = coordinator.run(niche=args.niche, topic=args.topic)
    print(json.dumps(result, indent=2))

    if result.get("status") != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
