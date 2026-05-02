import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

from video_assembly.stock_footage import StockFootageFetcher, FootageClip, NICHE_KEYWORDS


class TestStockFootageFetcher:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fetcher = StockFootageFetcher(
            pexels_api_key="fake_pexels",
            pixabay_api_key="fake_pixabay",
            cache_dir=self.tmp,
        )

    def test_init_reads_env(self, monkeypatch):
        monkeypatch.setenv("PEXELS_API_KEY", "env_pexels")
        monkeypatch.setenv("PIXABAY_API_KEY", "env_pixabay")
        f = StockFootageFetcher(cache_dir=self.tmp)
        assert f.pexels_key == "env_pexels"
        assert f.pixabay_key == "env_pixabay"

    def test_init_explicit_keys_override_env(self, monkeypatch):
        monkeypatch.setenv("PEXELS_API_KEY", "env_pexels")
        f = StockFootageFetcher(pexels_api_key="explicit", cache_dir=self.tmp)
        assert f.pexels_key == "explicit"

    def test_cache_dir_created(self):
        new_dir = self.tmp / "subdir"
        StockFootageFetcher(cache_dir=new_dir)
        assert new_dir.exists()

    @patch("video_assembly.stock_footage.requests.get")
    def test_fetch_pexels_returns_clip(self, mock_get):
        # Two requests: search + download
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = {
            "videos": [{
                "id": 101,
                "duration": 12,
                "video_files": [
                    {"link": "http://example.com/v.mp4", "width": 1080, "height": 1920}
                ],
            }]
        }
        dl_resp = MagicMock()
        dl_resp.raise_for_status = MagicMock()
        dl_resp.iter_content = MagicMock(return_value=[b"fake_video_bytes"])
        mock_get.side_effect = [search_resp, dl_resp]

        clip = self.fetcher._fetch_pexels("nature", 30)

        assert clip is not None
        assert clip.source == "pexels"
        assert clip.keyword == "nature"
        assert clip.duration == 12
        assert Path(clip.path).exists()

    @patch("video_assembly.stock_footage.requests.get")
    def test_fetch_pexels_skips_short_clips(self, mock_get):
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = {
            "videos": [
                {"id": 1, "duration": 1, "video_files": [{"link": "http://x.com/a.mp4", "width": 1080, "height": 1920}]},
                {"id": 2, "duration": 2, "video_files": [{"link": "http://x.com/b.mp4", "width": 1080, "height": 1920}]},
            ]
        }
        mock_get.return_value = search_resp

        clip = self.fetcher._fetch_pexels("landscape", 30)
        assert clip is None

    @patch("video_assembly.stock_footage.requests.get")
    def test_fetch_pixabay_returns_clip(self, mock_get):
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = {
            "hits": [{
                "id": 202,
                "duration": 8,
                "videos": {"medium": {"url": "http://pixabay.com/v.mp4"}},
            }]
        }
        dl_resp = MagicMock()
        dl_resp.raise_for_status = MagicMock()
        dl_resp.iter_content = MagicMock(return_value=[b"data"])
        mock_get.side_effect = [search_resp, dl_resp]

        clip = self.fetcher._fetch_pixabay("office", 30)

        assert clip is not None
        assert clip.source == "pixabay"
        assert clip.duration == 8

    @patch.object(StockFootageFetcher, "_fetch_pexels", return_value=None)
    @patch("video_assembly.stock_footage.requests.get")
    def test_fetch_single_falls_back_to_pixabay(self, mock_get, _mock_pexels):
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = {
            "hits": [{"id": 5, "duration": 7, "videos": {"medium": {"url": "http://pb.com/v.mp4"}}}]
        }
        dl_resp = MagicMock()
        dl_resp.raise_for_status = MagicMock()
        dl_resp.iter_content = MagicMock(return_value=[b"data"])
        mock_get.side_effect = [search_resp, dl_resp]

        clip = self.fetcher._fetch_single("test", 30)
        assert clip is not None
        assert clip.source == "pixabay"

    @patch.object(StockFootageFetcher, "_fetch_single")
    def test_fetch_for_duration_accumulates_clips(self, mock_fetch):
        mock_fetch.return_value = FootageClip(
            path="/tmp/fake.mp4", duration=10.0, source="pexels", keyword="test"
        )
        clips = self.fetcher.fetch_for_duration(["test"], 25.0)
        total = sum(c.duration for c in clips)
        assert total >= 25.0
        assert len(clips) >= 3

    @patch.object(StockFootageFetcher, "_fetch_single", return_value=None)
    def test_fetch_for_duration_returns_empty_on_failure(self, _mock):
        clips = self.fetcher.fetch_for_duration(["test"], 30.0)
        assert clips == []

    @patch("video_assembly.stock_footage.requests.get")
    def test_download_caches_file(self, mock_get):
        dl_resp = MagicMock()
        dl_resp.raise_for_status = MagicMock()
        dl_resp.iter_content = MagicMock(return_value=[b"cached"])
        mock_get.return_value = dl_resp

        path = self.fetcher._download("http://example.com/v.mp4", "test_cache.mp4")
        assert path is not None
        assert Path(path).exists()

        # Second call should not hit requests.get again
        mock_get.reset_mock()
        path2 = self.fetcher._download("http://example.com/v.mp4", "test_cache.mp4")
        assert path2 == path
        mock_get.assert_not_called()

    @patch("video_assembly.stock_footage.requests.get")
    def test_best_pexels_file_prefers_portrait(self, _mock_get):
        files = [
            {"link": "http://a.com/land.mp4", "width": 1920, "height": 1080},
            {"link": "http://a.com/port.mp4", "width": 1080, "height": 1920},
        ]
        best = self.fetcher._best_pexels_file(files)
        assert best["height"] == 1920


class TestNicheKeywords:
    def test_niche_keywords_finance(self):
        assert "city skyline" in NICHE_KEYWORDS["finance"]
        assert "money" in NICHE_KEYWORDS["finance"]

    def test_niche_keywords_dark_psychology(self):
        assert "shadows" in NICHE_KEYWORDS["dark psychology"]
        assert "dark room" in NICHE_KEYWORDS["dark_psychology"]

    def test_niche_keywords_tech_variants(self):
        # ai, tech, ai/tech should all map to same keywords
        assert NICHE_KEYWORDS["ai"] == NICHE_KEYWORDS["tech"]
        assert NICHE_KEYWORDS["ai/tech"] == NICHE_KEYWORDS["ai"]
        assert "laptop" in NICHE_KEYWORDS["ai"]


class TestBestPexelsFile1080p:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fetcher = StockFootageFetcher(pexels_api_key="x", cache_dir=self.tmp)

    def test_rejects_sub_1080p_files(self):
        files = [
            {"link": "http://a.com/sd.mp4", "width": 720, "height": 1280},
            {"link": "http://a.com/sd2.mp4", "width": 480, "height": 854},
        ]
        assert self.fetcher._best_pexels_file(files) is None

    def test_accepts_1080p_portrait(self):
        files = [{"link": "http://a.com/hd.mp4", "width": 1080, "height": 1920}]
        best = self.fetcher._best_pexels_file(files)
        assert best is not None
        assert best["width"] == 1080

    def test_prefers_portrait_over_landscape_when_both_hd(self):
        files = [
            {"link": "http://a.com/land.mp4", "width": 1920, "height": 1080},
            {"link": "http://a.com/port.mp4", "width": 1080, "height": 1920},
        ]
        best = self.fetcher._best_pexels_file(files)
        assert best["height"] == 1920

    def test_falls_back_to_landscape_when_no_portrait(self):
        files = [{"link": "http://a.com/land.mp4", "width": 1920, "height": 1080}]
        best = self.fetcher._best_pexels_file(files)
        assert best is not None
        assert best["width"] == 1920


class TestGetClip:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fetcher = StockFootageFetcher(pexels_api_key="fake_pexels", cache_dir=self.tmp)

    @patch.object(StockFootageFetcher, "_fetch_pexels")
    def test_get_clip_uses_niche_keywords(self, mock_fetch):
        fake_path = str(self.tmp / "fake.mp4")
        Path(fake_path).write_bytes(b"x")
        mock_fetch.return_value = FootageClip(
            path=fake_path, duration=10.0, source="pexels", keyword="city skyline"
        )

        result = self.fetcher.get_clip("finance", [])

        assert result == fake_path
        # First keyword tried should be from the finance niche map
        first_kw = mock_fetch.call_args[0][0]
        assert first_kw in NICHE_KEYWORDS["finance"]

    @patch.object(StockFootageFetcher, "_fetch_pexels")
    def test_get_clip_merges_topic_tags(self, mock_fetch):
        mock_fetch.return_value = None

        self.fetcher.get_clip("finance", ["stocks", "trading"])

        called_kws = [c[0][0] for c in mock_fetch.call_args_list]
        assert "stocks" in called_kws
        assert "trading" in called_kws

    @patch.object(StockFootageFetcher, "_fetch_pexels")
    def test_get_clip_cache_hit_skips_api(self, mock_fetch):
        # Pre-populate cache: write a file and register it in the index.
        cached_file = self.tmp / "pexels_cached.mp4"
        cached_file.write_bytes(b"video")
        kw = "city skyline"
        key = StockFootageFetcher._kw_hash(kw)
        self.fetcher._keyword_index[key] = cached_file.name
        self.fetcher._save_keyword_index()

        result = self.fetcher.get_clip("finance", [])

        assert result == str(cached_file)
        mock_fetch.assert_not_called()

    @patch.object(StockFootageFetcher, "_fetch_pexels")
    def test_get_clip_stale_index_entry_refetches(self, mock_fetch):
        # Register an index entry pointing to a non-existent file.
        kw = "city skyline"
        key = StockFootageFetcher._kw_hash(kw)
        self.fetcher._keyword_index[key] = "missing.mp4"

        fresh_path = str(self.tmp / "pexels_fresh.mp4")
        Path(fresh_path).write_bytes(b"x")
        mock_fetch.return_value = FootageClip(
            path=fresh_path, duration=8.0, source="pexels", keyword=kw
        )

        result = self.fetcher.get_clip("finance", [])

        assert result == fresh_path
        mock_fetch.assert_called()

    @patch.object(StockFootageFetcher, "_fetch_pexels", return_value=None)
    def test_get_clip_returns_none_when_no_results(self, _mock):
        result = self.fetcher.get_clip("finance", [])
        assert result is None

    @patch.object(StockFootageFetcher, "_fetch_pexels")
    def test_get_clip_saves_to_keyword_index(self, mock_fetch):
        clip_path = str(self.tmp / "pexels_999.mp4")
        Path(clip_path).write_bytes(b"x")
        mock_fetch.return_value = FootageClip(
            path=clip_path, duration=10.0, source="pexels", keyword="city skyline"
        )

        self.fetcher.get_clip("finance", [])

        index_path = self.tmp / "keyword_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert len(index) >= 1

    def test_get_clip_unknown_niche_uses_topic_tags_only(self):
        with patch.object(StockFootageFetcher, "_fetch_pexels", return_value=None) as mock_fetch:
            self.fetcher.get_clip("unknown_niche", ["sunset", "beach"])
            called_kws = [c[0][0] for c in mock_fetch.call_args_list]
            assert "sunset" in called_kws
            assert "beach" in called_kws

    def test_get_clip_unknown_niche_no_tags_returns_none(self):
        result = self.fetcher.get_clip("unknown_niche", [])
        assert result is None
