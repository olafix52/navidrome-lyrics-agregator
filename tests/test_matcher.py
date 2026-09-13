"""Unit tests for the lyrics matcher and cascade orchestration."""

from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from src.config import AppConfig
from src.matcher import LyricsMatcher
from src.models import (
    LyricsFormat,
    LyricsResult,
    LyricsSyncType,
    MatchStatus,
    TrackMetadata,
)
from src.providers.base import BaseLyricsProvider


class MockProvider(BaseLyricsProvider):
    def __init__(self, name: str, result: LyricsResult | None = None):
        super().__init__()
        self.name = name
        self.mock_result = result

    async def get_lyrics(self, track: TrackMetadata):
        return self.mock_result


@pytest.mark.asyncio
async def test_matcher_cascade_success(tmp_path: Path):
    audio_path = tmp_path / "Queen - Bohemian Rhapsody.flac"
    audio_path.write_bytes(b"dummy")

    track = TrackMetadata(
        file_path=audio_path,
        title="Bohemian Rhapsody",
        artist="Queen",
        duration=354.0,
    )

    p1 = MockProvider("p1", None)
    p2 = MockProvider(
        "p2",
        LyricsResult(
            content="<tt>Mama</tt>",
            format=LyricsFormat.TTML,
            sync_type=LyricsSyncType.WORD_SYNC,
            provider_name="p2",
            duration=355.0,
            title="Bohemian Rhapsody",
            artist="Queen",
        ),
    )
    p3 = MockProvider("p3", None)

    config = AppConfig(music_dir=tmp_path, overwrite=False)
    matcher = LyricsMatcher(config, [p1, p2, p3])

    result = await matcher.process_track(track)

    assert result.status == MatchStatus.SUCCESS
    assert result.provider == "p2"
    assert result.format == LyricsFormat.TTML
    assert (tmp_path / "Queen - Bohemian Rhapsody.ttml").exists()


@pytest.mark.asyncio
async def test_matcher_duration_rejection(tmp_path: Path):
    audio_path = tmp_path / "Queen - Bohemian Rhapsody.flac"
    audio_path.write_bytes(b"dummy")

    track = TrackMetadata(
        file_path=audio_path,
        title="Bohemian Rhapsody",
        artist="Queen",
        duration=354.0,
    )

    # Provider returns live version with very different duration
    p1 = MockProvider(
        "p1",
        LyricsResult(
            content="[00:01.00] Live text",
            format=LyricsFormat.LRC,
            sync_type=LyricsSyncType.LINE_SYNC,
            provider_name="p1",
            duration=420.0,
            title="Bohemian Rhapsody",
            artist="Queen",
        ),
    )

    config = AppConfig(music_dir=tmp_path, duration_tolerance_seconds=2.5)
    matcher = LyricsMatcher(config, [p1])

    result = await matcher.process_track(track)
    assert result.status == MatchStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_matcher_word_sync_precedence_over_line_sync(tmp_path: Path):
    audio_path = tmp_path / "Queen - Bohemian Rhapsody.flac"
    audio_path.write_bytes(b"dummy")

    track = TrackMetadata(
        file_path=audio_path,
        title="Bohemian Rhapsody",
        artist="Queen",
        duration=354.0,
    )

    # Provider 1 (e.g. Apple Music) returns LINE_SYNC
    p1 = MockProvider(
        "apple_music",
        LyricsResult(
            content="[00:01.00] Line synced text",
            format=LyricsFormat.LRC,
            sync_type=LyricsSyncType.LINE_SYNC,
            provider_name="apple_music",
            duration=354.0,
            title="Bohemian Rhapsody",
            artist="Queen",
        ),
    )

    # Provider 2 (e.g. Unison / LRCLIB) returns WORD_SYNC
    p2 = MockProvider(
        "unison",
        LyricsResult(
            content="<tt><span>Word synced</span></tt>",
            format=LyricsFormat.TTML,
            sync_type=LyricsSyncType.WORD_SYNC,
            provider_name="unison",
            duration=354.0,
            title="Bohemian Rhapsody",
            artist="Queen",
        ),
    )

    config = AppConfig(music_dir=tmp_path, overwrite=False)
    matcher = LyricsMatcher(config, [p1, p2])

    result = await matcher.process_track(track)
    assert result.status == MatchStatus.SUCCESS
    assert result.provider == "unison"
    assert result.format == LyricsFormat.TTML
    assert (tmp_path / "Queen - Bohemian Rhapsody.ttml").exists()
