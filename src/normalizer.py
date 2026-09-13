"""Metadata normalization, string sanitization, and duration verification algorithms."""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional, Tuple


def safe_float(value, default: float = 0.0) -> Optional[float]:
    """Safely convert a value to float, returning default for None/invalid.

    Handles the common pattern where JSON APIs return ``"duration": null``
    and ``dict.get("duration", 0)`` yields ``None`` (because the key exists).
    ``float(None)`` raises ``TypeError``; this helper avoids that.

    Returns ``None`` when the converted value equals 0 and default is 0,
    preserving the ``or None`` idiom used throughout the provider code.
    """
    if value is None:
        return default or None
    try:
        result = float(value)
        return result or (default or None)
    except (TypeError, ValueError):
        return default or None


# Regex patterns for stripping metadata junk from track titles
TITLE_CLEANUP_PATTERNS = [
    # Feat / Featuring
    re.compile(r"[\(\[\{]\s*(?:feat|ft|featuring)\.?\s+[^\)\]\}]+[\)\]\}]", re.IGNORECASE),
    re.compile(r"\s+(?:feat|ft|featuring)\.?\s+.+$", re.IGNORECASE),
    # Remastered / Remaster / Edition
    re.compile(r"[\(\[\{]\s*(?:\d{4}\s+)?(?:digital\s+)?remaster(?:ed)?(?:.*?)[\]\}\)]", re.IGNORECASE),
    re.compile(r"-\s*(?:\d{4}\s+)?(?:digital\s+)?remaster(?:ed)?.*$", re.IGNORECASE),
    re.compile(r"[\(\[\{]\s*(?:deluxe|expanded|anniversary|collector(?:'s)?|special|limited)\s+(?:edition|version|release)[\]\}\)]", re.IGNORECASE),
    re.compile(r"-\s*(?:deluxe|expanded|anniversary|collector(?:'s)?|special|limited)\s+(?:edition|version|release).*$", re.IGNORECASE),
    # Audio / Video / Quality tags (e.g. [Official Audio], (HD 4K), (Lyric Video))
    re.compile(r"[\(\[\{]\s*(?:official\s+)?(?:audio|video|music\s+video|lyric\s+video|visualizer|hd|hq|4k|mv|uhd)(?:[\s/]+(?:audio|video|hd|hq|4k|mv|uhd|\d+p))*\s*[\)\]\}]", re.IGNORECASE),
    # Live / Acoustic / Mono / Stereo / Bonus
    re.compile(r"[\(\[\{]\s*(?:live(?:\s+(?:at|in|from).*)?|acoustic|mono|stereo|bonus\s+track|explicit|clean|radio\s+edit|club\s+mix|extended\s+mix)[\]\}\)]", re.IGNORECASE),
    re.compile(r"-\s*(?:live(?:\s+(?:at|in|from).*)?|radio\s+edit|acoustic).*$", re.IGNORECASE),
    # Trailing year markers e.g. (2011) or [2020]
    re.compile(r"[\(\[\{]\s*(?:19|20)\d{2}\s*[\)\]\}]$", re.IGNORECASE),
]

# Track number prefix pattern e.g. "01 - ", "01. ", "12 - ", "01 " (with leading zero or separator)
TRACK_NUM_PREFIX_PATTERN = re.compile(r"^\s*(?:\d{1,3}\s*[\.\-_]\s*|\b0\d{1,2}\s+)")


def normalize_unicode(text: str) -> str:
    """Normalize text using NFKC normalization and clean odd whitespace."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    # Replace non-breaking spaces and other special spaces with standard space
    normalized = re.sub(r"[\u00A0\u1680\u180E\u2000-\u200B\u202F\u205F\u3000\uFEFF]", " ", normalized)
    # Normalize multiple whitespace characters to a single space
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def clean_title(title: str) -> str:
    """Clean track title by removing annotations, remasters, bonus tags, and features."""
    if not title:
        return ""

    text = normalize_unicode(title)

    # Strip track number prefix if present
    text = TRACK_NUM_PREFIX_PATTERN.sub("", text)

    # Apply cleanup regex patterns
    for pattern in TITLE_CLEANUP_PATTERNS:
        text = pattern.sub("", text)

    # Clean double spaces and punctuation left over
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -_–—/|[](){}")
    return text


def clean_artist(artist: str) -> str:
    """Clean artist name by removing secondary features and standardizing separators."""
    if not artist:
        return ""

    text = normalize_unicode(artist)

    # Strip feat / ft from artist name
    text = re.sub(r"\s+[\(\[\{]?(?:feat|ft|featuring)\.?\s+.*$", "", text, flags=re.IGNORECASE)

    # Replace slash/semicolon multi-artist delimiters with standard comma if needed
    text = re.sub(r"\s*[/;]\s*", ", ", text)

    # Clean double spaces
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -_–—/|[](){}")


# Common multi-artist delimiters
MULTI_ARTIST_DELIMITERS = [",", " & ", " and ", " x ", " X ", " / ", " feat. ", " ft. ", "、"]

# Bidirectional known artist aliases and transliterations
KNOWN_ARTIST_ALIASES = {
    "kanye west": {"ye", "侃爷", "坎耶·维斯特", "坎耶维斯特"},
    "ye": {"kanye west", "侃爷", "坎耶·维斯特", "坎耶维斯特"},
    "侃爷": {"kanye west", "ye"},
    "jay chou": {"周杰伦"},
    "周杰伦": {"jay chou"},
    "eason chan": {"陈奕迅"},
    "陈奕迅": {"eason chan"},
    "taylor swift": {"霉霉", "泰勒·斯威夫特"},
    "ed sheeran": {"黄老板", "艾德·希兰"},
    "eminem": {"姆爷", "阿姆"},
    "billie eilish": {"碧梨"},
    "justin bieber": {"比伯", "贾斯汀·比伯"},
    "bruno mars": {"火星哥"},
    "ariana grande": {"a妹"},
    "lana del rey": {"打雷姐"},
    "michael jackson": {"迈克尔·杰克逊", "mj"},
}


def extract_primary_artist(artist: str) -> str:
    """Extract primary artist from a composite artist string (e.g., 'Queen & David Bowie' -> 'Queen')."""
    cleaned = clean_artist(artist)
    primary = cleaned
    for delim in MULTI_ARTIST_DELIMITERS:
        if delim in primary:
            parts = primary.split(delim)
            if parts and parts[0].strip():
                primary = parts[0].strip()
                break
    return primary


def calculate_artist_similarity(target_artist: str, candidate_artist: str) -> float:
    """Calculate normalized similarity between target and candidate artist names.
    
    Supports composite artist splitting, primary artists, and known artist aliases.
    """
    if not target_artist or not candidate_artist:
        return 0.0

    t_clean = clean_artist(target_artist).lower()
    c_clean = clean_artist(candidate_artist).lower()

    if t_clean == c_clean:
        return 1.0

    # 1. Check known aliases
    t_aliases = KNOWN_ARTIST_ALIASES.get(t_clean, set())
    for alias in t_aliases:
        if alias in c_clean:
            return 1.0

    c_aliases = KNOWN_ARTIST_ALIASES.get(c_clean, set())
    for alias in c_aliases:
        if alias in t_clean:
            return 1.0

    # 2. Direct string similarity
    direct_sim = calculate_string_similarity(t_clean, c_clean)

    # 3. Primary artist comparison
    t_prim = extract_primary_artist(target_artist).lower()
    c_prim = extract_primary_artist(candidate_artist).lower()
    prim_sim = calculate_string_similarity(t_prim, c_prim)

    # 4. Split candidate sub-artists (e.g. "Ye (侃爷)、PARTYNEXTDOOR" -> ["Ye", "侃爷", "PARTYNEXTDOOR"])
    sub_sim = 0.0
    split_pattern = r"[,/&、]|(?:\s+(?:feat\.?|ft\.?|and|x|X)\s+)"
    candidate_parts = re.split(split_pattern, c_clean)
    for part in candidate_parts:
        part = part.strip()
        if not part:
            continue
        # Strip or extract parenthetical name: e.g. "ye (侃爷)"
        sub_names = [part]
        paren_match = re.search(r"^(.+?)\s*[\(\[（](.+?)[\)\]）]$", part)
        if paren_match:
            sub_names.extend([paren_match.group(1).strip(), paren_match.group(2).strip()])

        for name in sub_names:
            if not name:
                continue
            if name == t_clean or name in t_aliases:
                return 1.0
            s = calculate_string_similarity(t_clean, name)
            if s > sub_sim:
                sub_sim = s

    return max(direct_sim, prim_sim, sub_sim)


def is_duration_matching(
    expected_duration: float,
    actual_duration: Optional[float],
    tolerance_seconds: float = 2.5,
) -> bool:
    """Check if actual duration is within tolerance of expected duration.
    
    If actual_duration is None or 0, returns True to avoid rejecting providers that don't report duration.
    """
    if actual_duration is None or actual_duration <= 0 or expected_duration <= 0:
        return True

    diff = abs(expected_duration - actual_duration)
    return diff <= tolerance_seconds


def calculate_string_similarity(str1: str, str2: str) -> float:
    """Calculate normalized string similarity score between 0.0 and 1.0."""
    if not str1 or not str2:
        return 0.0

    s1 = normalize_unicode(str1).lower()
    s2 = normalize_unicode(str2).lower()

    if s1 == s2:
        return 1.0

    # Clean punctuation and extra spaces
    s1_clean = re.sub(r"[^\w\s]", "", s1)
    s2_clean = re.sub(r"[^\w\s]", "", s2)

    if s1_clean == s2_clean:
        return 0.98

    # Sequence matcher ratio
    seq_ratio = SequenceMatcher(None, s1_clean, s2_clean).ratio()

    # Token set ratio (handles reordered words and partial overlap)
    tokens1 = set(s1_clean.split())
    tokens2 = set(s2_clean.split())
    if tokens1 and tokens2:
        intersection = tokens1.intersection(tokens2)
        union = tokens1.union(tokens2)
        token_ratio = len(intersection) / len(union) if union else 0.0
        # Partial containment ratio (e.g. "Song (Remastered)" containing "Song")
        min_len = min(len(tokens1), len(tokens2))
        containment_ratio = (len(intersection) / min_len) * 0.85 if min_len else 0.0
    else:
        token_ratio = 0.0
        containment_ratio = 0.0

    return max(seq_ratio, token_ratio, containment_ratio)


def calculate_candidate_score(
    target_title: str,
    target_artist: str,
    candidate_title: str,
    candidate_artist: str,
) -> float:
    """Calculate weighted candidate similarity score.
    
    Strictly rejects candidates when:
    - Song title similarity is too low (< 0.40)
    - Target artist is provided, but candidate artist has no match / overlap (< 0.35)
    """
    clean_t_title = clean_title(target_title)
    clean_c_title = clean_title(candidate_title)
    t_sim = calculate_string_similarity(clean_t_title, clean_c_title)

    # Completely different song title
    if t_sim < 0.40:
        return 0.0

    target_has_artist = bool(target_artist and clean_artist(target_artist).strip())
    cand_has_artist = bool(candidate_artist and clean_artist(candidate_artist).strip())

    if not target_has_artist:
        return t_sim

    if not cand_has_artist:
        # Candidate doesn't specify artist, accept based on title with confidence penalty
        return t_sim * 0.70

    a_sim = calculate_artist_similarity(target_artist, candidate_artist)

    # Completely different artist -> reject candidate
    if a_sim < 0.35:
        return 0.0

    return (t_sim * 0.60) + (a_sim * 0.40)


def verify_track_match(
    expected_title: str,
    expected_artist: str,
    found_title: Optional[str],
    found_artist: Optional[str],
    expected_duration: float,
    found_duration: Optional[float],
    tolerance_seconds: float = 2.5,
    min_similarity: float = 0.75,
) -> Tuple[bool, float, str]:
    """Verify if a found lyric result matches the audio track metadata.
    
    Returns:
        (is_match, match_score, reason)
    """
    # Duration check
    if not is_duration_matching(expected_duration, found_duration, tolerance_seconds):
        diff = abs(expected_duration - (found_duration or 0))
        return False, 0.0, f"Duration mismatch (diff {diff:.2f}s > {tolerance_seconds}s)"

    # If title/artist not reported by provider, assume valid if duration matched
    if not found_title and not found_artist:
        return True, 0.8, "No metadata reported by provider, accepted based on duration"

    # Title similarity
    clean_exp_title = clean_title(expected_title)
    clean_fnd_title = clean_title(found_title or "")
    title_sim = calculate_string_similarity(clean_exp_title, clean_fnd_title)

    # Artist similarity
    clean_exp_artist = clean_artist(expected_artist)
    clean_fnd_artist = clean_artist(found_artist or "")
    if clean_exp_artist and clean_fnd_artist:
        artist_sim = calculate_artist_similarity(clean_exp_artist, clean_fnd_artist)
    else:
        artist_sim = 0.8

    # Weighted overall score (Title 60%, Artist 40%)
    overall_score = (title_sim * 0.6) + (artist_sim * 0.4)

    if title_sim < (min_similarity * 0.85):
        return False, overall_score, f"Title similarity too low ({title_sim:.2f} < {min_similarity * 0.85:.2f})"

    if clean_exp_artist and clean_fnd_artist and artist_sim < 0.35:
        return False, overall_score, f"Artist mismatch ({clean_fnd_artist} vs {clean_exp_artist})"

    if overall_score < min_similarity:
        return False, overall_score, f"Overall similarity too low ({overall_score:.2f} < {min_similarity:.2f})"

    return True, overall_score, f"Matched (score: {overall_score:.2f})"
