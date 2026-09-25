"""Unit tests for the lyrics matcher and cascade orchestration."""

import asyncio
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

    # Sequential cascade: with a parallel window p2 may be started speculatively before
    # p1's line-sync result is known (its result is then discarded).
    config = AppConfig(music_dir=tmp_path, early_exit_on_line_sync=False, cascade_concurrency=1)
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


@pytest.mark.asyncio
async def test_matcher_negative_cache_workflow(tmp_path: Path):
    from src.cache import LyricsCache
    db_file = tmp_path / "cache.db"
    cache = LyricsCache(db_path=db_file, ttl_days=7.0)

    audio_path = tmp_path / "unfound.mp3"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Missing", artist="Unknown", duration=150.0)

    query_count = 0
    p1 = MockProvider("p1", None)
    orig_get = p1.get_lyrics

    async def counted_get(t):
        nonlocal query_count
        query_count += 1
        return await orig_get(t)

    p1.get_lyrics = counted_get

    config = AppConfig(music_dir=tmp_path)
    matcher = LyricsMatcher(config, [p1], cache=cache)

    # 1. First run: provider queried, not found, stored in negative cache
    res1 = await matcher.process_track(track)
    assert res1.status == MatchStatus.NOT_FOUND
    assert query_count == 1
    assert await cache.is_negative_hit(track) is not None

    # 2. Second run: negative cache hit -> SKIPPED without querying provider!
    res2 = await matcher.process_track(track)
    assert res2.status == MatchStatus.SKIPPED
    assert "Negative cache" in (res2.error_message or "")
    assert query_count == 1  # No additional network query!

    # 3. Third run with ignore_cache: provider queried again
    config.ignore_cache = True
    res3 = await matcher.process_track(track)
    assert res3.status == MatchStatus.NOT_FOUND
    assert query_count == 2


@pytest.mark.asyncio
async def test_matcher_invalidates_negative_cache_on_success(tmp_path: Path):
    from src.cache import LyricsCache
    db_file = tmp_path / "cache_invalidate.db"
    cache = LyricsCache(db_path=db_file, ttl_days=7.0)

    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Found Song", artist="Artist", duration=200.0)

    # Pre-populate negative cache
    await cache.record_negative(track)
    assert await cache.is_negative_hit(track) is not None

    p1 = MockProvider(
        "p1",
        LyricsResult(
            content="[00:10.00]Hello world",
            format=LyricsFormat.LRC,
            sync_type=LyricsSyncType.LINE_SYNC,
            provider_name="p1",
            duration=200.0,
            title="Found Song",
            artist="Artist",
        ),
    )

    # When ignore_cache is true, we re-check and find lyrics
    config = AppConfig(music_dir=tmp_path, ignore_cache=True)
    matcher = LyricsMatcher(config, [p1], cache=cache)

    res = await matcher.process_track(track)
    assert res.status == MatchStatus.SUCCESS
    # Negative cache entry must now be deleted
    assert await cache.is_negative_hit(track) is None


@pytest.mark.asyncio
async def test_single_flight_coalescing():
    from src.matcher import SingleFlight

    sf = SingleFlight()
    call_count = 0

    async def _slow_operation(val: int):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return val * 2

    # Launch 5 concurrent calls with the same key
    results = await asyncio.gather(
        sf.execute("k1", lambda: _slow_operation(10)),
        sf.execute("k1", lambda: _slow_operation(10)),
        sf.execute("k1", lambda: _slow_operation(10)),
        sf.execute("k1", lambda: _slow_operation(10)),
        sf.execute("k1", lambda: _slow_operation(10)),
    )

    assert results == [20, 20, 20, 20, 20]
    assert call_count == 1  # Only 1 execution occurred!
    assert sf.stats_total == 5
    assert sf.stats_coalesced == 4


@pytest.mark.asyncio
async def test_matcher_single_flight_duplicate_tracks(tmp_path: Path):
    """Test that two files with identical track metadata trigger provider queries only once."""
    file1 = tmp_path / "song1.flac"
    file2 = tmp_path / "song2.mp3"
    file1.write_bytes(b"dummy1")
    file2.write_bytes(b"dummy2")

    track1 = TrackMetadata(file_path=file1, title="Bohemian Rhapsody", artist="Queen", duration=354.0)
    track2 = TrackMetadata(file_path=file2, title="Bohemian Rhapsody", artist="Queen", duration=354.0)

    query_count = 0

    class CountingProvider(BaseLyricsProvider):
        def __init__(self):
            super().__init__()
            self.name = "counting"

        async def get_lyrics(self, track: TrackMetadata):
            nonlocal query_count
            query_count += 1
            await asyncio.sleep(0.02)  # Simulate network latency
            return LyricsResult(
                content="[00:01.00]Mama",
                format=LyricsFormat.LRC,
                sync_type=LyricsSyncType.LINE_SYNC,
                provider_name="counting",
                duration=354.0,
                title="Bohemian Rhapsody",
                artist="Queen",
            )

    provider = CountingProvider()
    config = AppConfig(music_dir=tmp_path)
    matcher = LyricsMatcher(config, [provider])

    # Run both concurrently
    res1, res2 = await asyncio.gather(
        matcher.process_track(track1),
        matcher.process_track(track2),
    )

    assert res1.status == MatchStatus.SUCCESS
    assert res2.status == MatchStatus.SUCCESS
    # Exactly one network query made
    assert query_count == 1
    # Both files received sidecars
    assert (tmp_path / "song1.lrc").exists()
    assert (tmp_path / "song2.lrc").exists()




def _lrc_result(content: str = "[00:10.00]Hello world") -> LyricsResult:
    return LyricsResult(
        content=content,
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="p1",
        duration=200.0,
        title="Song",
        artist="Artist",
    )


@pytest.mark.asyncio
async def test_upgrade_does_not_rewrite_equal_quality_lyrics(tmp_path: Path):
    """An existing LRC must not be re-downloaded/rewritten every scan when nothing better exists."""
    from src.cache import LyricsCache

    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"dummy")
    existing = tmp_path / "song.lrc"
    existing.write_text("[00:10.00]My own edited line\n", encoding="utf-8")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=200.0)

    calls = 0
    p1 = MockProvider("p1", _lrc_result())
    orig = p1.get_lyrics

    async def counted(t):
        nonlocal calls
        calls += 1
        return await orig(t)

    p1.get_lyrics = counted

    cache = LyricsCache(db_path=tmp_path / "cache.db", ttl_days=7.0)
    matcher = LyricsMatcher(AppConfig(music_dir=tmp_path), [p1], cache=cache)

    res1 = await matcher.process_track(track)
    assert res1.status == MatchStatus.SKIPPED
    assert existing.read_text(encoding="utf-8") == "[00:10.00]My own edited line\n"
    assert calls == 1

    # Next scan: remembered in the upgrade scope -> no provider query at all
    res2 = await matcher.process_track(track)
    assert res2.status == MatchStatus.SKIPPED
    assert calls == 1

    # The upgrade-scope entry must not affect a copy of the same song that has no lyrics
    other_audio = tmp_path / "copy" / "song.mp3"
    other_audio.parent.mkdir()
    other_audio.write_bytes(b"dummy")
    other = TrackMetadata(file_path=other_audio, title="Song", artist="Artist", duration=200.0)
    res3 = await matcher.process_track(other)
    assert res3.status == MatchStatus.SUCCESS
    assert (other_audio.parent / "song.lrc").exists()


@pytest.mark.asyncio
async def test_upgrade_writes_strictly_better_lyrics(tmp_path: Path):
    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"dummy")
    (tmp_path / "song.lrc").write_text("[00:10.00]Hello world\n", encoding="utf-8")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=200.0)

    ttml = LyricsResult(
        content='<tt xmlns="http://www.w3.org/ns/ttml"><body><p begin="00:10.000" end="00:12.000">'
                '<span begin="00:10.000" end="00:11.000">Hello</span></p></body></tt>',
        format=LyricsFormat.TTML,
        sync_type=LyricsSyncType.WORD_SYNC,
        provider_name="p1",
        duration=200.0,
        title="Song",
        artist="Artist",
    )
    matcher = LyricsMatcher(AppConfig(music_dir=tmp_path, cache={"enabled": False}), [MockProvider("p1", ttml)])

    res = await matcher.process_track(track)
    assert res.status == MatchStatus.SUCCESS
    assert (tmp_path / "song.ttml").exists()
    assert not (tmp_path / "song.lrc").exists()  # lower-quality sidecar replaced


@pytest.mark.asyncio
async def test_unsynced_existing_lrc_upgraded_by_line_sync(tmp_path: Path):
    """An .lrc without timestamps ranks below a real line-synced LRC."""
    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"dummy")
    (tmp_path / "song.lrc").write_text("Hello world\n", encoding="utf-8")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=200.0)

    matcher = LyricsMatcher(AppConfig(music_dir=tmp_path, cache={"enabled": False}), [MockProvider("p1", _lrc_result())])
    res = await matcher.process_track(track)
    assert res.status == MatchStatus.SUCCESS
    assert (tmp_path / "song.lrc").read_text(encoding="utf-8").startswith("[00:10.00]")


@pytest.mark.asyncio
async def test_overwrite_still_rewrites_equal_quality(tmp_path: Path):
    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"dummy")
    (tmp_path / "song.lrc").write_text("[00:10.00]Old\n", encoding="utf-8")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=200.0)

    config = AppConfig(music_dir=tmp_path, overwrite=True, cache={"enabled": False})
    matcher = LyricsMatcher(config, [MockProvider("p1", _lrc_result())])
    res = await matcher.process_track(track)
    assert res.status == MatchStatus.SUCCESS
    assert "Hello world" in (tmp_path / "song.lrc").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_dry_run_leaves_no_negative_cache_entries(tmp_path: Path):
    from src.cache import LyricsCache

    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=200.0)
    cache = LyricsCache(db_path=tmp_path / "cache.db", ttl_days=7.0)

    matcher = LyricsMatcher(AppConfig(music_dir=tmp_path, dry_run=True), [MockProvider("p1", None)], cache=cache)
    res = await matcher.process_track(track)
    assert res.status == MatchStatus.NOT_FOUND
    assert await cache.is_negative_hit(track) is None

    # A pre-existing entry is not removed by a dry-run success either
    await cache.record_negative(track)
    config = AppConfig(music_dir=tmp_path, dry_run=True, ignore_cache=True)
    matcher = LyricsMatcher(config, [MockProvider("p1", _lrc_result())], cache=cache)
    res = await matcher.process_track(track)
    assert res.status == MatchStatus.SUCCESS
    assert not (tmp_path / "song.lrc").exists()
    assert await cache.is_negative_hit(track) is not None


@pytest.mark.asyncio
async def test_output_dir_mirrors_library_structure(tmp_path: Path):
    """Same file name in two albums must produce two separate sidecars under output_dir."""
    music = tmp_path / "music"
    out = tmp_path / "lyrics"
    a = music / "Artist A" / "Album 1" / "01 - Intro.flac"
    b = music / "Artist B" / "Album 2" / "01 - Intro.flac"
    for p in (a, b):
        p.parent.mkdir(parents=True)
        p.write_bytes(b"dummy")

    config = AppConfig(music_dir=music, output_dir=out, cache={"enabled": False})
    for path, artist in ((a, "Artist A"), (b, "Artist B")):
        provider = MockProvider("p1", _lrc_result(f"[00:10.00]{artist}").model_copy(update={"artist": artist, "title": "Intro"}))
        matcher = LyricsMatcher(config, [provider])
        track = TrackMetadata(file_path=path, title="Intro", artist=artist, duration=200.0)
        assert (await matcher.process_track(track)).status == MatchStatus.SUCCESS

    assert "Artist A" in (out / "Artist A" / "Album 1" / "01 - Intro.lrc").read_text(encoding="utf-8")
    assert "Artist B" in (out / "Artist B" / "Album 2" / "01 - Intro.lrc").read_text(encoding="utf-8")
    assert not (out / "01 - Intro.lrc").exists()

    # Existing mirrored sidecars are detected on the next scan
    from src.storage import get_existing_lyrics_file
    found = get_existing_lyrics_file(a, output_dir=out, music_dir=music)
    assert found is not None and found[0] == out / "Artist A" / "Album 1" / "01 - Intro.lrc"


@pytest.mark.asyncio
async def test_manual_search_cache_is_bounded(tmp_path: Path):
    matcher = LyricsMatcher(AppConfig(music_dir=tmp_path, cache={"enabled": False}), [])
    matcher._search_cache_max_entries = 5
    for i in range(20):
        matcher._store_search_results(f"k{i}", 1000.0, [])
    assert len(matcher._search_cache) == 5
    assert "k19" in matcher._search_cache

    matcher._store_search_results("fresh", 1000.0 + matcher._cache_ttl + 1, [])
    assert list(matcher._search_cache) == ["fresh"]  # expired entries evicted


CREDITS_ONLY_LRC = "[00:00.00-1] 作词 : Chief Keef\n[00:00.00-1] 作曲 : Chief Keef"


def test_count_lyric_lines_ignores_credits_and_instrumental_markers():
    from src.web.parser import count_lyric_lines

    assert count_lyric_lines(CREDITS_ONLY_LRC, LyricsFormat.LRC) == 0
    assert count_lyric_lines("[00:01.00]纯音乐，请欣赏", LyricsFormat.LRC) == 0
    assert count_lyric_lines("[00:00.10]Lyrics by: Someone\n[00:05.00]Real line", LyricsFormat.LRC) == 1
    assert count_lyric_lines("[00:05.00]Hello\n[00:07.00]World", LyricsFormat.LRC) == 2


@pytest.mark.asyncio
async def test_credits_only_result_is_not_found_and_never_rewritten(tmp_path: Path):
    """NetEase returns credit lines only for songs without lyrics; that must not count as lyrics."""
    audio_path = tmp_path / "song.flac"
    audio_path.write_bytes(b"dummy")
    track = TrackMetadata(file_path=audio_path, title="Song", artist="Artist", duration=200.0)
    credits = LyricsResult(content=CREDITS_ONLY_LRC, format=LyricsFormat.LRC, sync_type=LyricsSyncType.LINE_SYNC,
                           provider_name="netease", title="Song", artist="Artist", duration=200.0)

    config = AppConfig(music_dir=tmp_path, cache={"enabled": False})
    res = await LyricsMatcher(config, [MockProvider("netease", credits)]).process_track(track)
    assert res.status == MatchStatus.NOT_FOUND
    assert not (tmp_path / "song.lrc").exists()

    # A credits-only file already on disk (saved by older versions) is replaced by real lyrics
    (tmp_path / "song.lrc").write_text(CREDITS_ONLY_LRC, encoding="utf-8")
    res = await LyricsMatcher(config, [MockProvider("p1", _lrc_result())]).process_track(track)
    assert res.status == MatchStatus.SUCCESS
    assert "Hello world" in (tmp_path / "song.lrc").read_text(encoding="utf-8")
