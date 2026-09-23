"""Lyrics blending engine reconciling high-quality base text with word-level timing donors.

Inspired by mild-lyrics (by gcoolL):
- Aligns line sequences using difflib.SequenceMatcher.
- Maps word/syllable cuts from donor onto clean base text at character-level (_recut & _relay).
- Recovers uncensored words (e.g. 'f***' timing applied to 'fucking') while preserving base punctuation and casing.
- Supports 3-way blends (triblend / kutriblend) with a spare donor filling missing lines.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

from src.models import LyricsFormat, LyricsResult, LyricsSyncType
from src.ttml import build_ttml
from src.web.parser import KaraokeLine, KaraokeWord, parse_lyrics_to_karaoke

logger = logging.getLogger("nla.blender")

CONTRACTED = "'\u2019"
BLEND_SAME_FLOOR = 0.45
RELAY_LIKE_FLOOR = 0.60


def _key(s: str) -> str:
    """Normalize string to lowercase alphanumeric characters for robust matching."""
    return "".join(c for c in (s or "").lower() if c.isalnum())


def _shared(a: List[str], b: List[str], pairs: Dict[int, int], floor: float = BLEND_SAME_FLOOR) -> bool:
    """Determine whether two line sequences represent the same song based on matched share."""
    real = min(sum(1 for x in a if x), sum(1 for x in b if x))
    return bool(real) and len(pairs) >= floor * real


def _near_pairs(a: List[str], b: List[str], mate: Dict[int, int], floor: float = 0.70) -> Dict[int, int]:
    """Recover near-matching lines within windows established by exact matches."""
    new_pairs = dict(mate)
    unmatched_a = [i for i, k in enumerate(a) if k and i not in mate]
    if not unmatched_a:
        return new_pairs

    matched_a = sorted(mate.keys())
    used_b = set(mate.values())

    for i in unmatched_a:
        # Find window in b bounded by neighboring matched lines in a
        prev_a = [x for x in matched_a if x < i]
        next_a = [x for x in matched_a if x > i]
        min_b = mate[prev_a[-1]] + 1 if prev_a else 0
        max_b = mate[next_a[0]] - 1 if next_a else len(b) - 1

        candidates = [j for j in range(min_b, max_b + 1) if j not in used_b and b[j]]
        best_j, best_ratio = None, 0.0
        for j in candidates:
            ratio = SequenceMatcher(None, a[i], b[j], autojunk=False).ratio()
            if ratio >= floor and ratio > best_ratio:
                best_ratio = ratio
                best_j = j

        if best_j is not None:
            new_pairs[i] = best_j
            used_b.add(best_j)

    return new_pairs


def pair_lines(
    base_lines: List[KaraokeLine],
    donor_lines: List[KaraokeLine],
    floor: float = BLEND_SAME_FLOOR,
) -> Optional[Dict[int, int]]:
    """Monotonically pair base line indices to donor line indices by text alignment."""
    a = [_key(line.text) for line in base_lines]
    b = [_key(line.text) for line in donor_lines]

    sm = SequenceMatcher(None, a, b, autojunk=False)
    pairs: Dict[int, int] = {}
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            if a[i + k]:
                pairs[i + k] = j + k

    if not _shared(a, b, pairs, floor=floor):
        logger.debug(f"[Blender] Sequence share below threshold ({len(pairs)} matched)")
        return None

    # Recover near pairs within matched windows
    pairs = _near_pairs(a, b, pairs)

    # Ensure strictly monotonic ordering
    monotonic_pairs: Dict[int, int] = {}
    last_j = -1
    for i in sorted(pairs.keys()):
        j = pairs[i]
        if j > last_j:
            monotonic_pairs[i] = j
            last_j = j

    if not _shared(a, b, monotonic_pairs, floor=floor):
        return None

    return monotonic_pairs


def _recut(
    theirs: str,
    ours: str,
    bounds: List[int],
    breaks: Set[int],
    floor: float = RELAY_LIKE_FLOOR,
) -> Optional[List[int]]:
    """Map cut positions in donor's character stream onto our base text characters."""
    sm = SequenceMatcher(None, theirs, ours, autojunk=False)
    if sm.ratio() < floor:
        return None

    at = [0] * (len(theirs) + 1)
    lo, hi = list(at), list(at)
    loose = None

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                at[i1 + k] = lo[i1 + k] = hi[i1 + k] = j1 + k
            if loose:
                lo[i1] = loose
        elif i2 > i1:
            for k in range(i2 - i1):
                at[i1 + k] = j1 + round(k * (j2 - j1) / (i2 - i1))
                lo[i1 + k], hi[i1 + k] = j1, j2
        loose = None if tag == "equal" else j1

    at[-1] = lo[-1] = hi[-1] = len(ours)
    if loose is not None:
        lo[-1] = loose

    out: List[int] = []
    for b in bounds:
        free = [p for p in range(lo[b], hi[b] + 1) if p in breaks]
        out.append(min(free, key=lambda p: (abs(p - at[b]), -p)) if free else at[b])
    return out


def _unsplit(syls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recombine accidental contraction cuts (e.g. 'It' and 's' -> 'It\'s')."""
    out: List[Dict[str, Any]] = []
    for y in syls:
        was = out[-1] if out else None
        if (
            was
            and was.get("IsPartOfWord")
            and str(was.get("Text") or "").rstrip()[-1:] in CONTRACTED
        ):
            out[-1] = {
                **was,
                "Text": str(was.get("Text") or "") + str(y.get("Text") or ""),
                "EndTime": max(float(was.get("EndTime", 0)), float(y.get("EndTime", 0))),
                "IsPartOfWord": bool(y.get("IsPartOfWord")),
            }
        else:
            out.append(dict(y))
    return out


def _unlump(syls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Share out single donor tokens covering multiple words by proportional character count."""
    out: List[Dict[str, Any]] = []
    for y in syls:
        text = str(y.get("Text") or "")
        parts = re.findall(r"[^\s\u200b]+[\s\u200b]*", text)
        s, e = y.get("StartTime"), y.get("EndTime")
        if len(parts) < 2 or not isinstance(s, (int, float)) or not isinstance(e, (int, float)):
            out.append(y)
            continue

        joined: List[str] = []
        for piece in parts:
            if joined and not any(c.isalnum() for c in piece):
                joined[-1] += piece
            else:
                joined.append(piece)
        parts = joined
        if len(parts) < 2:
            out.append(y)
            continue

        span = max(0.0, float(e) - float(s))
        total = sum(len(p.strip()) for p in parts) or 1
        at = float(s)
        for k, piece in enumerate(parts):
            body = piece.rstrip()
            last = k == len(parts) - 1
            end = float(e) if last else at + span * len(body) / total
            made = {
                **y,
                "Text": body if last else piece,
                "StartTime": at,
                "EndTime": max(end, at),
                "IsPartOfWord": bool(y.get("IsPartOfWord")) if last else (piece == body),
            }
            out.append(made)
            at = end
    return out


def relay_word_timings(
    text: str,
    donor_words: List[KaraokeWord],
    floor: float = RELAY_LIKE_FLOOR,
) -> Optional[List[KaraokeWord]]:
    """Slice `text` along `donor_words` boundaries, preserving pristine text letters."""
    if not text.strip() or not donor_words:
        return None

    idx = [i for i, c in enumerate(text) if c.isalnum()]
    ours = _key(text)
    if not idx or len(ours) != len(idx):
        return None

    spans = [(w, _key(w.text)) for w in donor_words if _key(w.text)]
    if not spans:
        return None

    theirs, bounds, n = "", [], 0
    for _, k in spans:
        theirs, n = theirs + k, n + len(k)
        bounds.append(n)

    if theirs == ours:
        cuts = bounds
    else:
        breaks = {0, len(ours)} | {p for p in range(1, len(ours)) if idx[p] - idx[p - 1] > 1}
        cuts = _recut(theirs, ours, bounds, breaks, floor)

    if cuts is None:
        return None

    syl_dicts: List[Dict[str, Any]] = []
    cut, held = 0, None

    for (w, _k), at in zip(spans, cuts):
        st, en = w.start, w.end
        stop = idx[at] if at < len(idx) else len(text)
        piece = text[cut:stop]
        if not _key(piece):
            if syl_dicts:
                syl_dicts[-1]["EndTime"] = max(syl_dicts[-1]["EndTime"], float(en))
            else:
                held = float(st) if held is None else min(held, float(st))
            continue

        body = piece.rstrip()
        syl_dicts.append({
            "Text": body,
            "StartTime": float(st) if held is None else held,
            "EndTime": float(en),
            "IsPartOfWord": piece == body,
        })
        held, cut = None, stop

    if not syl_dicts:
        return None

    if cut < len(text):
        syl_dicts[-1]["Text"] += text[cut:].rstrip()
    syl_dicts[-1]["IsPartOfWord"] = False

    cleaned_syls = _unlump(_unsplit(syl_dicts))

    result_words: List[KaraokeWord] = []
    for d in cleaned_syls:
        result_words.append(
            KaraokeWord(
                text=str(d.get("Text", "")),
                start=float(d.get("StartTime", 0.0)),
                end=float(d.get("EndTime", 0.0)),
            )
        )
    return result_words


def blend_karaoke_lines(
    base_lines: List[KaraokeLine],
    donor_lines: List[KaraokeLine],
    spare_lines: Optional[List[KaraokeLine]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Reconcile base line sequence with donor(s) word & line timings."""
    if not base_lines or not donor_lines:
        return None

    # Step 1: Pair base lines with primary donor
    pairs = pair_lines(base_lines, donor_lines)
    if not pairs:
        logger.debug("[Blender] Failed to pair base lines with primary donor")
        return None

    # Step 2: If spare donor provided (triblend/kutriblend), pair base lines with spare
    spare_pairs: Dict[int, int] = {}
    if spare_lines:
        spare_pairs = pair_lines(base_lines, spare_lines) or {}

    out_ttml_lines: List[Dict[str, Any]] = []

    for i, base_line in enumerate(base_lines):
        line_text = base_line.text.strip()
        if not line_text:
            continue

        matched_donor_idx = pairs.get(i)
        donor_line = donor_lines[matched_donor_idx] if matched_donor_idx is not None else None

        # Check if spare donor can fill missing line
        spare_donor_line = None
        if donor_line is None and spare_lines and i in spare_pairs:
            spare_donor_line = spare_lines[spare_pairs[i]]

        chosen_donor = donor_line or spare_donor_line

        if chosen_donor is not None:
            # Transfer word timings if available
            relayed_words: Optional[List[KaraokeWord]] = None
            if chosen_donor.words:
                relayed_words = relay_word_timings(base_line.text, chosen_donor.words)

            if relayed_words:
                start_s = relayed_words[0].start
                end_s = relayed_words[-1].end
                tokens = []
                for idx_w, w in enumerate(relayed_words):
                    w_txt = w.text
                    if idx_w < len(relayed_words) - 1 and not w_txt.endswith(" ") and " " in base_line.text:
                        w_txt += " "
                    tokens.append({"start_s": w.start, "end_s": w.end, "text": w_txt})
            else:
                start_s = chosen_donor.start if chosen_donor.start is not None else (base_line.start or 0.0)
                end_s = chosen_donor.end if chosen_donor.end is not None else (start_s + 4.0)
                tokens = [{"start_s": start_s, "end_s": end_s, "text": base_line.text}]
        else:
            # Unmatched line: keep base timing if present, or interpolate from surrounding lines
            start_s = base_line.start if base_line.start is not None else (out_ttml_lines[-1]["end_s"] if out_ttml_lines else 0.0)
            end_s = base_line.end if base_line.end is not None else (start_s + 4.0)
            tokens = [{"start_s": start_s, "end_s": end_s, "text": base_line.text}]

        out_ttml_lines.append({
            "start_s": start_s,
            "end_s": end_s,
            "agent": base_line.agent or "v1",
            "tokens": tokens,
        })

    # Sort and guarantee non-decreasing start times
    out_ttml_lines.sort(key=lambda x: x["start_s"])
    for idx in range(len(out_ttml_lines) - 1):
        if out_ttml_lines[idx]["end_s"] > out_ttml_lines[idx + 1]["start_s"]:
            out_ttml_lines[idx]["end_s"] = out_ttml_lines[idx + 1]["start_s"]

    return out_ttml_lines


def blend_lyrics(
    base: LyricsResult,
    donor: LyricsResult,
    spare_donor: Optional[LyricsResult] = None,
    blend_name: str = "blend",
    donor_label: str = "QQ Music",
    spare_label: str = "",
) -> Optional[LyricsResult]:
    """Combine base lyrics text with timing donor(s) into a unified TTML LyricsResult."""
    if not base or not base.content or not donor or not donor.content:
        return None

    base_lines = parse_lyrics_to_karaoke(base.content, base.format)
    donor_lines = parse_lyrics_to_karaoke(donor.content, donor.format)
    spare_lines = (
        parse_lyrics_to_karaoke(spare_donor.content, spare_donor.format)
        if spare_donor and spare_donor.content
        else None
    )

    ttml_lines = blend_karaoke_lines(base_lines, donor_lines, spare_lines)
    if not ttml_lines:
        return None

    # Check if we obtained word-sync tokens
    has_words = any(len(line.get("tokens", [])) > 1 for line in ttml_lines)
    sync_type = LyricsSyncType.WORD_SYNC if has_words else LyricsSyncType.LINE_SYNC

    via_text = f"Apple Music + {donor_label}"
    if spare_label:
        via_text += f" + {spare_label}"

    ttml_content = build_ttml(
        lines=ttml_lines,
        title=base.title or donor.title,
        artist=base.artist or donor.artist,
        provider=f"Blend ({blend_name})",
        source=via_text,
        attribution={
            "provider": f"Blend ({blend_name})",
            "source": via_text,
            "maker": base.provider_name or "Apple Music",
            "uploader": donor_label,
        },
    )

    return LyricsResult(
        provider_name=blend_name,
        format=LyricsFormat.TTML,
        sync_type=sync_type,
        content=ttml_content,
        title=base.title or donor.title,
        artist=base.artist or donor.artist,
        duration=base.duration or donor.duration,
        match_score=max(base.match_score, donor.match_score),
    )
