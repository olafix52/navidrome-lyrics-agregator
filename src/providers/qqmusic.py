"""QQ Music (Tencent) Lyrics Provider."""

import base64
import html
import json
import logging
import re
from typing import Any, Dict, List, Optional
import zlib

from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata, detect_sync_type
from src.normalizer import calculate_candidate_score, clean_artist, clean_title, is_duration_matching, safe_float
from src.providers.base import BaseLyricsProvider
from src.ttml import build_ttml

logger = logging.getLogger("nla.providers.qqmusic")

# ---------------------------------------------------------------------------
# Tencent Modified 3DES-ECB Decryptor for QRC Lyrics
# ---------------------------------------------------------------------------

QRC_KEY = b"!@#)(*$%123ZXC!@!@#)(NHL"

ENCRYPT = 1
DECRYPT = 0

# Tencent proprietary modified DES S-boxes:
# SBOX2[23] = 15 (standard DES is 14)
# SBOX4[53] = 10 (standard DES is 1)
SBOX = (
    (
        14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
        0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
        4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
        15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13,
    ),
    (
        15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
        3, 13, 4, 7, 15, 2, 8, 15, 12, 0, 1, 10, 6, 9, 11, 5,  # index 23: modified to 15
        0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
        13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9,
    ),
    (
        10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
        13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
        13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
        1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12,
    ),
    (
        7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
        13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
        10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
        3, 15, 0, 6, 10, 10, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14,  # index 53: modified to 10
    ),
    (
        2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
        14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
        4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
        11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3,
    ),
    (
        12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
        10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
        9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
        4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13,
    ),
    (
        4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
        13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
        1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
        6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12,
    ),
    (
        13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
        1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
        7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
        2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11,
    ),
)

KEY_PERM_C = (
    56, 48, 40, 32, 24, 16, 8, 0, 57, 49, 41, 33, 25, 17,
    9, 1, 58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35,
)
KEY_PERM_D = (
    62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21,
    13, 5, 60, 52, 44, 36, 28, 20, 12, 4, 27, 19, 11, 3,
)
KEY_COMPRESSION = (
    13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9,
    22, 18, 11, 3, 25, 7, 15, 6, 26, 19, 12, 1,
    40, 51, 30, 36, 46, 54, 29, 39, 50, 44, 32, 47,
    43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31,
)
KEY_RND_SHIFT = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)

IP_L = (
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
)
IP_R = (
    56, 48, 40, 32, 24, 16, 8, 0, 58, 50, 42, 34, 26, 18, 10, 2,
    60, 52, 44, 36, 28, 20, 12, 4, 62, 54, 46, 38, 30, 22, 14, 6,
)

P_PERM = (
    (15, 0), (6, 1), (19, 2), (20, 3), (28, 4), (11, 5), (27, 6), (16, 7),
    (0, 8), (14, 9), (22, 10), (25, 11), (4, 12), (17, 13), (30, 14), (9, 15),
    (1, 16), (7, 17), (23, 18), (13, 19), (31, 20), (26, 21), (2, 22), (8, 23),
    (18, 24), (12, 25), (29, 26), (5, 27), (21, 28), (10, 29), (3, 30), (24, 31),
)

_INVIP_BYTE_ORDER = (3, 2, 1, 0, 7, 6, 5, 4)


def _bitnum(buf: bytes, b: int, c: int) -> int:
    idx = (b // 32) * 4 + 3 - (b % 32) // 8
    return ((buf[idx] >> (7 - (b % 8))) & 1) << c


def _bit_r(a: int, b: int, c: int) -> int:
    return ((a >> (31 - b)) & 1) << c


def _bit_l(a: int, b: int, c: int) -> int:
    return ((a << b) & 0x80000000) >> c


def _sboxbit(a: int) -> int:
    return (a & 0x20) | ((a & 0x1F) >> 1) | ((a & 0x01) << 4)


def des_key_schedule(key8: bytes, mode: int) -> List[List[int]]:
    schedule = [[0] * 6 for _ in range(16)]
    c = d = 0
    for i in range(28):
        c |= _bitnum(key8, KEY_PERM_C[i], 31 - i)
        d |= _bitnum(key8, KEY_PERM_D[i], 31 - i)

    for i in range(16):
        s = KEY_RND_SHIFT[i]
        c = ((c << s) | (c >> (28 - s))) & 0xFFFFFFF0
        d = ((d << s) | (d >> (28 - s))) & 0xFFFFFFF0

        to_gen = (15 - i) if mode == DECRYPT else i
        rk = schedule[to_gen]
        for j in range(24):
            rk[j >> 3] |= _bit_r(c, KEY_COMPRESSION[j], 7 - (j & 7))
        for j in range(24, 48):
            rk[j >> 3] |= _bit_r(d, KEY_COMPRESSION[j] - 27, 7 - (j & 7))
    return schedule


def _ip(block: bytes) -> tuple[int, int]:
    left = right = 0
    for k in range(32):
        left |= _bitnum(block, IP_L[k], 31 - k)
        right |= _bitnum(block, IP_R[k], 31 - k)
    return left, right


def _inv_ip(s0: int, s1: int) -> bytes:
    out = bytearray(8)
    for k in range(8):
        base = 7 - k
        v = 0
        for t in range(4):
            v |= _bit_r(s1, base + 8 * t, 7 - 2 * t)
            v |= _bit_r(s0, base + 8 * t, 6 - 2 * t)
        out[_INVIP_BYTE_ORDER[k]] = v
    return bytes(out)


def _f(state: int, key6: List[int]) -> int:
    t1 = (
        _bit_l(state, 31, 0)
        | ((state & 0xF0000000) >> 1)
        | _bit_l(state, 4, 5)
        | _bit_l(state, 3, 6)
        | ((state & 0x0F000000) >> 3)
        | _bit_l(state, 8, 11)
        | _bit_l(state, 7, 12)
        | ((state & 0x00F00000) >> 5)
        | _bit_l(state, 12, 17)
        | _bit_l(state, 11, 18)
        | ((state & 0x000F0000) >> 7)
        | _bit_l(state, 16, 23)
    )
    t2 = (
        _bit_l(state, 15, 0)
        | ((state & 0x0000F000) << 15)
        | _bit_l(state, 20, 5)
        | _bit_l(state, 19, 6)
        | ((state & 0x00000F00) << 13)
        | _bit_l(state, 24, 11)
        | _bit_l(state, 23, 12)
        | ((state & 0x000000F0) << 11)
        | _bit_l(state, 28, 17)
        | _bit_l(state, 27, 18)
        | ((state & 0x0000000F) << 9)
        | _bit_l(state, 0, 23)
    )
    t1 &= 0xFFFFFFFF
    t2 &= 0xFFFFFFFF

    b0 = ((t1 >> 24) & 0xFF) ^ key6[0]
    b1 = ((t1 >> 16) & 0xFF) ^ key6[1]
    b2 = ((t1 >> 8) & 0xFF) ^ key6[2]
    b3 = ((t2 >> 24) & 0xFF) ^ key6[3]
    b4 = ((t2 >> 16) & 0xFF) ^ key6[4]
    b5 = ((t2 >> 8) & 0xFF) ^ key6[5]

    state = (
        (SBOX[0][_sboxbit(b0 >> 2)] << 28)
        | (SBOX[1][_sboxbit(((b0 & 0x03) << 4) | (b1 >> 4))] << 24)
        | (SBOX[2][_sboxbit(((b1 & 0x0F) << 2) | (b2 >> 6))] << 20)
        | (SBOX[3][_sboxbit(b2 & 0x3F)] << 16)
        | (SBOX[4][_sboxbit(b3 >> 2)] << 12)
        | (SBOX[5][_sboxbit(((b3 & 0x03) << 4) | (b4 >> 4))] << 8)
        | (SBOX[6][_sboxbit(((b4 & 0x0F) << 2) | (b5 >> 6))] << 4)
        | SBOX[7][_sboxbit(b5 & 0x3F)]
    )

    out = 0
    for b, c in P_PERM:
        out |= _bit_l(state, b, c)
    return out & 0xFFFFFFFF


def des_crypt_block(block: bytes, schedule: List[List[int]]) -> bytes:
    s0, s1 = _ip(block)
    for idx in range(15):
        t = s1
        s1 = _f(s1, schedule[idx]) ^ s0
        s0 = t
    s0 = _f(s1, schedule[15]) ^ s0
    return _inv_ip(s0, s1)


def triple_des_setup(key24: bytes, mode: int) -> List[List[List[int]]]:
    sch: List[List[List[int]]] = [None, None, None]  # type: ignore[list-item]
    if mode == ENCRYPT:
        sch[0] = des_key_schedule(key24[0:8], ENCRYPT)
        sch[1] = des_key_schedule(key24[8:16], DECRYPT)
        sch[2] = des_key_schedule(key24[16:24], ENCRYPT)
    else:
        sch[2] = des_key_schedule(key24[0:8], DECRYPT)
        sch[1] = des_key_schedule(key24[8:16], ENCRYPT)
        sch[0] = des_key_schedule(key24[16:24], DECRYPT)
    return sch


def triple_des_ecb(data: bytes, key24: bytes, mode: int) -> bytes:
    if len(data) % 8:
        raise ValueError(f"Ciphertext length {len(data)} is not a multiple of 8")
    sch = triple_des_setup(key24, mode)
    out = bytearray()
    for i in range(0, len(data), 8):
        blk = des_crypt_block(data[i : i + 8], sch[0])
        blk = des_crypt_block(blk, sch[1])
        blk = des_crypt_block(blk, sch[2])
        out.extend(blk)
    return bytes(out)


def qrc_decrypt(encrypted_hex: str) -> str:
    """Decrypt Tencent hex QRC ciphertext into plaintext XML using modified 3DES + zlib inflate."""
    raw = bytes.fromhex(encrypted_hex.strip())
    plain = triple_des_ecb(raw, QRC_KEY, DECRYPT)
    return zlib.decompress(plain).decode("utf-8", errors="ignore")


# ---------------------------------------------------------------------------
# QRC to TTML Conversion
# ---------------------------------------------------------------------------

_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_TOKEN_RE = re.compile(r"(.*?)\((\d+),(\d+)\)", re.S)


def convert_qrc_to_ttml(qrc_xml_or_text: str, title: str = "", artist: str = "") -> Optional[str]:
    """Convert QRC XML or decrypted text into Apple-compatible TTML (word_sync)."""
    if not qrc_xml_or_text or not isinstance(qrc_xml_or_text, str):
        return None

    # Extract LyricContent attribute if XML; otherwise use raw string
    m = re.search(r'LyricContent="(.*?)"\s*/>', qrc_xml_or_text, re.S)
    content = html.unescape(m.group(1)) if m else qrc_xml_or_text

    offset_ms = 0
    parsed_lines = []

    for raw in content.splitlines():
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

        start_ms = int(lm.group(1)) + offset_ms
        dur_ms = int(lm.group(2))
        if start_ms < 0:
            start_ms = 0
        body = lm.group(3)

        tokens = []
        for tm in _TOKEN_RE.finditer(body):
            w_text = tm.group(1)
            w_start_ms = int(tm.group(2)) + offset_ms
            w_dur_ms = int(tm.group(3))
            if w_start_ms < 0:
                w_start_ms = 0
            tokens.append({
                "start_s": w_start_ms / 1000.0,
                "end_s": (w_start_ms + w_dur_ms) / 1000.0,
                "text": w_text,
            })

        line_start_s = start_ms / 1000.0
        line_end_s = (start_ms + dur_ms) / 1000.0

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
        logger.debug(f"[qqmusic] Failed to build TTML from QRC: {e}")
        return None


# ---------------------------------------------------------------------------
# QQ Music Provider
# ---------------------------------------------------------------------------

class QQMusicProvider(BaseLyricsProvider):
    """Provider for QQ Music (Tencent) QRC word-sync TTML & synced LRC lyrics."""

    name = "qqmusic"
    description = "QQ Music / Tencent (QRC word-sync TTML & synced LRC lyrics)"

    DEFAULT_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
    SMARTBOX_SEARCH_URL = "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg"
    DEFAULT_MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    DEFAULT_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Referer": "https://y.qq.com/",
            "User-Agent": self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        query = f"{artist} {title}".strip()

        search_url = self.config.custom_url or self.DEFAULT_SEARCH_URL
        headers = self._get_headers()

        search_params = {
            "w": query,
            "format": "json",
            "p": 1,
            "n": 5,
        }

        resp = await self.request_with_retry("GET", search_url, params=search_params, headers=headers)
        data = None
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = None

        # Fallback to smartbox search if primary search failed, returned no JSON, or empty song list
        if not data or not data.get("data", {}).get("song", {}).get("list"):
            try:
                sb_resp = await self.request_with_retry(
                    "GET",
                    self.SMARTBOX_SEARCH_URL,
                    params={"key": query, "format": "json"},
                    headers=headers,
                )
                if sb_resp and sb_resp.status_code == 200:
                    sb_data = sb_resp.json()
                    sb_items = sb_data.get("data", {}).get("song", {}).get("itemlist", [])
                    if sb_items:
                        converted_list = [
                            {
                                "songname": item.get("name", ""),
                                "songmid": item.get("mid", ""),
                                "singer": [{"name": item.get("singer", "")}],
                                "interval": 0,
                            }
                            for item in sb_items
                        ]
                        data = {"data": {"song": {"list": converted_list}}}
            except Exception as e:
                logger.debug(f"[{self.name}] Smartbox fallback search error: {e}")

        if not data:
            return None

        try:
            # Handle Tencent search censor keyword blocking (subcode: -10002 / "query forbid")
            if data.get("subcode") == -10002 or data.get("message") == "query forbid":
                logger.debug(f"[{self.name}] Query forbidden by Tencent filter ({query}), attempting fallback queries...")
                words = [w for w in re.findall(r"\w+", title) if len(w) > 2]
                for w in sorted(words, key=len, reverse=True):
                    fb_query = f"{artist} {w}".strip()
                    if fb_query.lower() == query.lower():
                        continue
                    fb_resp = await self.request_with_retry("GET", search_url, params={"w": fb_query, "format": "json", "p": 1, "n": 5}, headers=headers)
                    if fb_resp:
                        try:
                            fb_data = fb_resp.json()
                            if fb_data.get("subcode") != -10002 and fb_data.get("data", {}).get("song", {}).get("list"):
                                data = fb_data
                                logger.debug(f"[{self.name}] Succeeded with fallback query: {fb_query}")
                                break
                        except Exception:
                            pass

            song_list = data.get("data", {}).get("song", {}).get("list", [])
            if not song_list:
                logger.debug(f"[{self.name}] No songs found for query: {query}")
                return None

            candidates: List[tuple[float, Dict[str, Any]]] = []
            for song in song_list:
                interval = safe_float(song.get("interval"), 0.0) or 0.0
                if track.duration > 0 and interval > 0:
                    if not is_duration_matching(track.duration, interval, 3.0):
                        continue

                song_name = song.get("songname", "")
                singers = song.get("singer", [])
                singer_names = " ".join(s.get("name", "") for s in singers if isinstance(s, dict))

                score = calculate_candidate_score(
                    target_title=title,
                    target_artist=artist,
                    candidate_title=song_name,
                    candidate_artist=singer_names,
                )

                if score >= 0.6:
                    candidates.append((score, song))

            if not candidates:
                logger.debug(f"[{self.name}] Low candidate scores for: {query}")
                return None

            candidates.sort(key=lambda x: x[0], reverse=True)

            for cand_score, cand_song in candidates[:3]:
                songmid = cand_song.get("songmid")
                if not songmid:
                    continue

                candidate_title = cand_song.get("songname") or title
                singers = cand_song.get("singer", [])
                candidate_artist = " / ".join(s.get("name", "") for s in singers if isinstance(s, dict)) or artist

                # 1. Attempt word-sync QRC via GetPlayLyricInfo
                musicu_payload = {
                    "comm": {"ct": "19", "cv": "1873", "uin": "0"},
                    "req": {
                        "module": "music.musichallSong.PlayLyricInfo",
                        "method": "GetPlayLyricInfo",
                        "param": {
                            "songMID": songmid,
                            "qrc": 1,
                            "qrc_t": 0,
                            "trans": 1,
                            "roma": 1,
                            "crypt": 1,
                        },
                    },
                }

                musicu_resp = await self.request_with_retry("POST", self.DEFAULT_MUSICU_URL, json=musicu_payload, headers=headers)
                if musicu_resp and musicu_resp.status_code == 200:
                    try:
                        try:
                            m_data = musicu_resp.json()
                        except (json.JSONDecodeError, ValueError):
                            logger.debug(f"[{self.name}] Invalid JSON response for candidate, skipping")
                            m_data = None

                        if m_data:
                            # Handle direct top-level lyric (legacy mock / response format)
                            if "lyric" in m_data and "req" not in m_data:
                                raw_lyric = m_data.get("lyric", "")
                                if raw_lyric:
                                    sync_type = detect_sync_type(raw_lyric, LyricsFormat.LRC)
                                    return LyricsResult(
                                        content=raw_lyric.strip(),
                                        format=LyricsFormat.LRC,
                                        sync_type=sync_type,
                                        provider_name=self.name,
                                        title=candidate_title,
                                        artist=candidate_artist,
                                        match_score=cand_score,
                                        metadata={"songmid": songmid, "source_format": "lrc", "match_score": cand_score},
                                    )

                            req_data = m_data.get("req", {}).get("data", {})
                            qrc_hex = req_data.get("lyric", "")
                            if qrc_hex and isinstance(qrc_hex, str):
                                # If encrypted hex QRC
                                if len(qrc_hex) > 64 and all(c in "0123456789abcdefABCDEF \r\n" for c in qrc_hex):
                                    qrc_xml = qrc_decrypt(qrc_hex)
                                    ttml_content = convert_qrc_to_ttml(qrc_xml, title=candidate_title, artist=candidate_artist)
                                    if ttml_content and "<tt" in ttml_content.lower():
                                        return LyricsResult(
                                            content=ttml_content.strip(),
                                            format=LyricsFormat.TTML,
                                            sync_type=LyricsSyncType.WORD_SYNC,
                                            provider_name=self.name,
                                            title=candidate_title,
                                            artist=candidate_artist,
                                            match_score=cand_score,
                                            metadata={"songmid": songmid, "source_format": "qrc", "match_score": cand_score},
                                        )
                                elif qrc_hex.strip().startswith("["):
                                    sync_type = detect_sync_type(qrc_hex, LyricsFormat.LRC)
                                    return LyricsResult(
                                        content=qrc_hex.strip(),
                                        format=LyricsFormat.LRC,
                                        sync_type=sync_type,
                                        provider_name=self.name,
                                        title=candidate_title,
                                        artist=candidate_artist,
                                        match_score=cand_score,
                                        metadata={"songmid": songmid, "source_format": "lrc", "match_score": cand_score},
                                    )
                    except Exception as e:
                        logger.debug(f"[{self.name}] Error decrypting/converting QRC for {songmid}: {e}")

                # 2. Fallback to line-synced LRC via fcg_query_lyric_new.fcg
                lyric_url = self.DEFAULT_LYRIC_URL
                lyric_params = {
                    "songmid": songmid,
                    "format": "json",
                    "nobase64": 1,
                }

                lyric_resp = await self.request_with_retry("GET", lyric_url, params=lyric_params, headers=headers)
                if not lyric_resp or lyric_resp.status_code != 200:
                    continue

                try:
                    l_data = lyric_resp.json()
                except (json.JSONDecodeError, ValueError):
                    logger.debug(f"[{self.name}] Invalid JSON response for candidate, skipping")
                    continue

                if l_data.get("retcode", -1) != 0 and l_data.get("code", -1) != 0:
                    continue

                raw_lyric = l_data.get("lyric", "")
                if not raw_lyric:
                    continue

                # Handle base64 fallback if server sent encoded lyric
                if not raw_lyric.strip().startswith("["):
                    try:
                        decoded = base64.b64decode(raw_lyric).decode("utf-8", errors="ignore")
                        if decoded.strip():
                            raw_lyric = decoded
                    except Exception:
                        pass

                raw_lyric = raw_lyric.strip()
                if not raw_lyric:
                    continue

                sync_type = detect_sync_type(raw_lyric, LyricsFormat.LRC)

                return LyricsResult(
                    content=raw_lyric,
                    format=LyricsFormat.LRC,
                    sync_type=sync_type,
                    provider_name=self.name,
                    title=candidate_title,
                    artist=candidate_artist,
                    match_score=cand_score,
                    metadata={"songmid": songmid, "source_format": "lrc", "match_score": cand_score},
                )

            return None
        except Exception as e:
            logger.debug(f"[{self.name}] Error processing lyrics: {e}")
            return None
