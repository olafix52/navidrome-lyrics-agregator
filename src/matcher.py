"""Matching engine orchestrating provider cascade and verification for audio tracks."""

import logging
from typing import List, Optional
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

    async def close(self) -> None:
        """Clean up all provider connections."""
        for provider in self.providers:
            try:
                await provider.close()
            except Exception:
                pass
