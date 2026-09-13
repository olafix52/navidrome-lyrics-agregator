"""Better Lyrics Unison Community Lyrics Provider."""

import logging
from typing import Optional
from src.models import (
    LyricsFormat,
    LyricsResult,
    LyricsSyncType,
    TrackMetadata,
    detect_sync_type,
)
from src.normalizer import calculate_candidate_score, clean_artist, clean_title
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.unison")


class UnisonProvider(BaseLyricsProvider):
    """Provider for Unison / Better Lyrics crowdsourced synced lyrics."""

    name = "unison"
    description = "Unison (Better Lyrics crowdsourced synced and TTML lyrics)"

    DEFAULT_API_BASE = "https://unison.boidu.dev"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        api_base = self.config.custom_url or self.DEFAULT_API_BASE

        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        # 1. Try direct lookup by song and artist
        endpoint = f"{api_base.rstrip('/')}/lyrics"
        params = {
            "song": title,
            "artist": artist,
        }

        response = await self.request_with_retry("GET", endpoint, params=params)
        data = None
        if response and response.status_code == 200:
            try:
                res_json = response.json()
                if res_json.get("success") and isinstance(res_json.get("data"), dict):
                    candidate_data = res_json["data"]
                    c_title = candidate_data.get("song") or candidate_data.get("title") or ""
                    c_artist = candidate_data.get("artist") or ""
                    if calculate_candidate_score(title, artist, c_title, c_artist) >= 0.60:
                        data = candidate_data
            except Exception:
                data = None

        # 2. Fallback to /lyrics/search if direct lookup failed
        if not data or not data.get("lyrics"):
            search_url = f"{api_base.rstrip('/')}/lyrics/search"
            search_query = f"{artist} {title}".strip()
            search_resp = await self.request_with_retry("GET", search_url, params={"q": search_query})
            if search_resp and search_resp.status_code == 200:
                try:
                    s_json = search_resp.json()
                    items = s_json.get("data", []) if s_json.get("success") else []
                    if items and isinstance(items, list):
                        scored_items = []
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            cand_id = item.get("id")
                            cand_title = item.get("song") or item.get("title") or ""
                            cand_artist = item.get("artist") or ""
                            cand_score = calculate_candidate_score(title, artist, cand_title, cand_artist)
                            if cand_score >= 0.60:
                                scored_items.append((cand_score, cand_id))

                        scored_items.sort(key=lambda x: x[0], reverse=True)
                        for _, cand_id in scored_items:
                            if not cand_id:
                                continue
                            get_url = f"{api_base.rstrip('/')}/lyrics/{cand_id}"
                            get_resp = await self.request_with_retry("GET", get_url)
                            if get_resp and get_resp.status_code == 200:
                                g_json = get_resp.json()
                                if g_json.get("success") and isinstance(g_json.get("data"), dict):
                                    data = g_json["data"]
                                    break
                except Exception as e:
                    logger.debug(f"[{self.name}] Error searching Unison lyrics: {e}")

        if not data or not isinstance(data, dict):
            return None

        raw_content = (data.get("lyrics") or "").strip()
        if not raw_content:
            return None

        format_str = (data.get("format") or "").lower()
        sync_type_str = (data.get("syncType") or "").lower()
        resp_title = data.get("song") or title
        resp_artist = data.get("artist") or artist

        # Detect format & sync level
        if format_str == "ttml" or ("<tt" in raw_content.lower() and "</tt>" in raw_content.lower()):
            fmt = LyricsFormat.TTML
            sync_type = detect_sync_type(raw_content, LyricsFormat.TTML, hint=sync_type_str)
        elif format_str in ("yaml", "lyricsfile") or ("lines:" in raw_content and "start_ms:" in raw_content):
            fmt = LyricsFormat.YAML
            sync_type = detect_sync_type(raw_content, LyricsFormat.YAML, hint=sync_type_str)
        elif format_str == "lrc" or ("[" in raw_content and "]" in raw_content and ":" in raw_content):
            fmt = LyricsFormat.LRC
            sync_type = detect_sync_type(raw_content, LyricsFormat.LRC, hint=sync_type_str)
        else:
            fmt = LyricsFormat.TXT
            sync_type = LyricsSyncType.UNSYNCED

        return LyricsResult(
            content=raw_content,
            format=fmt,
            sync_type=sync_type,
            provider_name=self.name,
            title=resp_title,
            artist=resp_artist,
            metadata={
                "unison_id": data.get("id"),
                "score": data.get("score"),
                "format": format_str,
            },
        )
