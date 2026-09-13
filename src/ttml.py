"""TTML (Timed Text Markup Language) formatting utilities for word-level sync (Apple/AMLL)."""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import Any, Dict, List, Optional


def format_ttml_timestamp(seconds: float) -> str:
    """Format seconds into standard TTML timestamp mm:ss.xxx."""
    if seconds < 0:
        seconds = 0.0
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


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
    """
    tt = ET.Element(
        "tt",
        {
            "xmlns": "http://www.w3.org/ns/ttml",
            "xmlns:itunes": "http://music.apple.com/lyric-ttml-internal",
            "xmlns:ttm": "http://www.w3.org/ns/ttml#metadata",
            "itunes:timing": "Word",
            "xml:lang": "en",
        },
    )
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

    for idx, line in enumerate(lines):
        ts = float(line.get("start_s", 0.0))
        te = float(line.get("end_s", ts))
        if te < ts:
            te = ts

        p = ET.SubElement(
            div,
            "p",
            {
                "begin": format_ttml_timestamp(ts),
                "end": format_ttml_timestamp(te),
                "itunes:key": f"L{idx + 1}",
                "ttm:agent": "v1",
            },
        )

        tokens = line.get("tokens", [])
        if not tokens:
            line_text = line.get("text", "")
            if line_text:
                span = ET.SubElement(
                    p,
                    "span",
                    {
                        "begin": format_ttml_timestamp(ts),
                        "end": format_ttml_timestamp(te),
                    },
                )
                span.text = line_text
            continue

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
                "span",
                {
                    "begin": format_ttml_timestamp(w_start),
                    "end": format_ttml_timestamp(w_end),
                },
            )
            span.text = w_text

    xml_bytes = ET.tostring(tt, encoding="utf-8")
    return minidom.parseString(xml_bytes).toprettyxml(indent="  ")
