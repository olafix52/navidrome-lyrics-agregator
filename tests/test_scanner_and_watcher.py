"""Unit tests for LibraryScanner, DirectoryWatcher, and AppleMusicProvider."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from src.config import AppConfig, ProviderConfig, load_config
from src.matcher import LyricsMatcher
from src.models import (
    LyricsFormat,
    LyricsResult,
    LyricsSyncType,
    MatchStatus,
    ProcessResult,
    TrackMetadata,
)
from src.providers.apple_music import AppleMusicProvider
from src.scanner import LibraryScanner
from src.watcher import AudioFileEventHandler


@pytest.mark.asyncio
async def test_apple_music_provider():
    provider = AppleMusicProvider(config=ProviderConfig())
    track = TrackMetadata(
        file_path=Path("/music/Taylor Swift - Cruel Summer.flac"),
        title="Cruel Summer",
        artist="Taylor Swift",
        duration=178.0,
    )

    mock_search = MagicMock()
    mock_search.json.return_value = [{"id": "am_123", "appleMusicId": "am_123", "musicNames": ["Cruel Summer"], "artistNames": ["Taylor Swift"]}]

    mock_get = MagicMock()
    mock_get.json.return_value = {
        "id": "am_123",
        "ttml": "<tt xmlns='http://www.w3.org/ns/ttml'><body><p><span begin='00:01.00' end='00:02.00'>Fever dream high</span></p></body></tt>",
        "trackName": "Cruel Summer",
        "artistName": "Taylor Swift",
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_get]):
        result = await provider.get_lyrics(track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "Fever dream high" in result.content


@pytest.mark.asyncio
async def test_scanner_discover_and_summary(tmp_path: Path):
    # Create dummy music folder structure
    artist_dir = tmp_path / "Queen" / "A Night at the Opera"
    artist_dir.mkdir(parents=True)
    (artist_dir / "01 - Death on Two Legs.flac").write_bytes(b"dummy audio")
    (artist_dir / "02 - Lazing on a Sunday Afternoon.mp3").write_bytes(b"dummy audio")
    (artist_dir / "cover.jpg").write_bytes(b"dummy image")

    config = AppConfig(music_dir=tmp_path, concurrency=2)
    mock_matcher = MagicMock()

    scanner = LibraryScanner(config, mock_matcher)
    files = scanner.discover_audio_files(tmp_path)
    assert len(files) == 2

    # Test summary rendering without errors
    dummy_results = [
        ProcessResult(
            file_path=files[0],
            status=MatchStatus.SUCCESS,
            provider="amll",
            format=LyricsFormat.TTML,
        ),
        ProcessResult(
            file_path=files[1],
            status=MatchStatus.SKIPPED,
        ),
    ]
    scanner.display_summary(dummy_results)


def test_fast_discover_audio_files(tmp_path: Path):
    from src.tag_reader import fast_discover_audio_files

    # 1. Non-existent path
    assert fast_discover_audio_files(tmp_path / "does_not_exist") == []

    # 2. Nested directory with audio and non-audio files
    album1 = tmp_path / "Artist" / "Album1"
    album2 = tmp_path / "Artist" / "Album2"
    album1.mkdir(parents=True)
    album2.mkdir(parents=True)

    f1 = album1 / "01.flac"
    f2 = album1 / "02.opus"
    f3 = album2 / "01.mp3"
    non_audio1 = album1 / "cover.jpg"
    non_audio2 = album2 / "lyrics.lrc"

    f1.write_bytes(b"flac")
    f2.write_bytes(b"opus")
    f3.write_bytes(b"mp3")
    non_audio1.write_bytes(b"jpg")
    non_audio2.write_bytes(b"lrc")

    discovered = fast_discover_audio_files(tmp_path)
    assert len(discovered) == 3
    assert f1 in discovered
    assert f2 in discovered
    assert f3 in discovered
    assert non_audio1 not in discovered
    assert non_audio2 not in discovered

    # 3. Single file as root_dir
    assert fast_discover_audio_files(f1) == [f1]
    assert fast_discover_audio_files(non_audio1) == []


@pytest.mark.asyncio
async def test_watchdog_event_handler(tmp_path: Path):
    loop = asyncio.get_running_loop()
    mock_matcher = MagicMock()
    mock_matcher.process_track = AsyncMock(return_value=ProcessResult(
        file_path=tmp_path / "song.flac",
        status=MatchStatus.SUCCESS,
        provider="amll",
        format=LyricsFormat.TTML,
    ))

    handler = AudioFileEventHandler(
        loop=loop,
        matcher=mock_matcher,
        debounce_seconds=0.01,
    )

    audio_file = tmp_path / "song.flac"
    audio_file.write_bytes(b"dummy audio")

    await handler._debounce_and_process(audio_file)


def test_load_config_custom_file(tmp_path: Path):
    custom_cfg = tmp_path / "custom.yaml"
    custom_cfg.write_text("concurrency: 8\nlog_level: DEBUG\n", encoding="utf-8")
    cfg = load_config(custom_cfg)
    assert cfg.concurrency == 8
    assert cfg.log_level == "DEBUG"


def test_config_deep_merge():
    from src.config import _deep_merge
    base = {
        "concurrency": 2,
        "providers": {
            "amll": {"enabled": True, "timeout": 10},
            "lrclib": {"enabled": False},
        },
    }
    override = {
        "concurrency": 5,
        "providers": {
            "amll": {"timeout": 20},
            "netease": {"enabled": True},
        },
    }
    merged = _deep_merge(base, override)
    assert merged["concurrency"] == 5
    assert merged["providers"]["amll"]["enabled"] is True
    assert merged["providers"]["amll"]["timeout"] == 20
    assert merged["providers"]["lrclib"]["enabled"] is False
    assert merged["providers"]["netease"]["enabled"] is True


@pytest.mark.asyncio
async def test_scanner_handles_worker_exceptions(tmp_path: Path):
    track1 = tmp_path / "track1.mp3"
    track2 = tmp_path / "track2.mp3"
    track1.write_bytes(b"dummy1")
    track2.write_bytes(b"dummy2")

    config = AppConfig(music_dir=tmp_path, concurrency=2)
    mock_matcher = MagicMock()
    scanner = LibraryScanner(config, mock_matcher)

    # Make _process_single_file raise on track1 and succeed on track2
    async def _mock_process(fp: Path):
        if fp == track1:
            raise RuntimeError("Unexpected failure")
        return ProcessResult(file_path=fp, status=MatchStatus.SUCCESS)

    with patch.object(scanner, "_process_single_file", side_effect=_mock_process):
        results = await scanner.process_files([track1, track2], show_progress=False)
        # Exception should be filtered out, only successful ProcessResult returned
        assert len(results) == 1
        assert results[0].file_path == track2

    # Also test with show_progress=True
    with patch.object(scanner, "_process_single_file", side_effect=_mock_process):
        results = await scanner.process_files([track1, track2], show_progress=True)
        assert len(results) == 1
        assert results[0].file_path == track2


def test_ttml_rounding_boundary():
    from src.ttml import format_ttml_timestamp
    # 59.9999 seconds should round to 01:00.000 rather than 00:60.000
    assert format_ttml_timestamp(59.9999) == "01:00.000"
    assert format_ttml_timestamp(59.9994) == "00:59.999"
    assert format_ttml_timestamp(119.9999) == "02:00.000"


def test_iter_discover_audio_files(tmp_path: Path):
    from src.tag_reader import iter_discover_audio_files

    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    t1 = album / "01.flac"
    t2 = album / "02.mp3"
    t1.write_bytes(b"flac")
    t2.write_bytes(b"mp3")

    items = list(iter_discover_audio_files(tmp_path))
    assert len(items) == 2
    assert t1 in items
    assert t2 in items


@pytest.mark.asyncio
async def test_scanner_streaming_scan_and_process(tmp_path: Path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    t1 = album / "01.flac"
    t2 = album / "02.mp3"
    t1.write_bytes(b"flac")
    t2.write_bytes(b"mp3")

    config = AppConfig(music_dir=tmp_path, concurrency=2)
    mock_matcher = MagicMock()
    scanner = LibraryScanner(config, mock_matcher)

    async def _mock_process(fp: Path):
        return ProcessResult(file_path=fp, status=MatchStatus.SUCCESS, provider="lrclib")

    with patch.object(scanner, "_process_single_file", side_effect=_mock_process):
        results = await scanner.scan_and_process(show_progress=False)
        assert len(results) == 2
        paths = {r.file_path for r in results}
        assert paths == {t1, t2}


