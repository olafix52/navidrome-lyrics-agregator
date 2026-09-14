"""Kugou Music API Lyrics Provider."""

import base64
import logging
import re
from typing import Optional
import urllib.parse
import zlib

from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, is_duration_matching, safe_float
from src.providers.base import BaseLyricsProvider
from src.ttml import build_ttml

logger = logging.getLogger("nla.providers.kugou")

# Kugou KRC XOR Key (16 bytes)
KRC_KEY = bytes([
    0x40, 0x47, 0x61, 0x77,  # @Gaw
    0x5e, 0x32, 0x74, 0x47,  # ^2tG
    0x51, 0x36, 0x31, 0x2d,  # Q61-
    0xce, 0xd2, 0x6e, 0x69,  # Íni
])

_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_WORD_RE = re.compile(r"<(\d+),(\d+)(?:,\d+)?>([^<]*)")


def krc_decrypt(b64_content: str) -> str:
    """Decrypt Base64-encoded Kugou KRC payload using 4-byte skip, XOR key, and zlib inflate."""
    raw = base64.b64decode(b64_content.strip())
    # Skip first 4 bytes (magic header)
    encrypted = raw[4:]
    decrypted = bytes(b ^ KRC_KEY[i % len(KRC_KEY)] for i, b in enumerate(encrypted))
    return zlib.decompress(decrypted).decode("utf-8", errors="ignore")


def convert_krc_to_ttml(krc_text: str, title: str = "", artist: str = "") -> Optional[str]:
    """Convert decrypted Kugou KRC lyrics into Apple-compatible TTML (word_sync)."""
    if not krc_text or not isinstance(krc_text, str):
        return None

    offset_ms = 0
    parsed_lines = []

    for raw in krc_text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # Parse offset tag if present
        off_m = re.match(r"^\[offset:(-?\d+)\]", line, re.IGNORECASE)
        if off_m:
            try:
                offset_ms = int(off_m.group(1))
            except Exception:
                pass
            continue

        lm = _LINE_RE.match(line)
        if not lm:
            continue

        line_start_ms = int(lm.group(1)) + offset_ms
        line_dur_ms = int(lm.group(2))
        if line_start_ms < 0:
            line_start_ms = 0
        body = lm.group(3)

        tokens = []
        for tm in _WORD_RE.finditer(body):
            rel_offset_ms = int(tm.group(1))
            word_dur_ms = int(tm.group(2))
            w_text = tm.group(3)

            w_start_ms = line_start_ms + rel_offset_ms
            if w_start_ms < 0:
                w_start_ms = 0

            tokens.append({
                "start_s": w_start_ms / 1000.0,
                "end_s": (w_start_ms + word_dur_ms) / 1000.0,
                "text": w_text,
            })

        line_start_s = line_start_ms / 1000.0
        line_end_s = (line_start_ms + line_dur_ms) / 1000.0

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
        logger.debug(f"[kugou] Failed to build TTML from KRC: {e}")
        return None


class KugouProvider(BaseLyricsProvider):
    """Provider for Kugou Music KRC word-sync TTML & synced LRC lyrics."""

    name = "kugou"
    description = "Kugou Music Database (KRC word-sync TTML & synced LRC lyrics)"

    SEARCH_API = "https://mobileservice.kugou.com/api/v3/search/song"
    KRCS_API = "https://krcs.kugou.com/search"
    DOWNLOAD_API = "https://lyrics.kugou.com/download"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        keyword = f"{artist} {title}".strip() if artist else title.strip()

        # 1. Search song (encode spaces as %20 rather than '+' for Kugou API compatibility)
        encoded_keyword = urllib.parse.quote(keyword)
        search_url = f"{self.SEARCH_API}?format=json&keyword={encoded_keyword}&page=1&pagesize=5"

        resp = await self.request_with_retry("GET", search_url)
        if not resp:
            return None

        try:
            data = resp.json()
            songs = data.get("data", {}).get("info", [])
            if not songs:
                return None

            scored_songs = []
            for song in songs:
                file_hash = song.get("hash")
                if not file_hash:
                    continue

                song_duration = safe_float(song.get("duration"), 0.0) or 0.0
                if track.duration > 0 and song_duration > 0:
                    if not is_duration_matching(track.duration, song_duration, 3.0):
                        continue

                song_title = song.get("songname") or ""
                song_artist = song.get("singername") or ""

                score = calculate_candidate_score(
                    target_title=title,
                    target_artist=artist,
                    candidate_title=song_title,
                    candidate_artist=song_artist,
                )

                if score >= 0.60:
                    scored_songs.append((score, song))

            if not scored_songs:
                logger.debug(f"[{self.name}] No candidate songs above threshold (0.60) for: {keyword}")
                return None

            scored_songs.sort(key=lambda x: x[0], reverse=True)

            for score, song in scored_songs:
                file_hash = song.get("hash")
                song_duration = safe_float(song.get("duration"), 0.0) or 0.0
                song_title = song.get("songname")
                song_artist = song.get("singername")

                # 2. Get lyric candidate
                duration_ms = int(song_duration * 1000) if song_duration > 0 else int(track.duration * 1000)
                krcs_url = (
                    f"{self.KRCS_API}?ver=1&man=yes&client=mobi"
                    f"&keyword={encoded_keyword}&duration={duration_ms}&hash={file_hash}"
                )

                krcs_resp = await self.request_with_retry("GET", krcs_url)
                if not krcs_resp:
                    continue

                krcs_data = krcs_resp.json()
                candidates = krcs_data.get("candidates", [])
                if not candidates:
                    continue

                best_candidate = candidates[0]
                candidate_id = best_candidate.get("id")
                access_key = best_candidate.get("accesskey")

                if not candidate_id or not access_key:
                    continue

                candidate_title = song_title or title
                candidate_artist = song_artist or artist

                # 3. Download lyric - First attempt KRC (word_sync)
                dl_url_krc = (
                    f"{self.DOWNLOAD_API}?ver=1&client=pc"
                    f"&id={candidate_id}&accesskey={access_key}&fmt=krc&charset=utf8"
                )

                dl_resp = await self.request_with_retry("GET", dl_url_krc)
                if dl_resp:
                    try:
                        dl_data = dl_resp.json()
                        b64_content = dl_data.get("content", "")
                        if b64_content:
                            raw_bytes = base64.b64decode(b64_content.strip())
                            # If already plain LRC text (legacy mock / response format)
                            if raw_bytes.strip().startswith(b"["):
                                lrc_text = raw_bytes.decode("utf-8", errors="replace").strip()
                                if lrc_text:
                                    return LyricsResult(
                                        content=lrc_text,
                                        format=LyricsFormat.LRC,
                                        sync_type=LyricsSyncType.LINE_SYNC if "[" in lrc_text else LyricsSyncType.UNSYNCED,
                                        provider_name=self.name,
                                        duration=song_duration or None,
                                        title=candidate_title,
                                        artist=candidate_artist,
                                        metadata={"kugou_id": candidate_id, "hash": file_hash, "source_format": "lrc"},
                                    )

                            # Decrypt KRC and convert to TTML
                            krc_text = krc_decrypt(b64_content)
                            ttml_content = convert_krc_to_ttml(
                                krc_text,
                                title=candidate_title,
                                artist=candidate_artist,
                            )
                            if ttml_content and "<tt" in ttml_content.lower():
                                return LyricsResult(
                                    content=ttml_content.strip(),
                                    format=LyricsFormat.TTML,
                                    sync_type=LyricsSyncType.WORD_SYNC,
                                    provider_name=self.name,
                                    duration=song_duration or None,
                                    title=candidate_title,
                                    artist=candidate_artist,
                                    metadata={"kugou_id": candidate_id, "hash": file_hash, "source_format": "krc"},
                                )
                    except Exception as e:
                        logger.debug(f"[{self.name}] Error decrypting/converting KRC: {e}")

                # 4. Fallback to LRC (line_sync)
                dl_url_lrc = (
                    f"{self.DOWNLOAD_API}?ver=1&client=pc"
                    f"&id={candidate_id}&accesskey={access_key}&fmt=lrc&charset=utf8"
                )
                dl_lrc_resp = await self.request_with_retry("GET", dl_url_lrc)
                if dl_lrc_resp:
                    try:
                        dl_lrc_data = dl_lrc_resp.json()
                        b64_lrc = dl_lrc_data.get("content", "")
                        if b64_lrc:
                            lrc_text = base64.b64decode(b64_lrc).decode("utf-8", errors="replace").strip()
                            if lrc_text:
                                return LyricsResult(
                                    content=lrc_text,
                                    format=LyricsFormat.LRC,
                                    sync_type=LyricsSyncType.LINE_SYNC if "[" in lrc_text else LyricsSyncType.UNSYNCED,
                                    provider_name=self.name,
                                    duration=song_duration or None,
                                    title=candidate_title,
                                    artist=candidate_artist,
                                    metadata={"kugou_id": candidate_id, "hash": file_hash, "source_format": "lrc"},
                                )
                    except Exception as e:
                        logger.debug(f"[{self.name}] Base64 decode error for LRC: {e}")

        except Exception as e:
            logger.debug(f"[{self.name}] Kugou search error: {e}")

        return None
