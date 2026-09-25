"""Restore explicit words that lyrics databases mask (``f**k``, ``b***h``, ``n-gga`` ...).

Many sources censor explicit lyrics even for explicit releases. A masked token is restored
only when a word from a curated list fits it exactly: same length, every visible letter in
place, one mask character per hidden letter. Everything else is left untouched, so normal
hyphenated words and stutters (``tippy-toes``, ``du-du``) never change.

Because a restored word has exactly the length of its masked form, restoration also works
when a word is split over several TTML word spans (``<span>n-</span><span>ggas</span>``):
the line text is restored as a whole and mapped back to the spans character by character,
leaving all timing untouched.
"""

import re
from typing import Dict, List, Optional, Tuple

from src.models import LyricsFormat

# Priority order matters: when several words fit an asterisk mask (``s**t`` -> shit/slut),
# the first one listed wins.
EXPLICIT_WORDS: Tuple[str, ...] = (
    # f-word
    "fuck", "fucks", "fucked", "fucker", "fuckers", "fucking", "fuckin", "fuckboy", "fuckboys",
    "motherfucker", "motherfuckers", "motherfucking", "motherfuckin",
    "mothafucka", "mothafuckas", "muthafucka", "muthafuckas", "motherfucka", "motherfuckas",
    # n-word
    "nigga", "niggas", "niggaz", "nigger", "niggers",
    # others
    "shit", "shits", "shitty", "shittin", "shitting", "bullshit",
    "bitch", "bitches", "bitchin", "bitching", "bitchass",
    "pussy", "pussies",
    "dick", "dicks", "cock", "cocks", "cunt", "cunts",
    "ass", "asses", "asshole", "assholes", "badass", "dumbass", "jackass",
    "damn", "goddamn", "damned",
    "hoe", "hoes", "whore", "whores", "slut", "sluts", "thot", "thots",
    "piss", "pissed",
    # drugs (censored in "clean" lyrics)
    "weed", "dope", "coke", "crack", "molly", "lean", "marijuana", "percs", "xans",
)

_MASK_CHARS = "*#_-"
_ASTERISK_MASKS = "*#_"
# A candidate token: letters and mask characters, containing at least one of each
_TOKEN = re.compile(r"[A-Za-z*#_\-]*[*#_\-][A-Za-z*#_\-]*")

_BY_LENGTH: Dict[int, List[str]] = {}
for _word in EXPLICIT_WORDS:
    _BY_LENGTH.setdefault(len(_word), []).append(_word)


def _restore_token(token: str) -> Optional[str]:
    """Unmasked form of ``token`` or None if it is not an unambiguous masked explicit word."""
    core = token.strip("-")
    lead = len(token) - len(token.lstrip("-"))
    if not core or not any(c.isalpha() for c in core) or not any(c in _MASK_CHARS for c in core):
        return None
    uses_asterisk = any(c in _ASTERISK_MASKS for c in core)
    if not uses_asterisk and len(core) < 4:
        return None  # "a-s", "o-k": too short to treat a hyphen as a mask

    visible = [(i, c.lower()) for i, c in enumerate(core) if c.isalpha()]
    matches = [
        w for w in _BY_LENGTH.get(len(core), ())
        if all(w[i] == c for i, c in visible)
    ]
    if not matches:
        return None
    if not uses_asterisk and len(matches) > 1:
        return None  # hyphen-only masks must be unambiguous
    word = matches[0]

    letters = [c for c in core if c.isalpha()]
    upper = len(letters) > 1 and all(c.isupper() for c in letters)
    out = []
    for i, c in enumerate(core):
        if c.isalpha():
            out.append(c)
        else:
            out.append(word[i].upper() if upper else word[i])
    return token[:lead] + "".join(out) + token[lead + len(core):]


def uncensor_text(text: str) -> Tuple[str, int]:
    """Restore masked explicit words in plain text. Returns (text, number of words restored).

    The result always has the same length as the input.
    """
    count = 0

    def repl(m: "re.Match[str]") -> str:
        nonlocal count
        restored = _restore_token(m.group(0))
        if restored is None or restored == m.group(0):
            return m.group(0)
        count += 1
        return restored

    return _TOKEN.sub(repl, text), count


_P_BLOCK = re.compile(r"(<p\b[^>]*>)(.*?)(</p>)", re.S)
_TEXT = re.compile(r"[^<>]+")


def _uncensor_markup_block(block: str) -> Tuple[str, int]:
    """Restore words across the text nodes of one TTML <p> element (spans may split words)."""
    # Text nodes lie between '>' (or the block start) and '<' (or the block end); runs that
    # start after '<' are tag names/attributes and are never touched.
    nodes = [
        (m.start(), m.end()) for m in _TEXT.finditer(block)
        if (m.start() == 0 or block[m.start() - 1] == ">") and (m.end() == len(block) or block[m.end()] == "<")
    ]
    if not nodes:
        return block, 0
    joined = "".join(block[s:e] for s, e in nodes)
    restored, count = uncensor_text(joined)
    if not count:
        return block, 0
    out, pos, offset = [], 0, 0
    for s, e in nodes:
        out.append(block[pos:s])
        out.append(restored[offset:offset + (e - s)])
        offset += e - s
        pos = e
    out.append(block[pos:])
    return "".join(out), count


def uncensor_lyrics_content(content: str, fmt: LyricsFormat) -> Tuple[str, int]:
    """Restore masked explicit words in a lyrics document (TTML, LRC, Lyricsfile YAML, TXT)."""
    if not content:
        return content, 0
    if fmt != LyricsFormat.TTML:
        return uncensor_text(content)

    total = 0

    def repl(m: "re.Match[str]") -> str:
        nonlocal total
        inner, count = _uncensor_markup_block(m.group(2))
        total += count
        return m.group(1) + inner + m.group(3)

    return _P_BLOCK.sub(repl, content), total


# ---------------------------------------------------------------------------
# Fully masked words ("****"): restore from another source's uncensored text
# ---------------------------------------------------------------------------
#
# Apple Music derived lyrics replace the n-word with a fixed "****" regardless of its
# length, so the word cannot be inferred from the mask. Other sources (LRCLIB, Musixmatch,
# Genius, ...) often carry the same line uncensored: the masked line is aligned word by
# word with the best matching reference line and only the masked positions are filled in,
# and only with words from EXPLICIT_WORDS.

_WORD = re.compile(r"[A-Za-z0-9'*#]+")
_FULL_MASK = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{0,2}[*#]{2,}[A-Za-z]{0,3}(?:'s)?(?![A-Za-z0-9])")
_POSSESSIVE = "'s"
_ADLIB = re.compile(r"\([^)]*\)")
_EXPLICIT = frozenset(EXPLICIT_WORDS)
LINE_MATCH_FLOOR = 0.6


def has_masked_words(content: str) -> bool:
    """True if the text still contains masked words (e.g. after pattern restoration)."""
    return bool(_FULL_MASK.search(re.sub(r"<[^>]+>", " ", content or "")))


def _norm(word: str) -> str:
    word = word.lower()
    if word.endswith(_POSSESSIVE):
        word = word[: -len(_POSSESSIVE)]  # "****'s" aligns with "nigga" / "nigga's"
    return re.sub(r"[^a-z0-9]", "", word)


def _reference_lines(references: List[str]) -> List[List[str]]:
    lines: List[List[str]] = []
    for ref in references:
        for raw in re.sub(r"<[^>]+>", " ", ref or "").splitlines():
            raw = re.sub(r"\[[^\]]*\]", " ", raw)  # LRC timestamps / tags
            # Transcriptions differ in bracketed ad-libs ("Fuck niggas (fuck you), ...");
            # compare with and without them.
            for variant in {raw, _ADLIB.sub(" ", raw)}:
                words = _WORD.findall(variant)
                if len(words) >= 2 and not any(c in "*#" for w in words for c in w):
                    lines.append(words)
    return lines


def _fill_line(text: str, ref_lines: List[List[str]]) -> Tuple[str, int]:
    """Replace masked words in one line of text using the best aligned reference line."""
    from difflib import SequenceMatcher

    tokens = list(_WORD.finditer(text))
    if not any(_FULL_MASK.fullmatch(t.group(0)) for t in tokens):
        return text, 0
    keys = ["\x00" if _FULL_MASK.fullmatch(t.group(0)) else _norm(t.group(0)) for t in tokens]
    visible = [k for k in keys if k != "\x00"]
    if len(visible) < 1:
        return text, 0

    best_ref, best_ratio = None, 0.0
    for ref in ref_lines:
        ref_keys = [_norm(w) for w in ref]
        # similarity of the unmasked words; masked positions count as one unknown word each
        ratio = SequenceMatcher(None, keys, ref_keys, autojunk=False).ratio()
        if ratio > best_ratio:
            best_ratio, best_ref = ratio, ref
    if best_ref is None or best_ratio < LINE_MATCH_FLOOR:
        return text, 0

    ref_keys = [_norm(w) for w in best_ref]
    replacements: Dict[int, str] = {}
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, keys, ref_keys, autojunk=False).get_opcodes():
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            continue
        for k in range(i2 - i1):
            if keys[i1 + k] != "\x00":
                continue
            candidate = best_ref[j1 + k].strip("'")
            if candidate.lower() not in _EXPLICIT:
                continue
            masked = tokens[i1 + k].group(0)
            suffix = _POSSESSIVE if masked.lower().endswith(_POSSESSIVE) else ""
            masked_core = masked[: len(masked) - len(suffix)]
            if candidate.lower().endswith(_POSSESSIVE):
                candidate = candidate[: -len(_POSSESSIVE)]
            if candidate.lower() not in _EXPLICIT:
                continue
            # visible letters around the mask must agree ("****y" -> "...y")
            lead = re.match(r"[A-Za-z]*", masked_core).group(0)
            tail = re.search(r"[A-Za-z]*$", masked_core).group(0)
            if not candidate.lower().startswith(lead.lower()) or not candidate.lower().endswith(tail.lower()):
                continue
            word = candidate.lower()
            if i1 + k == 0 or masked[:1].isupper():
                word = word[:1].upper() + word[1:]
            replacements[i1 + k] = word + suffix

    if not replacements:
        return text, 0
    out, pos = [], 0
    for idx, tok in enumerate(tokens):
        if idx in replacements:
            out.append(text[pos:tok.start()])
            out.append(replacements[idx])
            pos = tok.end()
    out.append(text[pos:])
    return "".join(out), len(replacements)


def restore_from_references(content: str, fmt: LyricsFormat, references: List[str]) -> Tuple[str, int]:
    """Fill fully masked words in ``content`` from uncensored ``references`` (any format)."""
    ref_lines = _reference_lines(references)
    if not content or not ref_lines:
        return content, 0

    if fmt != LyricsFormat.TTML:
        total = 0
        out_lines = []
        for raw in content.split("\n"):
            # keep LRC timestamps untouched: only the text after them is aligned/replaced
            m = re.match(r"^((?:\s*\[[^\]]*\])*)(.*)$", raw, re.S)
            head, body = m.group(1), m.group(2)
            new_body, count = _fill_line(body, ref_lines)
            total += count
            out_lines.append(head + new_body)
        return "\n".join(out_lines), total

    total = 0

    def repl_block(m: "re.Match[str]") -> str:
        nonlocal total
        block = m.group(2)
        nodes = [
            (t.start(), t.end()) for t in _TEXT.finditer(block)
            if (t.start() == 0 or block[t.start() - 1] == ">") and (t.end() == len(block) or block[t.end()] == "<")
        ]
        if not nodes:
            return m.group(0)
        joined = "".join(block[s:e] for s, e in nodes)
        new_joined, count = _fill_line(joined, ref_lines)
        if not count:
            return m.group(0)
        # Map the changed words back onto the text nodes: masks sit inside a single word span,
        # so node boundaries are recomputed from the unchanged text around each node.
        old_parts = [block[s:e] for s, e in nodes]
        new_parts = _redistribute(old_parts, joined, new_joined)
        if new_parts is None:
            return m.group(0)
        total += count
        out, pos = [], 0
        for (s, e), part in zip(nodes, new_parts):
            out.append(block[pos:s])
            out.append(part)
            pos = e
        out.append(block[pos:])
        return m.group(1) + "".join(out) + m.group(3)

    return _P_BLOCK.sub(repl_block, content), total


def _redistribute(old_parts: List[str], old: str, new: str) -> Optional[List[str]]:
    """Split ``new`` into parts corresponding to ``old_parts`` (only whole-node masks changed)."""
    from difflib import SequenceMatcher

    # Character map old -> new; every changed region must lie within a single part
    bounds, acc = [], 0
    for part in old_parts:
        bounds.append((acc, acc + len(part)))
        acc += len(part)
    edits = [op for op in SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op[0] != "equal"]
    new_parts = list(old_parts)
    # Right-to-left, so earlier offsets inside a part stay valid
    for tag, i1, i2, j1, j2 in reversed(edits):
        owner = next((k for k, (s, e) in enumerate(bounds) if s <= i1 and i2 <= e), None)
        if owner is None:
            return None
        s, _ = bounds[owner]
        part = new_parts[owner]
        new_parts[owner] = part[: i1 - s] + new[j1:j2] + part[i2 - s:]
    return new_parts
