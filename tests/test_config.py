"""Unit tests for niche config loading."""
import textwrap
from pathlib import Path

import pytest

from publisher.config import NicheConfig, load_all_niches, load_niche_config


SAMPLE_YAML = textwrap.dedent("""\
    niches:
      cooking:
        description_template: "Amazing {topic} recipe 🍳"
        hashtags:
          - "#cooking"
          - "#recipe"
        privacy_level: "PUBLIC_TO_EVERYONE"
        disable_duet: false
        disable_comment: false
        disable_stitch: false
        max_title_length: 100
        schedule:
          cron: "0 9,18 * * *"
          timezone: "America/New_York"
      fitness:
        description_template: "{topic} workout tips 💪"
        hashtags:
          - "#fitness"
        schedule:
          cron: "0 7 * * *"
          timezone: "UTC"
""")


@pytest.fixture
def config_file(tmp_path):
    f = tmp_path / "niches.yaml"
    f.write_text(SAMPLE_YAML)
    return f


def test_load_niche_config_basic(config_file):
    cfg = load_niche_config(config_file, "cooking")
    assert cfg.name == "cooking"
    assert cfg.description_template == "Amazing {topic} recipe 🍳"
    assert "#cooking" in cfg.hashtags
    assert cfg.schedule.cron == "0 9,18 * * *"
    assert cfg.schedule.timezone == "America/New_York"
    assert cfg.privacy_level == "PUBLIC_TO_EVERYONE"


def test_load_niche_config_defaults(config_file):
    cfg = load_niche_config(config_file, "fitness")
    assert cfg.privacy_level == "PUBLIC_TO_EVERYONE"
    assert cfg.disable_duet is False
    assert cfg.max_title_length == 150


def test_load_niche_config_missing_niche(config_file):
    with pytest.raises(ValueError, match="'nonexistent' not found"):
        load_niche_config(config_file, "nonexistent")


def test_load_all_niches(config_file):
    niches = load_all_niches(config_file)
    assert set(niches.keys()) == {"cooking", "fitness"}


def test_format_title_basic(config_file):
    cfg = load_niche_config(config_file, "cooking")
    title = cfg.format_title("pasta carbonara")
    assert "pasta carbonara" in title
    assert "#cooking" in title
    assert "#recipe" in title


def test_format_title_truncated():
    cfg = NicheConfig(
        name="test",
        description_template="Watch this {topic} video",
        hashtags=["#a", "#b"],
        schedule=None,  # type: ignore
        max_title_length=30,
    )
    title = cfg.format_title("extremely long topic that exceeds the maximum")
    assert len(title) <= 30
