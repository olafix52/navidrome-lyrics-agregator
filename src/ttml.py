import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


ET.register_namespace("", "http://www.w3.org/ns/ttml")
ET.register_namespace("ttm", "http://www.w3.org/ns/ttml#metadata")
ET.register_namespace("itunes", "http://music.apple.com/lyric-ttml-internal")


def format_ttml_timestamp(seconds: float) -> str:
    """Format seconds into standard TTML timestamp mm:ss.xxx."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    m, remainder_ms = divmod(total_ms, 60000)
    s = remainder_ms / 1000.0
    return f"{m:02d}:{s:06.3f}"


def format_ttml_document(tt: ET.Element) -> str:
    """Serialize a TTML ElementTree into an indented XML document.

    Ensures that structural tags (<tt>, <head>, <metadata>, <body>, <div>, <p>)
    are cleanly indented on separate lines, while keeping inline contents inside
    each <p> element (such as lead <span> and <span ttm:role="x-bg">) compact
    without newline/whitespace injection that would split word syllables.
    """
    ET.indent(tt, space="  ")
    for p_elem in tt.iter():
        if p_elem.tag.split("}")[-1].lower() == "p":
            p_elem.text = None
            for desc in p_elem.iter():
                if desc is not p_elem:
                    desc.tail = None
                    if len(desc) > 0:
                        desc.text = None

    xml_bytes = ET.tostring(tt, encoding="utf-8")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{xml_bytes.decode("utf-8")}\n'


def build_ttml(
    lines: List[Dict[str, Any]],
    title: str = "",
    artist: str = "",
) -> str:
    """Build Apple Music / AMLL compliant TTML document from timed lines and syllables.

    Each line dict should contain:
      - 'start_s': float (line start time in seconds)
      - 'end_s': float (line end time in seconds)
      - 'tokens': list of dicts, each with:
          - 'start_s': float
          - 'end_s': float
          - 'text': str
        or
      - 'text': str (fallback if no syllable tokens are present)
      - 'background': optional list of dicts, each with:
          - 'start_s': float
          - 'end_s': float
          - 'tokens': list of dicts (start_s, end_s, text)
    """
    tt = ET.Element(
        "{http://www.w3.org/ns/ttml}tt",
        {
            "{http://music.apple.com/lyric-ttml-internal}timing": "Word",
            "{http://www.w3.org/XML/1998/namespace}lang": "en",
        },
    )
    head = ET.SubElement(tt, "{http://www.w3.org/ns/ttml}head")
    meta = ET.SubElement(head, "{http://www.w3.org/ns/ttml}metadata")
    if title:
        ET.SubElement(meta, "{http://www.w3.org/ns/ttml#metadata}title").text = title
    has_v2 = any("v2" in line.get("agent", "") for line in lines)
    if has_v2:
        import re
        artist_parts = [
            a.strip()
            for a in re.split(r"\s+(?:feat\.?|ft\.?|&|,|/|with)\s+", artist, flags=re.IGNORECASE)
            if a.strip()
        ]
        a1 = artist_parts[0] if artist_parts else artist
        a2 = artist_parts[1] if len(artist_parts) > 1 else ""

        a_el1 = ET.SubElement(
            meta,
            "{http://www.w3.org/ns/ttml#metadata}agent",
            {"type": "person", "{http://www.w3.org/XML/1998/namespace}id": "v1"},
        )
        if a1:
            a_el1.text = a1

        a_el2 = ET.SubElement(
            meta,
            "{http://www.w3.org/ns/ttml#metadata}agent",
            {"type": "person", "{http://www.w3.org/XML/1998/namespace}id": "v2"},
        )
        if a2:
            a_el2.text = a2
    else:
        a_el = ET.SubElement(
            meta,
            "{http://www.w3.org/ns/ttml#metadata}agent",
            {"type": "person", "{http://www.w3.org/XML/1998/namespace}id": "v1"},
        )
        if artist:
            a_el.text = artist

    body = ET.SubElement(tt, "{http://www.w3.org/ns/ttml}body")
    div = ET.SubElement(body, "{http://www.w3.org/ns/ttml}div")

    for idx, line in enumerate(lines):
        ts = float(line.get("start_s", 0.0))
        te = float(line.get("end_s", ts))
        if te < ts:
            te = ts

        agent = line.get("agent", "v1") or "v1"

        p = ET.SubElement(
            div,
            "{http://www.w3.org/ns/ttml}p",
            {
                "begin": format_ttml_timestamp(ts),
                "end": format_ttml_timestamp(te),
                "{http://music.apple.com/lyric-ttml-internal}key": f"L{idx + 1}",
                "{http://www.w3.org/ns/ttml#metadata}agent": agent,
            },
        )

        tokens = line.get("tokens", [])
        bg_groups = line.get("background", [])

        # Build background vocal elements
        bg_elements: List[tuple[float, ET.Element]] = []
        for bg in bg_groups:
            if not isinstance(bg, dict):
                continue
            b_start = float(bg.get("start_s", ts))
            b_end = float(bg.get("end_s", te))
            if b_end < b_start:
                b_end = b_start
            b_tokens = bg.get("tokens", [])
            if not b_tokens:
                continue

            bg_span = ET.Element(
                "{http://www.w3.org/ns/ttml}span",
                {
                    "{http://www.w3.org/ns/ttml#metadata}role": "x-bg",
                    "begin": format_ttml_timestamp(b_start),
                    "end": format_ttml_timestamp(b_end),
                },
            )
            for b_tok in b_tokens:
                bw_start = float(b_tok.get("start_s", b_start))
                bw_end = float(b_tok.get("end_s", b_end))
                if bw_end < bw_start:
                    bw_end = bw_start
                bw_text = b_tok.get("text", "")
                if not bw_text and bw_start == bw_end:
                    continue
                bs_el = ET.SubElement(
                    bg_span,
                    "{http://www.w3.org/ns/ttml}span",
                    {
                        "begin": format_ttml_timestamp(bw_start),
                        "end": format_ttml_timestamp(bw_end),
                    },
                )
                bs_el.text = bw_text

            bg_elements.append((b_start, bg_span))

        # Apple Music guideline: if background vocal begins before lead vocal, place x-bg first
        for b_start, bg_el in bg_elements:
            if b_start < ts:
                p.append(bg_el)

        if not tokens:
            line_text = line.get("text", "")
            if line_text:
                span = ET.SubElement(
                    p,
                    "{http://www.w3.org/ns/ttml}span",
                    {
                        "begin": format_ttml_timestamp(ts),
                        "end": format_ttml_timestamp(te),
                    },
                )
                span.text = line_text
        else:
            for token in tokens:
                w_start = float(token.get("start_s", ts))
                w_end = float(token.get("end_s", te))
                if w_end < w_start:
                    w_end = w_start
                w_text = token.get("text", "")
                if not w_text and w_start == w_end:
                    continue

                span = ET.SubElement(
                    p,
                    "{http://www.w3.org/ns/ttml}span",
                    {
                        "begin": format_ttml_timestamp(w_start),
                        "end": format_ttml_timestamp(w_end),
                    },
                )
                span.text = w_text

        # Append background vocals that begin at or after main vocal start
        for b_start, bg_el in bg_elements:
            if b_start >= ts:
                p.append(bg_el)

    return format_ttml_document(tt)
