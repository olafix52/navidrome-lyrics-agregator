"""Unit tests for the lyrics matcher and cascade orchestration."""

from pathlib import Path
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


@pytest.mark.asyncio
async def test_matcher_early_exit_on_line_sync(tmp_path: Path):
    audio_path = tmp_path / "track.flac"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=180.0)

    p1 = MockProvider(
        "p1_line",
        LyricsResult(
            content="[00:01.00] Line synced",
            format=LyricsFormat.LRC,
            sync_type=LyricsSyncType.LINE_SYNC,
            provider_name="p1_line",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )
    p2 = MockProvider(
        "p2_word",
        LyricsResult(
            content="<tt><span>Word synced</span></tt>",
            format=LyricsFormat.TTML,
            sync_type=LyricsSyncType.WORD_SYNC,
            provider_name="p2_word",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )

    config = AppConfig(music_dir=tmp_path, early_exit_on_line_sync=True)
    matcher = LyricsMatcher(config, [p1, p2])

    result = await matcher.process_track(track)
    assert result.status == MatchStatus.SUCCESS
    assert result.provider == "p1_line"
    assert result.format == LyricsFormat.LRC


@pytest.mark.asyncio
async def test_matcher_skips_non_word_sync_provider_after_line_sync(tmp_path: Path):
    audio_path = tmp_path / "track.flac"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=180.0)

    p1 = MockProvider(
        "p1_line",
        LyricsResult(
            content="[00:01.00] Line synced",
            format=LyricsFormat.LRC,
            sync_type=LyricsSyncType.LINE_SYNC,
            provider_name="p1_line",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )
    # p2 does not support word-sync (e.g. Kuwo or Lyricsify)
    p2 = MockProvider("p2_non_word", None)
    p2.supports_word_sync = False

    p3 = MockProvider(
        "p3_word",
        LyricsResult(
            content="<tt><span>Word synced</span></tt>",
            format=LyricsFormat.TTML,
            sync_type=LyricsSyncType.WORD_SYNC,
            provider_name="p3_word",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )

    p2_queried = False
    orig_p2_get = p2.get_lyrics
    async def track_p2_get(t):
        nonlocal p2_queried
        p2_queried = True
        return await orig_p2_get(t)
    p2.get_lyrics = track_p2_get

    config = AppConfig(music_dir=tmp_path, early_exit_on_line_sync=False)
    matcher = LyricsMatcher(config, [p1, p2, p3])

    result = await matcher.process_track(track)
    assert result.status == MatchStatus.SUCCESS
    assert result.provider == "p3_word"
    # p2 must have been skipped without being queried!
    assert p2_queried is False


@pytest.mark.asyncio
async def test_matcher_word_sync_budget(tmp_path: Path):
    audio_path = tmp_path / "track.flac"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=180.0)

    p1 = MockProvider(
        "p1_line",
        LyricsResult(
            content="[00:01.00] Line synced",
            format=LyricsFormat.LRC,
            sync_type=LyricsSyncType.LINE_SYNC,
            provider_name="p1_line",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )
    p2 = MockProvider("p2_word_fail", None)
    p3 = MockProvider(
        "p3_word_skipped_by_budget",
        LyricsResult(
            content="<tt><span>Word synced</span></tt>",
            format=LyricsFormat.TTML,
            sync_type=LyricsSyncType.WORD_SYNC,
            provider_name="p3_word_skipped_by_budget",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )

    p3_queried = False
    orig_p3_get = p3.get_lyrics
    async def track_p3_get(t):
        nonlocal p3_queried
        p3_queried = True
        return await orig_p3_get(t)
    p3.get_lyrics = track_p3_get

    # Budget of 1: allows checking p2, then budget exhausted before p3
    config = AppConfig(music_dir=tmp_path, word_sync_search_budget=1)
    matcher = LyricsMatcher(config, [p1, p2, p3])

    result = await matcher.process_track(track)
    assert result.status == MatchStatus.SUCCESS
    assert result.provider == "p1_line"
    assert p3_queried is False


@pytest.mark.asyncio
async def test_matcher_skips_plain_only_provider_when_allow_plain_false(tmp_path: Path):
    audio_path = tmp_path / "track.flac"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=180.0)

    p1 = MockProvider("p1_fail", None)
    p2_plain_only = MockProvider(
        "p2_genius",
        LyricsResult(
            content="Plain lyrics text",
            format=LyricsFormat.TXT,
            sync_type=LyricsSyncType.UNSYNCED,
            provider_name="p2_genius",
            duration=180.0,
            title="Song",
            artist="Artist",
        ),
    )
    p2_plain_only.supports_word_sync = False
    p2_plain_only.supports_line_sync = False

    p2_queried = False
    orig_p2_get = p2_plain_only.get_lyrics
    async def track_p2_get(t):
        nonlocal p2_queried
        p2_queried = True
        return await orig_p2_get(t)
    p2_plain_only.get_lyrics = track_p2_get

    config = AppConfig(music_dir=tmp_path, allow_plain_lyrics=False)
    matcher = LyricsMatcher(config, [p1, p2_plain_only])

    result = await matcher.process_track(track)
    assert result.status == MatchStatus.NOT_FOUND
    # Plain-only provider should be completely skipped without even querying
    assert p2_queried is False
