"""Data models and representations for Navidrome Lyrics Aggregator."""

import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, ConfigDict


class LyricsFormat(str, Enum):
    """Supported lyrics file formats."""
    TTML = "ttml"
    YAML = "yaml"
    LRC = "lrc"
    TXT = "txt"

    @property
    def extension(self) -> str:
        if self == LyricsFormat.YAML:
            return ".lyricsfile.yaml"
        return f".{self.value}"

    @property
    def priority(self) -> int:
        """Priority score: higher is better."""
        priorities = {
            LyricsFormat.TTML: 40,
            LyricsFormat.YAML: 30,
            LyricsFormat.LRC: 20,
            LyricsFormat.TXT: 10,
        }
        return priorities.get(self, 0)


class LyricsSyncType(str, Enum):
    """Synchronization precision of lyrics."""
    WORD_SYNC = "word_sync"    # Syllable or word-level timing (e.g., TTML, Lyricsfile YAML)
    LINE_SYNC = "line_sync"    # Line-level timestamps (e.g., standard LRC)
    UNSYNCED = "unsynced"      # Plain text without timestamps


class StorageMode(str, Enum):
    """Storage destination for lyrics."""
    SIDECAR = "sidecar"      # Only save companion sidecar files (.ttml, .lrc, .yaml)
    EMBEDDED = "embedded"    # Only embed directly into audio tags (USLT, SYLT, LYRICS, ©lyr)
    BOTH = "both"            # Both companion sidecar file AND audio tags


def detect_sync_type(content: str, fmt: LyricsFormat, hint: Optional[str] = None) -> LyricsSyncType:
    """Accurately detect whether lyrics content has word-level sync, line-level sync, or is unsynced."""
    if not content or not content.strip():
        return LyricsSyncType.UNSYNCED
    text = content.strip()

    if fmt == LyricsFormat.TTML:
        if "<tt" not in text.lower():
            return LyricsSyncType.UNSYNCED
        if (
            ("<span" in text and "begin=" in text)
            or ('itunes:timing="Word"' in text)
            or ('itunes:timing="Syllable"' in text)
            or (hint and ("word" in hint.lower() or "syllable" in hint.lower()))
        ):
            return LyricsSyncType.WORD_SYNC
        if "<p" in text and "begin=" in text:
            return LyricsSyncType.LINE_SYNC
        if hint and "line" in hint.lower():
            return LyricsSyncType.LINE_SYNC
        return LyricsSyncType.UNSYNCED

    elif fmt == LyricsFormat.YAML:
        if "words:" in text:
            return LyricsSyncType.WORD_SYNC
        if hint and ("word" in hint.lower() or "syllable" in hint.lower()):
            return LyricsSyncType.WORD_SYNC
        if "start_ms:" in text or "lines:" in text:
            return LyricsSyncType.LINE_SYNC
        if hint and "line" in hint.lower():
            return LyricsSyncType.LINE_SYNC
        return LyricsSyncType.UNSYNCED

    elif fmt == LyricsFormat.LRC:
        if re.search(r"<\d{1,2}:\d{2}(?:\.\d{1,3})?>", text):
            return LyricsSyncType.WORD_SYNC
        if hint and ("word" in hint.lower() or "syllable" in hint.lower()):
            return LyricsSyncType.WORD_SYNC
        if re.search(r"\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]", text):
            return LyricsSyncType.LINE_SYNC
        if hint and "line" in hint.lower():
            return LyricsSyncType.LINE_SYNC
        return LyricsSyncType.UNSYNCED

    return LyricsSyncType.UNSYNCED


class TrackMetadata(BaseModel):
    """Extracted and cleaned metadata from an audio file."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    file_path: Path
    title: str
    artist: str
    album: Optional[str] = None
    duration: float = Field(..., description="Duration in seconds")
    album_artist: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    isrc: Optional[str] = None
    musicbrainz_trackid: Optional[str] = None
    clean_title: Optional[str] = None
    clean_artist: Optional[str] = None
    has_embedded_lyrics: Optional[bool] = None

    def display_name(self) -> str:
        return f"{self.artist} - {self.title}"


class SubsonicTrack(BaseModel):
    """Song metadata entity retrieved from Navidrome / Subsonic API."""
    id: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: float = Field(default=0.0, description="Duration in seconds")
    path: str = Field(default="", description="Relative path on server, e.g. Artist/Album/01 Track.mp3")
    suffix: str = Field(default="mp3", description="File extension without dot")
    lyrics_present: bool = Field(default=False, description="Whether server reports lyrics already exist")
    year: Optional[int] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None


class LyricsResult(BaseModel):
    """Lyrics payload returned by a provider."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    content: str = Field(..., description="Raw lyrics text / TTML XML / YAML content")
    format: LyricsFormat
    sync_type: LyricsSyncType
    provider_name: str
    duration: Optional[float] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    match_score: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_word_synced(self) -> bool:
        return self.sync_type == LyricsSyncType.WORD_SYNC

    @property
    def is_synced(self) -> bool:
        return self.sync_type in (LyricsSyncType.WORD_SYNC, LyricsSyncType.LINE_SYNC)


class MatchStatus(str, Enum):
    """Result status of processing a track."""
    SUCCESS = "success"
    SKIPPED = "skipped"
    NOT_FOUND = "not_found"
    ERROR = "error"


class ProcessResult(BaseModel):
    """Result of attempting to find and write lyrics for a track."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    file_path: Path
    status: MatchStatus
    provider: Optional[str] = None
    format: Optional[LyricsFormat] = None
    target_file: Optional[Path] = None
    embedded: bool = False
    error_message: Optional[str] = None
    match_score: float = 0.0
