"""Slack webhook alerter for pipeline events."""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class SlackAlerter:
    def __init__(self, webhook_url: Optional[str] = None) -> None:
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL")

    def _send(self, text: str) -> None:
        if not self.webhook_url:
            logger.debug("No Slack webhook configured — skipping alert")
            return
        try:
            resp = requests.post(self.webhook_url, json={"text": text}, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Slack alert failed: %s", exc)

    def alert_run_failed(self, niche: str, topic: str, error: str, run_id: str) -> None:
        self._send(
            f":x: *Pipeline run failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Niche: `{niche}` | Topic: `{topic}`\n"
            f"Error: `{error[:200]}`"
        )

    def alert_high_failure_rate(self, failed: int, total: int) -> None:
        rate = failed / total if total > 0 else 0
        self._send(
            f":warning: *High pipeline failure rate*\n"
            f"Failed: {failed}/{total} ({rate:.0%})"
        )
