import re
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
    provider: Optional[str] = None,
    source: Optional[str] = None,
    attribution: Optional[Dict[str, Any]] = None,
    songwriters: Optional[List[str]] = None,
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

    # Build copyright / attribution string (Spicy Lyrics attribution compliance)
    copyright_parts: List[str] = []
    if provider:
        p_name = "Spicy Lyrics" if provider.lower() == "spicylyrics" else provider
        if source and source.lower() not in ("spicy_lyrics", "unknown"):
            copyright_parts.append(f"Lyrics provided by {p_name} ({source.replace('_', ' ').title()})")
        else:
            copyright_parts.append(f"Lyrics provided by {p_name}")
    elif source:
        copyright_parts.append(f"Source: {source.replace('_', ' ').title()}")

    is_community = not source or source.lower() in ("spicy_lyrics", "unknown")
    if is_community and attribution and isinstance(attribution, dict):
        maker = attribution.get("Maker")
        uploader = attribution.get("Uploader")
        if isinstance(maker, dict) and maker.get("username"):
            m_text = maker["username"]
            if maker.get("url"):
                m_text += f" ({maker['url']})"
            copyright_parts.append(f"Synced by {m_text}")
        elif isinstance(maker, str) and maker.strip():
            copyright_parts.append(f"Synced by {maker.strip()}")

        if isinstance(uploader, dict) and uploader.get("username"):
            u_text = uploader["username"]
            if uploader.get("url"):
                u_text += f" ({uploader['url']})"
            copyright_parts.append(f"Uploaded by {u_text}")
        elif isinstance(uploader, str) and uploader.strip():
            copyright_parts.append(f"Uploaded by {uploader.strip()}")

    if copyright_parts:
        c_el = ET.SubElement(meta, "{http://www.w3.org/ns/ttml#metadata}copyright")
        c_el.text = " · ".join(copyright_parts)

    # Structured attribution node for player extraction
    if provider or source or (is_community and attribution):
        attr_attribs: Dict[str, str] = {}
        if provider:
            attr_attribs["provider"] = "Spicy Lyrics" if provider.lower() == "spicylyrics" else provider
        if source:
            attr_attribs["source"] = source
        attr_el = ET.SubElement(meta, "attribution", attr_attribs)

        if is_community and attribution and isinstance(attribution, dict):
            maker = attribution.get("Maker")
            if isinstance(maker, dict) and maker.get("username"):
                m_el = ET.SubElement(attr_el, "maker")
                m_el.set("username", str(maker["username"]))
                if maker.get("id"):
                    m_el.set("id", str(maker["id"]))
                if maker.get("url"):
                    m_el.set("url", str(maker["url"]))
            elif isinstance(maker, str) and maker.strip():
                m_el = ET.SubElement(attr_el, "maker")
                m_el.set("username", maker.strip())

            uploader = attribution.get("Uploader")
            if isinstance(uploader, dict) and uploader.get("username"):
                u_el = ET.SubElement(attr_el, "uploader")
                u_el.set("username", str(uploader["username"]))
                if uploader.get("id"):
                    u_el.set("id", str(uploader["id"]))
                if uploader.get("url"):
                    u_el.set("url", str(uploader["url"]))
            elif isinstance(uploader, str) and uploader.strip():
                u_el = ET.SubElement(attr_el, "uploader")
                u_el.set("username", uploader.strip())

    # iTunes metadata (songwriters)
    itunes_meta = ET.SubElement(meta, "{http://music.apple.com/lyric-ttml-internal}iTunesMetadata")
    sw_container = ET.SubElement(itunes_meta, "songwriters")
    if songwriters and isinstance(songwriters, list):
        for sw in songwriters:
            if sw and str(sw).strip():
                sw_el = ET.SubElement(sw_container, "songwriter")
                sw_el.text = str(sw).strip()
    elif artist:
        sw_el = ET.SubElement(sw_container, "songwriter")
        sw_el.text = artist

    has_v2 = any("v2" in line.get("agent", "") for line in lines)
    if has_v2:
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
