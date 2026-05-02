"""TikTok Content Posting API v2 client with OAuth2 support."""
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_API_BASE = "https://open.tiktokapis.com"

CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    open_id: str
    expires_in: int
    refresh_expires_in: int
    scope: str


class TikTokOAuth2Client:
    def __init__(self, client_key: str, client_secret: str, redirect_uri: str):
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def get_authorization_url(self, scopes: list[str], state: str = "") -> str:
        params = {
            "client_key": self.client_key,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": ",".join(scopes),
            "state": state,
        }
        return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_token(self, code: str) -> TokenSet:
        resp = requests.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            open_id=data["open_id"],
            expires_in=data["expires_in"],
            refresh_expires_in=data["refresh_token_expires_in"],
            scope=data["scope"],
        )

    def refresh_access_token(self, refresh_token: str) -> TokenSet:
        resp = requests.post(
            TIKTOK_TOKEN_URL,
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            open_id=data["open_id"],
            expires_in=data["expires_in"],
            refresh_expires_in=data["refresh_token_expires_in"],
            scope=data["scope"],
        )


class TikTokAPIError(Exception):
    def __init__(self, message: str, error_code: Optional[str] = None, http_status: Optional[int] = None):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


class TikTokContentClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {access_token}"})

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{TIKTOK_API_BASE}{path}"
        resp = self.session.post(url, json=payload, timeout=60)
        if not resp.ok:
            raise TikTokAPIError(
                f"HTTP {resp.status_code}: {resp.text}",
                http_status=resp.status_code,
            )
        data = resp.json()
        if data.get("error", {}).get("code", "ok") != "ok":
            err = data["error"]
            raise TikTokAPIError(
                err.get("message", "unknown error"),
                error_code=err.get("code"),
            )
        return data.get("data", data)

    def init_direct_post(
        self,
        title: str,
        video_path: Path,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        disable_duet: bool = False,
        disable_comment: bool = False,
        disable_stitch: bool = False,
    ) -> dict:
        """Initialize a FILE_UPLOAD direct post. Returns publish_id and upload_url info."""
        video_size = video_path.stat().st_size
        chunk_size = min(CHUNK_SIZE, video_size)
        total_chunk_count = math.ceil(video_size / chunk_size)

        payload = {
            "post_info": {
                "title": title,
                "privacy_level": privacy_level,
                "disable_duet": disable_duet,
                "disable_comment": disable_comment,
                "disable_stitch": disable_stitch,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        }
        return self._post("/v2/post/publish/video/init/", payload)

    def upload_video_chunks(self, upload_url: str, video_path: Path, chunk_size: int, total_chunks: int) -> None:
        """Upload video file to TikTok in chunks via PUT requests."""
        video_size = video_path.stat().st_size
        with open(video_path, "rb") as f:
            for chunk_index in range(total_chunks):
                start = chunk_index * chunk_size
                end = min(start + chunk_size, video_size) - 1
                data = f.read(chunk_size)
                resp = requests.put(
                    upload_url,
                    data=data,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {start}-{end}/{video_size}",
                        "Content-Length": str(len(data)),
                    },
                    timeout=300,
                )
                if not resp.ok:
                    raise TikTokAPIError(
                        f"Chunk upload failed at index {chunk_index}: HTTP {resp.status_code}",
                        http_status=resp.status_code,
                    )

    def get_publish_status(self, publish_id: str) -> dict:
        """Poll publish status. Returns status dict with post_id when done."""
        return self._post("/v2/post/publish/status/fetch/", {"publish_id": publish_id})

    def wait_for_publish(self, publish_id: str, poll_interval: int = 5, max_wait: int = 300) -> dict:
        """Poll until published or failed. Returns final status dict."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            status = self.get_publish_status(publish_id)
            state = status.get("status", "")
            if state in ("PUBLISH_COMPLETE", "SUCCESS"):
                return status
            if state in ("FAILED", "ERROR"):
                raise TikTokAPIError(f"Publish failed: {status.get('fail_reason', 'unknown')}")
            time.sleep(poll_interval)
        raise TikTokAPIError(f"Publish timed out after {max_wait}s for publish_id={publish_id}")

    def publish_video(
        self,
        video_path: Path,
        title: str,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        disable_duet: bool = False,
        disable_comment: bool = False,
        disable_stitch: bool = False,
    ) -> dict:
        """Full publish flow: init → upload → poll → return post info."""
        init_data = self.init_direct_post(
            title=title,
            video_path=video_path,
            privacy_level=privacy_level,
            disable_duet=disable_duet,
            disable_comment=disable_comment,
            disable_stitch=disable_stitch,
        )
        publish_id = init_data["publish_id"]
        upload_url = init_data["upload_url"]
        chunk_size = init_data.get("chunk_size", CHUNK_SIZE)
        total_chunks = init_data.get("total_chunk_count", 1)

        self.upload_video_chunks(upload_url, video_path, chunk_size, total_chunks)
        return self.wait_for_publish(publish_id)
