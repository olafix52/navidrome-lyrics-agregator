"""Musixmatch iOS Mobile API Lyrics Provider (supporting RichSync TTML word-sync and Line-synced LRC)."""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.cache import aget_cached_spotify_id
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, safe_float
from src.providers.base import BaseLyricsProvider
from src.tag_reader import _extract_spotify_id
from src.ttml import build_ttml, format_ttml_timestamp

logger = logging.getLogger("nla.providers.musixmatch")

POISONED_PATTERNS = (
    "wob gopini den",
    "tefe woxica fero",
    "gogoh vudob wiya",
    "keric sohu peduf",
)

MXM_WROTE = re.compile(r"writer\(s\)\s*:\s*(.+)", re.I)
MXM_STOP = 0.005  # Words shorter than 5ms are treated as zero duration
MXM_GAP = 0.4  # Micro-gap threshold: gaps between words < 0.4s are smoothed
DEWORD_THRESHOLD = 0.2  # If <= 20% of lines have > 1 syllable token in long lyrics, deword (fake word-sync)
MXM_COLD = 1800.0  # 30 minutes cooldown after 401/captcha
FALLBACK_STATIC_TOKEN = "21051986b9886e2d7bd5d8295b15d605c14e13e33326a3a0e50e1b"


class _PoisonedLyrics(Exception):
    """Musixmatch served anti-scraping honeypot lyrics."""


def _as_dict(value: Any) -> Dict[str, Any]:
    """Musixmatch sends ``"body": []`` (a list) for calls without results; treat it as empty."""
    return value if isinstance(value, dict) else {}


def _macro_call(macro_calls: Dict[str, Any], name: str) -> "tuple[int, Dict[str, Any]]":
    """(status_code, body) of one call inside a macro response, tolerant of list/empty bodies."""
    message = _as_dict(_as_dict(macro_calls.get(name)).get("message"))
    status = _as_dict(message.get("header")).get("status_code", 0)
    return status, _as_dict(message.get("body"))


def is_poisoned_lyrics(text: str) -> bool:
    """Detect Musixmatch anti-scraping dummy honeypot lyrics (e.g. 'Wob gopini den...')."""
    if not text:
        return False
    lower = text.lower()
    return any(p in lower for p in POISONED_PATTERNS)


_MXM_FOOTER = re.compile(r"\n*\*{3,}\s*This Lyrics is NOT for Commercial use.*$", re.I | re.S)


def strip_musixmatch_footer(text: str) -> str:
    """Remove the '******* This Lyrics is NOT for Commercial use *******' footer (+ tracking id)."""
    return _MXM_FOOTER.sub("", text or "").strip()


def extract_musixmatch_writers(*bodies: Any) -> List[str]:
    """Extract songwriter names from Musixmatch copyright lines."""
    for body in bodies:
        if not body:
            continue
        text = ""
        if isinstance(body, str):
            text = body
        elif isinstance(body, dict):
            text = str(body.get("lyrics_copyright") or body.get("subtitle_copyright") or "")
        m = MXM_WROTE.search(text)
        if not m:
            continue
        names = []
        seen = set()
        for name in re.split(r"\s*[/,、，&]\s*", m.group(1)):
            cleaned = name.strip().rstrip(".")
            if cleaned and len(cleaned) > 1 and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                names.append(cleaned)
        if names:
            return names
    return []


def _apply_line_separators(tokens: List[Dict[str, Any]], line_text: str) -> None:
    """Make the tokens' concatenated text match the line text ``x`` (word spacing).

    TTML spans are rendered back to back, so each token must carry the whitespace that
    follows it. RichSync usually sends explicit space entries (already attached); when it
    does not, the separators are taken from the line text. The last token never keeps a
    trailing separator.
    """
    if not tokens:
        return
    if line_text and "".join(t["text"] for t in tokens).strip() != line_text:
        pos = 0
        aligned: List[str] = []
        for t in tokens:
            word = t["text"].strip()
            at = line_text.find(word, pos) if word else -1
            if at < 0 or line_text[pos:at].strip():
                aligned = []
                break
            end = at + len(word)
            sep_end = end
            while sep_end < len(line_text) and line_text[sep_end].isspace():
                sep_end += 1
            aligned.append(word + line_text[end:sep_end])
            pos = sep_end
        if aligned and pos >= len(line_text.rstrip()):
            for t, text in zip(tokens, aligned):
                t["text"] = text
    tokens[-1]["text"] = tokens[-1]["text"].rstrip()


def convert_richsync_to_ttml(
    richsync_data: Any,
    title: str = "",
    artist: str = "",
    songwriters: Optional[List[str]] = None,
) -> Optional[str]:
    """Convert Musixmatch RichSync JSON array to Apple-compatible TTML with syllable spans.

    Implements:
    - Accurate word boundaries using space/whitespace tokens as end markers.
    - Zero-duration word repair (_mxm_spans) based on line/document medians.
    - Micro-gap smoothing (< 0.4s) to eliminate flickering during continuous singing.
    - Deword detection: if lines lack genuine syllable division, reject so caller falls back to line-sync.
    """
    if isinstance(richsync_data, str):
        try:
            richsync_data = json.loads(richsync_data)
        except Exception:
            return None

    if not isinstance(richsync_data, list) or not richsync_data:
        return None

    parsed_lines: List[Dict[str, Any]] = []

    for line in richsync_data:
        if not isinstance(line, dict):
            continue
        ts = float(line.get("ts", 0.0))
        te = float(line.get("te", ts))
        if te < ts:
            te = ts

        raw_words = line.get("l", [])
        line_text = str(line.get("x") or "").strip()

        if not raw_words:
            if line_text:
                parsed_lines.append({
                    "start_s": ts,
                    "end_s": te,
                    "tokens": [{"start_s": ts, "end_s": te, "text": line_text}],
                    "text": line_text,
                })
            continue

        out_tokens: List[Dict[str, Any]] = []
        gap: Optional[float] = None

        for w in raw_words:
            if not isinstance(w, dict):
                continue
            text = str(w.get("c", ""))
            offset = float(w.get("o", 0.0))
            at = ts + offset

            if not text.strip():
                # Whitespace / space token: marks when preceding word ended singing, and is
                # the separator between the two words
                gap = at if gap is None else gap
                if out_tokens and not out_tokens[-1]["text"].endswith(text):
                    out_tokens[-1]["text"] += text
                continue

            if out_tokens:
                # Preceding word ended either at gap or at start of current word
                prev_end = max(out_tokens[-1]["start_s"], gap if gap is not None else at)
                out_tokens[-1]["end_s"] = prev_end

            out_tokens.append({
                "text": text,
                "start_s": at,
                "end_s": at,
            })
            gap = None

        if out_tokens:
            out_tokens[-1]["end_s"] = max(out_tokens[-1]["start_s"], te)
            for a, b in zip(out_tokens, out_tokens[1:]):
                a["end_s"] = min(max(a["end_s"], a["start_s"]), b["start_s"])
            _apply_line_separators(out_tokens, line_text)

        parsed_lines.append({
            "start_s": ts,
            "end_s": te,
            "tokens": out_tokens,
            "text": line_text or " ".join(t["text"] for t in out_tokens),
        })

    if not parsed_lines:
        return None

    # Dewording detection: if <= 20% of lines have > 1 syllable token in documents with > 2 lines
    lines_with_tokens = [l for l in parsed_lines if l.get("tokens")]
    if len(lines_with_tokens) > 2:
        multi_token_lines = sum(1 for l in lines_with_tokens if len(l["tokens"]) > 1)
        if multi_token_lines <= DEWORD_THRESHOLD * len(lines_with_tokens):
            logger.debug(
                f"[musixmatch] Dewording: only {multi_token_lines}/{len(lines_with_tokens)} lines have multiple syllables"
            )
            return None

    # Zero-duration repair
    all_durations = [
        t["end_s"] - t["start_s"]
        for l in parsed_lines
        for t in l.get("tokens", [])
        if (t["end_s"] - t["start_s"]) > MXM_STOP
    ]
    all_durations.sort()
    usual_duration = all_durations[len(all_durations) // 2] if all_durations else 0.25

    for i, line in enumerate(parsed_lines):
        syls = line.get("tokens", [])
        line_durations = [
            t["end_s"] - t["start_s"]
            for t in syls
            if (t["end_s"] - t["start_s"]) > MXM_STOP
        ]
        line_durations.sort()
        span = line_durations[len(line_durations) // 2] if line_durations else usual_duration

        for j, y in enumerate(syls):
            if (y["end_s"] - y["start_s"]) > MXM_STOP:
                continue

            # Word stopped when it began; repair using median
            if j + 1 < len(syls):
                room = syls[j + 1]["start_s"]
            elif i + 1 < len(parsed_lines):
                next_tokens = parsed_lines[i + 1].get("tokens", [])
                room = next_tokens[0]["start_s"] if next_tokens else parsed_lines[i + 1]["start_s"]
            else:
                room = line["end_s"]

            repaired_end = y["start_s"] + span
            if isinstance(room, (int, float)) and room > y["start_s"]:
                repaired_end = min(repaired_end, room)
            y["end_s"] = max(y["end_s"], repaired_end)

    # Micro-gap smoothing: close gaps < 0.4s between consecutive words within document
    flat_tokens = [t for l in parsed_lines for t in l.get("tokens", [])]
    for a, b in zip(flat_tokens, flat_tokens[1:]):
        gap_duration = b["start_s"] - a["end_s"]
        if 0.0 < gap_duration < MXM_GAP:
            a["end_s"] = b["start_s"]

    # Ensure line end_s is at least the last token's end_s
    for line in parsed_lines:
        if line.get("tokens"):
            last_token_end = line["tokens"][-1]["end_s"]
            line["end_s"] = max(line["end_s"], last_token_end)

    try:
        return build_ttml(
            lines=parsed_lines,
            title=title,
            artist=artist,
            provider="Musixmatch",
            source="richsync",
            songwriters=songwriters,
        )
    except Exception as e:
        logger.debug(f"[musixmatch] Failed to build TTML: {e}")
        return None


class MusixmatchProvider(BaseLyricsProvider):
    """Provider for Musixmatch synchronized lyrics via iOS Mobile API (RichSync TTML & LRC line-sync)."""

    name = "musixmatch"
    description = "Musixmatch Database (RichSync TTML word-sync & LRC line-sync via iOS Mobile API)"

    DEFAULT_API_BASE = "https://apic-appmobile.musixmatch.com/ws/1.1"
    APP_ID = "mac-ios-v2.0"
    IOS_HEADERS = {
        "User-Agent": "Musixmatch/2024101401 CFNetwork/1498.700.2 Darwin/23.6.0",
        "X-Cookie": "x-mxm-token-guid=",
        "x-mxm-app-version": "10.1.1",
        "X-User-Agent": "Musixmatch/2025120901 CFNetwork/3860.300.31 Darwin/25.2.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "application/json",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_token: Optional[str] = self.config.api_key or self.config.extra.get("user_token")
        default_token_path = None if "PYTEST_CURRENT_TEST" in os.environ else "data/musixmatch_token.json"
        token_path = self.config.extra.get("token_path", default_token_path)
        self._token_file: Optional[Path] = Path(token_path) if token_path else None
        self._cold_until: float = 0.0

    def _save_token_to_disk(self, token: str, now: float) -> None:
        if not self._token_file:
            return
        try:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            self._token_file.write_text(json.dumps({"token": token, "at": now}), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[{self.name}] Failed to save token cache: {e}")

    def _record_cold_cooldown(self, now: float) -> None:
        self._cold_until = now + MXM_COLD
        if not self._token_file:
            return
        try:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            self._token_file.write_text(json.dumps({"cold": now}), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[{self.name}] Failed to write cold cooldown state: {e}")

    async def _get_user_token(self, force: bool = False) -> Optional[str]:
        """Obtain or refresh an anonymous user token from Musixmatch with disk caching and cooldown."""
        if self.config.api_key and set(self.config.api_key) != {"0"}:
            return self.config.api_key
        if self.config.extra.get("user_token") and set(self.config.extra.get("user_token")) != {"0"}:
            return self.config.extra.get("user_token")

        now = time.time()
        cold_at = 0.0
        held_token = None

        if self._token_file and self._token_file.is_file():
            try:
                cached = json.loads(self._token_file.read_text(encoding="utf-8"))
                cold_at = float(cached.get("cold", 0.0))
                held_token = cached.get("token")
            except Exception as e:
                logger.debug(f"[{self.name}] Failed to load token cache: {e}")

        # If we have a held token and we are not forced to refresh, use it!
        if not force:
            if self._user_token and set(self._user_token) != {"0"}:
                return self._user_token
            if held_token and set(held_token) != {"0"}:
                self._user_token = held_token
                return held_token

        # Check if in cold cooldown period after 401/captcha
        if (now - cold_at < MXM_COLD) or (now < self._cold_until):
            logger.debug(
                f"[{self.name}] In cooldown period after 401/captcha ({int(MXM_COLD - (now - cold_at))}s remaining)"
            )
            return None if force else (held_token or self._user_token)

        # Query token.get
        api_base = self.config.custom_url or self.DEFAULT_API_BASE
        token_url = f"{api_base.rstrip('/')}/token.get"
        params = {
            "app_id": self.APP_ID,
            "format": "json",
        }

        resp = await self.request_with_retry("GET", token_url, params=params, headers=self.IOS_HEADERS)
        if resp:
            try:
                data = resp.json()
                message = data.get("message", {})
                status_code = message.get("header", {}).get("status_code", 0)
                if status_code in (401, 402):
                    logger.warning(f"[{self.name}] Token request refused (401/captcha), cooling down for {int(MXM_COLD // 60)} min")
                    self._record_cold_cooldown(now)
                    return FALLBACK_STATIC_TOKEN

                body = message.get("body", {})
                token = body.get("user_token")
                if token and set(token) != {"0"}:
                    self._user_token = token
                    self._save_token_to_disk(token, now)
                    return token
                else:
                    logger.debug(f"[{self.name}] Dummy zero-token received from Musixmatch (cloud IP)")
                    self._record_cold_cooldown(now)
                    return FALLBACK_STATIC_TOKEN
            except Exception as e:
                logger.debug(f"[{self.name}] Failed to parse token: {e}")

        return self._user_token or FALLBACK_STATIC_TOKEN

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        token = await self._get_user_token()
        if not token:
            # Only happens during the cooldown after a 401/captcha; logged when it starts
            logger.debug(f"[{self.name}] No user token available (cooldown)")
            return None

        api_base = self.config.custom_url or self.DEFAULT_API_BASE
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)

        # Musixmatch iOS Mobile API macro endpoint (bundles track info, richsync, and subtitles)
        macro_endpoint = f"{api_base.rstrip('/')}/macro.subtitles.get"
        params = {
            "format": "json",
            "namespace": "lyrics_richsynced",
            "optional_calls": "track.richsync",
            "richsync_compact_type": "words",
            "subtitle_format": "lrc",
            "app_id": self.APP_ID,
            "usertoken": token,
            "q_artist": artist,
            "q_track": title,
        }
        if track.album:
            params["q_album"] = track.album
        if track.duration > 0:
            params["q_duration"] = str(round(track.duration, 3))
            params["f_subtitle_length"] = str(int(round(track.duration)))
            params["f_subtitle_length_max_deviation"] = "3"

        sp_id = _extract_spotify_id(track.spotify_id) if track.spotify_id else None
        if not sp_id:
            cached_sp = await aget_cached_spotify_id(
                artist=track.clean_artist or track.artist,
                title=track.clean_title or track.title,
                isrc=track.isrc,
            )
            if cached_sp:
                track.spotify_id = cached_sp
                sp_id = _extract_spotify_id(cached_sp)

        if sp_id:
            params["track_spotify_id"] = sp_id

        message = await self._request_macro(macro_endpoint, params)
        if message is None:
            return None  # request failed / unauthorized: a search would fail the same way
        try:
            result = self._parse_macro(message, title, artist)
        except _PoisonedLyrics:
            return None  # anti-scraping honeypot: do not spend more requests on this track
        if result is not None and result.sync_type != LyricsSyncType.UNSYNCED:
            return result

        # Musixmatch's matcher sometimes resolves a query to an empty stub entry (no length,
        # no subtitles) although the song has synced lyrics under another track id. Look the
        # track up by search and fetch that entry directly.
        fallback_id = await self._search_track_id(params["usertoken"], title, artist, track.duration)
        if fallback_id is not None and fallback_id != (result.metadata.get("musixmatch_track_id") if result else None):
            by_id = {k: v for k, v in params.items() if not k.startswith(("q_", "f_subtitle", "track_spotify_id"))}
            by_id["track_id"] = str(fallback_id)
            message = await self._request_macro(macro_endpoint, by_id)
            try:
                fallback = self._parse_macro(message, title, artist) if message is not None else None
            except _PoisonedLyrics:
                fallback = None
            if fallback is not None and (result is None or fallback.sync_type != LyricsSyncType.UNSYNCED):
                return fallback
        return result

    async def _request_macro(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """GET a macro endpoint (refreshing the token once on 401/402). Returns its ``message`` if OK."""
        response = await self.request_with_retry("GET", endpoint, params=params, headers=self.IOS_HEADERS)
        if not response:
            return None
        try:
            message = _as_dict(_as_dict(response.json()).get("message"))
            status_code = _as_dict(message.get("header")).get("status_code", 0)
            if status_code in (401, 402):
                logger.debug(f"[{self.name}] Unauthorized (401/402), invalidating token and attempting retry")
                self._user_token = None
                token = await self._get_user_token(force=True)
                if not token:
                    return None
                params["usertoken"] = token
                response = await self.request_with_retry("GET", endpoint, params=params, headers=self.IOS_HEADERS)
                if not response:
                    return None
                message = _as_dict(_as_dict(response.json()).get("message"))
                status_code = _as_dict(message.get("header")).get("status_code", 0)
            return message if status_code == 200 else None
        except Exception as e:
            logger.debug(f"[{self.name}] Invalid macro response: {e}")
            return None

    async def _search_track_id(
        self, token: str, title: str, artist: str, duration: float
    ) -> Optional[int]:
        """Best ``track.search`` hit that has synced lyrics and matches name and duration.

        Search results mix live versions, remixes and covers, so candidates are filtered by
        title/artist similarity and (when both are known) a duration within 3 seconds, then
        ranked by word-sync availability and Musixmatch's popularity rating.
        """
        api_base = self.config.custom_url or self.DEFAULT_API_BASE
        params = {
            "format": "json",
            "app_id": self.APP_ID,
            "usertoken": token,
            "q_track": title,
            "q_artist": artist,
            "f_has_lyrics": "1",
            "s_track_rating": "desc",
            "page_size": "10",
        }
        response = await self.request_with_retry(
            "GET", f"{api_base.rstrip('/')}/track.search", params=params, headers=self.IOS_HEADERS
        )
        if not response:
            return None
        try:
            body = _as_dict(_as_dict(_as_dict(response.json()).get("message")).get("body"))
            track_list = body.get("track_list")
            track_list = track_list if isinstance(track_list, list) else []
        except Exception as e:
            logger.debug(f"[{self.name}] Invalid track.search response: {e}")
            return None

        min_similarity = self.config.extra.get("min_similarity", 0.6)
        best_key, best_id = None, None
        for entry in track_list:
            cand = _as_dict(_as_dict(entry).get("track"))
            if not (cand.get("has_subtitles") or cand.get("has_richsync")) or not cand.get("track_id"):
                continue
            score = calculate_candidate_score(title, artist, cand.get("track_name") or "", cand.get("artist_name") or "")
            if score < min_similarity:
                continue
            length = safe_float(cand.get("track_length")) or 0.0
            if duration > 0 and length > 0 and abs(length - duration) > 3.0:
                continue  # different version (live, edit, remix)
            verified = length > 0 and duration > 0
            key = (
                verified,  # duration actually checked
                bool(cand.get("has_richsync")),
                round(score, 2),
                -abs(length - duration) if verified else 0.0,
                safe_float(cand.get("track_rating")) or 0.0,
            )
            if best_key is None or key > best_key:
                best_key, best_id = key, int(cand["track_id"])
        if best_id is not None:
            logger.debug(f"[{self.name}] Matcher found no synced lyrics; using search result track_id={best_id}")
        return best_id

    def _parse_macro(self, message: Dict[str, Any], title: str, artist: str) -> Optional[LyricsResult]:
        """Best lyrics (RichSync TTML > LRC > plain) contained in a macro.subtitles.get response."""
        try:
            return self._parse_macro_calls(message, title, artist)
        except _PoisonedLyrics:
            raise
        except Exception as e:
            logger.debug(f"[{self.name}] Error parsing macro response: {e}")
            return None

    def _parse_macro_calls(self, message: Dict[str, Any], title: str, artist: str) -> Optional[LyricsResult]:
        macro_calls = _as_dict(_as_dict(message.get("body")).get("macro_calls"))

        # Calls without a result come back with a list body instead of an object, so every
        # level is read defensively: one missing part must not discard the others.
        _, track_body = _macro_call(macro_calls, "matcher.track.get")
        track_data = _as_dict(track_body.get("track"))
        cand_title = track_data.get("track_name")
        cand_artist = track_data.get("artist_name")
        cand_duration = safe_float(track_data.get("track_length"))
        track_id = track_data.get("track_id")

        richsync_status, richsync_body = _macro_call(macro_calls, "track.richsync.get")
        rich_body_obj = _as_dict(richsync_body.get("richsync"))
        subtitles_status, subtitles_body = _macro_call(macro_calls, "track.subtitles.get")
        sub_list = subtitles_body.get("subtitle_list")
        sub_list = sub_list if isinstance(sub_list, list) else []
        sub_obj = _as_dict(_as_dict(sub_list[0]).get("subtitle")) if sub_list else {}
        lyrics_status, lyrics_body = _macro_call(macro_calls, "track.lyrics.get")
        lyr_obj = _as_dict(lyrics_body.get("lyrics"))

        # Extract songwriters from copyright headers

        songwriters = extract_musixmatch_writers(rich_body_obj, sub_obj, lyr_obj)

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
        if richsync_status == 200:
            rs_body = rich_body_obj.get("richsync_body")
            if rs_body and not is_poisoned_lyrics(str(rs_body)):
                ttml_content = convert_richsync_to_ttml(
                    rs_body,
                    title=cand_title,
                    artist=cand_artist,
                    songwriters=songwriters,
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
        if subtitles_status == 200:
            if sub_list:
                sub_body = sub_obj.get("subtitle_body", "")
                if sub_body and isinstance(sub_body, str) and sub_body.strip():
                    if is_poisoned_lyrics(sub_body):
                        logger.debug(f"[{self.name}] Honeypot/poisoned lyrics detected in subtitle body, rejecting.")
                        raise _PoisonedLyrics()
                    return LyricsResult(
                        content=sub_body.strip(),
                        format=LyricsFormat.LRC,
                        sync_type=LyricsSyncType.LINE_SYNC,
                        provider_name=self.name,
                        duration=safe_float(sub_obj.get("subtitle_length")) or cand_duration,
                        title=cand_title,
                        artist=cand_artist,
                        match_score=score,
                        metadata={
                            "musixmatch_id": sub_obj.get("subtitle_id"),
                            "musixmatch_track_id": track_id,
                        },
                    )

        # 3. Fallback to plain lyrics from track.lyrics.get if available
        if lyrics_status == 200:
            lyr_body = strip_musixmatch_footer(lyr_obj.get("lyrics_body") or "")
            if lyr_body:
                if is_poisoned_lyrics(lyr_body):
                    logger.debug(f"[{self.name}] Honeypot/poisoned lyrics detected in plain lyrics body, rejecting.")
                    raise _PoisonedLyrics()
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

        return None
