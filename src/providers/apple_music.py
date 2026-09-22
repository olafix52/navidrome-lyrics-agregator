import json
import logging
from typing import Any, Dict, Optional
from src.models import (
    LyricsFormat,
    LyricsResult,
    TrackMetadata,
    detect_sync_type,
)
from src.normalizer import calculate_string_similarity, clean_artist, clean_title
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.apple_music")


class AppleMusicProvider(BaseLyricsProvider):
    """Provider for Apple Music syllable-level TTML lyrics."""

    name = "apple_music"
    description = "Apple Music Catalog / TTML API"
    supports_line_sync = False

    DEFAULT_AMLL_BRIDGE = "https://api.amll.dev"
    DEFAULT_STOREFRONT = "us"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        bridge_url = self.config.custom_url or self.DEFAULT_AMLL_BRIDGE
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        # 1. If custom developer token / bearer token is provided in config, query amp-api directly
        dev_token = self.config.api_key or self.config.extra.get("developer_token")
        if dev_token:
            res = await self._fetch_via_amp_api(title, artist, track, dev_token)
            if res:
                return res

        # 2. Query via AMLL Apple Music lookup bridge
        search_params = {
            "trackName": title,
            "artistName": artist,
        }
        if track.isrc:
            search_params["isrc"] = track.isrc

        return await self._fetch_via_bridge(bridge_url, search_params, track)

    async def _fetch_via_bridge(
        self,
        bridge_url: str,
        params: Dict[str, Any],
        track: TrackMetadata,
    ) -> Optional[LyricsResult]:
        search_url = f"{bridge_url.rstrip('/')}/v1/lyrics/search"
        response = await self.request_with_retry("GET", search_url, params=params)
        if not response:
            return None

        try:
            data = response.json()
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                d_data = data.get("data", {})
                if isinstance(d_data, dict):
                    items = d_data.get("items", d_data.get("results", []))
                elif isinstance(d_data, list):
                    items = d_data

            if not items or not isinstance(items, list):
                return None

            clean_t = track.clean_title or clean_title(track.title)
            clean_a = track.clean_artist or clean_artist(track.artist)

            # Score and prioritize candidates
            scored_candidates = []
            for item in items:
                if not isinstance(item, dict):
                    continue

                am_id = (
                    (item.get("appleMusicIds") and item["appleMusicIds"][0])
                    or item.get("appleMusicId")
                    or item.get("id")
                )
                if not am_id:
                    continue

                music_names = item.get("musicNames", [])
                artist_names = item.get("artistNames", [])

                if music_names:
                    title_scores = [calculate_string_similarity(clean_t, clean_title(n)) for n in music_names]
                    best_title_score = max(title_scores, default=0.0)
                elif item.get("trackName"):
                    best_title_score = calculate_string_similarity(clean_t, clean_title(item["trackName"]))
                else:
                    best_title_score = 0.0

                if artist_names:
                    artist_scores = [calculate_string_similarity(clean_a, clean_artist(a)) for a in artist_names]
                    best_artist_score = max(artist_scores, default=0.0)
                elif item.get("artistName"):
                    best_artist_score = calculate_string_similarity(clean_a, clean_artist(item["artistName"]))
                else:
                    best_artist_score = 0.0

                total_score = (best_title_score * 0.7) + (best_artist_score * 0.3)
                if best_title_score >= 0.50:
                    scored_candidates.append((total_score, am_id, item))

            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            for total_score, am_id, item in scored_candidates[:5]:
                get_url = f"{bridge_url.rstrip('/')}/v1/lyrics/get"
                get_resp = await self.request_with_retry("GET", get_url, params={"id": item.get("id")})
                if not get_resp:
                    continue

                try:
                    res_json = get_resp.json()
                except (json.JSONDecodeError, ValueError):
                    logger.debug(f"[{self.name}] Invalid JSON response for candidate, skipping")
                    continue

                if isinstance(res_json, dict) and "data" in res_json and isinstance(res_json["data"], dict):
                    res_json = res_json["data"]

                ttml = res_json.get("lyrics") or res_json.get("ttml") or res_json.get("content")
                if ttml and isinstance(ttml, str) and "<tt" in ttml.lower():
                    music_names = res_json.get("musicNames", [])
                    artist_names = res_json.get("artistNames", [])
                    sync_type = detect_sync_type(ttml, LyricsFormat.TTML)
                    return LyricsResult(
                        content=ttml,
                        format=LyricsFormat.TTML,
                        sync_type=sync_type,
                        provider_name=self.name,
                        title=music_names[0] if music_names else (res_json.get("trackName") or track.title),
                        artist=", ".join(artist_names) if artist_names else (res_json.get("artistName") or track.artist),
                        match_score=total_score,
                        metadata={"apple_music_id": am_id, "provider": "apple_music"},
                    )
        except Exception as e:
            logger.debug(f"[{self.name}] Bridge fetch error: {e}")

        return None

    async def _fetch_via_amp_api(
        self,
        title: str,
        artist: str,
        track: TrackMetadata,
        dev_token: str,
    ) -> Optional[LyricsResult]:
        storefront = self.config.extra.get("storefront", self.DEFAULT_STOREFRONT)
        clean_token = dev_token.replace("Bearer ", "").strip()
        headers = {
            "Authorization": f"Bearer {clean_token}",
            "Origin": "https://music.apple.com",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
        media_token = self.config.extra.get("media_user_token") or self.config.extra.get("music_user_token")
        if media_token:
            headers["Media-User-Token"] = media_token
            headers["Music-User-Token"] = media_token

        search_url = f"https://amp-api.music.apple.com/v1/catalog/{storefront}/search"
        params = {
            "term": f"{artist} {title}",
            "types": "songs",
            "limit": 3,
        }

        resp = await self.request_with_retry("GET", search_url, params=params, headers=headers)
        if not resp:
            return None

        try:
            data = resp.json()
            songs = data.get("results", {}).get("songs", {}).get("data", [])
            for song in songs:
                song_id = song.get("id")
                if not song_id:
                    continue

                best_title_score = calculate_string_similarity(
                    clean_title(title),
                    clean_title(song.get("attributes", {}).get("name", "")),
                )

                # 1. Try syllable-level TTML lyrics endpoint first
                syllable_url = f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}/syllable-lyrics"
                lyr_resp = await self.request_with_retry("GET", syllable_url, headers=headers)
                
                # 2. Fallback to line-level TTML lyrics endpoint if syllable lyrics not available
                if not lyr_resp or lyr_resp.status_code != 200:
                    line_url = f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}/lyrics"
                    lyr_resp = await self.request_with_retry("GET", line_url, headers=headers)

                if not lyr_resp or lyr_resp.status_code != 200:
                    continue

                try:
                    lyr_data = lyr_resp.json()
                except (json.JSONDecodeError, ValueError):
                    logger.debug(f"[{self.name}] Invalid JSON response for candidate, skipping")
                    continue

                lyr_items = lyr_data.get("data", [])
                if lyr_items:
                    ttml = lyr_items[0].get("attributes", {}).get("ttml")
                    if ttml and "<tt" in ttml.lower():
                        sync_type = detect_sync_type(ttml, LyricsFormat.TTML)
                        return LyricsResult(
                            content=ttml,
                            format=LyricsFormat.TTML,
                            sync_type=sync_type,
                            provider_name=self.name,
                            title=song.get("attributes", {}).get("name"),
                            artist=song.get("attributes", {}).get("artistName"),
                            match_score=best_title_score,
                            metadata={"song_id": song_id},
                        )
        except Exception as e:
            logger.debug(f"[{self.name}] AMP API error: {e}")

        return None
