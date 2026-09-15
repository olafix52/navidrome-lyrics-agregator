"""Matching engine orchestrating provider cascade and verification for audio tracks."""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from src.config import AppConfig
from src.models import (
    LyricsSyncType,
    MatchStatus,
    ProcessResult,
    TrackMetadata,
)
from src.normalizer import verify_track_match
from src.providers.base import BaseLyricsProvider
from src.storage import save_lyrics_sidecar, should_skip_track

logger = logging.getLogger("nla.matcher")


class LyricsMatcher:
    """Orchestrates lyrics retrieval across multiple providers in cascade order."""

    def __init__(self, config: AppConfig, providers: List[BaseLyricsProvider]):
        self.config = config
        self.providers = providers
        self._search_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._cache_ttl = 600.0  # 10 minutes cache TTL

    async def process_track(self, track: TrackMetadata) -> ProcessResult:
        """Process a single audio track: check existing sidecar, query providers, verify, and save."""
        # 1. Check if track should be skipped
        skip, skip_reason = should_skip_track(
            track.file_path,
            overwrite=self.config.overwrite,
            upgrade_quality=self.config.upgrade_quality,
        )
        if skip:
            logger.debug(f"[SKIPPED] {track.display_name()} - {skip_reason}")
            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SKIPPED,
                error_message=skip_reason,
            )

        logger.info(f"[SEARCHING] {track.display_name()} ({track.duration:.1f}s)")

        # 2. Iterate through provider cascade with quality priority (WORD_SYNC > LINE_SYNC > UNSYNCED)
        best_match = None  # Tuple[LyricsResult, BaseLyricsProvider, float]

        for provider in self.providers:
            try:
                logger.debug(f"[{provider.name}] Querying for '{track.display_name()}'...")
                lyrics = await provider.get_lyrics(track)

                if not lyrics or not lyrics.content:
                    continue

                # Plain lyrics check
                if lyrics.sync_type == LyricsSyncType.UNSYNCED and not self.config.allow_plain_lyrics:
                    logger.debug(f"[{provider.name}] Plain lyrics ignored (allow_plain_lyrics=False)")
                    continue

                # Verify match (duration + similarity)
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

                # 1. Top Tier: Word-level synchronization (TTML with spans or lyricsfile YAML)
                if lyrics.sync_type == LyricsSyncType.WORD_SYNC:
                    best_match = (lyrics, provider, score)
                    break  # Maximum possible quality found, stop searching

                # 2. Middle Tier: Line-level synchronization (LRC or line-only TTML)
                if lyrics.sync_type == LyricsSyncType.LINE_SYNC:
                    if not best_match or best_match[0].sync_type == LyricsSyncType.UNSYNCED:
                        best_match = (lyrics, provider, score)
                    # Continue cascade to check if any remaining provider has WORD_SYNC
                    continue

                # 3. Lowest Tier: Plain unsynced text
                if not best_match:
                    best_match = (lyrics, provider, score)

            except Exception as e:
                logger.warning(f"[{provider.name}] Exception while processing {track.display_name()}: {e}")

        if best_match:
            lyrics, provider, score = best_match
            target_path = save_lyrics_sidecar(
                track.file_path,
                lyrics,
                dry_run=self.config.dry_run,
            )

            logger.info(
                f"[FOUND] {track.display_name()} -> {lyrics.format.value.upper()} ({lyrics.sync_type.value}) "
                f"via {provider.name} (score: {score:.2f}) -> {target_path.name}"
            )

            return ProcessResult(
                file_path=track.file_path,
                status=MatchStatus.SUCCESS,
                provider=provider.name,
                format=lyrics.format,
                target_file=target_path,
                match_score=score,
            )

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
                }
            except asyncio.TimeoutError:
                logger.debug(f"[{provider.name}] Manual search timed out after {timeout_per_provider}s")
                return None
            except Exception as e:
                logger.debug(f"[{provider.name}] Error during manual provider search: {e}")
                return None

        # Execute concurrently and stream as each completes
        tasks = [asyncio.create_task(_query(p)) for p in self.providers]
        for fut in asyncio.as_completed(tasks):
            res = await fut
            if res is not None:
                collected.append(res)
                yield res

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
