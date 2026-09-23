"""Blend lyrics providers implementing the 5 specific fusions from mild-lyrics.

1. blend:      Apple Music lines + QQ Music word timing
2. kublend:    Apple Music lines + Kugou word timing
3. neblend:    Apple Music lines + NetEase word timing
4. triblend:   Apple Music lines + NetEase word timing + QQ Music filler
5. kutriblend: Apple Music lines + NetEase word timing + Kugou filler
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.blender import blend_lyrics
from src.models import LyricsResult, LyricsSyncType, TrackMetadata
from src.providers.apple_music import AppleMusicProvider
from src.providers.base import BaseLyricsProvider
from src.providers.kugou import KugouProvider
from src.providers.lrclib import LrclibProvider
from src.providers.netease import NetEaseProvider
from src.providers.qqmusic import QQMusicProvider
from src.providers.spicylyrics import SpicyLyricsProvider

logger = logging.getLogger("nla.providers.blends")


class _BaseBlendProvider(BaseLyricsProvider):
    """Base provider reconciling Apple Music text lines with word-synced timing donor(s)."""

    supports_line_sync: bool = False
    supports_word_sync: bool = True

    donor_name: str = ""
    donor_label: str = ""
    spare_name: str = ""
    spare_label: str = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apple = AppleMusicProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )
        self._spicy = SpicyLyricsProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )
        self._lrclib = LrclibProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )
        self._donor_provider: Optional[BaseLyricsProvider] = None
        self._spare_provider: Optional[BaseLyricsProvider] = None
        self._init_donors()

    def _init_donors(self) -> None:
        raise NotImplementedError

    async def _fetch_base(self, track: TrackMetadata) -> Optional[LyricsResult]:
        """Fetch pristine text and line structure from Apple Music, SpicyLyrics, or LRCLIB."""
        try:
            res = await self._apple.get_lyrics(track)
            if res and res.content:
                return res
        except Exception as e:
            logger.debug(f"[{self.name}] Apple Music base lookup error: {e}")

        try:
            res = await self._spicy.get_lyrics(track)
            if res and res.content:
                return res
        except Exception as e:
            logger.debug(f"[{self.name}] SpicyLyrics base fallback error: {e}")

        try:
            res = await self._lrclib.get_lyrics(track)
            if res and res.content:
                return res
        except Exception as e:
            logger.debug(f"[{self.name}] LRCLIB base fallback error: {e}")

        return None

    async def _fetch_donor(self, track: TrackMetadata) -> Optional[LyricsResult]:
        if not self._donor_provider:
            return None
        try:
            return await self._donor_provider.get_lyrics(track)
        except Exception as e:
            logger.debug(f"[{self.name}] Timing donor ({self.donor_label}) error: {e}")
            return None

    async def _fetch_spare(self, track: TrackMetadata) -> Optional[LyricsResult]:
        if not self._spare_provider:
            return None
        try:
            return await self._spare_provider.get_lyrics(track)
        except Exception as e:
            logger.debug(f"[{self.name}] Spare timing donor ({self.spare_label}) error: {e}")
            return None

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        tasks = [self._fetch_base(track), self._fetch_donor(track)]
        if self.spare_name:
            tasks.append(self._fetch_spare(track))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        base_res = results[0] if not isinstance(results[0], Exception) else None
        donor_res = results[1] if not isinstance(results[1], Exception) else None
        spare_res = results[2] if len(results) > 2 and not isinstance(results[2], Exception) else None

        if not base_res or not donor_res:
            return None

        # If base is already word-synced and donor is not, return base as-is
        if base_res.sync_type == LyricsSyncType.WORD_SYNC and donor_res.sync_type != LyricsSyncType.WORD_SYNC:
            logger.debug(f"[{self.name}] Base is already word-synced and donor is not, returning base as-is")
            return base_res

        # Reconcile base lines with donor word timings
        blended = blend_lyrics(
            base=base_res,
            donor=donor_res,
            spare_donor=spare_res,
            blend_name=self.name,
            donor_label=self.donor_label,
            spare_label=self.spare_label,
        )
        if blended:
            return blended

        # Graceful fallback if alignment was rejected
        if base_res.sync_type == LyricsSyncType.WORD_SYNC:
            return base_res
        if donor_res.sync_type == LyricsSyncType.WORD_SYNC:
            return donor_res
        return base_res

    async def close(self) -> None:
        await super().close()
        await self._apple.close()
        await self._spicy.close()
        await self._lrclib.close()
        if self._donor_provider:
            await self._donor_provider.close()
        if self._spare_provider:
            await self._spare_provider.close()


class AppleQQBlendProvider(_BaseBlendProvider):
    """Apple Music lines with QQ Music word timing ('blend')."""

    name = "blend"
    description = "Apple Music lines + QQ Music word timing (mild-lyrics blend)"
    donor_name = "qqmusic"
    donor_label = "QQ Music"

    def _init_donors(self) -> None:
        self._donor_provider = QQMusicProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )


class AppleKugouBlendProvider(_BaseBlendProvider):
    """Apple Music lines with Kugou word timing ('kublend')."""

    name = "kublend"
    description = "Apple Music lines + Kugou word timing (mild-lyrics kublend)"
    donor_name = "kugou"
    donor_label = "Kugou"

    def _init_donors(self) -> None:
        self._donor_provider = KugouProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )


class AppleNetEaseBlendProvider(_BaseBlendProvider):
    """Apple Music lines with NetEase word timing ('neblend')."""

    name = "neblend"
    description = "Apple Music lines + NetEase word timing (mild-lyrics neblend)"
    donor_name = "netease"
    donor_label = "NetEase"

    def _init_donors(self) -> None:
        self._donor_provider = NetEaseProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )


class AppleNetEaseQQBlendProvider(_BaseBlendProvider):
    """Apple Music lines with NetEase word timing and QQ Music filler ('triblend')."""

    name = "triblend"
    description = "Apple Music lines + NetEase word timing + QQ Music gap-fill (mild-lyrics triblend)"
    donor_name = "netease"
    donor_label = "NetEase"
    spare_name = "qqmusic"
    spare_label = "QQ Music"

    def _init_donors(self) -> None:
        self._donor_provider = NetEaseProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )
        self._spare_provider = QQMusicProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )


class AppleNetEaseKugouBlendProvider(_BaseBlendProvider):
    """Apple Music lines with NetEase word timing and Kugou filler ('kutriblend')."""

    name = "kutriblend"
    description = "Apple Music lines + NetEase word timing + Kugou gap-fill (mild-lyrics kutriblend)"
    donor_name = "netease"
    donor_label = "NetEase"
    spare_name = "kugou"
    spare_label = "Kugou"

    def _init_donors(self) -> None:
        self._donor_provider = NetEaseProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )
        self._spare_provider = KugouProvider(
            user_agent=self.user_agent,
            default_timeout=self.timeout,
            max_retries=self.max_retries,
        )
