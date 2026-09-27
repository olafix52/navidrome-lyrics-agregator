import logging
import os
from pathlib import Path
import threading
import uuid
from typing import Dict, Optional, Tuple
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, StorageMode, detect_sync_type
from src.tag_writer import embed_lyrics_in_audio, has_embedded_lyrics

logger = logging.getLogger("nla.storage")

# Priority order of lyrics extensions from highest to lowest
LYRICS_EXTENSIONS_PRIORITY = [
    (LyricsFormat.TTML, [".ttml"]),
    (LyricsFormat.YAML, [".lyricsfile.yaml", ".yaml"]),
    (LyricsFormat.LRC, [".lrc"]),
    (LyricsFormat.TXT, [".txt"]),
]


class FolderLyricsIndex:
    """Thread-safe directory-level cache of existing sidecar files.
    
    Caches filename entries and sizes per directory, validated via directory mtime.
    Eliminates redundant filesystem stat calls for non-existent sidecar files,
    yielding up to 10-20x faster sidecar verification on large libraries.
    """

    def __init__(self, max_directories: int = 10000):
        self._cache: Dict[Path, Tuple[int, Dict[str, int]]] = {}
        self._lock = threading.Lock()
        self._max_directories = max_directories

    def get_dir_entries(self, dir_path: Path) -> Dict[str, int]:
        if not dir_path.is_dir():
            return {}

        try:
            mtime_ns = dir_path.stat().st_mtime_ns
        except OSError:
            return {}

        with self._lock:
            cached = self._cache.get(dir_path)
            if cached is not None and cached[0] == mtime_ns:
                return cached[1]

            entries: Dict[str, int] = {}
            try:
                with os.scandir(dir_path) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                entries[entry.name] = entry.stat().st_size
                        except OSError:
                            continue
            except OSError:
                pass

            if len(self._cache) >= self._max_directories:
                self._cache.pop(next(iter(self._cache)), None)

            self._cache[dir_path] = (mtime_ns, entries)
            return entries

    def register_file(self, file_path: Path, size: int) -> None:
        with self._lock:
            cached = self._cache.get(file_path.parent)
            if cached is not None:
                try:
                    mtime_ns = file_path.parent.stat().st_mtime_ns
                except OSError:
                    mtime_ns = cached[0]
                cached[1][file_path.name] = size
                self._cache[file_path.parent] = (mtime_ns, cached[1])

    def unregister_file(self, file_path: Path) -> None:
        with self._lock:
            cached = self._cache.get(file_path.parent)
            if cached is not None:
                try:
                    mtime_ns = file_path.parent.stat().st_mtime_ns
                except OSError:
                    mtime_ns = cached[0]
                cached[1].pop(file_path.name, None)
                self._cache[file_path.parent] = (mtime_ns, cached[1])

    def invalidate(self, dir_path: Optional[Path] = None) -> None:
        with self._lock:
            if dir_path:
                self._cache.pop(dir_path, None)
            else:
                self._cache.clear()


GLOBAL_FOLDER_INDEX = FolderLyricsIndex()


def resolve_sidecar_dir(
    audio_path: Path,
    output_dir: Optional[Path] = None,
    music_dir: Optional[Path] = None,
) -> Path:
    """Directory holding the sidecar files for ``audio_path``.

    Without ``output_dir`` sidecars live next to the audio file. With ``output_dir`` and
    ``music_dir`` the library's folder structure is mirrored below ``output_dir``
    (``<output_dir>/<Artist>/<Album>/``), so identically named tracks from different
    albums (``01 - Intro``) never collide; audio outside ``music_dir`` is mirrored by its
    full path. Without ``music_dir`` there is no library root to mirror from and the
    sidecar goes directly into ``output_dir``.
    """
    if not output_dir:
        return audio_path.parent
    if not music_dir:
        return Path(output_dir)
    parent = audio_path.parent
    try:
        return Path(output_dir) / parent.relative_to(music_dir)
    except ValueError:
        pass
    try:
        return Path(output_dir) / parent.resolve().relative_to(Path(music_dir).resolve())
    except (ValueError, OSError):
        return Path(output_dir) / parent.relative_to(parent.anchor)


def get_existing_lyrics_file(
    audio_path: Path,
    output_dir: Optional[Path] = None,
    folder_index: Optional[FolderLyricsIndex] = None,
    music_dir: Optional[Path] = None,
) -> Optional[Tuple[Path, LyricsFormat]]:
    """Check if any companion lyrics file exists for the given audio file.
    
    Returns tuple of (file_path, format) of the highest quality existing lyrics, or None.
    """
    search_dir = resolve_sidecar_dir(audio_path, output_dir, music_dir)
    idx = folder_index or GLOBAL_FOLDER_INDEX
    entries = idx.get_dir_entries(search_dir)
    stem = audio_path.stem

    for fmt, exts in LYRICS_EXTENSIONS_PRIORITY:
        for ext in exts:
            name = f"{stem}{ext}"
            size = entries.get(name, 0)
            if size > 0:
                return search_dir / name, fmt
    return None


_SYNC_RANK = {
    LyricsSyncType.WORD_SYNC: 3,
    LyricsSyncType.LINE_SYNC: 2,
    LyricsSyncType.UNSYNCED: 1,
}


def lyrics_quality_rank(sync_type: LyricsSyncType, fmt: LyricsFormat) -> Tuple[int, int]:
    """Comparable quality rank: sync precision first, then container format priority."""
    return _SYNC_RANK.get(sync_type, 0), fmt.priority


def get_existing_lyrics_rank(
    audio_path: Path,
    output_dir: Optional[Path] = None,
    music_dir: Optional[Path] = None,
) -> Optional[Tuple[int, int]]:
    """Quality rank of the best existing sidecar for the track, or None if there is none."""
    existing = get_existing_lyrics_file(audio_path, output_dir=output_dir, music_dir=music_dir)
    if not existing:
        return None
    return get_sidecar_rank(*existing)


def get_sidecar_rank(lyrics_path: Path, fmt: LyricsFormat) -> Optional[Tuple[int, int]]:
    """Quality rank of one sidecar file judged by its content, or None if it is unreadable."""
    try:
        content = lyrics_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    from src.web.parser import count_lyric_lines  # local import: parser depends on models only

    if count_lyric_lines(content, fmt) == 0:
        return (0, 0)  # e.g. credits-only file: any real lyrics are an upgrade
    return lyrics_quality_rank(detect_sync_type(content, fmt), fmt)


def should_skip_track(
    audio_path: Path,
    overwrite: bool = False,
    upgrade_quality: bool = True,
    storage_mode: str = "sidecar",
    output_dir: Optional[Path] = None,
    music_dir: Optional[Path] = None,
    embedded_present: Optional[bool] = None,
) -> Tuple[bool, Optional[str]]:
    """Determine if a track should be skipped based on existing sidecar files or embedded audio tags.

    ``embedded_present`` may carry an already known "file has embedded lyrics" flag
    (e.g. from ``read_track_metadata``) to avoid parsing the audio file again.

    Returns:
        (should_skip, reason)
    """
    if overwrite:
        return False, None

    mode = str(storage_mode).lower()

    def _has_tags() -> bool:
        return embedded_present if embedded_present is not None else has_embedded_lyrics(audio_path)

    # 1. Embedded-only mode
    if mode == StorageMode.EMBEDDED.value:
        if _has_tags():
            return True, f"Embedded lyrics already exist in tags ({audio_path.name})"
        return False, None

    # 2. Sidecar or Both mode: check sidecar files
    existing = get_existing_lyrics_file(audio_path, output_dir=output_dir, music_dir=music_dir)

    # In 'both' mode, also check embedded tags if sidecar already exists
    if mode == StorageMode.BOTH.value:
        if existing and _has_tags():
            existing_path, existing_format = existing
            if not upgrade_quality:
                return True, f"Both {existing_format.value.upper()} sidecar and embedded lyrics already exist"
            if is_word_synced_sidecar(existing_path, existing_format):
                return True, f"Both word-synced {existing_format.value.upper()} sidecar and embedded lyrics already exist ({audio_path.name})"
        # If either is missing, allow searching so we can populate both
        return False, None

    # Standard sidecar-only mode
    if not existing:
        return False, None

    existing_path, existing_format = existing

    # If upgrade_quality is disabled, skip any existing file
    if not upgrade_quality:
        return True, f"{existing_format.value.upper()} file already exists ({existing_path.name})"

    # Word sync is the top quality. Judged by content, not by extension: a line-synced
    # TTML (itunes:timing="Line") can still be upgraded to word sync.
    if is_word_synced_sidecar(existing_path, existing_format):
        return True, f"Word-synced {existing_format.value.upper()} file already exists ({existing_path.name})"

    return False, None


def is_word_synced_sidecar(lyrics_path: Path, fmt: LyricsFormat) -> bool:
    """Whether a sidecar already holds top-quality lyrics: a word-synced TTML or YAML.

    Judged by content, not by extension. A word-synced (Enhanced) LRC still qualifies for
    an upgrade to TTML, as before.
    """
    if fmt not in (LyricsFormat.TTML, LyricsFormat.YAML):
        return False
    rank = get_sidecar_rank(lyrics_path, fmt)
    # An unreadable file is left alone rather than re-fetched on every scan
    return rank is None or rank[0] >= _SYNC_RANK[LyricsSyncType.WORD_SYNC]


def save_lyrics_sidecar(
    audio_path: Path,
    lyrics: LyricsResult,
    dry_run: bool = False,
    remove_lower_quality: bool = True,
    output_dir: Optional[Path] = None,
    music_dir: Optional[Path] = None,
    replace_existing: bool = False,
) -> Path:
    """Atomically save lyrics content as a companion sidecar file.
    
    Args:
        audio_path: Path to the audio file
        lyrics: LyricsResult object containing content and format
        dry_run: If True, simulate without writing to disk
        remove_lower_quality: If True, remove the track's other sidecars that are not more
            precisely synced than the saved lyrics (the new file supersedes them)
        output_dir: Optional custom destination root (library structure is mirrored below it)
        music_dir: Library root used to mirror the folder structure below ``output_dir``
        replace_existing: With ``remove_lower_quality``, remove the other sidecars even when
            they are more precisely synced (an explicit user choice, e.g. a Web UI save)
        
    Returns:
        Target file path
    """
    dest_dir = resolve_sidecar_dir(audio_path, output_dir, music_dir)
    target_path = dest_dir / f"{audio_path.stem}{lyrics.format.extension}"

    if dry_run:
        logger.info(f"[DRY RUN] Would write {lyrics.format.value.upper()} lyrics to {target_path.name}")
        return target_path

    # Ensure parent directory exists
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write via temporary file (unique per writer: watcher and scanner can run concurrently)
    temp_path = target_path.parent / f".{target_path.name}.tmp_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(lyrics.content.strip() + "\n")
        temp_path.replace(target_path)
        GLOBAL_FOLDER_INDEX.register_file(target_path, len(lyrics.content.encode("utf-8")))
        logger.debug(f"Saved {lyrics.format.value.upper()} lyrics to {target_path}")

        # Remove the sidecars the new file supersedes, whatever their format: a stale file of a
        # higher-priority format (e.g. a line-synced .ttml next to a new .lrc) would shadow it.
        if remove_lower_quality:
            new_sync = _SYNC_RANK.get(lyrics.sync_type, 0)
            for fmt, exts in LYRICS_EXTENSIONS_PRIORITY:
                for ext in exts:
                    old_candidate = dest_dir / f"{audio_path.stem}{ext}"
                    if old_candidate == target_path or not old_candidate.is_file():
                        continue
                    if not replace_existing:
                        old_rank = get_sidecar_rank(old_candidate, fmt)
                        if old_rank is None or old_rank[0] > new_sync:
                            continue  # keep more precisely synced (or unreadable) lyrics
                    try:
                        old_candidate.unlink()
                        GLOBAL_FOLDER_INDEX.unregister_file(old_candidate)
                        logger.info(f"Removed superseded sidecar: {old_candidate.name}")
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
    music_dir: Optional[Path] = None,
    replace_existing: bool = False,
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
            music_dir=music_dir,
            replace_existing=replace_existing,
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
