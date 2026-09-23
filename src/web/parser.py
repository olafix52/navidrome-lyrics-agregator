"""Karaoke parsing utilities converting TTML, YAML, LRC, and TXT into structured karaoke timing models."""

import html
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel

from src.models import LyricsFormat


class KaraokeWord(BaseModel):
    """Word or syllable level timing in seconds."""
    text: str
    start: float
    end: float


class KaraokeLine(BaseModel):
    """Line level timing in seconds with optional word-level breakdown."""
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    words: Optional[List[KaraokeWord]] = None
    agent: Optional[str] = "v1"


def parse_time_str_to_seconds(time_str: str) -> Optional[float]:
    """Parse various timestamp formats (e.g. '01:23.456', '00:01:23.456', '12.34s', '1234ms') into seconds."""
    if not time_str:
        return None
    s = time_str.strip().lower()

    if s.endswith("ms"):
        try:
            return float(s[:-2]) / 1000.0
        except ValueError:
            return None
    if s.endswith("s"):
        s = s[:-1]

    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            m = int(parts[0])
            sec = float(parts[1])
            return m * 60.0 + sec
        elif len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            sec = float(parts[2])
            return h * 3600.0 + m * 60.0 + sec
    except ValueError:
        return None
    return None


def _get_element_attr(el: ET.Element, attr_name: str) -> str:
    """Retrieve attribute value ignoring any XML namespace prefix."""
    attr_lower = attr_name.lower()
    for k, v in el.attrib.items():
        if k.split("}")[-1].lower() == attr_lower:
            return v
    return ""


def parse_ttml_to_karaoke(ttml_content: str) -> List[KaraokeLine]:
    """Parse TTML XML document into structured KaraokeLine objects."""
    lines: List[KaraokeLine] = []
    if not ttml_content or "<tt" not in ttml_content:
        return lines

    root: Optional[ET.Element] = None
    try:
        # Standard XML parse (preserves all declared namespaces)
        root = ET.fromstring(ttml_content)
    except Exception:
        try:
            # If standard parse fails (e.g. undeclared namespace prefix),
            # safely strip namespaces and namespace prefixes from tags and attributes
            t = re.sub(r'\sxmlns(?::[a-zA-Z0-9_-]+)?="[^"]*"', '', ttml_content)
            t = re.sub(r'\s[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+=)', r' \1', t)
            t = re.sub(r'<(/)?([a-zA-Z0-9_-]+):([a-zA-Z0-9_-]+)', r'<\1\3', t)
            root = ET.fromstring(t)
        except Exception:
            root = None

    if root is not None:
        p_elements = [el for el in root.iter() if el.tag.split("}")[-1].lower() == "p"]
        for p in p_elements:
            start_s = parse_time_str_to_seconds(_get_element_attr(p, "begin"))
            end_s = parse_time_str_to_seconds(_get_element_attr(p, "end"))
            agent = _get_element_attr(p, "agent") or "v1"

            # Gather line text excluding auxiliary roles (translation, romanization)
            text_pieces = []
            if p.text:
                text_pieces.append(p.text)
            for child in p:
                c_tag = child.tag.split("}")[-1].lower()
                c_role = _get_element_attr(child, "role")
                if c_tag == "span" and c_role in ("x-translation", "x-roman"):
                    if child.tail:
                        text_pieces.append(child.tail)
                    continue
                text_pieces.append("".join(child.itertext()))
                if child.tail:
                    text_pieces.append(child.tail)

            raw_line_text = "".join(html.unescape(t) for t in text_pieces)
            line_text = re.sub(r'\s+', ' ', raw_line_text).strip()

            words: List[KaraokeWord] = []
            timed_spans = [
                s for s in p.iter()
                if s.tag.split("}")[-1].lower() == "span"
                and _get_element_attr(s, "role") not in ("x-translation", "x-roman")
                and _get_element_attr(s, "begin")
                and not any(child.tag.split("}")[-1].lower() == "span" for child in s)
            ]

            if timed_spans:
                for idx, span in enumerate(timed_spans):
                    raw_w_text = html.unescape("".join(span.itertext()))
                    w_text = raw_w_text.strip()
                    if not w_text:
                        continue
                    w_start = parse_time_str_to_seconds(_get_element_attr(span, "begin")) or start_s
                    w_end = parse_time_str_to_seconds(_get_element_attr(span, "end")) or end_s

                    has_space_after = (
                        raw_w_text.endswith(" ")
                        or (span.tail and any(c.isspace() for c in span.tail))
                    )
                    if has_space_after and idx < len(timed_spans) - 1:
                        w_text += " "

                    if w_start is not None and w_end is not None:
                        words.append(KaraokeWord(text=w_text, start=w_start, end=w_end))

            if line_text:
                lines.append(KaraokeLine(
                    text=line_text,
                    start=start_s,
                    end=end_s,
                    words=words if words else None,
                    agent=agent,
                ))
    else:
        # Fallback regex parsing if XML is completely malformed
        p_matches = re.findall(r'<p\b([^>]*)>(.*?)</p>', ttml_content, re.DOTALL | re.IGNORECASE)
        for attrs, body in p_matches:
            begin_m = re.search(r'begin="([^"]+)"', attrs)
            end_m = re.search(r'end="([^"]+)"', attrs)
            agent_m = re.search(r'(?:ttm:)?agent="([^"]+)"', attrs)
            start_s = parse_time_str_to_seconds(begin_m.group(1)) if begin_m else None
            end_s = parse_time_str_to_seconds(end_m.group(1)) if end_m else None
            agent = agent_m.group(1) if agent_m else "v1"

            raw_text = re.sub(r'<[^>]+>', ' ', body)
            line_text = re.sub(r'\s+', ' ', html.unescape(raw_text)).strip()

            spans = re.findall(r'<span\b([^>]*)>(.*?)</span>', body, re.DOTALL | re.IGNORECASE)
            words: List[KaraokeWord] = []
            if spans:
                for idx, (s_attrs, s_text) in enumerate(spans):
                    s_begin = re.search(r'begin="([^"]+)"', s_attrs)
                    s_end = re.search(r'end="([^"]+)"', s_attrs)
                    w_start = parse_time_str_to_seconds(s_begin.group(1)) if s_begin else start_s
                    w_end = parse_time_str_to_seconds(s_end.group(1)) if s_end else end_s
                    clean_w = re.sub(r'<[^>]+>', '', s_text)
                    w_text = html.unescape(clean_w).strip()
                    if w_text and w_start is not None and w_end is not None:
                        if idx < len(spans) - 1:
                            w_text += " "
                        words.append(KaraokeWord(text=w_text, start=w_start, end=w_end))

            if line_text:
                lines.append(KaraokeLine(
                    text=line_text,
                    start=start_s,
                    end=end_s,
                    words=words if words else None,
                    agent=agent,
                ))

    # Auto-fill missing line ends based on next line starts
    for i in range(len(lines) - 1):
        if lines[i].end is None and lines[i + 1].start is not None:
            lines[i].end = lines[i + 1].start
    if lines and lines[-1].end is None and lines[-1].start is not None:
        lines[-1].end = lines[-1].start + 4.0

    return lines


def parse_yaml_to_karaoke(yaml_content: str) -> List[KaraokeLine]:
    """Parse Lyricsfile 1.0 YAML content into structured KaraokeLine objects."""
    lines: List[KaraokeLine] = []
    try:
        data = yaml.safe_load(yaml_content)
        if not isinstance(data, dict):
            return lines

        raw_lines = data.get("lines")
        if not raw_lines or not isinstance(raw_lines, list):
            plain = data.get("plain", "")
            for pl in plain.splitlines():
                if pl.strip():
                    lines.append(KaraokeLine(text=pl.strip()))
            return lines

        for item in raw_lines:
            text = item.get("text", "").strip()
            start_ms = item.get("start_ms")
            end_ms = item.get("end_ms")

            start_s = (start_ms / 1000.0) if start_ms is not None else None
            end_s = (end_ms / 1000.0) if end_ms is not None else None

            words_data = item.get("words")
            words: Optional[List[KaraokeWord]] = None
            if words_data and isinstance(words_data, list):
                words = []
                for w in words_data:
                    w_text = w.get("text", "")
                    w_start_ms = w.get("start_ms")
                    w_end_ms = w.get("end_ms")
                    if w_start_ms is not None and w_end_ms is not None:
                        words.append(KaraokeWord(
                            text=w_text,
                            start=w_start_ms / 1000.0,
                            end=w_end_ms / 1000.0,
                        ))

            lines.append(KaraokeLine(
                text=text,
                start=start_s,
                end=end_s,
                words=words if words else None,
            ))

        # Fill line ends if missing
        for i in range(len(lines) - 1):
            if lines[i].end is None and lines[i + 1].start is not None:
                lines[i].end = lines[i + 1].start
        if lines and lines[-1].end is None and lines[-1].start is not None:
            lines[-1].end = lines[-1].start + 4.0

    except Exception:
        pass

    return lines


def parse_lrc_to_karaoke(lrc_content: str) -> List[KaraokeLine]:
    """Parse standard or enhanced word-synced LRC file into structured KaraokeLine objects."""
    lines: List[KaraokeLine] = []
    ts_pattern = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")
    word_ts_pattern = re.compile(r"<(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?>([^<]*)")

    raw_lines = lrc_content.splitlines()
    timed_entries: List[Dict[str, Any]] = []

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue

        matches = list(ts_pattern.finditer(line))
        if not matches:
            continue

        # Extract text following the timestamps
        last_match = matches[-1]
        content_after = line[last_match.end():].strip()

        # Check for enhanced word timestamps: e.g. <00:12.34>word1 <00:13.00>word2
        words: Optional[List[KaraokeWord]] = None
        word_matches = list(word_ts_pattern.finditer(content_after))
        if word_matches:
            words = []
            for i, wm in enumerate(word_matches):
                m, s, ms = wm.group(1), wm.group(2), wm.group(3) or "0"
                ms = ms.ljust(3, "0")[:3]
                w_start = int(m) * 60.0 + int(s) + int(ms) / 1000.0
                w_text = wm.group(4)
                # Word end is next word start or estimated
                if i + 1 < len(word_matches):
                    nm, ns, nms = word_matches[i + 1].group(1), word_matches[i + 1].group(2), word_matches[i + 1].group(3) or "0"
                    nms = nms.ljust(3, "0")[:3]
                    w_end = int(nm) * 60.0 + int(ns) + int(nms) / 1000.0
                else:
                    w_end = w_start + 0.5
                words.append(KaraokeWord(text=w_text, start=w_start, end=w_end))
            content_after = "".join(w.text for w in words).strip()

        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            millis = int((m.group(3) or "0").ljust(3, "0")[:3])
            start_seconds = mins * 60.0 + secs + (millis / 1000.0)

            timed_entries.append({
                "start": start_seconds,
                "text": content_after,
                "words": words,
            })

    # Sort lines chronologically
    timed_entries.sort(key=lambda x: x["start"])

    for i, entry in enumerate(timed_entries):
        start_s = entry["start"]
        text = entry["text"]
        words = entry["words"]

        # Determine end time
        if i + 1 < len(timed_entries):
            end_s = timed_entries[i + 1]["start"]
        else:
            end_s = start_s + 4.0

        if text:  # Avoid empty lines
            lines.append(KaraokeLine(
                text=text,
                start=start_s,
                end=end_s,
                words=words,
            ))

    return lines


def parse_lyrics_to_karaoke(content: str, fmt: LyricsFormat) -> List[KaraokeLine]:
    """Universal parser converting any supported lyrics content into KaraokeLine timing structures."""
    if not content or not content.strip():
        return []

    if fmt == LyricsFormat.TTML:
        res = parse_ttml_to_karaoke(content)
        if res:
            return res
    elif fmt == LyricsFormat.YAML:
        res = parse_yaml_to_karaoke(content)
        if res:
            return res
    elif fmt == LyricsFormat.LRC:
        res = parse_lrc_to_karaoke(content)
        if res:
            return res

    # Fallback to plain text split by lines
    lines: List[KaraokeLine] = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            lines.append(KaraokeLine(text=line))
    return lines


def format_seconds_to_ttml_time(seconds: Optional[float]) -> str:
    """Format seconds into TTML time string 'mm:ss.xxx'."""
    if seconds is None or seconds < 0:
        return "00:00.000"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


def karaoke_to_ttml(lines: List[KaraokeLine], title: str = "", artist: str = "") -> str:
    """Convert a list of KaraokeLine objects into a standard TTML XML document compatible with Apple Music / ToxiPlays."""

    title_escaped = html.escape(title or "Unknown Track")
    artist_escaped = html.escape(artist or "")

    has_v2 = any("v2" in (line.agent or "") for line in lines)
    if has_v2:
        artist_parts = [
            a.strip()
            for a in re.split(r"\s+(?:feat\.?|ft\.?|&|,|/|with)\s+", artist, flags=re.IGNORECASE)
            if a.strip()
        ]
        a1 = html.escape(artist_parts[0]) if artist_parts else artist_escaped
        a2 = html.escape(artist_parts[1]) if len(artist_parts) > 1 else ""
        agent_metadata = f"""      <ttm:agent type="person" xml:id="v1">{a1}</ttm:agent>
      <ttm:agent type="person" xml:id="v2">{a2}</ttm:agent>"""
    else:
        agent_metadata = f"""      <ttm:agent type="person" xml:id="v1">{artist_escaped}</ttm:agent>"""

    p_blocks: List[str] = []
    for line in lines:
        if not line.text.strip():
            continue

        start_s = line.start if line.start is not None else 0.0
        end_s = line.end if line.end is not None else (start_s + 4.0)

        p_begin = format_seconds_to_ttml_time(start_s)
        p_end = format_seconds_to_ttml_time(end_s)
        agent_attr = f' ttm:agent="{line.agent or "v1"}"'

        if line.words and len(line.words) > 0:
            spans = []
            for w in line.words:
                w_begin = format_seconds_to_ttml_time(w.start)
                w_end = format_seconds_to_ttml_time(w.end)
                w_text = html.escape(w.text)
                spans.append(f'<span begin="{w_begin}" end="{w_end}">{w_text}</span>')
            p_content = " ".join(spans)
        else:
            line_escaped = html.escape(line.text)
            p_content = f'<span begin="{p_begin}" end="{p_end}">{line_escaped}</span>'

        p_blocks.append(f'      <p begin="{p_begin}" end="{p_end}"{agent_attr}>\n        {p_content}\n      </p>')

    body_content = "\n".join(p_blocks)

    songwriter_xml = f"""        <songwriters>
          <songwriter>{artist_escaped}</songwriter>
        </songwriters>""" if artist_escaped else ""

    ttml_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal">
  <head>
    <metadata>
      <ttm:title>{title_escaped}</ttm:title>
{agent_metadata}
      <itunes:iTunesMetadata>
{songwriter_xml}
      </itunes:iTunesMetadata>
    </metadata>
  </head>
  <body>
    <div>
{body_content}
    </div>
  </body>
</tt>"""
    return ttml_xml

