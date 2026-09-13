"""Sidecar lyrics storage manager, existing file detection, and atomic writing."""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple
from src.models import LyricsFormat, LyricsResult

logger = logging.getLogger("nla.storage")

# Priority order of lyrics extensions from highest to lowest
LYRICS_EXTENSIONS_PRIORITY = [
    (LyricsFormat.TTML, [".ttml"]),
    (LyricsFormat.YAML, [".lyricsfile.yaml", ".yaml"]),
    (LyricsFormat.LRC, [".lrc"]),
    (LyricsFormat.TXT, [".txt"]),
]


def get_existing_lyrics_file(audio_path: Path) -> Optional[Tuple[Path, LyricsFormat]]:
    """Check if any companion lyrics file exists for the given audio file.
    
    Returns tuple of (file_path, format) of the highest quality existing lyrics, or None.
    """
    for fmt, exts in LYRICS_EXTENSIONS_PRIORITY:
        for ext in exts:
            candidate = audio_path.parent / f"{audio_path.stem}{ext}"
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate, fmt
    return None


def should_skip_track(
    audio_path: Path,
    overwrite: bool = False,
    upgrade_quality: bool = True,
) -> Tuple[bool, Optional[str]]:
    """Determine if a track should be skipped based on existing sidecar files.
    
    Returns:
        (should_skip, reason)
    """
    if overwrite:
        return False, None

    existing = get_existing_lyrics_file(audio_path)
    if not existing:
        return False, None

    existing_path, existing_format = existing

    # If we already have TTML (top quality), we always skip unless overwrite is True
    if existing_format == LyricsFormat.TTML:
        return True, f"TTML file already exists ({existing_path.name})"

    # If upgrade_quality is disabled, skip any existing file
    if not upgrade_quality:
        return True, f"{existing_format.value.upper()} file already exists ({existing_path.name})"

    # If existing format is YAML, only skip if we don't want to attempt TTML upgrade
    if existing_format == LyricsFormat.YAML:
        return True, f"Lyricsfile YAML already exists ({existing_path.name})"

    # Existing is LRC or TXT, allowed to try upgrading to TTML/YAML
    return False, None


def save_lyrics_sidecar(
    audio_path: Path,
    lyrics: LyricsResult,
    dry_run: bool = False,
    remove_lower_quality: bool = True,
) -> Path:
    """Atomically save lyrics content as a companion sidecar file.
    
    Args:
        audio_path: Path to the audio file
        lyrics: LyricsResult object containing content and format
        dry_run: If True, simulate without writing to disk
        remove_lower_quality: If True and we saved TTML/YAML, remove obsolete .lrc/.txt files
        
    Returns:
        Target file path
    """
    target_path = audio_path.parent / f"{audio_path.stem}{lyrics.format.extension}"

    if dry_run:
        logger.info(f"[DRY RUN] Would write {lyrics.format.value.upper()} lyrics to {target_path.name}")
        return target_path

    # Ensure parent directory exists
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write via temporary file
    temp_path = target_path.parent / f"{target_path.name}.tmp_{os.getpid()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(lyrics.content.strip() + "\n")
        temp_path.replace(target_path)
        logger.debug(f"Saved {lyrics.format.value.upper()} lyrics to {target_path}")

        # Clean up lower quality sidecars if upgraded to higher quality format
        if remove_lower_quality and lyrics.format in (LyricsFormat.TTML, LyricsFormat.YAML):
            for fmt, exts in LYRICS_EXTENSIONS_PRIORITY:
                if fmt.priority < lyrics.format.priority:
                    for ext in exts:
                        old_candidate = audio_path.parent / f"{audio_path.stem}{ext}"
                        if old_candidate.is_file() and old_candidate != target_path:
                            try:
                                old_candidate.unlink()
                                logger.info(f"Removed obsolete lower quality sidecar: {old_candidate.name}")
                            except Exception as e:
                                logger.warning(f"Could not remove old sidecar {old_candidate}: {e}")

        return target_path
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise IOError(f"Failed to write lyrics to {target_path}: {e}") from e
