"""Sidecar lyrics storage manager, existing file detection, and atomic writing."""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple
from src.models import LyricsFormat, LyricsResult, StorageMode
from src.tag_writer import embed_lyrics_in_audio, has_embedded_lyrics

logger = logging.getLogger("nla.storage")

# Priority order of lyrics extensions from highest to lowest
LYRICS_EXTENSIONS_PRIORITY = [
    (LyricsFormat.TTML, [".ttml"]),
    (LyricsFormat.YAML, [".lyricsfile.yaml", ".yaml"]),
    (LyricsFormat.LRC, [".lrc"]),
    (LyricsFormat.TXT, [".txt"]),
]


def get_existing_lyrics_file(audio_path: Path, output_dir: Optional[Path] = None) -> Optional[Tuple[Path, LyricsFormat]]:
    """Check if any companion lyrics file exists for the given audio file.
    
    Returns tuple of (file_path, format) of the highest quality existing lyrics, or None.
    """
    search_dir = output_dir if output_dir else audio_path.parent
    for fmt, exts in LYRICS_EXTENSIONS_PRIORITY:
        for ext in exts:
            candidate = search_dir / f"{audio_path.stem}{ext}"
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate, fmt
    return None


def should_skip_track(
    audio_path: Path,
    overwrite: bool = False,
    upgrade_quality: bool = True,
    storage_mode: str = "sidecar",
    output_dir: Optional[Path] = None,
) -> Tuple[bool, Optional[str]]:
    """Determine if a track should be skipped based on existing sidecar files or embedded audio tags.
    
    Returns:
        (should_skip, reason)
    """
    if overwrite:
        return False, None

    mode = str(storage_mode).lower()

    # 1. Embedded-only mode
    if mode == StorageMode.EMBEDDED.value:
        if has_embedded_lyrics(audio_path):
            return True, f"Embedded lyrics already exist in tags ({audio_path.name})"
        return False, None

    # 2. Sidecar or Both mode: check sidecar files
    existing = get_existing_lyrics_file(audio_path, output_dir=output_dir)

    # In 'both' mode, also check embedded tags if sidecar already exists
    if mode == StorageMode.BOTH.value:
        has_tags = has_embedded_lyrics(audio_path)
        if existing and has_tags:
            existing_path, existing_format = existing
            if existing_format == LyricsFormat.TTML:
                return True, f"Both TTML sidecar and embedded lyrics already exist ({audio_path.name})"
            if not upgrade_quality:
                return True, f"Both {existing_format.value.upper()} sidecar and embedded lyrics already exist"
            if existing_format == LyricsFormat.YAML:
                return True, f"Both YAML sidecar and embedded lyrics already exist"
        # If either is missing, allow searching so we can populate both
        return False, None

    # Standard sidecar-only mode
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
    output_dir: Optional[Path] = None,
) -> Path:
    """Atomically save lyrics content as a companion sidecar file.
    
    Args:
        audio_path: Path to the audio file
        lyrics: LyricsResult object containing content and format
        dry_run: If True, simulate without writing to disk
        remove_lower_quality: If True and we saved TTML/YAML, remove obsolete .lrc/.txt files
        output_dir: Optional custom destination folder instead of audio file directory
        
    Returns:
        Target file path
    """
    dest_dir = output_dir if output_dir else audio_path.parent
    target_path = dest_dir / f"{audio_path.stem}{lyrics.format.extension}"

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
                        old_candidate = dest_dir / f"{audio_path.stem}{ext}"
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


def save_lyrics_for_track(
    audio_path: Path,
    lyrics: LyricsResult,
    storage_mode: str = "sidecar",
    output_dir: Optional[Path] = None,
    dry_run: bool = False,
    remove_lower_quality: bool = True,
    enhanced_lrc: bool = True,
) -> Tuple[Optional[Path], bool]:
    """Save lyrics according to the configured storage mode: sidecar, embedded, or both.
    
    Returns:
        (sidecar_path, was_embedded)
    """
    mode = str(storage_mode).lower()
    sidecar_path: Optional[Path] = None
    was_embedded: bool = False

    # 1. Save sidecar file if mode is 'sidecar' or 'both'
    if mode in (StorageMode.SIDECAR.value, StorageMode.BOTH.value):
        sidecar_path = save_lyrics_sidecar(
            audio_path=audio_path,
            lyrics=lyrics,
            dry_run=dry_run,
            remove_lower_quality=remove_lower_quality,
            output_dir=output_dir,
        )

    # 2. Embed lyrics in audio file tags if mode is 'embedded' or 'both'
    if mode in (StorageMode.EMBEDDED.value, StorageMode.BOTH.value):
        if audio_path.is_file():
            was_embedded = embed_lyrics_in_audio(
                audio_path=audio_path,
                lyrics=lyrics,
                dry_run=dry_run,
                enhanced_lrc=enhanced_lrc,
            )
        else:
            logger.debug(f"Audio file {audio_path} not found locally for tag embedding")

    return sidecar_path, was_embedded
