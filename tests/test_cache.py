"""Unit tests for SQLite persistent negative lyrics cache."""

import asyncio
from pathlib import Path
import time
import pytest
from src.cache import LyricsCache, NegativeCacheHit
from src.models import TrackMetadata


@pytest.fixture
def temp_cache(tmp_path: Path) -> LyricsCache:
    db_file = tmp_path / "cache" / "test_lyrics_cache.db"
    return LyricsCache(db_path=db_file, ttl_days=7.0)


@pytest.mark.asyncio
async def test_cache_initialization_and_wal(temp_cache: LyricsCache):
    await temp_cache.initialize()
    assert temp_cache.db_path.exists()

    # WAL is persisted in the database file: an independent connection must see it
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(str(temp_cache.db_path))) as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0].lower()
        assert mode == "wal"


@pytest.mark.asyncio
async def test_cache_record_negative_and_hit(temp_cache: LyricsCache):
    track = TrackMetadata(
        file_path=Path("/music/test/artist_song.mp3"),
        title="Unknown Song",
        artist="Rare Artist",
        duration=180.0,
    )

    hit = await temp_cache.is_negative_hit(track)
    assert hit is None

    await temp_cache.record_negative(track, providers_checked=["lrclib", "musixmatch"])

    hit = await temp_cache.is_negative_hit(track)
    assert isinstance(hit, NegativeCacheHit)
    assert hit.failure_count == 1
    assert "lrclib" in hit.last_providers
    assert "musixmatch" in hit.last_providers
    assert hit.remaining_days > 6.0

    # Record again -> failure count increments
    await temp_cache.record_negative(track, providers_checked=["lrclib", "musixmatch", "netease"])
    hit2 = await temp_cache.is_negative_hit(track)
    assert hit2 is not None
    assert hit2.failure_count == 2
    assert "netease" in hit2.last_providers


@pytest.mark.asyncio
async def test_cache_miss_on_different_track(temp_cache: LyricsCache):
    track1 = TrackMetadata(
        file_path=Path("/music/1.mp3"),
        title="Track One",
        artist="Artist A",
        duration=200.0,
    )
    track2 = TrackMetadata(
        file_path=Path("/music/2.mp3"),
        title="Track Two",
        artist="Artist A",
        duration=200.0,
    )

    await temp_cache.record_negative(track1)
    assert await temp_cache.is_negative_hit(track1) is not None
    assert await temp_cache.is_negative_hit(track2) is None


@pytest.mark.asyncio
async def test_cache_expiration(tmp_path: Path):
    db_file = tmp_path / "expired_test.db"
    cache = LyricsCache(db_path=db_file, ttl_days=0.0000005)  # ~0.043 seconds
    await cache.initialize()

    track = TrackMetadata(
        file_path=Path("/music/exp.mp3"),
        title="Ephemeral",
        artist="Fading",
        duration=120.0,
    )

    await cache.record_negative(track)
    # Wait for expiry
    await asyncio.sleep(0.08)

    hit = await cache.is_negative_hit(track)
    assert hit is None


@pytest.mark.asyncio
async def test_cache_remove_on_success(temp_cache: LyricsCache):
    track = TrackMetadata(
        file_path=Path("/music/found.mp3"),
        title="Found Track",
        artist="Band",
        duration=240.0,
    )

    await temp_cache.record_negative(track)
    assert await temp_cache.is_negative_hit(track) is not None

    removed = await temp_cache.remove(track)
    assert removed is True
    assert await temp_cache.is_negative_hit(track) is None


@pytest.mark.asyncio
async def test_cache_clear_and_stats(temp_cache: LyricsCache):
    track1 = TrackMetadata(file_path=Path("/1.mp3"), title="T1", artist="A1", duration=100.0)
    track2 = TrackMetadata(file_path=Path("/2.mp3"), title="T2", artist="A2", duration=200.0)

    await temp_cache.record_negative(track1)
    await temp_cache.record_negative(track2)

    stats = await temp_cache.get_stats()
    assert stats["total_negative_entries"] == 2
    assert stats["active_negative_entries"] == 2
    assert stats["expired_negative_entries"] == 0

    cleared = await temp_cache.clear()
    assert cleared == 2

    stats_after = await temp_cache.get_stats()
    assert stats_after["total_negative_entries"] == 0


@pytest.mark.asyncio
async def test_concurrent_writes(temp_cache: LyricsCache):
    tracks = [
        TrackMetadata(file_path=Path(f"/music/{i}.mp3"), title=f"Title {i}", artist="Artist", duration=100.0 + i)
        for i in range(50)
    ]

    # Concurrently write 50 records
    await asyncio.gather(*(temp_cache.record_negative(t) for t in tracks))

    stats = await temp_cache.get_stats()
    assert stats["total_negative_entries"] == 50


@pytest.mark.asyncio
async def test_spotify_id_cache_in_memory_and_sqlite(temp_cache: LyricsCache):
    from src.cache import (
        clear_spotify_id_mem_cache,
        get_cached_spotify_id,
        set_cached_spotify_id,
    )

    clear_spotify_id_mem_cache()
    valid_id = "0IPJBx1bjznqjdYBXj3l19"

    # Initially not found
    assert get_cached_spotify_id("Mata", "Patoreakcja", db_path=temp_cache.db_path) is None

    # Save to cache
    set_cached_spotify_id("Mata", "Patoreakcja", valid_id, db_path=temp_cache.db_path)

    # 1. In-memory hit
    assert get_cached_spotify_id("Mata", "Patoreakcja", db_path=temp_cache.db_path) == valid_id
    # Case and whitespace insensitivity
    assert get_cached_spotify_id("  mata  ", "  PATOREAKCJA  ", db_path=temp_cache.db_path) == valid_id

    # 2. Clear in-memory cache to force SQLite read
    clear_spotify_id_mem_cache()

    # SQLite persistent hit
    assert get_cached_spotify_id("Mata", "Patoreakcja", db_path=temp_cache.db_path) == valid_id

    # Stats should show 1 cached Spotify ID
    stats = await temp_cache.get_stats()
    assert stats["total_spotify_ids"] == 1


@pytest.mark.asyncio
async def test_spotify_id_cache_isrc_lookup(temp_cache: LyricsCache):
    from src.cache import (
        clear_spotify_id_mem_cache,
        get_cached_spotify_id,
        set_cached_spotify_id,
    )

    clear_spotify_id_mem_cache()
    valid_id = "4cOdK2wGLETKBW3PvgPWqT"
    isrc = "GBUM71029604"

    set_cached_spotify_id("Queen", "Bohemian Rhapsody", valid_id, isrc=isrc, db_path=temp_cache.db_path)

    # Lookup by ISRC even with different artist/title
    assert get_cached_spotify_id(isrc=isrc, db_path=temp_cache.db_path) == valid_id
    assert get_cached_spotify_id("Different Artist", "Different Title", isrc=isrc, db_path=temp_cache.db_path) == valid_id

    # Clear memory cache and verify persistent ISRC lookup
    clear_spotify_id_mem_cache()
    assert get_cached_spotify_id(isrc=isrc, db_path=temp_cache.db_path) == valid_id


@pytest.mark.asyncio
async def test_spotify_id_invalid_id_ignored(temp_cache: LyricsCache):
    from src.cache import (
        clear_spotify_id_mem_cache,
        get_cached_spotify_id,
        set_cached_spotify_id,
    )

    clear_spotify_id_mem_cache()
    # Less than 22 characters or invalid
    set_cached_spotify_id("Artist", "Song", "too_short", db_path=temp_cache.db_path)
    assert get_cached_spotify_id("Artist", "Song", db_path=temp_cache.db_path) is None

