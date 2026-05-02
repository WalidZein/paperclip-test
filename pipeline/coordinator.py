"""RunCoordinator: single-video end-to-end pipeline (no queue)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from publisher.config import load_niche_config
from publisher.uploader import VideoUploader
from script_generator.generator import ContentBrief, generate_script
from video_assembly.assembler import VideoAssembler
from video_assembly.tts import generate_tts

from .alerts import SlackAlerter
from .run_log import RunLog
from .runner import _build_output_dir, _slugify

logger = logging.getLogger(__name__)


class RunCoordinator:
    """Orchestrates one full run: script → TTS → video → publish."""

    def __init__(
        self,
        config_path: str | Path = "configs/niches.yaml",
        output_base: str | Path = "output",
        log_dir: str | Path = "run_logs",
        pexels_api_key: Optional[str] = None,
        pixabay_api_key: Optional[str] = None,
        stock_cache_dir: Optional[str | Path] = None,
        tiktok_access_token: Optional[str] = None,
        slack_webhook_url: Optional[str] = None,
        dry_run: bool = False,
    ) -> None:
        self.config_path = Path(config_path)
        self.output_base = Path(output_base)
        self.log_dir = Path(log_dir)
        self.dry_run = dry_run
        self._tiktok_token = tiktok_access_token or os.environ.get("TIKTOK_ACCESS_TOKEN")
        self._assembler = VideoAssembler(
            pexels_api_key=pexels_api_key or os.environ.get("PEXELS_API_KEY"),
            pixabay_api_key=pixabay_api_key or os.environ.get("PIXABAY_API_KEY"),
            cache_dir=Path(stock_cache_dir) if stock_cache_dir else None,
        )
        self._alerter = SlackAlerter(webhook_url=slack_webhook_url)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, niche: str, topic: str) -> dict:
        """Synchronous wrapper. Returns the run log dict."""
        return asyncio.run(self._run_async(niche, topic))

    # ------------------------------------------------------------------
    # Async orchestration
    # ------------------------------------------------------------------

    async def _run_async(self, niche: str, topic: str) -> dict:
        run_log = RunLog.new(niche=niche, topic=topic)
        title_slug = _slugify(topic)
        output_dir = _build_output_dir(self.output_base, niche, title_slug)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("[run %s] Starting — niche=%s topic=%s", run_log.run_id, niche, topic)

        # Validate niche config before doing any heavy work
        try:
            niche_cfg = load_niche_config(self.config_path, niche)
        except (ValueError, FileNotFoundError) as exc:
            run_log.finalize("failed", error=str(exc))
            run_log.write(self.log_dir)
            self._alerter.alert_run_failed(niche, topic, str(exc), run_log.run_id)
            logger.error("[run %s] Config validation failed: %s", run_log.run_id, exc)
            return run_log.to_dict()

        brief = ContentBrief(
            niche=niche,
            topic=topic,
            hook="",
            format="monologue",
            length_target_sec=30,
        )

        # Stage 1: Script generation
        script = await self._run_stage(
            run_log, "script_generation", lambda: generate_script(brief)
        )
        if script is None:
            return self._finalize_failed(run_log, niche, topic)

        # Stage 2: TTS
        tts_result = await self._run_stage(
            run_log,
            "tts",
            lambda: generate_tts(script.as_tagged_script(), niche, output_dir),
        )
        if tts_result is None:
            return self._finalize_failed(run_log, niche, topic)
        mp3_path, ts_path = tts_result

        # Stage 3: Video assembly
        keywords = script.hook.split()[:8] + [niche.replace("_", " ")]
        _mp3, _ts, _kw, _od, _ni = mp3_path, ts_path, keywords, output_dir, niche
        video_path = await self._run_stage(
            run_log,
            "video_assembly",
            lambda: Path(
                self._assembler.assemble(
                    audio_path=str(_mp3),
                    timing_metadata=str(_ts),
                    keywords=_kw,
                    output_path=str(_od / "video.mp4"),
                    niche=_ni,
                    tmp_dir=_od / "tmp",
                )
            ),
        )
        if video_path is None:
            return self._finalize_failed(run_log, niche, topic)

        # Stage 4: Publish
        if self.dry_run:
            logger.info("[run %s] dry_run=True — skipping publish", run_log.run_id)
            run_log.record_stage("publish", "skipped", 0.0)
            publish_url = None
        else:
            _vp, _ni2, _tp, _nc, _od2, _tok = (
                video_path, niche, topic, niche_cfg, output_dir, self._tiktok_token
            )
            pub_result = await self._run_stage(
                run_log,
                "publish",
                lambda: VideoUploader(
                    access_token=_tok,
                    log_file=_od2 / "publish.log",
                ).upload(_vp, _ni2, _tp, _nc),
            )
            if pub_result is None:
                return self._finalize_failed(run_log, niche, topic)
            publish_url = pub_result.get("url", "")

        run_log.finalize("success", publish_url=publish_url)
        log_path = run_log.write(self.log_dir)
        logger.info("[run %s] Done — log=%s", run_log.run_id, log_path)
        return run_log.to_dict()

    # ------------------------------------------------------------------
    # Stage helper
    # ------------------------------------------------------------------

    async def _run_stage(self, run_log: RunLog, name: str, fn) -> Optional[object]:
        t0 = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, fn)
            run_log.record_stage(name, "success", time.perf_counter() - t0)
            return result
        except Exception as exc:
            run_log.record_stage(name, "failed", time.perf_counter() - t0, error=str(exc))
            logger.error("[run %s] Stage '%s' failed: %s", run_log.run_id, name, exc)
            return None

    def _finalize_failed(self, run_log: RunLog, niche: str, topic: str) -> dict:
        error = next(
            (s.error for s in reversed(run_log.stages) if s.status == "failed"),
            "unknown error",
        )
        run_log.finalize("failed", error=error)
        run_log.write(self.log_dir)
        self._alerter.alert_run_failed(niche, topic, error or "", run_log.run_id)
        return run_log.to_dict()
