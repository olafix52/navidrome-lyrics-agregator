"""Matching engine orchestrating provider cascade and verification for audio tracks."""

import asyncio
import logging
import time
from collections import deque
from typing import Any, AsyncGenerator, Callable, Coroutine, Deque, Dict, List, Optional, Tuple, TypeVar
from src.cache import UPGRADE_SCOPE, LyricsCache, aget_cached_spotify_id
from src.config import AppConfig
from src.models import (
    LyricsSyncType,
    MatchStatus,
    ProcessResult,
    StorageMode,
    TrackMetadata,
)
from src.normalizer import verify_track_match
from src.uncensor import has_masked_words, restore_from_references, uncensor_lyrics_content
from src.web.parser import count_lyric_lines
from src.providers.base import BaseLyricsProvider, FetchScope
from src.storage import (
    get_existing_lyrics_rank,
    lyrics_quality_rank,
    save_lyrics_for_track,
    should_skip_track,
)
from src.tag_writer import has_embedded_lyrics

logger = logging.getLogger("nla.matcher")

T = TypeVar("T")


def _provider_fetch(provider: Any, track: TrackMetadata) -> Coroutine[Any, Any, Any]:
    """``provider.fetch`` (scope-deduplicated) with a fallback for duck-typed providers."""
    fetch = getattr(provider, "fetch", None)
    return fetch(track) if fetch is not None else provider.get_lyrics(track)


class SingleFlight:
    """Coalesces concurrent in-flight asynchronous operations with identical keys.
    
    Prevents stampedes on external providers when identical tracks (e.g. across compilations,
    duplicate albums, or multi-format libraries) are processed concurrently.
    """

    def __init__(self) -> None:
        self._in_flight: Dict[str, asyncio.Future[Any]] = {}
        self._lock = asyncio.Lock()
        self.stats_coalesced: int = 0
        self.stats_total: int = 0

    async def execute(self, key: str, fn: Callable[[], Coroutine[Any, Any, T]]) -> T:
        async with self._lock:
            self.stats_total += 1
            if key in self._in_flight:
                self.stats_coalesced += 1
                fut = self._in_flight[key]
                is_leader = False
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight[key] = fut
                is_leader = True

        if not is_leader:
            return await fut

        try:
            result = await fn()
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            async with self._lock:
                self._in_flight.pop(key, None)


class LyricsMatcher:
    """Orchestrates lyrics retrieval across multiple providers in cascade order."""

    def __init__(
        self,
        config: AppConfig,
        providers: List[BaseLyricsProvider],
        cache: Optional[LyricsCache] = None,
    ):
        self.config = config
        self.providers = providers
        self.cache = cache
        if self.cache is None and getattr(config, "cache", None) and config.cache.enabled:
            self.cache = LyricsCache(
                db_path=config.cache.db_path,
                ttl_days=config.cache.negative_ttl_days,
            )
        self._search_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._cache_ttl = 600.0  # 10 minutes cache TTL
        self._search_cache_max_entries = 256
        self._single_flight = SingleFlight()

    async def _fetch_best_lyrics(
        self, track: TrackMetadata, cache_scope: Optional[str] = None
    ) -> Tuple[Optional[Tuple[Any, str, float]], List[str], bool, Any]:
        """Fetch best lyrics from providers cascade without performing disk I/O.

        Returns ``(best_match, providers_queried, is_negative_hit, negative_hit)``.
        """
        ignore_cache = getattr(self.config, "ignore_cache", False) or self.config.overwrite
        if self.cache and not ignore_cache:
            hit = await self.cache.is_negative_hit(track, scope=cache_scope)
            if hit:
                return None, [], True, hit

        logger.info(f"[SEARCHING] {track.display_name()} ({track.duration:.1f}s)")

        # Pre-populate Spotify ID from cache if missing
        if not track.spotify_id:
            cached_sp = await aget_cached_spotify_id(
                artist=track.clean_artist or track.artist,
                title=track.clean_title or track.title,
                isrc=track.isrc,
            )
            if cached_sp:
                track.spotify_id = cached_sp

        best_match: Optional[Tuple[Any, str, float]] = None
        remaining_word_sync_budget = getattr(self.config, "word_sync_search_budget", None)
        early_exit = getattr(self.config, "early_exit_on_line_sync", False)
        providers_queried: List[str] = []
        window = max(1, int(getattr(self.config, "cascade_concurrency", 1) or 1))

        # Skip plain-only providers if allow_plain_lyrics is False
        candidates: List[BaseLyricsProvider] = []
        for provider in self.providers:
            if (
                not self.config.allow_plain_lyrics
                and not getattr(provider, "supports_line_sync", True)
                and not getattr(provider, "supports_word_sync", True)
            ):
                logger.debug(f"[{provider.name}] Skipping: plain lyrics disabled (allow_plain_lyrics=False)")
                continue
            candidates.append(provider)

        def has_line_sync() -> bool:
            return bool(best_match) and best_match[0].sync_type == LyricsSyncType.LINE_SYNC

        # Up to ``window`` providers run concurrently, but results are consumed strictly in
        # cascade order, so the chosen lyrics are identical to a sequential cascade; only the
        # waiting overlaps. With window=1 this is exactly the sequential cascade.
        inflight: Deque[Tuple[BaseLyricsProvider, "asyncio.Task[Any]"]] = deque()
        next_idx = 0

        def launch() -> None:
            nonlocal next_idx
            while len(inflight) < window and next_idx < len(candidates):
                if has_line_sync() and (early_exit or remaining_word_sync_budget == 0):
                    return  # nothing more will be consumed
                provider = candidates[next_idx]
                next_idx += 1
                if has_line_sync() and not getattr(provider, "supports_word_sync", True):
                    logger.debug(f"[{provider.name}] Skipping: cannot produce word-sync and line-sync already matched")
                    continue
                logger.debug(f"[{provider.name}] Querying for '{track.display_name()}'...")
                inflight.append((provider, asyncio.create_task(_provider_fetch(provider, track))))

        with FetchScope().activate():
            try:
                while True:
                    launch()
                    if not inflight:
                        break
                    provider, task = inflight.popleft()

                    # If we already matched LINE_SYNC, optimize the remaining cascade
                    if has_line_sync():
                        if early_exit:
                            logger.debug(f"[CASCADE] Early exit on line-sync active, ending cascade before {provider.name}")
                            task.cancel()
                            break
                        if not getattr(provider, "supports_word_sync", True):
                            logger.debug(f"[{provider.name}] Skipping: cannot produce word-sync and line-sync already matched")
                            task.cancel()
                            continue
                        if remaining_word_sync_budget is not None:
                            if remaining_word_sync_budget <= 0:
                                logger.debug(f"[CASCADE] Word-sync search budget reached, ending cascade before {provider.name}")
                                task.cancel()
                                break
                            remaining_word_sync_budget -= 1

                    providers_queried.append(provider.name)
                    try:
                        lyrics = await task
                    except Exception as e:
                        logger.warning(f"[{provider.name}] Exception while processing {track.display_name()}: {e}")
                        continue

                    scored = self._verify_candidate(track, provider.name, lyrics)
                    if scored is None:
                        continue
                    score = scored.match_score

                    if scored.sync_type == LyricsSyncType.WORD_SYNC:
                        best_match = (scored, provider.name, score)
                        break

                    if scored.sync_type == LyricsSyncType.LINE_SYNC:
                        if not best_match or best_match[0].sync_type == LyricsSyncType.UNSYNCED:
                            best_match = (scored, provider.name, score)
                        continue

                    if not best_match:
                        best_match = (scored, provider.name, score)
            finally:
                # Speculative lookups made obsolete by an earlier hit
                for _, pending in inflight:
                    pending.cancel()

        return best_match, providers_queried, False, None

    def _verify_candidate(self, track: TrackMetadata, provider_name: str, lyrics: Any) -> Optional[Any]:
        """Return the lyrics with ``match_score`` set if they are acceptable for ``track``."""
        if not lyrics or not lyrics.content:
            return None

        if lyrics.sync_type == LyricsSyncType.UNSYNCED and not self.config.allow_plain_lyrics:
            logger.debug(f"[{provider_name}] Plain lyrics ignored (allow_plain_lyrics=False)")
            return None

        if count_lyric_lines(lyrics.content, lyrics.format) == 0:
            logger.debug(f"[{provider_name}] Rejected: no lyric lines (only credits / instrumental marker)")
            return None

        is_match, score, reason = verify_track_match(
            expected_title=track.clean_title or track.title,
            expected_artist=track.clean_artist or track.artist,
            found_title=lyrics.title,
            found_artist=lyrics.artist,
            expected_duration=track.duration,
            found_duration=lyrics.duration,
            tolerance_seconds=self.config.duration_tolerance_seconds,
            min_similarity=self.config.min_similarity_score,
        )
        if not is_match:
            logger.debug(f"[{provider_name}] Match rejected: {reason}")
            return None

        lyrics.match_score = score
        return lyrics

    async def process_track(self, track: TrackMetadata) -> ProcessResult:
        """Process a single audio track: check existing sidecar, query providers, verify, and save."""
        # 1. Check if track should be skipped based on existing files / tags
        skip, skip_reason = await asyncio.to_thread(
            should_skip_track,
            track.file_path,
            overwrite=self.config.overwrite,
            upgrade_quality=self.config.upgrade_quality,
            storage_mode=getattr(self.config, "storage_mode", "sidecar"),
            output_dir=self.config.output_dir,
            music_dir=self.config.music_dir,
            embedded_present=track.has_embedded_lyrics,
        )
        if skip:
            logger.debug(f"[SKIPPED] {track.display_name()} - {skip_reason}")
            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SKIPPED,
                error_message=skip_reason,
            )

        # When lyrics already exist (upgrade attempt), a result is only written if it is
        # strictly better. Otherwise every scan would re-download and rewrite the same
        # LRC, report SUCCESS and re-trigger Navidrome scans (and, with embedded tags,
        # retrigger the file watcher in an endless loop).
        storage_mode = str(getattr(self.config, "storage_mode", "sidecar")).lower()
        existing_rank, tags_missing = await asyncio.to_thread(self._existing_lyrics_state, track, storage_mode)
        cache_scope = UPGRADE_SCOPE if existing_rank is not None else None

        # SingleFlight: coalesce concurrent in-flight queries for identical tracks
        flight_key = (
            f"{cache_scope or 'missing'}:"
            f"{(track.clean_artist or track.artist).strip().lower()}:"
            f"{(track.clean_title or track.title).strip().lower()}:"
            f"{int(round(track.duration or 0))}"
        )

        best_match, providers_queried, is_negative_hit, neg_hit = await self._single_flight.execute(
            flight_key,
            lambda: self._fetch_best_lyrics(track, cache_scope=cache_scope),
        )

        if is_negative_hit:
            remaining_days, failure_count = neg_hit.remaining_days, neg_hit.failure_count
            what = "no better lyrics than existing sidecar" if cache_scope else "no lyrics"
            logger.debug(
                f"[CACHE HIT - NEGATIVE] {track.display_name()} - skipped ({what} found on previous scan, "
                f"TTL remaining: {remaining_days:.1f}d, fail count: {failure_count})"
            )
            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SKIPPED,
                error_message=f"Negative cache: {what} found across providers ({remaining_days:.1f}d remaining)",
                recheck_after=time.time() + neg_hit.remaining_seconds,
                negative_key=neg_hit.cache_key,
            )

        ignore_cache = getattr(self.config, "ignore_cache", False) or self.config.overwrite
        # Dry runs must not leave persistent traces: a "not found" recorded during a
        # simulation would make the next real run skip the track for the whole TTL.
        write_cache = self.cache is not None and not self.config.dry_run

        if best_match:
            lyrics, provider_name, score = best_match

            save_mode = storage_mode
            if existing_rank is not None and lyrics_quality_rank(lyrics.sync_type, lyrics.format) <= existing_rank:
                if not tags_missing:
                    logger.info(
                        f"[NO UPGRADE] {track.display_name()} - best result ({lyrics.format.value.upper()}, "
                        f"{lyrics.sync_type.value} via {provider_name}) is not better than the existing sidecar"
                    )
                    recheck: Tuple[Optional[float], Optional[str]] = (None, None)
                    if write_cache and not ignore_cache and providers_queried:
                        await self.cache.record_negative(
                            track, providers_checked=providers_queried, scope=UPGRADE_SCOPE
                        )
                        recheck = self._negative_recheck(track, UPGRADE_SCOPE)
                    return ProcessResult(
                        file_path=track.file_path,
                        status=MatchStatus.SKIPPED,
                        error_message="No higher-quality lyrics found than the existing sidecar",
                        recheck_after=recheck[0],
                        negative_key=recheck[1],
                    )
                # 'both' mode with missing tags: keep the existing (better or equal) sidecar,
                # only populate the audio tags.
                save_mode = StorageMode.EMBEDDED.value

            if getattr(self.config, "uncensor_lyrics", False):
                restored = await self.uncensor(track, lyrics, exclude=provider_name)
                if restored:
                    logger.info(f"[UNCENSOR] {track.display_name()}: restored {restored} masked word(s)")

            # Remove from negative cache if previously cached
            if write_cache:
                await self.cache.remove(track)
                await self.cache.remove(track, scope=UPGRADE_SCOPE)

            # Offload blocking sidecar and audio tag file writes to worker thread
            target_path, was_embedded = await asyncio.to_thread(
                save_lyrics_for_track,
                audio_path=track.file_path,
                lyrics=lyrics,
                storage_mode=save_mode,
                output_dir=getattr(self.config, "output_dir", None),
                music_dir=self.config.music_dir,
                dry_run=self.config.dry_run,
                enhanced_lrc=getattr(self.config, "embed_word_sync", True),
            )

            dest_desc = []
            if target_path:
                dest_desc.append(target_path.name)
            if was_embedded:
                dest_desc.append("audio tags")
            dest_str = " + ".join(dest_desc) if dest_desc else "tags"

            logger.info(
                f"[FOUND] {track.display_name()} -> {lyrics.format.value.upper()} ({lyrics.sync_type.value}) "
                f"via {provider_name} (score: {score:.2f}) -> {dest_str}"
            )

            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SUCCESS,
                provider=provider_name,
                format=lyrics.format,
                target_file=target_path,
                embedded=was_embedded,
                match_score=score,
            )

        # Record in negative cache so subsequent scans skip this track immediately
        recheck: Tuple[Optional[float], Optional[str]] = (None, None)
        if write_cache and not ignore_cache and providers_queried:
            await self.cache.record_negative(track, providers_checked=providers_queried, scope=cache_scope)
            recheck = self._negative_recheck(track, cache_scope)

        logger.info(f"[NOT FOUND] No matching lyrics found for {track.display_name()}")
        return ProcessResult(
            file_path=track.file_path,
            status=MatchStatus.NOT_FOUND,
            recheck_after=recheck[0],
            negative_key=recheck[1],
        )

    def _negative_recheck(self, track: TrackMetadata, scope: Optional[str]) -> Tuple[float, str]:
        """(recheck_after, cache_key) of a negative entry just recorded for ``track``."""
        return time.time() + self.cache.ttl_days * 86400.0, self.cache.get_cache_key(track, scope=scope)

    async def uncensor(self, track: TrackMetadata, lyrics: Any, exclude: Optional[str] = None) -> int:
        """Restore masked explicit words in ``lyrics.content`` in place. Returns words restored.

        1. Pattern restoration (``f**k``, ``b***h``, ``n-gga``) needs no network.
        2. Words masked without any hint (Apple Music's fixed ``****`` for the n-word) are
           filled from the uncensored text of other providers, queried in order of how
           reliably they carry uncensored lyrics, until nothing masked is left.
        """
        content, restored = uncensor_lyrics_content(lyrics.content, lyrics.format)
        if has_masked_words(content):
            with FetchScope().activate():
                for provider in self._reference_providers(exclude):
                    try:
                        ref = await asyncio.wait_for(_provider_fetch(provider, track), timeout=20.0)
                    except Exception as e:
                        logger.debug(f"[UNCENSOR] reference {provider.name} failed: {e}")
                        continue
                    if not ref or not ref.content or not self._is_same_song(track, ref):
                        continue
                    content, count = restore_from_references(content, lyrics.format, [ref.content])
                    restored += count
                    if not has_masked_words(content):
                        break
        lyrics.content = content
        return restored

    # Sources that usually carry uncensored text, best first; others follow in cascade order.
    _REFERENCE_PREFERENCE = ("lrclib", "musixmatch", "genius", "netease", "kugou", "qqmusic")

    def _reference_providers(self, exclude: Optional[str]) -> List[BaseLyricsProvider]:
        from src.providers.blends import _BaseBlendProvider  # blends reuse Apple text (masked)

        candidates = [
            p for p in self.providers
            if p.name != exclude and not isinstance(p, _BaseBlendProvider)
        ]
        order = {name: i for i, name in enumerate(self._REFERENCE_PREFERENCE)}
        return sorted(candidates, key=lambda p: order.get(p.name, len(order)))

    def _is_same_song(self, track: TrackMetadata, ref: Any) -> bool:
        is_match, _, _ = verify_track_match(
            expected_title=track.clean_title or track.title,
            expected_artist=track.clean_artist or track.artist,
            found_title=ref.title,
            found_artist=ref.artist,
            expected_duration=track.duration,
            found_duration=ref.duration,
            tolerance_seconds=max(5.0, self.config.duration_tolerance_seconds),
            min_similarity=self.config.min_similarity_score,
        )
        return is_match

    def _existing_lyrics_state(
        self, track: TrackMetadata, storage_mode: str
    ) -> Tuple[Optional[Tuple[int, int]], bool]:
        """Return (quality rank of existing sidecar or None, whether 'both' mode still lacks tags).

        Returns (None, False) when results should always be saved (overwrite, embedded-only mode,
        or no existing sidecar).
        """
        if self.config.overwrite or storage_mode not in (StorageMode.SIDECAR.value, StorageMode.BOTH.value):
            return None, False
        rank = get_existing_lyrics_rank(
            track.file_path, output_dir=self.config.output_dir, music_dir=self.config.music_dir
        )
        if rank is None:
            return None, False
        if storage_mode != StorageMode.BOTH.value:
            return rank, False
        has_tags = track.has_embedded_lyrics
        if has_tags is None:
            has_tags = has_embedded_lyrics(track.file_path)
        return rank, not has_tags

    async def stream_provider_search(
        self,
        track: TrackMetadata,
        timeout_per_provider: float = 3.5,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream provider search results as each provider finishes (as_completed) with individual timeouts."""
        cache_key = f"{track.clean_artist or track.artist}:{track.clean_title or track.title}:{int(track.duration)}"
        now = time.time()
        if cache_key in self._search_cache:
            ts, cached_candidates = self._search_cache[cache_key]
            if now - ts < self._cache_ttl:
                for cand in cached_candidates:
                    yield cand
                return

        collected: List[Dict[str, Any]] = []

        async def _query(provider: BaseLyricsProvider) -> Optional[Dict[str, Any]]:
            try:
                # Per-provider timeout prevents hanging on slow/rate-limited remote services
                lyrics = await asyncio.wait_for(
                    _provider_fetch(provider, track),
                    timeout=timeout_per_provider,
                )
                if not lyrics or not lyrics.content:
                    return None
                if count_lyric_lines(lyrics.content, lyrics.format) == 0:
                    return None

                is_match, score, _ = verify_track_match(
                    expected_title=track.clean_title or track.title,
                    expected_artist=track.clean_artist or track.artist,
                    found_title=lyrics.title,
                    found_artist=lyrics.artist,
                    expected_duration=track.duration,
                    found_duration=lyrics.duration,
                    tolerance_seconds=max(5.0, self.config.duration_tolerance_seconds),
                    min_similarity=max(0.35, self.config.min_similarity_score - 0.30),
                )
                if not is_match and score < 0.35:
                    return None

                return {
                    "provider": provider.name,
                    "title": lyrics.title,
                    "artist": lyrics.artist,
                    "format": lyrics.format.value,
                    "sync_type": lyrics.sync_type.value,
                    "duration": lyrics.duration,
                    "match_score": round(score, 2),
                    "content": lyrics.content,
                    "preview": "\n".join(lyrics.content.splitlines()[:6]),
                    "metadata": lyrics.metadata,
                }
            except asyncio.TimeoutError:
                logger.debug(f"[{provider.name}] Manual search timed out after {timeout_per_provider}s")
                return None
            except Exception as e:
                logger.debug(f"[{provider.name}] Error during manual provider search: {e}")
                return None

        # Execute concurrently and stream as each completes. All queries share one FetchScope,
        # so blends reuse the standalone providers' responses instead of re-requesting them.
        scope = FetchScope()
        ctx = scope.new_context()
        tasks = [asyncio.create_task(_query(p), context=ctx) for p in self.providers]
        try:
            for fut in asyncio.as_completed(tasks):
                res = await fut
                if res is not None:
                    collected.append(res)
                    yield res
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            scope.close()

        # Cache results if any found
        if collected:
            sync_order = {"word_sync": 3, "line_sync": 2, "unsynced": 1}
            collected.sort(
                key=lambda x: (sync_order.get(x["sync_type"], 0), x["match_score"]),
                reverse=True,
            )
            self._store_search_results(cache_key, now, collected)

    def _store_search_results(self, key: str, ts: float, candidates: List[Dict[str, Any]]) -> None:
        """Store manual-search results, evicting expired and oldest entries (long-running web server)."""
        cache = self._search_cache
        cache.pop(key, None)
        expired = [k for k, (t, _) in cache.items() if ts - t >= self._cache_ttl]
        for k in expired:
            del cache[k]
        while len(cache) >= self._search_cache_max_entries:
            del cache[next(iter(cache))]
        cache[key] = (ts, candidates)

    async def search_all_providers(
        self,
        track: TrackMetadata,
        timeout_per_provider: float = 3.5,
    ) -> List[Dict[str, Any]]:
        """Query all enabled providers concurrently with cache and return sorted candidate versions."""
        cache_key = f"{track.clean_artist or track.artist}:{track.clean_title or track.title}:{int(track.duration)}"
        now = time.time()
        if cache_key in self._search_cache:
            ts, cached_candidates = self._search_cache[cache_key]
            if now - ts < self._cache_ttl:
                return cached_candidates

        candidates = []
        async for item in self.stream_provider_search(track, timeout_per_provider=timeout_per_provider):
            candidates.append(item)

        sync_order = {"word_sync": 3, "line_sync": 2, "unsynced": 1}
        candidates.sort(
            key=lambda x: (sync_order.get(x["sync_type"], 0), x["match_score"]),
            reverse=True,
        )
        self._store_search_results(cache_key, now, candidates)
        return candidates

    async def close(self) -> None:
        """Clean up all provider connections and pooled database connections."""
        for provider in self.providers:
            try:
                await provider.close()
            except Exception:
                pass
        if self.cache is not None:
            self.cache.close()
