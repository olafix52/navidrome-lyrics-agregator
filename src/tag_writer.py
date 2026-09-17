"""Audio tag writer embedding synchronized (USLT/SYLT, Vorbis, ©lyr) and plain lyrics into audio files."""

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, SYLT, TXXX, USLT, Encoding, ID3NoHeaderError
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from src.models import LyricsFormat, LyricsResult, LyricsSyncType
from src.web.parser import KaraokeLine, parse_lyrics_to_karaoke

logger = logging.getLogger("nla.tag_writer")


def _karaoke_lines_to_lrc(lines: List[KaraokeLine]) -> str:
    """Serialize KaraokeLine list back to standard LRC string with [mm:ss.xx] timestamps."""
    out: List[str] = []
    for line in lines:
        if line.start is not None:
            m = int(line.start // 60)
            s = line.start % 60
            out.append(f"[{m:02d}:{s:05.2f}]{line.text}")
        else:
            out.append(line.text)
    return "\n".join(out)


def _karaoke_lines_to_plain(lines: List[KaraokeLine]) -> str:
    """Extract clean unsynced lyrics text without any timestamps."""
    return "\n".join(line.text.strip() for line in lines if line.text.strip())


def _lyrics_to_tag_payloads(lyrics: LyricsResult) -> Tuple[str, str, List[Tuple[str, int]]]:
    """Convert LyricsResult into:
    1. lrc_text: Synced LRC string (or plain text if unsynced)
    2. plain_text: Unsynced plain text
    3. sylt_entries: List of (line_or_word, timestamp_ms) for ID3 SYLT frame
    """
    lines = parse_lyrics_to_karaoke(lyrics.content, lyrics.format)

    lrc_text = _karaoke_lines_to_lrc(lines) if lyrics.is_synced else _karaoke_lines_to_plain(lines)
    plain_text = _karaoke_lines_to_plain(lines)

    sylt_entries: List[Tuple[str, int]] = []
    if lyrics.is_synced:
        for line in lines:
            if line.start is not None and line.text.strip():
                sylt_entries.append((line.text.strip(), int(round(line.start * 1000))))

    return lrc_text, plain_text, sylt_entries


def has_embedded_lyrics(audio_path: Path) -> bool:
    """Check if the audio file already contains embedded lyrics tags."""
    if not audio_path.is_file():
        return False

    suffix = audio_path.suffix.lower()
    tags = None
    try:
        audio = mutagen.File(str(audio_path))
        if audio is not None:
            tags = audio.tags
    except Exception:
        pass

    if tags is None and suffix in (".mp3", ""):
        try:
            tags = ID3(str(audio_path))
        except Exception:
            tags = None

    if tags is None:
        return False

    try:
        # 1. MP3 / ID3
        if suffix == ".mp3" or isinstance(tags, ID3):
            for key in tags.keys():
                if key.startswith("USLT") or key.startswith("SYLT"):
                    return True
                if key == "TXXX:LYRICS" or key.startswith("TXXX:lyrics"):
                    return True

        # 2. FLAC / Vorbis / Ogg / Opus
        for key in ("LYRICS", "UNSYNCEDLYRICS", "lyrics", "unsyncedlyrics"):
            if key in tags and tags[key]:
                val = tags[key]
                if isinstance(val, (list, tuple)) and any(str(v).strip() for v in val):
                    return True
                elif isinstance(val, str) and val.strip():
                    return True

        # 3. M4A / MP4 / ALAC
        if "\xa9lyr" in tags and tags["\xa9lyr"]:
            val = tags["\xa9lyr"]
            if isinstance(val, (list, tuple)) and any(str(v).strip() for v in val):
                return True
            elif isinstance(val, str) and val.strip():
                return True

    except Exception as e:
        logger.debug(f"Error checking embedded lyrics in {audio_path}: {e}")

    return False


def extract_embedded_lyrics(audio_path: Path) -> Optional[str]:
    """Retrieve raw embedded lyrics string from audio file tags if present."""
    if not audio_path.is_file():
        return None

    suffix = audio_path.suffix.lower()
    tags = None
    try:
        audio = mutagen.File(str(audio_path))
        if audio is not None:
            tags = audio.tags
    except Exception:
        pass

    if tags is None and suffix in (".mp3", ""):
        try:
            tags = ID3(str(audio_path))
        except Exception:
            tags = None

    if tags is None:
        return None

    try:
        # ID3 (MP3)
        if suffix == ".mp3" or isinstance(tags, ID3):
            for key in tags.keys():
                if key.startswith("USLT"):
                    frame = tags[key]
                    if hasattr(frame, "text") and frame.text:
                        return str(frame.text)
                if key == "TXXX:LYRICS":
                    frame = tags[key]
                    if hasattr(frame, "text") and frame.text:
                        return str(frame.text[0])

        # Vorbis comments (FLAC, Ogg, Opus)
        for key in ("LYRICS", "UNSYNCEDLYRICS", "lyrics", "unsyncedlyrics"):
            if key in tags and tags[key]:
                val = tags[key]
                if isinstance(val, (list, tuple)) and val:
                    return str(val[0])
                elif isinstance(val, str):
                    return val

        # MP4 / M4A
        if "\xa9lyr" in tags and tags["\xa9lyr"]:
            val = tags["\xa9lyr"]
            if isinstance(val, (list, tuple)) and val:
                return str(val[0])
            elif isinstance(val, str):
                return val

    except Exception as e:
        logger.debug(f"Could not extract lyrics from {audio_path}: {e}")

    return None


def embed_lyrics_in_audio(
    audio_path: Path,
    lyrics: LyricsResult,
    dry_run: bool = False,
) -> bool:
    """Embed lyrics into audio metadata tags based on file format.
    
    Supports:
    - MP3: USLT, SYLT, TXXX:LYRICS (ID3v2.4)
    - FLAC: LYRICS & UNSYNCEDLYRICS (Vorbis comments)
    - OGG / Opus: LYRICS (Vorbis comments)
    - M4A / MP4 / ALAC: ©lyr atom
    
    Returns True if successfully written (or simulated in dry_run).
    """
    if not audio_path.is_file():
        logger.error(f"Audio file does not exist for embedding: {audio_path}")
        return False

    suffix = audio_path.suffix.lower()
    lrc_text, plain_text, sylt_entries = _lyrics_to_tag_payloads(lyrics)

    if dry_run:
        logger.info(f"[DRY RUN] Would embed {lyrics.sync_type.value} lyrics in {audio_path.name}")
        return True

    try:
        # 1. MP3 (ID3v2)
        if suffix == ".mp3":
            try:
                tags = ID3(str(audio_path))
            except ID3NoHeaderError:
                tags = ID3()

            # Set USLT (unsynced or standard LRC string for mobile players like Symfonium / Poweramp)
            tags.delall("USLT")
            tags.add(
                USLT(
                    encoding=Encoding.UTF8,
                    lang="eng",
                    desc="",
                    text=lrc_text if lyrics.is_synced else plain_text,
                )
            )

            # Set SYLT (millisecond synchronized timing entries)
            if sylt_entries:
                tags.delall("SYLT")
                tags.add(
                    SYLT(
                        encoding=Encoding.UTF8,
                        lang="eng",
                        format=2,  # 2 = milliseconds
                        type=1,    # 1 = lyrics
                        desc="",
                        text=sylt_entries,
                    )
                )

            # Add TXXX:LYRICS (standard companion field in ID3)
            tags.delall("TXXX:LYRICS")
            tags.add(TXXX(encoding=Encoding.UTF8, desc="LYRICS", text=[lrc_text]))

            tags.save(str(audio_path), v2_version=4)
            logger.debug(f"Embedded ID3 lyrics in {audio_path.name} (USLT/SYLT)")
            return True

        # 2. FLAC (Vorbis comments)
        elif suffix == ".flac":
            audio = FLAC(str(audio_path))
            audio["LYRICS"] = lrc_text
            if plain_text and plain_text != lrc_text:
                audio["UNSYNCEDLYRICS"] = plain_text
            audio.save()
            logger.debug(f"Embedded Vorbis LYRICS in {audio_path.name}")
            return True

        # 3. OGG / Opus
        elif suffix in (".ogg", ".opus", ".oga"):
            audio = mutagen.File(str(audio_path))
            if audio is not None and hasattr(audio, "tags") and audio.tags is not None:
                audio.tags["LYRICS"] = [lrc_text]
                if plain_text and plain_text != lrc_text:
                    audio.tags["UNSYNCEDLYRICS"] = [plain_text]
                audio.save()
                logger.debug(f"Embedded OGG/Opus LYRICS in {audio_path.name}")
                return True
            else:
                logger.warning(f"Could not load tags for OGG/Opus file: {audio_path.name}")
                return False

        # 4. M4A / MP4 / ALAC
        elif suffix in (".m4a", ".mp4", ".alac"):
            audio = MP4(str(audio_path))
            audio["\xa9lyr"] = [lrc_text if lyrics.is_synced else plain_text]
            audio.save()
            logger.debug(f"Embedded MP4 ©lyr in {audio_path.name}")
            return True

        # 5. Generic Mutagen fallback (e.g. APE, WAV with ID3)
        else:
            audio = mutagen.File(str(audio_path))
            if audio is not None:
                if hasattr(audio, "tags") and audio.tags is not None:
                    if isinstance(audio.tags, ID3):
                        audio.tags.delall("USLT")
                        audio.tags.add(USLT(encoding=Encoding.UTF8, lang="eng", desc="", text=lrc_text))
                        audio.save()
                        return True
                    else:
                        audio.tags["LYRICS"] = [lrc_text]
                        audio.save()
                        return True
            logger.warning(f"Unsupported audio container for lyrics embedding: {suffix}")
            return False

    except Exception as e:
        logger.error(f"Failed to embed lyrics in {audio_path.name}: {e}")
        return False
