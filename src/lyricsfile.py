"""Lyricsfile 1.0 Specification implementation, parser, validator, and converter.

Reference: https://github.com/tranxuanthang/lyricsfile
Specification: https://github.com/tranxuanthang/lyricsfile/blob/main/SPECIFICATION.md
"""

import re
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class LyricsfileWord(BaseModel):
    """Word-level timing within a line."""
    text: str
    start_ms: int
    end_ms: Optional[int] = None


class LyricsfileLine(BaseModel):
    """Synchronized line item."""
    text: str
    start_ms: int
    end_ms: Optional[int] = None
    words: Optional[List[LyricsfileWord]] = None


class LyricsfileMetadata(BaseModel):
    """Track and lyrics metadata in Lyricsfile 1.0."""
    title: str
    artist: str
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    language: Optional[str] = None
    instrumental: Optional[bool] = False
    offset_ms: Optional[int] = None


class LyricsfileDocument(BaseModel):
    """Full Lyricsfile 1.0 document."""
    version: str = Field(default="1.0")
    metadata: LyricsfileMetadata
    lines: Optional[List[LyricsfileLine]] = None
    plain: Optional[str] = None

    def to_yaml(self) -> str:
        """Serialize document to standard clean Lyricsfile YAML format."""
        data = self.model_dump(exclude_none=True)
        return yaml.dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def parse_lrc_timestamp_to_ms(ts_str: str) -> Optional[int]:
    """Parse LRC timestamp [mm:ss.xx] or [mm:ss.xxx] to milliseconds."""
    match = re.match(r"^(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?$", ts_str.strip())
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    frac_str = match.group(3) or "0"
    if len(frac_str) == 1:
        ms = int(frac_str) * 100
    elif len(frac_str) == 2:
        ms = int(frac_str) * 10
    else:
        ms = int(frac_str[:3])

    return (minutes * 60 + seconds) * 1000 + ms


def lrc_to_lyricsfile(
    lrc_content: str,
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration_ms: Optional[int] = None,
    language: Optional[str] = None,
) -> LyricsfileDocument:
    """Convert standard line-synced LRC text to a spec-compliant LyricsfileDocument."""
    lines_list: List[LyricsfileLine] = []
    plain_lines: List[str] = []

    lrc_regex = re.compile(r"^\[(\d{1,2}:\d{2}(?:\.\d{1,3})?)\](.*)$")

    for raw_line in lrc_content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = lrc_regex.match(line)
        if match:
            ts_str, text = match.groups()
            ms = parse_lrc_timestamp_to_ms(ts_str)
            text_clean = text.strip()
            if ms is not None and text_clean:
                lines_list.append(LyricsfileLine(text=text_clean, start_ms=ms))
                plain_lines.append(text_clean)
        else:
            # Check for plain text or meta tag
            if not line.startswith("[") or not line.endswith("]"):
                plain_lines.append(line)

    metadata = LyricsfileMetadata(
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        language=language,
        instrumental=False if lines_list else None,
    )

    return LyricsfileDocument(
        version="1.0",
        metadata=metadata,
        lines=lines_list if lines_list else None,
        plain="\n".join(plain_lines) if plain_lines else None,
    )


def validate_lyricsfile_yaml(yaml_str: str) -> Optional[LyricsfileDocument]:
    """Validate whether a YAML string conforms to the Lyricsfile 1.0 specification."""
    try:
        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            return None
        if "version" not in data or "metadata" not in data:
            return None
        return LyricsfileDocument(**data)
    except Exception:
        return None
