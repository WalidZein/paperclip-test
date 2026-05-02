"""Pipeline runner: ScriptGenerator → TTS → VideoComposer → OutputBundle."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import traceback
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Optional

from script_generator.generator import ContentBrief, generate_script
from video_assembly.assembler import VideoAssembler
from video_assembly.tts import generate_tts

from .queue import JobQueue

logger = logging.getLogger(__name__)

# Characters forbidden in path components
_SLUG_RE = re.compile(r"[^\w\s-]")
_MULTI_DASH_RE = re.compile(r"[-\s]+")


def _slugify(text: str, max_len: int = 48) -> str:
    text = _SLUG_RE.sub("", text.lower())
    text = _MULTI_DASH_RE.sub("-", text).strip("-")
    return text[:max_len]


def _build_output_dir(base: Path, niche: str, title_slug: str) -> Path:
    today = datetime.now(timezone.utc).date().isoformat()
    return base / today / niche / title_slug


class PipelineRunner:
    """Executes the full pipeline for a single job.

    Sync I/O calls (Anthropic API, ElevenLabs API, ffmpeg) are dispatched
    to a thread-pool executor so the caller can run multiple jobs concurrently
    with ``asyncio.gather``.
    """

    def __init__(
        self,
        queue: JobQueue,
        output_base: str | Path = "output",
        failed_base: str | Path = "failed",
        pexels_api_key: Optional[str] = None,
        pixabay_api_key: Optional[str] = None,
        stock_cache_dir: Optional[str | Path] = None,
    ) -> None:
        self.queue = queue
        self.output_base = Path(output_base)
        self.failed_base = Path(failed_base)
        self._assembler = VideoAssembler(
            pexels_api_key=pexels_api_key,
            pixabay_api_key=pixabay_api_key,
            cache_dir=Path(stock_cache_dir) if stock_cache_dir else None,
        )

    # ------------------------------------------------------------------
    # Public async entry point
    # ------------------------------------------------------------------

    async def run_job(self, job: dict) -> Optional[str]:
        """Run one pipeline job. Returns output_path on success, None on failure."""
        job_id: int = job["id"]
        brief_dict: dict = json.loads(job["brief_json"])
        niche: str = brief_dict.get("niche", "unknown")
        topic: str = brief_dict.get("topic", f"job_{job_id}")
        title_slug = _slugify(topic)

        output_dir = _build_output_dir(self.output_base, niche, title_slug)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("[job %d] Starting — niche=%s topic=%s", job_id, niche, topic)

        try:
            script = await self._step_generate_script(brief_dict)
            mp3_path, ts_path = await self._step_tts(script, niche, output_dir)
            video_path = await self._step_compose_video(
                script, niche, mp3_path, ts_path, output_dir
            )
            output_path = await self._step_write_bundle(
                brief_dict, script, mp3_path, video_path, output_dir
            )
            self.queue.mark_done(job_id, str(output_path))
            logger.info("[job %d] Done — output=%s", job_id, output_path)
            return str(output_path)

        except Exception:
            error = traceback.format_exc()
            logger.error("[job %d] Failed:\n%s", job_id, error)
            self._write_failed_bundle(job_id, brief_dict, error)
            self.queue.mark_failed(job_id, error, retry=True)
            return None

    # ------------------------------------------------------------------
    # Pipeline steps (each blocks in a thread pool to avoid event-loop stalls)
    # ------------------------------------------------------------------

    async def _step_generate_script(self, brief_dict: dict):
        loop = asyncio.get_running_loop()
        brief = ContentBrief(
            niche=brief_dict["niche"],
            topic=brief_dict["topic"],
            hook=brief_dict.get("hook", ""),
            format=brief_dict.get("format", "monologue"),
            length_target_sec=int(brief_dict.get("length_target_sec", 30)),
        )
        script = await loop.run_in_executor(None, partial(generate_script, brief))
        logger.debug("Script generated: %s…", script.hook[:60])
        return script

    async def _step_tts(self, script, niche: str, output_dir: Path):
        full_script = script.as_tagged_script()
        loop = asyncio.get_running_loop()
        mp3_path, ts_path = await loop.run_in_executor(
            None, partial(generate_tts, full_script, niche, output_dir)
        )
        return mp3_path, ts_path

    async def _step_compose_video(self, script, niche: str, mp3_path: Path, ts_path: Path, output_dir: Path):
        video_path = str(output_dir / "video.mp4")
        keywords = script.hook.split()[:8] + [niche.replace("_", " ")]

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            partial(
                self._assembler.assemble,
                audio_path=str(mp3_path),
                timing_metadata=str(ts_path),
                keywords=keywords,
                output_path=video_path,
                niche=niche,
                tmp_dir=output_dir / "tmp",
            ),
        )
        return Path(result)

    async def _step_write_bundle(self, brief_dict: dict, script, mp3_path: Path, video_path: Path, output_dir: Path) -> Path:
        # Copy voice file to canonical name
        voice_dest = output_dir / "voice.mp3"
        if mp3_path != voice_dest:
            voice_dest.write_bytes(mp3_path.read_bytes())

        # script.txt — tagged script
        (output_dir / "script.txt").write_text(script.as_tagged_script(), encoding="utf-8")

        # metadata.json
        metadata = {
            "title": brief_dict.get("topic", ""),
            "hashtags": script.hashtags,
            "niche": brief_dict.get("niche", ""),
            "posting_time": brief_dict.get("posting_time", ""),
            "hook_text": script.hook,
            "format": brief_dict.get("format", "monologue"),
            "duration_sec": brief_dict.get("length_target_sec", 30),
            "voice_style": brief_dict.get("voice_style", ""),
            "trending_audio": brief_dict.get("trending_audio"),
        }
        (output_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return output_dir

    # ------------------------------------------------------------------
    # Failed-job bundle
    # ------------------------------------------------------------------

    def _write_failed_bundle(self, job_id: int, brief_dict: dict, error: str) -> None:
        failed_dir = self.failed_base / str(job_id)
        failed_dir.mkdir(parents=True, exist_ok=True)
        (failed_dir / "brief.json").write_text(
            json.dumps(brief_dict, indent=2), encoding="utf-8"
        )
        (failed_dir / "error.log").write_text(error, encoding="utf-8")
        logger.info("[job %d] Error bundle written to %s", job_id, failed_dir)
