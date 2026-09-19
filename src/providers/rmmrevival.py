"""RMM Revival (lyrics.rmmreviv.al) Lyrics Provider."""

import logging
from typing import Any, Dict, Optional
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata, detect_sync_type
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, safe_float
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.rmmrevival")


class RMMRevivalProvider(BaseLyricsProvider):
    """Provider for Apple Music syllable-level TTML lyrics via lyrics.rmmreviv.al API."""

    name = "rmmrevival"
    description = "RMM Revival / Apple Music Worker (word-sync TTML & synced LRC)"

    DEFAULT_API_BASE = "https://lyrics.rmmreviv.al"
    ITUNES_SEARCH_API = "https://itunes.apple.com/search"

    async def _resolve_apple_music_id(self, title: str, artist: str, track: TrackMetadata) -> Optional[str]:
        """Resolve Apple Music track ID using iTunes Search API or rmmrevival search fallback."""
        # 1. Check if ID already exists in track metadata or tags
        if hasattr(track, "metadata") and isinstance(track.metadata, dict):
            existing_id = track.metadata.get("apple_music_id") or track.metadata.get("itunes_id")
            if existing_id:
                return str(existing_id)

        # 2. Search via free public iTunes Search API
        itunes_query = f"{artist} {title}".strip()
        itunes_params = {
            "term": itunes_query,
            "entity": "song",
            "limit": 5,
        }

        try:
            itunes_resp = await self.request_with_retry("GET", self.ITUNES_SEARCH_API, params=itunes_params)
            if itunes_resp and itunes_resp.status_code == 200:
                data = itunes_resp.json()
                results = data.get("results", [])
                best_id = None
                best_score = 0.0

                for item in results:
                    cand_title = item.get("trackName", "")
                    cand_artist = item.get("artistName", "")
                    score = calculate_candidate_score(title, artist, cand_title, cand_artist)
                    if score > best_score:
                        best_score = score
                        best_id = item.get("trackId")

                if best_id and best_score >= 0.60:
                    logger.debug(f"[{self.name}] Resolved track ID {best_id} (score {best_score:.2f}) via iTunes API")
                    return str(best_id)
        except Exception as e:
            logger.debug(f"[{self.name}] Error searching iTunes API: {e}")

        # 3. Fallback: Search via lyrics.rmmreviv.al /search endpoint
        api_base = (self.config.custom_url or self.DEFAULT_API_BASE).rstrip("/")
        search_url = f"{api_base}/search"
        search_params = {"q": f"{artist} {title}".strip()}

        try:
            search_resp = await self.request_with_retry("GET", search_url, params=search_params)
            if search_resp and search_resp.status_code == 200:
                data = search_resp.json()
                results = data.get("results", [])
                best_id = None
                best_score = 0.0

                for item in results:
                    cand_title = item.get("name", "")
                    cand_artist = item.get("artist", "")
                    score = calculate_candidate_score(title, artist, cand_title, cand_artist)
                    if score > best_score:
                        best_score = score
                        best_id = item.get("id")

                if best_id and best_score >= 0.60:
                    logger.debug(f"[{self.name}] Resolved track ID {best_id} (score {best_score:.2f}) via rmmrevival search")
                    return str(best_id)
        except Exception as e:
            logger.debug(f"[{self.name}] Error searching rmmrevival search: {e}")

        return None

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        track_id = await self._resolve_apple_music_id(title, artist, track)
        if not track_id:
            logger.debug(f"[{self.name}] Could not resolve Apple Music ID for {artist} - {title}")
            return None

        api_base = (self.config.custom_url or self.DEFAULT_API_BASE).rstrip("/")
        endpoint = f"{api_base}/lyrics"
        params = {"id": track_id}
        headers = {
            "User-Agent": self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        }

        resp = await self.request_with_retry("GET", endpoint, params=params, headers=headers)
        if not resp or resp.status_code != 200:
            return None

        try:
            data = resp.json()
            if not isinstance(data, dict):
                return None

            cand_title = data.get("name") or title
            cand_artist = data.get("artist") or artist
            duration = safe_float(data.get("duration"))
            score = calculate_candidate_score(title, artist, cand_title, cand_artist)
            meta = {
                "apple_music_id": track_id,
                "isrc": data.get("isrc"),
                "source": data.get("lyricsSource", "rmmrevival"),
            }

            # 1. Prefer syllable-level TTML lyrics
            ttml = data.get("ttml")
            if ttml and isinstance(ttml, str) and "<tt" in ttml.lower():
                sync_type = detect_sync_type(ttml, LyricsFormat.TTML)
                return LyricsResult(
                    content=ttml.strip(),
                    format=LyricsFormat.TTML,
                    sync_type=sync_type,
                    provider_name=self.name,
                    title=cand_title,
                    artist=cand_artist,
                    duration=duration,
                    match_score=score,
                    metadata=meta,
                )

            # 2. Fallback to synced LRC lyrics
            synced = data.get("syncedLyrics")
            if synced and isinstance(synced, str) and "[" in synced:
                sync_type = detect_sync_type(synced, LyricsFormat.LRC)
                return LyricsResult(
                    content=synced.strip(),
                    format=LyricsFormat.LRC,
                    sync_type=sync_type,
                    provider_name=self.name,
                    title=cand_title,
                    artist=cand_artist,
                    duration=duration,
                    match_score=score,
                    metadata=meta,
                )

            # 3. Fallback to unsynced plain text lyrics
            plain = data.get("lyrics")
            if plain and isinstance(plain, str) and plain.strip():
                return LyricsResult(
                    content=plain.strip(),
                    format=LyricsFormat.TXT,
                    sync_type=LyricsSyncType.UNSYNCED,
                    provider_name=self.name,
                    title=cand_title,
                    artist=cand_artist,
                    duration=duration,
                    match_score=score,
                    metadata=meta,
                )

        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing rmmrevival lyrics response: {e}")

        return None
