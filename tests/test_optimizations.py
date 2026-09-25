"""Tests for request deduplication, parallel cascade, incremental rescans, connection pooling
and the Web UI library index."""

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from src.cache import LyricsCache, close_cache_connections
from src.config import AppConfig, ProviderConfig
from src.matcher import LyricsMatcher
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, MatchStatus, TrackMetadata
from src.providers import build_provider_cascade
from src.providers.base import BaseLyricsProvider, FetchScope
from src.providers.blends import AppleNetEaseBlendProvider


def _result(provider: str, sync: LyricsSyncType = LyricsSyncType.LINE_SYNC) -> LyricsResult:
    if sync == LyricsSyncType.WORD_SYNC:
        content = (
            '<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="00:01.000" end="00:02.000">'
            '<span begin="00:01.000" end="00:02.000">Hello</span></p></div></body></tt>'
        )
        fmt = LyricsFormat.TTML
    else:
        content = "[00:01.00]Hello"
        fmt = LyricsFormat.LRC
    return LyricsResult(
        content=content, format=fmt, sync_type=sync, provider_name=provider,
        title="Song", artist="Artist", duration=200.0,
    )


class CountingProvider(BaseLyricsProvider):
    def __init__(self, name: str, result: Optional[LyricsResult] = None, delay: float = 0.0,
                 supports_word_sync: bool = True):
        super().__init__()
        self.name = name
        self.result = result
        self.delay = delay
        self.calls = 0
        self.cancelled = False
        self.supports_word_sync = supports_word_sync

    async def get_lyrics(self, track):
        self.calls += 1
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result.model_copy() if self.result else None


def _track(path: Path) -> TrackMetadata:
    return TrackMetadata(file_path=path, title="Song", artist="Artist", duration=200.0)


# ---------------------------------------------------------------------------
# 1. Shared provider instances + per-track request deduplication
# ---------------------------------------------------------------------------


def test_blends_share_provider_instances_and_user_config():
    config = AppConfig(
        enabled_providers=["spicylyrics", "neblend", "triblend", "kutriblend", "blend", "kublend", "netease"],
        providers={"spicylyrics": {"api_key": "sl_sk_user_key"}},
    )
    providers = {p.name: p for p in build_provider_cascade(config)}
    spicy = providers["spicylyrics"]
    blends = [providers[n] for n in ("neblend", "triblend", "kutriblend", "blend", "kublend")]

    # One Spicy Lyrics / Apple / LRCLIB instance for everything, with the user's settings
    assert all(b._spicy is spicy for b in blends)
    assert len({id(b._apple) for b in blends}) == 1
    assert len({id(b._lrclib) for b in blends}) == 1
    assert blends[0]._spicy.api_key == "sl_sk_user_key"
    # NetEase donor of neblend/triblend/kutriblend is the standalone cascade instance
    assert providers["neblend"]._donor_provider is providers["netease"]
    assert providers["triblend"]._donor_provider is providers["netease"]
    # QQ Music is not enabled standalone but still shared between blend and triblend
    assert providers["blend"]._donor_provider is providers["triblend"]._spare_provider


@pytest.mark.asyncio
async def test_fetch_scope_deduplicates_lookups_across_cascade_and_blends(tmp_path: Path):
    apple = CountingProvider("apple_music", _result("apple_music"), delay=0.01)
    netease = CountingProvider("netease", _result("netease", LyricsSyncType.WORD_SYNC), delay=0.01)
    spicy = CountingProvider("spicylyrics", None)
    lrclib = CountingProvider("lrclib", None)
    shared = {"apple_music": apple, "netease": netease, "spicylyrics": spicy, "lrclib": lrclib}
    neblend = AppleNetEaseBlendProvider(shared_provider=shared.__getitem__)

    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"x")
    # apple (line) -> neblend -> netease: apple and netease must each be requested once
    config = AppConfig(music_dir=tmp_path, cache={"enabled": False}, cascade_concurrency=1)
    matcher = LyricsMatcher(config, [apple, neblend, netease])
    res = await matcher.process_track(_track(audio))

    assert res.status == MatchStatus.SUCCESS
    assert apple.calls == 1
    assert netease.calls == 1


@pytest.mark.asyncio
async def test_fetch_scope_returns_independent_copies():
    p = CountingProvider("p", _result("p"))
    track = _track(Path("x.mp3"))
    with FetchScope().activate():
        a = await p.fetch(track)
        b = await p.fetch(track)
    a.match_score = 0.1
    assert b.match_score == 1.0
    assert p.calls == 1
    # Outside a scope every call is a real lookup
    await p.fetch(track)
    assert p.calls == 2


# ---------------------------------------------------------------------------
# 2. Parallel cascade window (results consumed in priority order)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_window_overlaps_waiting(tmp_path: Path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"x")
    p1 = CountingProvider("p1", None, delay=0.3)
    p2 = CountingProvider("p2", _result("p2", LyricsSyncType.WORD_SYNC), delay=0.3)

    config = AppConfig(music_dir=tmp_path, cache={"enabled": False}, cascade_concurrency=2, dry_run=True)
    start = time.monotonic()
    res = await LyricsMatcher(config, [p1, p2]).process_track(_track(audio))
    elapsed = time.monotonic() - start

    assert res.status == MatchStatus.SUCCESS and res.provider == "p2"
    assert elapsed < 0.5  # sequential would take ~0.6s


@pytest.mark.asyncio
async def test_parallel_window_keeps_cascade_priority(tmp_path: Path):
    """A faster lower-priority provider must not win over a higher-priority one."""
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"x")
    slow_first = CountingProvider("first", _result("first", LyricsSyncType.WORD_SYNC), delay=0.1)
    fast_second = CountingProvider("second", _result("second", LyricsSyncType.WORD_SYNC), delay=0.0)

    config = AppConfig(music_dir=tmp_path, cache={"enabled": False}, cascade_concurrency=3, dry_run=True)
    res = await LyricsMatcher(config, [slow_first, fast_second]).process_track(_track(audio))
    assert res.provider == "first"


@pytest.mark.asyncio
async def test_parallel_window_cancels_obsolete_speculative_lookups(tmp_path: Path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"x")
    hit = CountingProvider("hit", _result("hit", LyricsSyncType.WORD_SYNC))
    slow = CountingProvider("slow", _result("slow", LyricsSyncType.WORD_SYNC), delay=5.0)
    never = CountingProvider("never", None)

    config = AppConfig(music_dir=tmp_path, cache={"enabled": False}, cascade_concurrency=2, dry_run=True)
    start = time.monotonic()
    res = await LyricsMatcher(config, [hit, slow, never]).process_track(_track(audio))
    await asyncio.sleep(0)

    assert res.provider == "hit"
    assert time.monotonic() - start < 1.0
    assert slow.cancelled is True
    assert never.calls == 0  # outside the window


def test_cascade_concurrency_cli_flag():
    from src.main import apply_processing_overrides, build_parser

    config = AppConfig()
    apply_processing_overrides(build_parser().parse_args(["scan", "--cascade-concurrency", "1"]), config)
    assert config.cascade_concurrency == 1
    with pytest.raises(Exception):
        AppConfig(cascade_concurrency=0)


# ---------------------------------------------------------------------------
# 3. Incremental rescans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rescan_skips_unchanged_not_found_files_without_parsing(tmp_path: Path, monkeypatch):
    import src.scanner as scanner_mod
    from src.scanner import LibraryScanner

    music = tmp_path / "music"
    music.mkdir()
    audio = music / "song.flac"
    audio.write_bytes(b"dummy")

    reads: List[Path] = []
    monkeypatch.setattr(scanner_mod, "read_track_metadata", lambda p: reads.append(p) or _track(p))

    provider = CountingProvider("p1", None)
    cache = LyricsCache(tmp_path / "cache.db", ttl_days=7)
    config = AppConfig(music_dir=music, cascade_concurrency=1)
    scanner = LibraryScanner(config, LyricsMatcher(config, [provider], cache=cache))

    r1 = await scanner.scan_and_process(show_progress=False)
    assert [r.status for r in r1] == [MatchStatus.NOT_FOUND]
    assert len(reads) == 1 and provider.calls == 1

    # Second scan: file unchanged -> skipped with a stat only (no tag parse, no lookup)
    r2 = await scanner.scan_and_process(show_progress=False)
    assert [r.status for r in r2] == [MatchStatus.SKIPPED]
    assert "Unchanged" in (r2[0].error_message or "")
    assert len(reads) == 1 and provider.calls == 1

    # File modified -> processed again (negative cache still answers, no provider call)
    os.utime(audio, ns=(time.time_ns(), time.time_ns() + 10_000_000_000))
    r3 = await scanner.scan_and_process(show_progress=False)
    assert len(reads) == 2
    assert r3[0].status == MatchStatus.SKIPPED and "Negative cache" in r3[0].error_message

    # Clearing the cache also forgets file states
    await cache.clear()
    await scanner.scan_and_process(show_progress=False)
    assert len(reads) == 3 and provider.calls == 2


@pytest.mark.asyncio
async def test_file_state_dropped_when_negative_entry_removed(tmp_path: Path, monkeypatch):
    """Lyrics found for a duplicate removes the negative entry -> other copies are rechecked."""
    import src.scanner as scanner_mod
    from src.scanner import LibraryScanner

    music = tmp_path / "music"
    music.mkdir()
    audio = music / "song.flac"
    audio.write_bytes(b"dummy")
    monkeypatch.setattr(scanner_mod, "read_track_metadata", _track)

    cache = LyricsCache(tmp_path / "cache.db", ttl_days=7)
    config = AppConfig(music_dir=music)
    scanner = LibraryScanner(config, LyricsMatcher(config, [CountingProvider("p1", None)], cache=cache))
    await scanner.scan_and_process(show_progress=False)
    assert str(audio) in await cache.load_file_states()

    await cache.remove(_track(audio))
    assert await cache.load_file_states() == {}


@pytest.mark.asyncio
async def test_dry_run_and_force_do_not_use_file_states(tmp_path: Path, monkeypatch):
    import src.scanner as scanner_mod
    from src.scanner import LibraryScanner

    music = tmp_path / "music"
    music.mkdir()
    (music / "song.flac").write_bytes(b"dummy")
    monkeypatch.setattr(scanner_mod, "read_track_metadata", _track)
    cache = LyricsCache(tmp_path / "cache.db", ttl_days=7)

    dry = AppConfig(music_dir=music, dry_run=True)
    await LibraryScanner(dry, LyricsMatcher(dry, [CountingProvider("p", None)], cache=cache)).scan_and_process(show_progress=False)
    assert await cache.load_file_states() == {}

    normal = AppConfig(music_dir=music)
    await LibraryScanner(normal, LyricsMatcher(normal, [CountingProvider("p", None)], cache=cache)).scan_and_process(show_progress=False)
    assert len(await cache.load_file_states()) == 1

    provider = CountingProvider("p", None)
    forced = AppConfig(music_dir=music, overwrite=True, ignore_cache=True)
    await LibraryScanner(forced, LyricsMatcher(forced, [provider], cache=cache)).scan_and_process(show_progress=False)
    assert provider.calls == 1


# ---------------------------------------------------------------------------
# 4. SQLite connection pooling
# ---------------------------------------------------------------------------


def test_connection_pool_reuses_per_thread_and_reopens_after_close(tmp_path: Path):
    from src.cache import _POOL

    db = tmp_path / "pool.db"
    c1 = _POOL.get(db)
    assert _POOL.get(db) is c1

    other: Dict[str, object] = {}
    t = threading.Thread(target=lambda: other.setdefault("conn", _POOL.get(db)))
    t.start()
    t.join()
    assert other["conn"] is not c1  # one connection per thread

    close_cache_connections(db)
    c2 = _POOL.get(db)
    assert c2 is not c1
    assert c2.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.asyncio
async def test_cache_survives_close_and_failed_write(tmp_path: Path):
    cache = LyricsCache(tmp_path / "c.db", ttl_days=7)
    track = _track(tmp_path / "a.mp3")
    await cache.record_negative(track)
    cache.close()
    assert await cache.is_negative_hit(track) is not None

    # A failing write is rolled back and does not leave the pooled connection in a transaction
    with pytest.raises(Exception):
        await asyncio.to_thread(cache._save_file_states_sync, [("p", "not-an-int", 1, "c", 1.0, None, "extra")])
    other = TrackMetadata(file_path=tmp_path / "b.mp3", title="Other", artist="Artist", duration=200.0)
    await cache.record_negative(other)
    assert (await cache.get_stats())["total_negative_entries"] == 2


# ---------------------------------------------------------------------------
# 5. Web UI library index
# ---------------------------------------------------------------------------


def _make_index(music: Path, output_dir: Optional[Path] = None, poll_ttl: float = 3600.0):
    from src.web.library_index import LibraryIndex
    from src.web.server import encode_track_id

    config = AppConfig(music_dir=music, output_dir=output_dir)
    return LibraryIndex(lambda: config, encode_track_id, poll_ttl=poll_ttl)


def test_library_index_incremental_updates(tmp_path: Path, monkeypatch):
    import src.web.library_index as idx_mod

    music = tmp_path / "music"
    (music / "A").mkdir(parents=True)
    (music / "B").mkdir(parents=True)
    (music / "A" / "a.flac").write_bytes(b"x")
    (music / "B" / "b.flac").write_bytes(b"x")
    index = _make_index(music)

    assert [t.filename for t in index.tracks()] == ["a.flac", "b.flac"]
    assert index.stats()["missing_count"] == 2

    # Cached: no filesystem walk on subsequent requests
    walks = []
    orig = idx_mod.iter_discover_audio_files
    monkeypatch.setattr(idx_mod, "iter_discover_audio_files", lambda root: walks.append(root) or orig(root))
    (music / "A" / "a.lrc").write_text("[00:01.00]Hi", encoding="utf-8")
    assert index.stats()["missing_count"] == 2  # not yet notified

    index.mark_dir_dirty(music / "A")
    stats = index.stats()
    assert stats["line_sync_count"] == 1 and stats["missing_count"] == 1
    assert stats["format_counts"] == {"LRC": 1}
    assert walks == []  # only directory A was rescanned

    # New folder -> directory event -> full rebuild
    (music / "C").mkdir()
    (music / "C" / "c.flac").write_bytes(b"x")
    index.notify_path_changed(music / "C", is_directory=True)
    assert len(index.tracks()) == 3
    assert len(walks) == 1


def test_library_index_maps_output_dir_events(tmp_path: Path):
    music = tmp_path / "music"
    out = tmp_path / "lyrics"
    (music / "Artist").mkdir(parents=True)
    (music / "Artist" / "song.flac").write_bytes(b"x")
    index = _make_index(music, output_dir=out)
    index._watched_roots = (music, out)
    assert index.tracks()[0].has_lyrics is False

    (out / "Artist").mkdir(parents=True)
    sidecar = out / "Artist" / "song.ttml"
    sidecar.write_text('<tt><body><p begin="1" end="2"><span begin="1" end="2">x</span></p></body></tt>', encoding="utf-8")
    index.notify_path_changed(sidecar, is_directory=False)
    track = index.tracks()[0]
    assert track.has_lyrics is True and track.format == "ttml" and track.sync_type == "word_sync"


def test_library_index_watcher_picks_up_changes(tmp_path: Path):
    music = tmp_path / "music"
    music.mkdir()
    (music / "song.flac").write_bytes(b"x")
    index = _make_index(music)
    assert index.tracks()[0].has_lyrics is False

    if not index.start_watching():
        pytest.skip("filesystem watcher unavailable on this system")
    try:
        (music / "song.lrc").write_text("[00:01.00]Hi", encoding="utf-8")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not index.tracks()[0].has_lyrics:
            time.sleep(0.05)
        assert index.tracks()[0].has_lyrics is True
    finally:
        index.stop_watching()


def test_cached_track_tags_parses_each_file_version_once(tmp_path: Path, monkeypatch):
    import src.web.library_index as idx_mod

    audio = tmp_path / "song.flac"
    audio.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(idx_mod, "read_track_metadata", lambda p: calls.append(p) or _track(p))
    idx_mod._cached_tags.cache_clear()

    assert idx_mod.cached_track_tags(audio)["title"] == "Song"
    idx_mod.cached_track_tags(audio)
    assert len(calls) == 1
    os.utime(audio, ns=(time.time_ns(), time.time_ns() + 10_000_000_000))
    idx_mod.cached_track_tags(audio)
    assert len(calls) == 2
