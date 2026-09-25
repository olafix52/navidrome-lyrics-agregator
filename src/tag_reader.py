"""Audio metadata and tag extraction using mutagen for FLAC, MP3, M4A, Opus, Ogg, and more."""

import logging
import re
import os
from pathlib import Path
from typing import Any, Iterator, List, Optional, Union
import mutagen
from mutagen.mp4 import MP4

from src.models import TrackMetadata
from src.normalizer import clean_artist, clean_title
from src.tag_writer import has_embedded_lyrics

logger = logging.getLogger("nla.tag_reader")

SUPPORTED_AUDIO_EXTENSIONS = {
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".opus",
    ".ogg",
    ".oga",
    ".wav",
    ".wave",
    ".aiff",
    ".aif",
    ".wma",
    ".ape",
    ".wv",
    ".dsf",
    ".dff",
}


def is_supported_audio_file(file_path: Union[Path, str]) -> bool:
    """Check if file has a supported audio extension."""
    if isinstance(file_path, str):
        dot_idx = file_path.rfind(".")
        return dot_idx != -1 and file_path[dot_idx:].lower() in SUPPORTED_AUDIO_EXTENSIONS
    return file_path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS


def iter_discover_audio_files(root_dir: Path) -> Iterator[Path]:
    """Yield all supported audio files in target directory recursively using fast os.scandir.
    
    Streams paths lazily without allocating a massive in-memory list,
    enabling constant-memory producer-consumer pipelines.
    """
    if not root_dir.exists():
        logger.error(f"Music directory does not exist: {root_dir}")
        return

    if root_dir.is_file():
        if is_supported_audio_file(root_dir):
            yield root_dir
        return

    stack: List[str] = [str(root_dir)]

    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            dot_idx = entry.name.rfind(".")
                            if dot_idx != -1 and entry.name[dot_idx:].lower() in SUPPORTED_AUDIO_EXTENSIONS:
                                yield Path(entry.path)
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError) as e:
            logger.debug(f"Error scanning directory {current}: {e}")
            continue


def fast_discover_audio_files(root_dir: Path) -> List[Path]:
    """Find all supported audio files in target directory recursively using fast os.scandir.
    
    Bypasses expensive recursive Path.rglob allocations and dirent stat calls,
    yielding 3-5x faster directory traversal on large libraries.
    """
    return sorted(iter_discover_audio_files(root_dir))


def _get_first_tag_value(tags: Any, keys: List[str]) -> Optional[str]:
    """Helper to retrieve the first matching string tag value."""
    if not tags:
        return None
    for key in keys:
        if key in tags:
            val = tags[key]
            if isinstance(val, (list, tuple)) and val:
                item = val[0]
                if isinstance(item, bytes):
                    return item.decode("utf-8", errors="replace").strip()
                return str(item).strip()
            elif isinstance(val, bytes):
                return val.decode("utf-8", errors="replace").strip()
            elif isinstance(val, str) and val.strip():
                return val.strip()
            elif val is not None:
                return str(val).strip()
    return None


def _fallback_parse_filename(file_path: Path) -> tuple[str, str]:
    """Fallback parser when tags are missing: extracts Artist and Title from filename."""
    stem = file_path.stem.strip()
    # Check for "Artist - Title" format
    if " - " in stem:
        parts = stem.split(" - ", 1)
        # Check if first part is a track number e.g. "01 - Artist - Title"
        if len(parts) == 2 and parts[0].isdigit():
            # e.g., "01 - Title" (Artist might be parent directory name)
            artist = file_path.parent.parent.name if file_path.parent.parent else "Unknown Artist"
            title = parts[1].strip()
            return artist, title
        return parts[0].strip(), parts[1].strip()

    # If no separator, use parent directory as artist and stem as title
    artist = file_path.parent.name if file_path.parent else "Unknown Artist"
    title = stem
    return artist, title


def _extract_spotify_id(val: Optional[str]) -> Optional[str]:
    """Extract a 22-char Spotify track ID from a string, URL, or URI."""
    if not val:
        return None
    val = val.strip()
    if re.fullmatch(r"[A-Za-z0-9]{22}", val):
        return val
    m = re.search(r"(?:spotify:track:|spotify\.com/track/)([A-Za-z0-9]{22})", val)
    if m:
        return m.group(1)
    return None


def read_track_metadata(file_path: Path) -> Optional[TrackMetadata]:
    """Read metadata tags and duration from an audio file."""
    if not file_path.is_file() or not is_supported_audio_file(file_path):
        return None

    try:
        audio = mutagen.File(str(file_path))
        if audio is None:
            logger.warning(f"Could not parse audio file: {file_path}")
            return None

        duration = float(getattr(audio.info, "length", 0.0))
        tags = audio.tags

        title: Optional[str] = None
        artist: Optional[str] = None
        album: Optional[str] = None
        album_artist: Optional[str] = None
        isrc: Optional[str] = None
        mb_trackid: Optional[str] = None
        spotify_id: Optional[str] = None

        if isinstance(audio, MP4) and tags:
            title = _get_first_tag_value(tags, ["\xa9nam", "title"])
            artist = _get_first_tag_value(tags, ["\xa9ART", "artist"])
            album = _get_first_tag_value(tags, ["\xa9alb", "album"])
            album_artist = _get_first_tag_value(tags, ["aART", "albumartist"])
            isrc = _get_first_tag_value(tags, [
                "----:com.apple.iTunes:ISRC",
                "----:com.apple.iTunes:isrc",
            ])
            raw_sp = _get_first_tag_value(tags, [
                "----:com.apple.iTunes:SPOTIFY_TRACK_ID",
                "----:com.apple.iTunes:SPOTIFY_ID",
                "----:com.apple.iTunes:spotify_id",
            ])
            spotify_id = _extract_spotify_id(raw_sp)
        elif tags:
            # Vorbis / ID3 / FLAC / General keys
            title = _get_first_tag_value(tags, ["TIT2", "title", "TITLE", "Title"])
            artist = _get_first_tag_value(tags, ["TPE1", "artist", "ARTIST", "Artist"])
            album = _get_first_tag_value(tags, ["TALB", "album", "ALBUM", "Album"])
            album_artist = _get_first_tag_value(tags, ["TPE2", "albumartist", "ALBUMARTIST", "album_artist"])
            isrc = _get_first_tag_value(tags, [
                "TSRC",
                "TXXX:ISRC",
                "TXXX:isrc",
                "isrc",
                "ISRC",
            ])
            mb_trackid = _get_first_tag_value(tags, ["UFID:http://musicbrainz.org", "musicbrainz_trackid"])
            raw_sp = _get_first_tag_value(tags, [
                "TXXX:SPOTIFY_TRACK_ID",
                "TXXX:SPOTIFY_ID",
                "TXXX:Spotify Track ID",
                "SPOTIFY_TRACK_ID",
                "SPOTIFY_ID",
                "SPOTIFYID",
                "WOAS",
                "WXXX:SPOTIFY",
            ])
            spotify_id = _extract_spotify_id(raw_sp)
            if not spotify_id:
                comm = _get_first_tag_value(tags, ["COMM", "description", "COMMENT", "comment"])
                spotify_id = _extract_spotify_id(comm)

        # Normalize ISRC if present (remove hyphens, ensure 12 alphanumeric characters)
        if isrc:
            cleaned_isrc = re.sub(r"[^A-Za-z0-9]", "", isrc).upper()
            if len(cleaned_isrc) == 12:
                isrc = cleaned_isrc
            else:
                isrc = None

        # If title or artist is missing, fallback to filename
        if not title or not artist:
            fb_artist, fb_title = _fallback_parse_filename(file_path)
            title = title or fb_title
            artist = artist or fb_artist

        clean_t = clean_title(title)
        clean_a = clean_artist(artist)
        embedded_lyrics = has_embedded_lyrics(file_path)

        return TrackMetadata(
            file_path=file_path,
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            album_artist=album_artist,
            isrc=isrc,
            musicbrainz_trackid=mb_trackid,
            spotify_id=spotify_id,
            clean_title=clean_t,
            clean_artist=clean_a,
            has_embedded_lyrics=embedded_lyrics,
        )

    except Exception as e:
        logger.error(f"Error reading metadata from {file_path}: {e}")
        return None
