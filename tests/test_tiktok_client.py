"""Unit tests for TikTok API client."""
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from publisher.tiktok_client import (
    TikTokAPIError,
    TikTokContentClient,
    TikTokOAuth2Client,
)


class TestTikTokOAuth2Client:
    def setup_method(self):
        self.client = TikTokOAuth2Client(
            client_key="test_key",
            client_secret="test_secret",
            redirect_uri="https://example.com/callback",
        )

    def test_get_authorization_url(self):
        url = self.client.get_authorization_url(["video.publish"])
        assert "client_key=test_key" in url
        assert "redirect_uri=" in url
        assert "video.publish" in url
        assert "response_type=code" in url

    def test_exchange_code_for_token(self):
        mock_response = {
            "access_token": "tok_abc",
            "refresh_token": "ref_abc",
            "open_id": "user123",
            "expires_in": 86400,
            "refresh_token_expires_in": 2592000,
            "scope": "video.publish",
        }
        with patch("publisher.tiktok_client.requests.post") as mock_post:
            mock_post.return_value = Mock(ok=True, json=lambda: mock_response)
            token = self.client.exchange_code_for_token("auth_code_123")

        assert token.access_token == "tok_abc"
        assert token.refresh_token == "ref_abc"
        assert token.open_id == "user123"

    def test_refresh_access_token(self):
        mock_response = {
            "access_token": "tok_new",
            "refresh_token": "ref_new",
            "open_id": "user123",
            "expires_in": 86400,
            "refresh_token_expires_in": 2592000,
            "scope": "video.publish",
        }
        with patch("publisher.tiktok_client.requests.post") as mock_post:
            mock_post.return_value = Mock(ok=True, json=lambda: mock_response)
            token = self.client.refresh_access_token("old_refresh_token")

        assert token.access_token == "tok_new"


class TestTikTokContentClient:
    def setup_method(self):
        self.client = TikTokContentClient(access_token="test_access_token")

    def _mock_api_response(self, data: dict):
        resp = Mock()
        resp.ok = True
        resp.json = lambda: {"error": {"code": "ok", "message": ""}, "data": data}
        return resp

    def test_init_direct_post(self, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * (15 * 1024 * 1024))  # 15 MB

        expected = {"publish_id": "pub_123", "upload_url": "https://upload.example.com", "chunk_size": 10485760, "total_chunk_count": 2}

        with patch.object(self.client.session, "post", return_value=self._mock_api_response(expected)):
            result = self.client.init_direct_post(title="Test Title", video_path=video)

        assert result["publish_id"] == "pub_123"
        assert result["upload_url"] == "https://upload.example.com"

    def test_init_direct_post_api_error(self, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1024)

        error_resp = Mock()
        error_resp.ok = True
        error_resp.json = lambda: {"error": {"code": "video.too_large", "message": "File too large"}, "data": {}}

        with patch.object(self.client.session, "post", return_value=error_resp):
            with pytest.raises(TikTokAPIError, match="File too large"):
                self.client.init_direct_post("title", video)

    def test_upload_video_chunks(self, tmp_path):
        video = tmp_path / "test.mp4"
        content = b"x" * (5 * 1024 * 1024)
        video.write_bytes(content)

        with patch("publisher.tiktok_client.requests.put") as mock_put:
            mock_put.return_value = Mock(ok=True)
            self.client.upload_video_chunks(
                upload_url="https://upload.example.com",
                video_path=video,
                chunk_size=5 * 1024 * 1024,
                total_chunks=1,
            )

        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert call_args[1]["headers"]["Content-Range"] == f"bytes 0-{len(content) - 1}/{len(content)}"

    def test_upload_video_chunks_failure(self, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1024)

        with patch("publisher.tiktok_client.requests.put") as mock_put:
            mock_put.return_value = Mock(ok=False, status_code=500)
            with pytest.raises(TikTokAPIError, match="Chunk upload failed"):
                self.client.upload_video_chunks("https://upload.example.com", video, 1024, 1)

    def test_get_publish_status_complete(self):
        expected = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_abc123"}

        with patch.object(self.client.session, "post", return_value=self._mock_api_response(expected)):
            result = self.client.get_publish_status("pub_123")

        assert result["status"] == "PUBLISH_COMPLETE"
        assert result["publicaly_available_post_id"] == "post_abc123"

    def test_wait_for_publish_success(self):
        published = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_done"}
        with patch.object(self.client, "get_publish_status", return_value=published):
            result = self.client.wait_for_publish("pub_123", poll_interval=0)
        assert result["publicaly_available_post_id"] == "post_done"

    def test_wait_for_publish_failure(self):
        failed = {"status": "FAILED", "fail_reason": "content policy violation"}
        with patch.object(self.client, "get_publish_status", return_value=failed):
            with pytest.raises(TikTokAPIError, match="content policy violation"):
                self.client.wait_for_publish("pub_123", poll_interval=0)

    def test_wait_for_publish_timeout(self):
        processing = {"status": "PROCESSING_UPLOAD"}
        with patch.object(self.client, "get_publish_status", return_value=processing):
            with patch("publisher.tiktok_client.time.sleep"):
                with pytest.raises(TikTokAPIError, match="timed out"):
                    self.client.wait_for_publish("pub_123", poll_interval=0, max_wait=0)

    def test_publish_video_full_flow(self, tmp_path):
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1024)

        init_data = {
            "publish_id": "pub_xyz",
            "upload_url": "https://upload.tiktok.com/v1/video",
            "chunk_size": 1024,
            "total_chunk_count": 1,
        }
        final_status = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "post_xyz"}

        with patch.object(self.client, "init_direct_post", return_value=init_data) as mock_init, \
             patch.object(self.client, "upload_video_chunks") as mock_upload, \
             patch.object(self.client, "wait_for_publish", return_value=final_status) as mock_poll:
            result = self.client.publish_video(video, "Test video")

        mock_init.assert_called_once()
        mock_upload.assert_called_once_with("https://upload.tiktok.com/v1/video", video, 1024, 1)
        mock_poll.assert_called_once_with("pub_xyz")
        assert result["publicaly_available_post_id"] == "post_xyz"
