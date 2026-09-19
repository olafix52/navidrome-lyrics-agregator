"""AMLL TTML Database Provider (Apple Music-Like Lyrics database)."""

import logging
from typing import Any, Dict, List, Optional
from src.models import (
    LyricsFormat,
    LyricsResult,
    TrackMetadata,
    detect_sync_type,
)
from src.normalizer import calculate_candidate_score, clean_artist, clean_title
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.amll")


class AMLLProvider(BaseLyricsProvider):
    """Provider for AMLL TTML Database (https://api.amll.dev)."""

    name = "amll"
    description = "Apple Music-Like Lyrics Database (TTML with syllable-level sync)"

    DEFAULT_API_BASE = "https://api.amll.dev"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        api_base = self.config.custom_url or self.DEFAULT_API_BASE

        # 1. Search by ISRC if available
        if track.isrc:
            res = await self._search_and_fetch_by_param(api_base, {"isrc": track.isrc}, track)
            if res:
                return res

        # 2. Search by structured parameters (trackName + artistName)
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        
        search_params = {
            "trackName": title,
            "artistName": artist,
        }
        res = await self._search_and_fetch(api_base, search_params, track)
        if res:
            return res

        # 3. Fallback: Search with general query 'q'
        query_params = {"q": f"{artist} {title}"}
        return await self._search_and_fetch(api_base, query_params, track)

    async def _search_and_fetch(
        self,
        api_base: str,
        params: Dict[str, Any],
        track: TrackMetadata,
    ) -> Optional[LyricsResult]:
        search_url = f"{api_base.rstrip('/')}/v1/lyrics/search"
        response = await self.request_with_retry("GET", search_url, params=params)
        if not response:
            return None

        try:
            data = response.json()
            items: List[Dict[str, Any]] = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                d_data = data.get("data", {})
                if isinstance(d_data, dict):
                    items = d_data.get("items", d_data.get("results", []))
                elif isinstance(d_data, list):
                    items = d_data
                else:
                    items = data.get("results", data.get("items", []))

            if not items or not isinstance(items, list):
                return None

            clean_t = track.clean_title or clean_title(track.title)
            clean_a = track.clean_artist or clean_artist(track.artist)

            # Score and filter candidates
            scored_candidates = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                music_names = item.get("musicNames") or []
                if not music_names:
                    single_name = item.get("trackName") or item.get("title")
                    if single_name:
                        music_names = [single_name]

                artist_names = item.get("artistNames") or []
                if not artist_names:
                    single_artist = item.get("artistName") or item.get("artist")
                    if single_artist:
                        artist_names = [single_artist]

                best_item_score = 0.0
                if music_names and artist_names:
                    for mn in music_names:
                        for an in artist_names:
                            s = calculate_candidate_score(clean_t, clean_a, mn, an)
                            if s > best_item_score:
                                best_item_score = s
                elif music_names:
                    for mn in music_names:
                        s = calculate_candidate_score(clean_t, clean_a, mn, "")
                        if s > best_item_score:
                            best_item_score = s

                if best_item_score >= 0.60:
                    scored_candidates.append((best_item_score, item))

            # Sort descending by score
            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            for score, item in scored_candidates[:5]:
                lyric_id = item.get("id")
                filename = item.get("filename")
                
                music_names = item.get("musicNames", [])
                artist_names = item.get("artistNames", [])
                candidate_title = music_names[0] if music_names else (item.get("trackName") or item.get("title"))
                candidate_artist = ", ".join(artist_names) if artist_names else (item.get("artistName") or item.get("artist"))

                # Fetch full TTML
                lyric_data = await self._fetch_lyrics_by_id_or_filename(api_base, lyric_id, filename)
                if not lyric_data:
                    continue

                if isinstance(lyric_data, dict) and "data" in lyric_data and isinstance(lyric_data["data"], dict):
                    lyric_data = lyric_data["data"]

                ttml_content = lyric_data.get("lyrics") or lyric_data.get("ttml") or lyric_data.get("content")
                if not ttml_content or not isinstance(ttml_content, str):
                    continue

                if "<tt" not in ttml_content.lower():
                    continue

                sync_type = detect_sync_type(ttml_content, LyricsFormat.TTML)
                return LyricsResult(
                    content=ttml_content,
                    format=LyricsFormat.TTML,
                    sync_type=sync_type,
                    provider_name=self.name,
                    title=candidate_title or track.title,
                    artist=candidate_artist or track.artist,
                    match_score=score,
                    metadata={"amll_id": lyric_id, "filename": filename},
                )

        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing search results: {e}")

        return None

    async def _search_and_fetch_by_param(
        self,
        api_base: str,
        params: Dict[str, Any],
        track: TrackMetadata,
    ) -> Optional[LyricsResult]:
        get_url = f"{api_base.rstrip('/')}/v1/lyrics/get"
        response = await self.request_with_retry("GET", get_url, params=params)
        if not response:
            return None

        try:
            data = response.json()
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                data = data["data"]

            score = 1.0
            ttml = data.get("lyrics") or data.get("ttml") or data.get("content")
            if ttml and isinstance(ttml, str) and "<tt" in ttml.lower():
                return LyricsResult(
                    content=ttml,
                    format=LyricsFormat.TTML,
                    sync_type=detect_sync_type(ttml, LyricsFormat.TTML),
                    provider_name=self.name,
                    title=data.get("trackName") or data.get("title"),
                    artist=data.get("artistName") or data.get("artist"),
                    match_score=score,
                    metadata=data,
                )
        except Exception as e:
            logger.debug(f"[{self.name}] Error in param get: {e}")

        return None

    async def _fetch_lyrics_by_id_or_filename(
        self,
        api_base: str,
        lyric_id: Optional[Any],
        filename: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        get_url = f"{api_base.rstrip('/')}/v1/lyrics/get"
        params: Dict[str, Any] = {}
        if lyric_id is not None:
            params["id"] = lyric_id
        elif filename:
            params["filename"] = filename
        else:
            return None

        response = await self.request_with_retry("GET", get_url, params=params)
        if response:
            try:
                return response.json()
            except Exception:
                pass
        return None
