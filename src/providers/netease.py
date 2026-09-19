"""NetEase Cloud Music 163 API Lyrics Provider."""

import json
import logging
import re
from typing import Optional
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, is_duration_matching, safe_float
from src.providers.base import BaseLyricsProvider
from src.ttml import build_ttml

logger = logging.getLogger("nla.providers.netease")

_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_WORD_RE = re.compile(r"\((\d+),(\d+)(?:,\d+)?\)([^\(]*)")


def convert_yrc_to_ttml(yrc_text: str, title: str = "", artist: str = "") -> Optional[str]:
    """Convert NetEase YRC syllable-synced lyrics into Apple-compatible TTML."""
    if not yrc_text or not isinstance(yrc_text, str):
        return None

    parsed_lines = []
    for raw_line in yrc_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("{"):
            continue

        match = _LINE_RE.match(line)
        if not match:
            continue

        line_start_ms = int(match.group(1))
        line_dur_ms = int(match.group(2))
        body = match.group(3)

        line_start_s = line_start_ms / 1000.0
        line_end_s = (line_start_ms + line_dur_ms) / 1000.0

        word_matches = _WORD_RE.findall(body)
        tokens = []
        for ws_str, wd_str, w_text in word_matches:
            ws_ms = int(ws_str)
            wd_ms = int(wd_str)
            tokens.append({
                "start_s": ws_ms / 1000.0,
                "end_s": (ws_ms + wd_ms) / 1000.0,
                "text": w_text,
            })

        if tokens:
            parsed_lines.append({
                "start_s": line_start_s,
                "end_s": line_end_s,
                "tokens": tokens,
            })
        elif body.strip():
            parsed_lines.append({
                "start_s": line_start_s,
                "end_s": line_end_s,
                "text": body.strip(),
            })

    if not parsed_lines:
        return None

    try:
        return build_ttml(parsed_lines, title=title, artist=artist)
    except Exception as e:
        logger.debug(f"[netease] Failed to build TTML from YRC: {e}")
        return None


class NetEaseProvider(BaseLyricsProvider):
    """Provider for NetEase Cloud Music (163 Music API)."""

    name = "netease"
    description = "NetEase Cloud Music (163 API - YRC word-sync TTML & synced LRC lyrics)"

    DEFAULT_API_BASE = "https://music.163.com/api"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        api_base = self.config.custom_url or self.DEFAULT_API_BASE

        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        keyword = f"{artist} {title}".strip()

        search_url = f"{api_base.rstrip('/')}/cloudsearch/pc"
        params = {
            "s": keyword,
            "type": 1,  # 1 = single song
            "offset": 0,
            "limit": 5,
        }
        headers = {
            "Referer": "https://music.163.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        resp = await self.request_with_retry("GET", search_url, params=params, headers=headers)
        if not resp:
            return None

        try:
            data = resp.json()
            songs = data.get("result", {}).get("songs", [])
            if not songs:
                return None

            scored_songs = []
            for song in songs:
                song_id = song.get("id")
                if not song_id:
                    continue

                duration_ms = song.get("dt") or song.get("duration") or 0
                song_duration = safe_float(duration_ms, 0.0) / 1000.0 if duration_ms else 0.0

                if track.duration > 0 and song_duration > 0:
                    if not is_duration_matching(track.duration, song_duration, self.config.extra.get("tolerance", 3.0)):
                        continue

                song_name = song.get("name") or ""
                artists_list = song.get("ar") or song.get("artists") or []
                song_artists = ", ".join([a.get("name", "") for a in artists_list if isinstance(a, dict) and a.get("name")])

                score = calculate_candidate_score(
                    target_title=title,
                    target_artist=artist,
                    candidate_title=song_name,
                    candidate_artist=song_artists,
                )

                if score >= 0.60:
                    scored_songs.append((score, song, song_duration, song_name, song_artists))

            if not scored_songs:
                logger.debug(f"[{self.name}] No candidates above threshold (0.60) for: {keyword}")
                return None

            scored_songs.sort(key=lambda x: x[0], reverse=True)

            for best_score, song, song_duration, song_name, song_artists in scored_songs:
                song_id = song.get("id")

                # Fetch lyric for this song ID
                lyric_url = f"{api_base.rstrip('/')}/song/lyric"
                lyric_params = {
                    "os": "pc",
                    "id": song_id,
                    "lv": -1,
                    "kv": -1,
                    "tv": -1,
                    "yv": -1,
                    "rv": -1,
                }

                lyric_resp = await self.request_with_retry("GET", lyric_url, params=lyric_params, headers=headers)
                if not lyric_resp:
                    continue

                try:
                    lyric_data = lyric_resp.json()
                except (json.JSONDecodeError, ValueError):
                    logger.debug(f"[{self.name}] Invalid JSON response for candidate, skipping")
                    continue

                # 1. First attempt syllable-by-syllable YRC -> TTML (word_sync)
                yrc_obj = lyric_data.get("yrc")
                yrc_text = yrc_obj.get("lyric", "") if isinstance(yrc_obj, dict) else ""
                if yrc_text and isinstance(yrc_text, str) and yrc_text.strip():
                    if "纯音乐，请欣赏" not in yrc_text:
                        ttml_content = convert_yrc_to_ttml(
                            yrc_text,
                            title=song_name or title,
                            artist=song_artists or artist,
                        )
                        if ttml_content and "<tt" in ttml_content.lower():
                            return LyricsResult(
                                content=ttml_content.strip(),
                                format=LyricsFormat.TTML,
                                sync_type=LyricsSyncType.WORD_SYNC,
                                provider_name=self.name,
                                duration=song_duration or None,
                                title=song_name or title,
                                artist=song_artists or artist,
                                match_score=best_score,
                                metadata={"netease_id": song_id, "source_format": "yrc"},
                            )

                # 2. Fallback to line-synced LRC
                lrc_text = lyric_data.get("lrc", {}).get("lyric", "")

                if not lrc_text or not isinstance(lrc_text, str) or not lrc_text.strip():
                    continue

                # Ignore pure instrumental indicator without text if needed
                if "纯音乐，请欣赏" in lrc_text:
                    lrc_text = "[00:00.00] ♪ Instrumental ♪\n"

                return LyricsResult(
                    content=lrc_text.strip(),
                    format=LyricsFormat.LRC,
                    sync_type=LyricsSyncType.LINE_SYNC if "[" in lrc_text else LyricsSyncType.UNSYNCED,
                    provider_name=self.name,
                    duration=song_duration or None,
                    title=song_name or title,
                    artist=song_artists or artist,
                    match_score=best_score,
                    metadata={"netease_id": song_id, "source_format": "lrc"},
                )

        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing NetEase response: {e}")

        return None
