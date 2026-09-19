"""LRCLIB Lyrics Provider (https://lrclib.net)."""

import logging
from typing import Any, Dict, Optional
from src.models import (
    LyricsFormat,
    LyricsResult,
    LyricsSyncType,
    TrackMetadata,
    detect_sync_type,
)
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, safe_float
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.lrclib")


class LrclibProvider(BaseLyricsProvider):
    """Provider for LRCLIB (supporting YAML word-sync and LRC line-sync)."""

    name = "lrclib"
    description = "LRCLIB Database (YAML word-synced and LRC line-synced lyrics)"

    DEFAULT_API_BASE = "https://lrclib.net"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        api_base = self.config.custom_url or self.DEFAULT_API_BASE

        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        # 1. Try exact /api/get endpoint
        get_params: Dict[str, Any] = {
            "track_name": title,
            "artist_name": artist,
        }
        if track.album:
            get_params["album_name"] = track.album
        if track.duration > 0:
            get_params["duration"] = int(round(track.duration))

        get_url = f"{api_base.rstrip('/')}/api/get"
        response = await self.request_with_retry("GET", get_url, params=get_params)

        if response:
            try:
                data = response.json()
                res = self._parse_lrclib_response(data, track, score=1.0)
                if res:
                    return res
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing /api/get: {e}")

        # 2. Try /api/search endpoint fallback
        search_url = f"{api_base.rstrip('/')}/api/search"
        search_params = {
            "track_name": title,
            "artist_name": artist,
        }
        search_resp = await self.request_with_retry("GET", search_url, params=search_params)

        if search_resp:
            try:
                items = search_resp.json()
                if isinstance(items, list) and items:
                    scored_items = []
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        c_title = item.get("trackName") or item.get("name") or ""
                        c_artist = item.get("artistName") or ""
                        score = calculate_candidate_score(title, artist, c_title, c_artist)
                        if score >= 0.60:
                            scored_items.append((score, item))

                    scored_items.sort(key=lambda x: x[0], reverse=True)
                    for score, item in scored_items:
                        res = self._parse_lrclib_response(item, track, score=score)
                        if res:
                            return res
            except Exception as e:
                logger.debug(f"[{self.name}] Error parsing /api/search: {e}")

        return None

    def _parse_lrclib_response(
        self,
        data: Dict[str, Any],
        track: TrackMetadata,
        score: float = 1.0,
    ) -> Optional[LyricsResult]:
        if not isinstance(data, dict):
            return None

        # 1. Check for YAML lyricsfile
        yaml_content = data.get("lyricsfile") or data.get("yaml") or data.get("wordSyncedLyrics")
        synced_lyrics = data.get("syncedLyrics")

        # If YAML actually contains word-level synchronization, return WORD_SYNC YAML
        if yaml_content and isinstance(yaml_content, str) and yaml_content.strip():
            yaml_sync_type = detect_sync_type(yaml_content, LyricsFormat.YAML)
            if yaml_sync_type == LyricsSyncType.WORD_SYNC:
                return LyricsResult(
                    content=yaml_content.strip(),
                    format=LyricsFormat.YAML,
                    sync_type=LyricsSyncType.WORD_SYNC,
                    provider_name=self.name,
                    duration=safe_float(data.get("duration")),
                    title=data.get("trackName") or data.get("name"),
                    artist=data.get("artistName"),
                    album=data.get("albumName"),
                    match_score=score,
                    metadata={"lrclib_id": data.get("id"), "format": "yaml"},
                )

        # 2. Check for synced LRC lyrics (standard line sync)
        if synced_lyrics and isinstance(synced_lyrics, str) and synced_lyrics.strip():
            lrc_sync_type = detect_sync_type(synced_lyrics, LyricsFormat.LRC)
            return LyricsResult(
                content=synced_lyrics.strip(),
                format=LyricsFormat.LRC,
                sync_type=lrc_sync_type,
                provider_name=self.name,
                duration=safe_float(data.get("duration")),
                title=data.get("trackName") or data.get("name"),
                artist=data.get("artistName"),
                album=data.get("albumName"),
                match_score=score,
                metadata={"lrclib_id": data.get("id"), "format": "lrc"},
            )

        # 3. Fallback: If only line-synced YAML was provided and no syncedLyrics
        if yaml_content and isinstance(yaml_content, str) and yaml_content.strip():
            return LyricsResult(
                content=yaml_content.strip(),
                format=LyricsFormat.YAML,
                sync_type=LyricsSyncType.LINE_SYNC,
                provider_name=self.name,
                duration=safe_float(data.get("duration")),
                title=data.get("trackName") or data.get("name"),
                artist=data.get("artistName"),
                album=data.get("albumName"),
                match_score=score,
                metadata={"lrclib_id": data.get("id"), "format": "yaml"},
            )

        # Check for instrumental flag
        if data.get("instrumental") is True:
            return LyricsResult(
                content="[00:00.00] ♪ Instrumental ♪\n",
                format=LyricsFormat.LRC,
                sync_type=LyricsSyncType.LINE_SYNC,
                provider_name=self.name,
                duration=safe_float(data.get("duration")),
                title=data.get("trackName") or data.get("name"),
                artist=data.get("artistName"),
                album=data.get("albumName"),
                match_score=score,
                metadata={"instrumental": True},
            )

        # Plain lyrics fallback if configured/needed
        plain_lyrics = data.get("plainLyrics")
        if plain_lyrics and isinstance(plain_lyrics, str) and plain_lyrics.strip():
            return LyricsResult(
                content=plain_lyrics.strip(),
                format=LyricsFormat.TXT,
                sync_type=LyricsSyncType.UNSYNCED,
                provider_name=self.name,
                duration=safe_float(data.get("duration")),
                title=data.get("trackName") or data.get("name"),
                artist=data.get("artistName"),
                album=data.get("albumName"),
                match_score=score,
                metadata={"lrclib_id": data.get("id"), "format": "plain"},
            )

        return None
