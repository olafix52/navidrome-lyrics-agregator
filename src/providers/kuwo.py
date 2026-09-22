"""Kuwo Music Lyrics Provider."""

import ast
import base64
import html
import json
import logging
import zlib
from typing import Any, Dict, List, Optional
from src.models import LyricsFormat, LyricsResult, TrackMetadata, detect_sync_type
from src.normalizer import calculate_candidate_score, clean_artist, clean_title
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.kuwo")


def build_kuwo_params(music_rid: str) -> str:
    """Encrypt and base64-encode request parameters for newlyric.kuwo.cn."""
    buf_key = b"yeelion"
    params = f"user=12345,web,web,web&requester=localhost&req=1&rid={music_rid}"
    buf_str = params.encode("utf-8")
    output = bytearray(len(buf_str))
    for i in range(len(buf_str)):
        output[i] = buf_key[i % len(buf_key)] ^ buf_str[i]
    return base64.b64encode(output).decode("utf-8")


def decode_kuwo_lrc(content: bytes) -> str:
    """Extract and decompress zlib-compressed LRC data from Kuwo response."""
    if not content.startswith(b"tp=content"):
        return ""
    sep = content.find(b"\r\n\r\n")
    if sep == -1:
        return ""

    compressed = content[sep + 4:]
    try:
        decompressed = zlib.decompress(compressed)
        try:
            return decompressed.decode("utf-8")
        except UnicodeDecodeError:
            return decompressed.decode("gb18030", errors="ignore")
    except Exception as e:
        logger.debug(f"[kuwo] Failed to decompress lyrics: {e}")
        return ""


def clean_kuwo_lrc(lrc_text: str) -> str:
    """Filter out Kuwo-specific internal tags and normalize empty lines."""
    lines = []
    for line in lrc_text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith("[kuwo:") or trimmed.startswith("[ml:"):
            continue
        lines.append(trimmed)
    return "\n".join(lines)


class KuwoProvider(BaseLyricsProvider):
    """Provider for Kuwo Music synchronized LRC lyrics."""

    name = "kuwo"
    description = "Kuwo Music (synced LRC lyrics)"
    supports_word_sync = False

    DEFAULT_SEARCH_URL = "https://search.kuwo.cn/r.s"
    DEFAULT_LYRIC_URL = "https://newlyric.kuwo.cn/newlyric.lrc"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.kuwo.cn/",
        }

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        query = f"{artist} {title}".strip()

        search_url = self.config.custom_url or self.DEFAULT_SEARCH_URL
        headers = self._get_headers()

        search_params = {
            "all": query,
            "ft": "music",
            "client": "kt",
            "cluster": "0",
            "pn": "0",
            "rn": "5",
            "rformat": "json",
            "encoding": "utf8",
        }

        resp = await self.request_with_retry("GET", search_url, params=search_params, headers=headers)
        if not resp:
            return None

        # Kuwo search API returns Python-style dict literal with single quotes.
        # Using ast.literal_eval handles apostrophes in values correctly,
        # unlike the naive replace("'", '"') which corrupts names like "Don't Stop".
        try:
            try:
                data = ast.literal_eval(resp.text)
            except (ValueError, SyntaxError):
                # Fallback: attempt standard JSON parse in case format changed
                data = json.loads(resp.text)
            song_list = data.get("abslist", [])
            if not song_list:
                logger.debug(f"[{self.name}] No songs found for query: {query}")
                return None

            scored_candidates: List[tuple[float, Dict[str, Any]]] = []

            for song in song_list:
                song_name = html.unescape(song.get("SONGNAME", ""))
                song_artist = html.unescape(song.get("ARTIST", ""))

                score = calculate_candidate_score(
                    target_title=title,
                    target_artist=artist,
                    candidate_title=song_name,
                    candidate_artist=song_artist,
                )

                if score >= 0.6:
                    scored_candidates.append((score, song))

            if not scored_candidates:
                logger.debug(f"[{self.name}] No candidates above threshold (0.60) for: {query}")
                return None

            scored_candidates.sort(key=lambda x: x[0], reverse=True)

            for score, song in scored_candidates[:5]:
                music_rid = song.get("MUSICRID")
                if not music_rid:
                    target_id = song.get("DC_TARGETID")
                    if target_id:
                        music_rid = f"MUSIC_{target_id}"

                if not music_rid:
                    continue

                if not str(music_rid).startswith("MUSIC_"):
                    music_rid = f"MUSIC_{music_rid}"

                # Fetch lyrics
                lyric_param_str = build_kuwo_params(str(music_rid))
                lyric_url = f"{self.DEFAULT_LYRIC_URL}?{lyric_param_str}"

                lyric_resp = await self.request_with_retry("GET", lyric_url, headers=headers)
                if not lyric_resp or lyric_resp.status_code != 200:
                    continue

                raw_lrc = decode_kuwo_lrc(lyric_resp.content)
                if not raw_lrc:
                    continue

                cleaned_lrc = clean_kuwo_lrc(raw_lrc)
                if not cleaned_lrc:
                    continue

                sync_type = detect_sync_type(cleaned_lrc, LyricsFormat.LRC)
                candidate_title = html.unescape(song.get("SONGNAME", "")) or title
                candidate_artist = html.unescape(song.get("ARTIST", "")) or artist

                return LyricsResult(
                    content=cleaned_lrc,
                    format=LyricsFormat.LRC,
                    sync_type=sync_type,
                    provider_name=self.name,
                    title=candidate_title,
                    artist=candidate_artist,
                    match_score=score,
                    metadata={"music_rid": music_rid, "match_score": score},
                )

        except Exception as e:
            logger.debug(f"[{self.name}] Error searching or decoding Kuwo lyrics: {e}")
            return None
