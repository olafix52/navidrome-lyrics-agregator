"""BiniLyrics / Community Synced REST Provider."""

import logging
from typing import Any, Dict, List, Optional
from src.models import (
    LyricsFormat,
    LyricsResult,
    LyricsSyncType,
    TrackMetadata,
    detect_sync_type,
)
from src.normalizer import calculate_candidate_score, calculate_string_similarity, clean_artist, clean_title, safe_float
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.binilyrics")


class BiniLyricsProvider(BaseLyricsProvider):
    """Provider for BiniLyrics / Aligned TTML lyrics (lyrics-api.binimum.org)."""

    name = "binilyrics"
    description = "BiniLyrics / Aligned REST Provider (TTML / Synced)"

    DEFAULT_API_BASE = "https://lyrics-api.binimum.org"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        api_base = self.config.custom_url or self.DEFAULT_API_BASE
        endpoint = f"{api_base.rstrip('/')}/getLyrics"

        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        # 1. Try search by ISRC if available
        if track.isrc:
            res = await self._query_and_extract(endpoint, {"isrc": track.isrc}, track)
            if res:
                return res

        # 2. Try exact search with track + artist (+ optional duration)
        params: Dict[str, Any] = {
            "track": title,
            "artist": artist,
        }
        if track.duration > 0:
            params["duration"] = str(int(round(track.duration)))

        res = await self._query_and_extract(endpoint, params, track)
        if res:
            return res

        # 3. Fallback search with free-text query 'q'
        fallback_params = {"q": f"{artist} - {title}"}
        return await self._query_and_extract(endpoint, fallback_params, track)

    async def _query_and_extract(
        self,
        endpoint: str,
        params: Dict[str, Any],
        track: TrackMetadata,
    ) -> Optional[LyricsResult]:
        response = await self.request_with_retry("GET", endpoint, params=params)
        if not response:
            return None
        if hasattr(response, "status_code") and isinstance(response.status_code, int) and response.status_code != 200:
            return None

        try:
            data = response.json()
            if not isinstance(data, dict):
                return None

            # 1. Standard BiniLyrics API response with 'results' array
            results = data.get("results")
            if isinstance(results, list) and results:
                clean_t = track.clean_title or clean_title(track.title)
                clean_a = track.clean_artist or clean_artist(track.artist)

                candidates = []
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    item_track = item.get("track_name") or item.get("title") or ""
                    item_artist = item.get("artist_name") or item.get("artist") or ""
                    total = calculate_candidate_score(clean_t, clean_a, item_track, item_artist)
                    if item.get("timing_type") == "word" and total > 0.0:
                        total += 0.05
                    if total >= 0.60:
                        candidates.append((total, item))

                candidates.sort(key=lambda x: x[0], reverse=True)

                for _, item in candidates:
                    lyrics_url = item.get("lyricsUrl") or item.get("url")
                    ttml_content = item.get("ttml") or item.get("content")
                    timing_hint = item.get("timing_type")

                    if not ttml_content and lyrics_url:
                        lyr_resp = await self.request_with_retry("GET", lyrics_url)
                        if lyr_resp and lyr_resp.status_code == 200:
                            ttml_content = lyr_resp.text

                    if ttml_content and isinstance(ttml_content, str) and ttml_content.strip():
                        return self._create_result(
                            content=ttml_content.strip(),
                            title=item.get("track_name") or track.title,
                            artist=item.get("artist_name") or track.artist,
                            duration=safe_float(item.get("duration")),
                            metadata=item,
                            hint=timing_hint,
                        )

            # 2. Legacy / Direct payload fallback (mock/alternate formats)
            ttml_content = data.get("ttml") or data.get("content") or data.get("lyrics")
            ttml_url = data.get("ttmlUrl") or data.get("url") or data.get("lyricsUrl")

            if not ttml_content and ttml_url:
                ttml_resp = await self.request_with_retry("GET", ttml_url)
                if ttml_resp and ttml_resp.status_code == 200:
                    ttml_content = ttml_resp.text

            if ttml_content and isinstance(ttml_content, str) and ttml_content.strip():
                return self._create_result(
                    content=ttml_content.strip(),
                    title=data.get("title") or track.title,
                    artist=data.get("artist") or track.artist,
                    duration=safe_float(data.get("duration")),
                    metadata=data,
                    hint=data.get("timing_type") or data.get("timingType"),
                )

        except Exception as e:
            logger.debug(f"[{self.name}] BiniLyrics fetch error: {e}")

        return None

    def _create_result(
        self,
        content: str,
        title: str,
        artist: str,
        duration: Optional[float],
        metadata: Dict[str, Any],
        hint: Optional[str] = None,
    ) -> LyricsResult:
        if "<tt" in content.lower():
            fmt = LyricsFormat.TTML
            sync_type = detect_sync_type(content, LyricsFormat.TTML, hint=hint)
        elif "[" in content and "]" in content and ":" in content:
            fmt = LyricsFormat.LRC
            sync_type = detect_sync_type(content, LyricsFormat.LRC, hint=hint)
        else:
            fmt = LyricsFormat.TXT
            sync_type = LyricsSyncType.UNSYNCED

        return LyricsResult(
            content=content,
            format=fmt,
            sync_type=sync_type,
            provider_name=self.name,
            duration=duration,
            title=title,
            artist=artist,
            metadata=metadata,
        )
