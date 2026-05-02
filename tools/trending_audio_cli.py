#!/usr/bin/env python3
"""
Daily trending audio input helper.

Usage — add today's sounds (paste JSON when prompted):
    python tools/trending_audio_cli.py add

Usage — pipe JSON directly:
    cat sounds.json | python tools/trending_audio_cli.py add --stdin

Usage — validate today's file:
    python tools/trending_audio_cli.py validate

Usage — validate a specific date:
    python tools/trending_audio_cli.py validate --date 2026-05-01

Expected JSON schema (array or {"sounds": [...]} wrapper):
[
  {
    "tiktok_id": "7123456789012345678",
    "title": "Epic Dramatic Build",
    "artist": "SoundCreator",
    "mood": "dramatic",
    "file_path": "data/trending_audio/files/7123456789012345678.mp3"
  }
]

Valid moods: dramatic, calm, energetic, neutral
  dramatic  → used for dark_psychology niche
  calm      → used for personal_finance niche
  energetic → used for ai_tech niche
  neutral   → fallback for any niche with no mood match
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

VALID_MOODS = {"dramatic", "calm", "energetic", "neutral"}
DEFAULT_DIR = Path("data/trending_audio")
REQUIRED_FIELDS = ["tiktok_id", "title", "artist", "mood", "file_path"]


def _validate_entry(entry: dict, idx: int) -> list:
    issues = []
    for field in REQUIRED_FIELDS:
        if field not in entry:
            issues.append(f"entry[{idx}]: missing field '{field}'")
    if "mood" in entry and entry["mood"] not in VALID_MOODS:
        issues.append(
            f"entry[{idx}]: invalid mood '{entry['mood']}' "
            f"— must be one of {sorted(VALID_MOODS)}"
        )
    if "file_path" in entry and entry["file_path"]:
        p = Path(entry["file_path"])
        if not p.exists():
            issues.append(f"entry[{idx}]: file not found: {p}")
    return issues


def _read_sounds_json(source: str = "stdin") -> list:
    """Read and parse sounds from stdin."""
    if source == "stdin":
        raw = sys.stdin.read().strip()
    else:
        raw = source
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON — {exc}", file=sys.stderr)
        sys.exit(1)
    if isinstance(data, dict) and "sounds" in data:
        return data["sounds"]
    if isinstance(data, list):
        return data
    print("ERROR: Expected a JSON array or an object with a 'sounds' key.", file=sys.stderr)
    sys.exit(1)


def cmd_add(args):
    target_date = date.fromisoformat(args.date) if args.date else date.today()
    output_dir = Path(args.dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{target_date.isoformat()}.json"

    if output_path.exists() and not args.replace:
        print(f"File already exists: {output_path}")
        print("Use --replace to overwrite.")
        sys.exit(1)

    if not args.stdin:
        print(f"Paste trending sounds JSON for {target_date}, then press Ctrl+D:")

    sounds = _read_sounds_json()

    all_issues = []
    for i, entry in enumerate(sounds):
        all_issues.extend(_validate_entry(entry, i))

    if all_issues:
        print("Warnings:", file=sys.stderr)
        for w in all_issues:
            print(f"  WARNING: {w}", file=sys.stderr)

    payload = {"date": target_date.isoformat(), "sounds": sounds}
    with open(output_path, "w") as fh:
        json.dump(payload, fh, indent=2)

    print(f"Saved {len(sounds)} sound(s) → {output_path}")
    if not sounds:
        print("WARNING: No sounds written — pipeline will run without music today.", file=sys.stderr)


def cmd_validate(args):
    target_date = date.fromisoformat(args.date) if args.date else date.today()
    path = Path(args.dir) / f"{target_date.isoformat()}.json"

    if not path.exists():
        print(f"No trending audio file for {target_date}: {path}")
        print("Pipeline will proceed without background music.")
        sys.exit(0)

    with open(path) as fh:
        data = json.load(fh)

    sounds = data.get("sounds", [])
    print(f"Found {len(sounds)} sound(s) for {target_date}")

    all_issues = []
    for i, entry in enumerate(sounds):
        all_issues.extend(_validate_entry(entry, i))

    if all_issues:
        print("Warnings:")
        for w in all_issues:
            print(f"  WARNING: {w}")
        sys.exit(1)
    else:
        print("All entries valid.")


def main():
    parser = argparse.ArgumentParser(
        description="Trending audio daily input manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dir",
        default=str(DEFAULT_DIR),
        help=f"Directory for audio JSON files (default: {DEFAULT_DIR})",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    add_p = sub.add_parser("add", help="Add today's trending sounds")
    add_p.add_argument("--date", help="ISO date override (default: today)")
    add_p.add_argument("--replace", action="store_true", help="Overwrite existing file")
    add_p.add_argument("--stdin", action="store_true", help="Read JSON non-interactively from stdin")

    val_p = sub.add_parser("validate", help="Validate a trending audio file")
    val_p.add_argument("--date", help="ISO date override (default: today)")

    args = parser.parse_args()
    if args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "validate":
        cmd_validate(args)


if __name__ == "__main__":
    main()
