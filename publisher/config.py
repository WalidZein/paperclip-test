"""Per-niche posting configuration loaded from YAML."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ScheduleConfig:
    cron: str  # cron expression, e.g. "0 9,18 * * *"
    timezone: str = "UTC"


@dataclass
class NicheConfig:
    name: str
    description_template: str  # supports {topic} placeholder
    hashtags: list[str]
    schedule: ScheduleConfig
    privacy_level: str = "PUBLIC_TO_EVERYONE"
    disable_duet: bool = False
    disable_comment: bool = False
    disable_stitch: bool = False
    max_title_length: int = 150

    def format_title(self, topic: str) -> str:
        tags = " ".join(self.hashtags)
        title = self.description_template.format(topic=topic)
        combined = f"{title} {tags}"
        return combined[: self.max_title_length]


def load_niche_config(yaml_path: str | Path, niche_name: str) -> NicheConfig:
    path = Path(yaml_path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    niches = raw.get("niches", {})
    if niche_name not in niches:
        raise ValueError(f"Niche '{niche_name}' not found in {path}. Available: {list(niches.keys())}")

    cfg = niches[niche_name]
    sched_raw = cfg.get("schedule", {})
    schedule = ScheduleConfig(
        cron=sched_raw.get("cron", "0 9 * * *"),
        timezone=sched_raw.get("timezone", "UTC"),
    )
    return NicheConfig(
        name=niche_name,
        description_template=cfg["description_template"],
        hashtags=cfg.get("hashtags", []),
        schedule=schedule,
        privacy_level=cfg.get("privacy_level", "PUBLIC_TO_EVERYONE"),
        disable_duet=cfg.get("disable_duet", False),
        disable_comment=cfg.get("disable_comment", False),
        disable_stitch=cfg.get("disable_stitch", False),
        max_title_length=cfg.get("max_title_length", 150),
    )


def load_all_niches(yaml_path: str | Path) -> dict[str, NicheConfig]:
    path = Path(yaml_path)
    with open(path) as f:
        raw = yaml.safe_load(f)
    return {name: load_niche_config(path, name) for name in raw.get("niches", {})}
