"""Spicy Lyrics Provider (https://developers.spicylyrics.org/docs)."""

import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
import httpx

from src.models import (
    LyricsFormat,
    LyricsResult,
    LyricsSyncType,
    TrackMetadata,
)
from src.normalizer import clean_artist, clean_title, extract_primary_artist, is_duration_matching
from src.providers.base import BaseLyricsProvider
from src.ttml import build_ttml

logger = logging.getLogger("nla.providers.spicylyrics")


class SpicyLyricsProvider(BaseLyricsProvider):
    """Provider for Spicy Lyrics API (https://api.spicylyrics.org/v1/lyrics/{trackId})."""

    name = "spicylyrics"
    description = "Spicy Lyrics API (word-level syllable sync, line sync, and static lyrics)"

    DEFAULT_BASE_URL = "https://api.spicylyrics.org"
    SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
    SPOTIFY_API_BASE = "https://api.spotify.com/v1"
    SPOTIFY_EMBED_BOOTSTRAP_URL = "https://open.spotify.com/embed/track/4uLU6hMCjMI75M1A2tKUQC"
    SPOTIFY_PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v1/query"
    SPOTIFY_PATHFINDER_SEARCH_HASH = "eff59fa0a3d026b88b56fddbcf4bdfa16a186b8175a5c1a358c072e053c2e5b0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._spotify_token: Optional[str] = None
        self._spotify_token_expires_at: float = 0.0
        self._anon_spotify_token: Optional[str] = None
        self._anon_spotify_token_expires_at: float = 0.0

    @property
    def api_key(self) -> Optional[str]:
        """Retrieve the Spicy Lyrics secret or publishable key."""
        return (
            self.config.api_key
            or os.getenv("SPICY_LYRICS_SECRET_KEY")
            or os.getenv("NLA_SPICY_LYRICS_API_KEY")
        )

    @property
    def spotify_client_id(self) -> Optional[str]:
        return (
            self.config.extra.get("spotify_client_id")
            or os.getenv("SPOTIFY_CLIENT_ID")
            or os.getenv("NLA_SPOTIFY_CLIENT_ID")
        )

    @property
    def spotify_client_secret(self) -> Optional[str]:
        return (
            self.config.extra.get("spotify_client_secret")
            or os.getenv("SPOTIFY_CLIENT_SECRET")
            or os.getenv("NLA_SPOTIFY_CLIENT_SECRET")
        )

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        key = self.api_key
        if not key:
            logger.debug("[spicylyrics] No API key configured (SPICY_LYRICS_SECRET_KEY). Skipping.")
            return None

        track_id = await self._resolve_spotify_track_id(track)
        if not track_id:
            logger.debug(f"[spicylyrics] Could not determine Spotify track ID for '{track.display_name()}'.")
            return None

        if not re.fullmatch(r"^[A-Za-z0-9]{22}$", track_id):
            logger.warning(f"[spicylyrics] Invalid Spotify track ID format: '{track_id}'")
            return None

        base_url = (self.config.custom_url or self.DEFAULT_BASE_URL).rstrip("/")
        url = f"{base_url}/v1/lyrics/{track_id}"
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }

        response = await self.request_with_retry("GET", url, headers=headers)
        if not response:
            return None

        try:
            data = response.json()
            if not isinstance(data, dict):
                return None

            body = data.get("Body")
            if not isinstance(body, dict):
                return None

            return self._parse_lyrics_body(body, track, track_id)

        except Exception as e:
            logger.error(f"[spicylyrics] Error parsing response for track '{track.display_name()}': {e}", exc_info=True)
            return None

    def _parse_lyrics_body(
        self, body: Dict[str, Any], track: TrackMetadata, track_id: str
    ) -> Optional[LyricsResult]:
        lyrics_type = body.get("Type")
        source = body.get("source", "unknown")
        songwriters = body.get("SongWriters", [])
        attribution = body.get("UploadAttribution")
        end_time = body.get("EndTime")

        # EndTime in Spicy Lyrics represents the timestamp where the vocal performance ends (last syllable).
        # Songs with instrumental outros or long fadeouts have EndTime < track.duration (e.g. Waves has 40s outro).
        # If the lyrics EndTime exceeds audio duration by more than 5 seconds, reject as duration mismatch.
        if end_time is not None and track.duration > 0:
            if float(end_time) > track.duration + 5.0:
                logger.debug(
                    f"[spicylyrics] Rejecting lyrics for '{track.display_name()}': "
                    f"lyrics EndTime ({float(end_time):.1f}s) exceeds audio duration ({track.duration:.1f}s)"
                )
                return None
            duration = track.duration
        else:
            duration = track.duration if track.duration > 0 else (float(end_time) if end_time is not None else None)

        # 1. Syllable lyrics (Word-level sync -> TTML)
        if lyrics_type == "Syllable":
            content_lines = body.get("Content", [])
            if not content_lines or not isinstance(content_lines, list):
                return None

            ttml_lines: List[Dict[str, Any]] = []
            for item in content_lines:
                if not isinstance(item, dict):
                    continue
                lead = item.get("Lead", {})
                syllables = lead.get("Syllables", [])
                if not syllables:
                    continue

                tokens: List[Dict[str, Any]] = []
                for idx, s in enumerate(syllables):
                    text = s.get("Text", "")
                    if not text:
                        continue
                    is_part = s.get("IsPartOfWord", False)
                    # The last syllable of the vocal phrase cannot continue into a non-existent next syllable
                    if idx == len(syllables) - 1:
                        is_part = False
                    # If not continuing the word, append space for syllable boundary
                    if not is_part and idx < len(syllables) - 1 and not text.endswith(" "):
                        text += " "
                    tokens.append({
                        "start_s": float(s.get("StartTime", 0.0)),
                        "end_s": float(s.get("EndTime", 0.0)),
                        "text": text,
                    })

                if not tokens:
                    continue

                # Handle background vocals if present
                bg_groups = item.get("Background", [])
                background_list: List[Dict[str, Any]] = []
                if isinstance(bg_groups, list):
                    for bg in bg_groups:
                        if not isinstance(bg, dict):
                            continue
                        bg_sylls = bg.get("Syllables", [])
                        if not bg_sylls:
                            continue
                        bg_tokens: List[Dict[str, Any]] = []
                        for idx, bs in enumerate(bg_sylls):
                            bw = bs.get("Text", "")
                            if not bw:
                                continue
                            is_part = bs.get("IsPartOfWord", False)
                            if idx == len(bg_sylls) - 1:
                                is_part = False
                            if not is_part and idx < len(bg_sylls) - 1 and not bw.endswith(" "):
                                bw += " "
                            bg_tokens.append({
                                "start_s": float(bs.get("StartTime", 0.0)),
                                "end_s": float(bs.get("EndTime", 0.0)),
                                "text": bw,
                            })

                        if not bg_tokens:
                            continue

                        # Ensure parentheses around background vocal group
                        first_text = bg_tokens[0]["text"]
                        if not first_text.startswith("(") and not first_text.startswith("（"):
                            bg_tokens[0]["text"] = "(" + first_text

                        last_text = bg_tokens[-1]["text"]
                        last_text_stripped = last_text.rstrip(" ")
                        if not last_text_stripped.endswith(")") and not last_text_stripped.endswith("）"):
                            bg_tokens[-1]["text"] = last_text_stripped + ")"

                        b_start = float(bg.get("StartTime", bg_tokens[0]["start_s"]))
                        b_end = float(bg.get("EndTime", bg_tokens[-1]["end_s"]))
                        if b_end < b_start:
                            b_end = b_start

                        background_list.append({
                            "start_s": b_start,
                            "end_s": b_end,
                            "tokens": bg_tokens,
                        })

                l_start = float(lead.get("StartTime", tokens[0]["start_s"]))
                l_end = float(lead.get("EndTime", tokens[-1]["end_s"]))
                if l_end < l_start:
                    l_end = l_start

                is_opposite = bool(item.get("OppositeAligned", False))
                agent = "v2" if is_opposite else item.get("Agent", "v1")

                ttml_lines.append({
                    "start_s": l_start,
                    "end_s": l_end,
                    "tokens": tokens,
                    "background": background_list,
                    "agent": agent,
                })

            if not ttml_lines:
                return None

            ttml_content = build_ttml(ttml_lines, title=track.title, artist=track.artist)
            return LyricsResult(
                content=ttml_content,
                format=LyricsFormat.TTML,
                sync_type=LyricsSyncType.WORD_SYNC,
                provider_name=self.name,
                duration=float(duration) if duration is not None else None,
                title=track.title,
                artist=track.artist,
                metadata={
                    "source": source,
                    "spotify_id": track_id,
                    "attribution": attribution,
                    "songwriters": songwriters,
                },
            )

        # 2. Line lyrics (Line-level sync -> LRC)
        elif lyrics_type == "Line":
            content_lines = body.get("Content", [])
            if not content_lines or not isinstance(content_lines, list):
                return None

            lrc_lines: List[str] = []
            for item in content_lines:
                if not isinstance(item, dict):
                    continue
                text = item.get("Text", "").strip()
                start_s = float(item.get("StartTime", 0.0))
                m = int(start_s // 60)
                s = start_s % 60
                lrc_lines.append(f"[{m:02d}:{s:05.2f}]{text}")

            if not lrc_lines:
                return None

            return LyricsResult(
                content="\n".join(lrc_lines),
                format=LyricsFormat.LRC,
                sync_type=LyricsSyncType.LINE_SYNC,
                provider_name=self.name,
                duration=float(duration) if duration is not None else None,
                title=track.title,
                artist=track.artist,
                metadata={
                    "source": source,
                    "spotify_id": track_id,
                    "attribution": attribution,
                    "songwriters": songwriters,
                },
            )

        # 3. Static lyrics (Untimed -> TXT)
        elif lyrics_type == "Static":
            lines_list = body.get("Lines", [])
            if not lines_list or not isinstance(lines_list, list):
                return None

            plain_lines: List[str] = []
            for item in lines_list:
                if isinstance(item, dict):
                    text = item.get("Text", "").strip()
                    if text:
                        plain_lines.append(text)
                elif isinstance(item, str) and item.strip():
                    plain_lines.append(item.strip())

            if not plain_lines:
                return None

            return LyricsResult(
                content="\n".join(plain_lines),
                format=LyricsFormat.TXT,
                sync_type=LyricsSyncType.UNSYNCED,
                provider_name=self.name,
                title=track.title,
                artist=track.artist,
                metadata={
                    "source": source,
                    "spotify_id": track_id,
                    "attribution": attribution,
                    "songwriters": songwriters,
                },
            )

        return None

    async def _resolve_spotify_track_id(self, track: TrackMetadata) -> Optional[str]:
        """Resolve Spotify track ID from audio tags, Spotify Web API, anonymous Pathfinder, or MusicBrainz."""
        # 1. Direct tag in TrackMetadata
        if track.spotify_id and re.fullmatch(r"^[A-Za-z0-9]{22}$", track.spotify_id):
            return track.spotify_id

        # 2. Spotify API search if credentials are provided
        if self.spotify_client_id and self.spotify_client_secret:
            track_id = await self._search_spotify_api(track)
            if track_id:
                return track_id

        # 3. Anonymous Spotify Pathfinder GraphQL search (zero-config, supports ISRC and title+artist)
        anon_id = await self._search_spotify_anonymous(track)
        if anon_id:
            return anon_id

        # 4. MusicBrainz ISRC lookup (free open database fallback)
        if track.isrc:
            mb_id = await self._search_musicbrainz_isrc(track.isrc)
            if mb_id:
                return mb_id

        return None

    async def _get_spotify_access_token(self) -> Optional[str]:
        """Obtain Client Credentials token from accounts.spotify.com."""
        if self._spotify_token and time.time() < self._spotify_token_expires_at - 60:
            return self._spotify_token

        client_id = self.spotify_client_id
        client_secret = self.spotify_client_secret
        if not client_id or not client_secret:
            return None

        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}

        client = await self.get_client()
        try:
            resp = await client.post(self.SPOTIFY_TOKEN_URL, data=data, headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                self._spotify_token = payload.get("access_token")
                expires_in = payload.get("expires_in", 3600)
                self._spotify_token_expires_at = time.time() + expires_in
                return self._spotify_token
            else:
                logger.warning(f"[spicylyrics] Spotify token request failed ({resp.status_code}): {resp.text}")
        except Exception as e:
            logger.error(f"[spicylyrics] Error getting Spotify access token: {e}")

        return None

    async def _search_spotify_api(self, track: TrackMetadata) -> Optional[str]:
        """Search Spotify API for track by ISRC or title + artist."""
        token = await self._get_spotify_access_token()
        if not token:
            return None

        client = await self.get_client()
        headers = {"Authorization": f"Bearer {token}"}

        # Query by ISRC first if available
        if track.isrc:
            params = {"q": f"isrc:{track.isrc}", "type": "track", "limit": 1}
            try:
                resp = await client.get(f"{self.SPOTIFY_API_BASE}/search", params=params, headers=headers)
                if resp.status_code == 200:
                    items = resp.json().get("tracks", {}).get("items", [])
                    if items and items[0].get("id"):
                        return items[0]["id"]
            except Exception as e:
                logger.debug(f"[spicylyrics] Spotify ISRC search error: {e}")

        # Query by title and artist
        clean_t = track.clean_title or clean_title(track.title)
        clean_a = track.clean_artist or clean_artist(track.artist)
        query = f"track:{clean_t} artist:{clean_a}"

        params = {"q": query, "type": "track", "limit": 5}
        try:
            resp = await client.get(f"{self.SPOTIFY_API_BASE}/search", params=params, headers=headers)
            if resp.status_code == 200:
                items = resp.json().get("tracks", {}).get("items", [])
                for item in items:
                    item_id = item.get("id")
                    if not item_id:
                        continue
                    duration_ms = item.get("duration_ms", 0)
                    if track.duration > 0 and duration_ms > 0:
                        if not is_duration_matching(track.duration, duration_ms / 1000.0, tolerance_seconds=3.5):
                            continue
                    return item_id
        except Exception as e:
            logger.debug(f"[spicylyrics] Spotify track/artist search error: {e}")

        return None

    async def _search_musicbrainz_isrc(self, isrc: str) -> Optional[str]:
        """Search MusicBrainz API by ISRC for linked Spotify track URLs."""
        url = f"https://musicbrainz.org/ws/2/isrc/{isrc}"
        params = {"fmt": "json", "inc": "url-rels"}
        headers = {
            "User-Agent": "NavidromeLyricsAggregator/1.0.0 (https://github.com/olafix52/navidrome-lyrics-agregator)",
            "Accept": "application/json",
        }
        client = await self.get_client()
        try:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                recordings = data.get("recordings", [])
                for rec in recordings:
                    for rel in rec.get("relations", []):
                        url_res = rel.get("url", {}).get("resource", "")
                        m = re.search(r"spotify\.com/track/([A-Za-z0-9]{22})", url_res)
                        if m:
                            return m.group(1)
        except Exception as e:
            logger.debug(f"[spicylyrics] MusicBrainz ISRC lookup error: {e}")

        return None

    async def _get_anonymous_spotify_token(self) -> Optional[str]:
        """Obtain anonymous access token from public Spotify embed page."""
        if self._anon_spotify_token and time.time() < self._anon_spotify_token_expires_at - 60:
            return self._anon_spotify_token

        client = await self.get_client()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            resp = await client.get(self.SPOTIFY_EMBED_BOOTSTRAP_URL, headers=headers)
            if resp.status_code == 200:
                m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.DOTALL)
                if m:
                    data = json.loads(m.group(1))
                    session = data.get("props", {}).get("pageProps", {}).get("state", {}).get("settings", {}).get("session", {})
                    token = session.get("accessToken")
                    expires_ms = session.get("accessTokenExpirationTimestampMs")
                    if token:
                        self._anon_spotify_token = token
                        if isinstance(expires_ms, (int, float)):
                            self._anon_spotify_token_expires_at = expires_ms / 1000.0
                        else:
                            self._anon_spotify_token_expires_at = time.time() + 1200.0
                        return self._anon_spotify_token
        except Exception as e:
            logger.debug(f"[spicylyrics] Error obtaining anonymous Spotify token: {e}")

        return None

    async def _search_spotify_anonymous(self, track: TrackMetadata) -> Optional[str]:
        """Search Spotify via Pathfinder GraphQL using anonymous embed token."""
        token = await self._get_anonymous_spotify_token()
        if not token:
            return None

        client = await self.get_client()
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }

        queries: List[str] = []
        if track.isrc:
            queries.append(f"isrc:{track.isrc}")

        clean_t = track.clean_title or clean_title(track.title)
        primary_a = extract_primary_artist(track.artist)
        clean_a = track.clean_artist or clean_artist(track.artist)

        if clean_t:
            if primary_a:
                queries.append(f"{clean_t} {primary_a}")
            if clean_a and clean_a != primary_a:
                queries.append(f"{clean_t} {clean_a}")

        for query in queries:
            variables = {
                "searchTerm": query,
                "offset": 0,
                "limit": 5,
                "numberOfTopResults": 5,
                "includeAudiobooks": False,
                "includePreReleases": False,
                "includeAlbumPreReleases": False,
                "includeAuthors": False,
                "includeEpisodeContentRatingsV2": False,
            }
            params = {
                "operationName": "searchDesktop",
                "variables": json.dumps(variables, separators=(",", ":")),
                "extensions": json.dumps(
                    {"persistedQuery": {"version": 1, "sha256Hash": self.SPOTIFY_PATHFINDER_SEARCH_HASH}},
                    separators=(",", ":"),
                ),
            }
            try:
                resp = await client.get(self.SPOTIFY_PATHFINDER_URL, params=params, headers=headers)
                if resp.status_code in (401, 403):
                    self._anon_spotify_token = None
                    return None
                if resp.status_code != 200:
                    continue

                res_json = resp.json()
                items = res_json.get("data", {}).get("searchV2", {}).get("tracksV2", {}).get("items", [])
                for item in items:
                    td = item.get("item", {}).get("data", {})
                    uri = td.get("uri", "")
                    m = re.search(r"spotify:track:([A-Za-z0-9]{22})", uri)
                    if not m:
                        continue
                    found_id = m.group(1)

                    if query.startswith("isrc:"):
                        logger.debug(f"[spicylyrics] Resolved Spotify ID '{found_id}' via anonymous ISRC search ({query})")
                        return found_id

                    dur_ms = td.get("duration", {}).get("totalMilliseconds", 0)
                    if track.duration > 0 and dur_ms > 0:
                        if not is_duration_matching(track.duration, dur_ms / 1000.0, tolerance_seconds=4.0):
                            continue

                    logger.debug(f"[spicylyrics] Resolved Spotify ID '{found_id}' via anonymous search ('{query}')")
                    return found_id
            except Exception as e:
                logger.debug(f"[spicylyrics] Spotify anonymous search error for query '{query}': {e}")

        return None
