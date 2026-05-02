"""Structured JSON run log for a single pipeline execution."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageResult:
    name: str
    status: str  # "success" | "failed" | "skipped"
    duration_sec: float
    error: Optional[str] = None
    output: Optional[dict] = None


@dataclass
class RunLog:
    run_id: str
    niche: str
    topic: str
    started_at: str = field(default_factory=_now)
    stages: list[StageResult] = field(default_factory=list)
    status: str = "in_progress"
    finished_at: Optional[str] = None
    publish_url: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def new(cls, niche: str, topic: str) -> "RunLog":
        return cls(
            run_id=uuid.uuid4().hex[:12],
            niche=niche,
            topic=topic,
            started_at=_now(),
        )

    def record_stage(
        self,
        name: str,
        status: str,
        duration_sec: float,
        error: Optional[str] = None,
        output: Optional[dict] = None,
    ) -> None:
        self.stages.append(StageResult(name, status, duration_sec, error, output))

    def finalize(
        self,
        status: str,
        error: Optional[str] = None,
        publish_url: Optional[str] = None,
    ) -> None:
        self.status = status
        self.finished_at = _now()
        self.error = error
        self.publish_url = publish_url

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "niche": self.niche,
            "topic": self.topic,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "publish_url": self.publish_url,
            "error": self.error,
            "stages": [
                {
                    "name": s.name,
                    "status": s.status,
                    "duration_sec": round(s.duration_sec, 3),
                    "error": s.error,
                    "output": s.output,
                }
                for s in self.stages
            ],
        }

    def write(self, log_dir: str | Path) -> Path:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path
