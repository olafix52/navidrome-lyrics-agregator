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
from statistics import median
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
                "Gap": str(y.get("Gap") or ""),
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
                "Text": body,
                "Gap": str(y.get("Gap") or "") if last else piece[len(body):],
                "StartTime": at,
                "EndTime": max(end, at),
                "IsPartOfWord": bool(y.get("IsPartOfWord")) if last else (piece == body),
            }
            out.append(made)
            at = end
    return out


def _split_point(text: str, cut: int, stop: int) -> int:
    """Move a token boundary back so leading punctuation of the next word stays with it.

    The raw boundary is the next letter, so for ``said "go"`` the piece would be
    ``said "`` and the quote would light up with the wrong word. If the piece ends in
    whitespace followed by punctuation only, split right after the whitespace instead.
    """
    segment = text[cut:stop]
    m = re.search(r"\s+(\S+)$", segment)
    if m and not _key(m.group(1)) and _key(segment[: m.start()]):
        return cut + m.start(1)
    return stop


def relay_word_timings(
    text: str,
    donor_words: List[KaraokeWord],
    floor: float = RELAY_LIKE_FLOOR,
) -> Optional[List[KaraokeWord]]:
    """Slice `text` along `donor_words` boundaries, preserving pristine text letters.

    Returned word texts carry no surrounding whitespace (see ``_relay_tokens`` for the
    variant that keeps word separators, used to build TTML).
    """
    tokens = _relay_tokens(text, donor_words, floor)
    if tokens is None:
        return None
    return [KaraokeWord(text=w.text.rstrip(), start=w.start, end=w.end) for w in tokens]


def _relay_tokens(
    text: str,
    donor_words: List[KaraokeWord],
    floor: float = RELAY_LIKE_FLOOR,
) -> Optional[List[KaraokeWord]]:
    """Like ``relay_word_timings`` but each token keeps its trailing word separator."""
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
        stop = _split_point(text, cut, stop)
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
            "Gap": piece[len(body):],  # whitespace separating this token from the next word
            "StartTime": float(st) if held is None else held,
            "EndTime": float(en),
            "IsPartOfWord": piece == body,
        })
        held, cut = None, stop

    if not syl_dicts:
        return None

    if cut < len(text):
        syl_dicts[-1]["Text"] += syl_dicts[-1].get("Gap", "") + text[cut:].rstrip()
    syl_dicts[-1]["IsPartOfWord"] = False
    syl_dicts[-1]["Gap"] = ""

    cleaned_syls = _unlump(_unsplit(syl_dicts))

    # Token text carries its own trailing word separator: syllables of one word ("beau",
    # "ti", "ful ") join without spaces and CJK characters stay unspaced, so concatenating
    # the tokens reproduces the base line exactly.
    result_words: List[KaraokeWord] = []
    for d in cleaned_syls:
        result_words.append(
            KaraokeWord(
                text=str(d.get("Text", "")) + str(d.get("Gap", "")),
                start=float(d.get("StartTime", 0.0)),
                end=float(d.get("EndTime", 0.0)),
            )
        )
    return result_words


def _timeline_offset(
    base_lines: List[KaraokeLine],
    other_lines: List[KaraokeLine],
    pairs: Dict[int, int],
) -> float:
    """Median start-time difference (other - base) over paired lines.

    Donors are often timed against a different master (longer intro, other edit), so their
    timeline is shifted relative to the base. The median ignores a few mis-paired lines.
    """
    diffs = [
        other_lines[j].start - base_lines[i].start
        for i, j in pairs.items()
        if base_lines[i].start is not None and other_lines[j].start is not None
    ]
    return round(median(diffs), 3) if diffs else 0.0


def _line_tokens(base_line: KaraokeLine, donor_line: KaraokeLine, shift: float) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Word tokens for ``base_line`` timed by ``donor_line`` (times moved by ``shift`` seconds)."""
    relayed_words: Optional[List[KaraokeWord]] = None
    if donor_line.words:
        relayed_words = _relay_tokens(base_line.text, donor_line.words)

    if relayed_words:
        tokens = [
            {"start_s": w.start + shift if shift else w.start, "end_s": w.end + shift if shift else w.end, "text": w.text}
            for w in relayed_words
        ]
        return tokens[0]["start_s"], tokens[-1]["end_s"], tokens

    start_s = (donor_line.start if donor_line.start is not None else (base_line.start or 0.0)) + shift
    end_s = (donor_line.end + shift) if donor_line.end is not None else (start_s + 4.0)
    return start_s, end_s, [{"start_s": start_s, "end_s": end_s, "text": base_line.text}]


def blend_karaoke_lines(
    base_lines: List[KaraokeLine],
    donor_lines: List[KaraokeLine],
    spare_lines: Optional[List[KaraokeLine]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Reconcile base line sequence with donor(s) word & line timings.

    The output is expressed on the primary donor's timeline and always keeps the base
    line order (the base text is authoritative, timestamps never reorder lyrics).
    """
    if not base_lines or not donor_lines:
        return None

    # Step 1: Pair base lines with primary donor
    pairs = pair_lines(base_lines, donor_lines)
    if not pairs:
        logger.debug("[Blender] Failed to pair base lines with primary donor")
        return None
    donor_offset = _timeline_offset(base_lines, donor_lines, pairs)

    # Step 2: If spare donor provided (triblend/kutriblend), pair base lines with spare
    spare_pairs: Dict[int, int] = {}
    spare_shift = 0.0
    if spare_lines:
        spare_pairs = pair_lines(base_lines, spare_lines) or {}
        if spare_pairs:
            # Move spare timings onto the primary donor's timeline
            spare_shift = round(donor_offset - _timeline_offset(base_lines, spare_lines, spare_pairs), 3)

    out_ttml_lines: List[Dict[str, Any]] = []

    for i, base_line in enumerate(base_lines):
        line_text = base_line.text.strip()
        if not line_text:
            continue

        timed = False
        if i in pairs:
            start_s, end_s, tokens = _line_tokens(base_line, donor_lines[pairs[i]], 0.0)
            timed = True
        elif spare_lines and i in spare_pairs:
            start_s, end_s, tokens = _line_tokens(base_line, spare_lines[spare_pairs[i]], spare_shift)
            timed = True
        else:
            # Line only present in the base: base timing moved onto the donor timeline
            prev_end = out_ttml_lines[-1]["end_s"] if out_ttml_lines else 0.0
            start_s = base_line.start + donor_offset if base_line.start is not None else prev_end
            end_s = base_line.end + donor_offset if base_line.end is not None else (start_s + 4.0)
            tokens = [{"start_s": start_s, "end_s": end_s, "text": base_line.text}]

        out_ttml_lines.append({
            "start_s": start_s,
            "end_s": end_s,
            "agent": base_line.agent or "v1",
            "tokens": tokens,
            "_timed": timed,
        })

    # Keep base order; an untimed line that would still start before its predecessor
    # (inconsistent source timings) is pinned right after it instead of being reordered.
    for idx in range(1, len(out_ttml_lines)):
        prev, line = out_ttml_lines[idx - 1], out_ttml_lines[idx]
        if not line["_timed"] and line["start_s"] < prev["start_s"]:
            duration = max(0.0, line["end_s"] - line["start_s"])
            line["start_s"] = prev["end_s"] if prev["end_s"] >= prev["start_s"] else prev["start_s"]
            line["end_s"] = line["start_s"] + duration
            line["tokens"] = [{"start_s": line["start_s"], "end_s": line["end_s"], "text": line["tokens"][0]["text"]}]

    for idx in range(len(out_ttml_lines) - 1):
        nxt_start = out_ttml_lines[idx + 1]["start_s"]
        line = out_ttml_lines[idx]
        if line["end_s"] > nxt_start >= line["start_s"]:
            line["end_s"] = nxt_start

    for line in out_ttml_lines:
        line.pop("_timed", None)
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
