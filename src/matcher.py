"""Matching engine orchestrating provider cascade and verification for audio tracks."""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Callable, Coroutine, Dict, List, Optional, Tuple, TypeVar
from src.cache import LyricsCache, get_cached_spotify_id
from src.config import AppConfig
from src.models import (
    LyricsSyncType,
    MatchStatus,
    ProcessResult,
    TrackMetadata,
)
from src.normalizer import verify_track_match
from src.providers.base import BaseLyricsProvider
from src.storage import save_lyrics_for_track, should_skip_track

logger = logging.getLogger("nla.matcher")

T = TypeVar("T")


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
        self._single_flight = SingleFlight()

    async def _fetch_best_lyrics(
        self, track: TrackMetadata
    ) -> Tuple[Optional[Tuple[Any, str, float]], List[str], bool, Tuple[float, int]]:
        """Fetch best lyrics from providers cascade without performing disk I/O."""
        ignore_cache = getattr(self.config, "ignore_cache", False) or self.config.overwrite
        if self.cache and not ignore_cache:
            hit = await self.cache.is_negative_hit(track)
            if hit:
                return None, [], True, (hit.remaining_days, hit.failure_count)

        logger.info(f"[SEARCHING] {track.display_name()} ({track.duration:.1f}s)")

        # Pre-populate Spotify ID from cache if missing
        if not track.spotify_id:
            cached_sp = get_cached_spotify_id(
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

        for provider in self.providers:
            # Skip plain-only providers if allow_plain_lyrics is False
            if not self.config.allow_plain_lyrics and not getattr(provider, "supports_line_sync", True) and not getattr(provider, "supports_word_sync", True):
                logger.debug(f"[{provider.name}] Skipping: plain lyrics disabled (allow_plain_lyrics=False)")
                continue

            # If we already matched LINE_SYNC, optimize the remaining cascade
            if best_match and best_match[0].sync_type == LyricsSyncType.LINE_SYNC:
                if early_exit:
                    logger.debug(f"[CASCADE] Early exit on line-sync active, ending cascade before {provider.name}")
                    break

                if not getattr(provider, "supports_word_sync", True):
                    logger.debug(f"[{provider.name}] Skipping: cannot produce word-sync and line-sync already matched")
                    continue

                if remaining_word_sync_budget is not None:
                    if remaining_word_sync_budget <= 0:
                        logger.debug(f"[CASCADE] Word-sync search budget reached, ending cascade before {provider.name}")
                        break
                    remaining_word_sync_budget -= 1

            providers_queried.append(provider.name)
            try:
                logger.debug(f"[{provider.name}] Querying for '{track.display_name()}'...")
                lyrics = await provider.get_lyrics(track)

                if not lyrics or not lyrics.content:
                    continue

                if lyrics.sync_type == LyricsSyncType.UNSYNCED and not self.config.allow_plain_lyrics:
                    logger.debug(f"[{provider.name}] Plain lyrics ignored (allow_plain_lyrics=False)")
                    continue

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
                    logger.debug(f"[{provider.name}] Match rejected: {reason}")
                    continue

                lyrics.match_score = score

                if lyrics.sync_type == LyricsSyncType.WORD_SYNC:
                    best_match = (lyrics, provider.name, score)
                    break

                if lyrics.sync_type == LyricsSyncType.LINE_SYNC:
                    if not best_match or best_match[0].sync_type == LyricsSyncType.UNSYNCED:
                        best_match = (lyrics, provider.name, score)
                    continue

                if not best_match:
                    best_match = (lyrics, provider.name, score)

            except Exception as e:
                logger.warning(f"[{provider.name}] Exception while processing {track.display_name()}: {e}")

        return best_match, providers_queried, False, (0.0, 0)

    async def process_track(self, track: TrackMetadata) -> ProcessResult:
        """Process a single audio track: check existing sidecar, query providers, verify, and save."""
        # 1. Check if track should be skipped based on existing files / tags
        skip, skip_reason = should_skip_track(
            track.file_path,
            overwrite=self.config.overwrite,
            upgrade_quality=self.config.upgrade_quality,
            storage_mode=getattr(self.config, "storage_mode", "sidecar"),
            output_dir=self.config.output_dir,
        )
        if skip:
            logger.debug(f"[SKIPPED] {track.display_name()} - {skip_reason}")
            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SKIPPED,
                error_message=skip_reason,
            )

        # SingleFlight: coalesce concurrent in-flight queries for identical tracks
        flight_key = (
            f"{(track.clean_artist or track.artist).strip().lower()}:"
            f"{(track.clean_title or track.title).strip().lower()}:"
            f"{int(round(track.duration or 0))}"
        )

        best_match, providers_queried, is_negative_hit, neg_hit_info = await self._single_flight.execute(
            flight_key,
            lambda: self._fetch_best_lyrics(track),
        )

        if is_negative_hit:
            remaining_days, failure_count = neg_hit_info
            logger.debug(
                f"[CACHE HIT - NEGATIVE] {track.display_name()} - skipped (no lyrics found on previous scan, "
                f"TTL remaining: {remaining_days:.1f}d, fail count: {failure_count})"
            )
            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SKIPPED,
                error_message=f"Negative cache: no lyrics found across providers ({remaining_days:.1f}d remaining)",
            )

        if best_match:
            lyrics, provider_name, score = best_match

            # Remove from negative cache if previously cached
            if self.cache:
                await self.cache.remove(track)

            # Offload blocking sidecar and audio tag file writes to worker thread
            target_path, was_embedded = await asyncio.to_thread(
                save_lyrics_for_track,
                audio_path=track.file_path,
                lyrics=lyrics,
                storage_mode=getattr(self.config, "storage_mode", "sidecar"),
                output_dir=getattr(self.config, "output_dir", None),
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
        ignore_cache = getattr(self.config, "ignore_cache", False) or self.config.overwrite
        if self.cache and not ignore_cache and providers_queried:
            await self.cache.record_negative(track, providers_checked=providers_queried)

        logger.info(f"[NOT FOUND] No matching lyrics found for {track.display_name()}")
        return ProcessResult(
            file_path=track.file_path,
            status=MatchStatus.NOT_FOUND,
        )

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
                    provider.get_lyrics(track),
                    timeout=timeout_per_provider,
                )
                if not lyrics or not lyrics.content:
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

        # Execute concurrently and stream as each completes
        tasks = [asyncio.create_task(_query(p)) for p in self.providers]
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

        # Cache results if any found
        if collected:
            sync_order = {"word_sync": 3, "line_sync": 2, "unsynced": 1}
            collected.sort(
                key=lambda x: (sync_order.get(x["sync_type"], 0), x["match_score"]),
                reverse=True,
            )
            self._search_cache[cache_key] = (now, collected)

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
        self._search_cache[cache_key] = (now, candidates)
        return candidates

    async def close(self) -> None:
        """Clean up all provider connections."""
        for provider in self.providers:
            try:
                await provider.close()
            except Exception:
                pass
