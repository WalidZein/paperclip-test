import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class CaptionConfig:
    font: str = "Arial-Bold"
    font_size: int = 60
    color: str = "white"
    stroke_color: str = "black"
    stroke_width: int = 3
    position: str = "center"
    background: bool = True
    background_color: str = "black"
    background_opacity: float = 0.7


@dataclass
class VideoConfig:
    color_grading: str = "neutral"
    b_roll_keywords: List[str] = field(default_factory=list)


@dataclass
class NicheProfile:
    niche: str
    caption: CaptionConfig
    video: VideoConfig

    @classmethod
    def from_yaml(cls, path: Path) -> "NicheProfile":
        with open(path) as f:
            data = yaml.safe_load(f)
        caption_data = data.get("caption", {})
        video_data = data.get("video", {})
        caption = CaptionConfig(**{k: v for k, v in caption_data.items() if k in CaptionConfig.__dataclass_fields__})
        video = VideoConfig(**{k: v for k, v in video_data.items() if k in VideoConfig.__dataclass_fields__})
        return cls(niche=data["niche"], caption=caption, video=video)

    @classmethod
    def load(cls, niche: str, profiles_dir: Optional[Path] = None) -> "NicheProfile":
        if profiles_dir is None:
            profiles_dir = Path(__file__).parent / "profiles"
        path = profiles_dir / f"{niche}.yaml"
        if not path.exists():
            # Fall back to motivation profile as default
            default_path = profiles_dir / "motivation.yaml"
            if default_path.exists():
                return cls.from_yaml(default_path)
            raise ValueError(f"No profile found for niche '{niche}' and no default available")
        return cls.from_yaml(path)
