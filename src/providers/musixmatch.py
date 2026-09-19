"""Musixmatch Desktop API Lyrics Provider (supporting RichSync TTML and Line-synced LRC)."""

import json
import logging
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Any, Dict, List, Optional
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, safe_float
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.musixmatch")

POISONED_PATTERNS = (
    "wob gopini den",
    "tefe woxica fero",
    "gogoh vudob wiya",
    "keric sohu peduf",
)


def is_poisoned_lyrics(text: str) -> bool:
    """Detect Musixmatch anti-scraping dummy honeypot lyrics (e.g. 'Wob gopini den...')."""
    if not text:
        return False
    lower = text.lower()
    return any(p in lower for p in POISONED_PATTERNS)


def format_ttml_timestamp(seconds: float) -> str:
    """Format seconds into standard TTML timestamp mm:ss.xxx."""
    if seconds < 0:
        seconds = 0.0
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


def convert_richsync_to_ttml(richsync_data: Any, title: str, artist: str) -> Optional[str]:
    """Convert Musixmatch RichSync JSON array to Apple-compatible TTML with syllable spans."""
    if isinstance(richsync_data, str):
        try:
            richsync_data = json.loads(richsync_data)
        except Exception:
            return None

    if not isinstance(richsync_data, list) or not richsync_data:
        return None

    tt = ET.Element("tt", {
        "xmlns": "http://www.w3.org/ns/ttml",
        "xmlns:itunes": "http://music.apple.com/lyric-ttml-internal",
        "xmlns:ttm": "http://www.w3.org/ns/ttml#metadata",
        "itunes:timing": "Word",
        "xml:lang": "en",
    })
    head = ET.SubElement(tt, "head")
    meta = ET.SubElement(head, "metadata")
    if title:
        ET.SubElement(meta, "ttm:title").text = title
    if artist:
        ET.SubElement(meta, "ttm:agent", {"type": "person", "xml:id": "v1"}).text = artist
    else:
        ET.SubElement(meta, "ttm:agent", {"type": "person", "xml:id": "v1"})

    body = ET.SubElement(tt, "body")
    div = ET.SubElement(body, "div")

    for idx, line in enumerate(richsync_data):
        if not isinstance(line, dict):
            continue
        ts = float(line.get("ts", 0.0))
        te = float(line.get("te", ts))
        if te < ts:
            te = ts

        p = ET.SubElement(div, "p", {
            "begin": format_ttml_timestamp(ts),
            "end": format_ttml_timestamp(te),
            "itunes:key": f"L{idx + 1}",
            "ttm:agent": "v1",
        })

        words = line.get("l", [])
        if not words:
            line_text = line.get("x", "")
            if line_text:
                span = ET.SubElement(p, "span", {
                    "begin": format_ttml_timestamp(ts),
                    "end": format_ttml_timestamp(te),
                })
                span.text = line_text
            continue

        for i, w in enumerate(words):
            if not isinstance(w, dict):
                continue
            text = w.get("c", "")
            offset = float(w.get("o", 0.0))
            word_start = ts + offset

            if i + 1 < len(words) and isinstance(words[i + 1], dict):
                next_offset = float(words[i + 1].get("o", offset))
                word_end = ts + next_offset
            else:
                word_end = te

            if word_end < word_start:
                word_end = word_start

            span = ET.SubElement(p, "span", {
                "begin": format_ttml_timestamp(word_start),
                "end": format_ttml_timestamp(word_end),
            })
            span.text = text

    xml_bytes = ET.tostring(tt, encoding="utf-8")
    return minidom.parseString(xml_bytes).toprettyxml(indent="  ")


class MusixmatchProvider(BaseLyricsProvider):
    """Provider for Musixmatch synchronized lyrics via Desktop API (Word-Sync & Line-Sync)."""

    name = "musixmatch"
    description = "Musixmatch Database (RichSync TTML word-sync & LRC line-sync)"

    DEFAULT_API_BASE = "https://apic-desktop.musixmatch.com/ws/1.1"
    APP_ID = "web-desktop-app-v1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_token: Optional[str] = self.config.api_key or self.config.extra.get("user_token")
        self._token_expiry: float = 0.0

    async def _get_user_token(self) -> Optional[str]:
        """Obtain or refresh an anonymous user token from Musixmatch."""
        if self._user_token and time.time() < self._token_expiry:
            return self._user_token

        api_base = self.config.custom_url or self.DEFAULT_API_BASE
        token_url = f"{api_base.rstrip('/')}/token.get"
        params = {
            "app_id": self.APP_ID,
            "format": "json",
        }

        resp = await self.request_with_retry("GET", token_url, params=params)
        if resp:
            try:
                data = resp.json()
                body = data.get("message", {}).get("body", {})
                token = body.get("user_token")
                if token and set(token) != {"0"}:
                    self._user_token = token
                    self._token_expiry = time.time() + 86400 * 30  # 30 days
                    return token
                else:
                    logger.debug(f"[{self.name}] Dummy zero-token received from Musixmatch (cloud IP), using fallback token.")
            except Exception as e:
                logger.debug(f"[{self.name}] Failed to parse token: {e}")

        # Fallback default static client token
        if self._user_token and set(self._user_token) != {"0"}:
            return self._user_token
        return "21051986b9886e2d7bd5d8295b15d605c14e13e33326a3a0e50e1b"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        token = await self._get_user_token()
        if not token:
            logger.warning(f"[{self.name}] No user token available")
            return None

        api_base = self.config.custom_url or self.DEFAULT_API_BASE
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        # Musixmatch modern desktop API endpoint (bundles track info, richsync, and subtitles)
        macro_endpoint = f"{api_base.rstrip('/')}/macro.subtitles.get"
        params = {
            "format": "json",
            "namespace": "lyrics_richsynced",
            "optional_calls": "track.richsync",
            "subtitle_format": "lrc",
            "app_id": self.APP_ID,
            "usertoken": token,
            "q_artist": artist,
            "q_track": title,
        }
        if track.duration > 0:
            params["f_subtitle_length"] = str(int(round(track.duration)))
            params["f_subtitle_length_max_deviation"] = "3"

        response = await self.request_with_retry("GET", macro_endpoint, params=params)
        if not response:
            return None

        try:
            data = response.json()
            message = data.get("message", {})
            header = message.get("header", {})

            status_code = header.get("status_code", 0)
            if status_code in (401, 402):
                logger.debug(f"[{self.name}] Unauthorized (401/402), invalidating token")
                self._user_token = None
                self._token_expiry = 0
                return None

            if status_code != 200:
                return None

            macro_calls = message.get("body", {}).get("macro_calls", {})

            # Extract track info if available
            track_call = macro_calls.get("matcher.track.get", {})
            track_data = track_call.get("message", {}).get("body", {}).get("track", {})
            cand_title = track_data.get("track_name")
            cand_artist = track_data.get("artist_name")
            cand_duration = safe_float(track_data.get("track_length"))
            track_id = track_data.get("track_id")

            # Verify that the matched track actually corresponds to the requested track
            if cand_title and cand_artist:
                score = calculate_candidate_score(title, artist, cand_title, cand_artist)
                if score < self.config.extra.get("min_similarity", 0.6):
                    logger.debug(
                        f"[{self.name}] Rejecting mismatched candidate: requested '{artist} - {title}', "
                        f"got '{cand_artist} - {cand_title}' (score: {score:.2f})"
                    )
                    return None
            else:
                cand_title = title
                cand_artist = artist
                score = 1.0

            # 1. Attempt syllable-level RichSync TTML first
            richsync_call = macro_calls.get("track.richsync.get", {})
            if richsync_call.get("message", {}).get("header", {}).get("status_code") == 200:
                rs_body = (
                    richsync_call.get("message", {})
                    .get("body", {})
                    .get("richsync", {})
                    .get("richsync_body")
                )
                if rs_body and not is_poisoned_lyrics(str(rs_body)):
                    ttml_content = convert_richsync_to_ttml(
                        rs_body,
                        title=cand_title,
                        artist=cand_artist,
                    )
                    if ttml_content and not is_poisoned_lyrics(ttml_content):
                        return LyricsResult(
                            content=ttml_content,
                            format=LyricsFormat.TTML,
                            sync_type=LyricsSyncType.WORD_SYNC,
                            provider_name=self.name,
                            duration=cand_duration,
                            title=cand_title,
                            artist=cand_artist,
                            match_score=score,
                            metadata={"musixmatch_track_id": track_id, "type": "richsync"},
                        )

            # 2. Fallback to line-synced LRC from track.subtitles.get
            subtitles_call = macro_calls.get("track.subtitles.get", {})
            if subtitles_call.get("message", {}).get("header", {}).get("status_code") == 200:
                sub_list = (
                    subtitles_call.get("message", {})
                    .get("body", {})
                    .get("subtitle_list", [])
                )
                if sub_list and isinstance(sub_list, list):
                    sub = sub_list[0].get("subtitle", {})
                    sub_body = sub.get("subtitle_body", "")
                    if sub_body and isinstance(sub_body, str) and sub_body.strip():
                        if is_poisoned_lyrics(sub_body):
                            logger.debug(f"[{self.name}] Honeypot/poisoned lyrics detected in subtitle body, rejecting.")
                            return None
                        return LyricsResult(
                            content=sub_body.strip(),
                            format=LyricsFormat.LRC,
                            sync_type=LyricsSyncType.LINE_SYNC,
                            provider_name=self.name,
                            duration=safe_float(sub.get("subtitle_length")) or cand_duration,
                            title=cand_title,
                            artist=cand_artist,
                            match_score=score,
                            metadata={
                                "musixmatch_id": sub.get("subtitle_id"),
                                "musixmatch_track_id": track_id,
                            },
                        )

            # 3. Fallback to plain lyrics from track.lyrics.get if available
            lyrics_call = macro_calls.get("track.lyrics.get", {})
            if lyrics_call.get("message", {}).get("header", {}).get("status_code") == 200:
                lyr_body = (
                    lyrics_call.get("message", {})
                    .get("body", {})
                    .get("lyrics", {})
                    .get("lyrics_body", "")
                )
                if lyr_body and isinstance(lyr_body, str) and lyr_body.strip():
                    if is_poisoned_lyrics(lyr_body):
                        logger.debug(f"[{self.name}] Honeypot/poisoned lyrics detected in plain lyrics body, rejecting.")
                        return None
                    return LyricsResult(
                        content=lyr_body.strip(),
                        format=LyricsFormat.TXT,
                        sync_type=LyricsSyncType.UNSYNCED,
                        provider_name=self.name,
                        duration=cand_duration,
                        title=cand_title,
                        artist=cand_artist,
                        match_score=score,
                        metadata={"musixmatch_track_id": track_id},
                    )

        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing macro response: {e}")

        return None
