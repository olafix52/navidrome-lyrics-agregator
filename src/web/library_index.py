"""In-memory index of the music library for the Web UI.

Listing, filtering and statistics used to walk the whole library (and read every lyrics
file) on each request. The index is built once and then kept current:

- with a filesystem watcher (``start_watching``), only directories that received events are
  rescanned, plus a slow full resync as a safety net;
- without a watcher (or if it cannot start, e.g. inotify limits), it is fully rebuilt when
  older than ``poll_ttl`` seconds.

Writes made through the API call :meth:`LibraryIndex.mark_dir_dirty` directly, so they are
visible immediately either way.
"""

import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.models import LyricsFormat, LyricsSyncType, detect_sync_type
from src.storage import get_existing_lyrics_file
from src.tag_reader import is_supported_audio_file, iter_discover_audio_files, read_track_metadata

logger = logging.getLogger("nla.web.index")

_LYRICS_SUFFIXES = (".ttml", ".lrc", ".txt", ".yaml")


@dataclass(frozen=True)
class IndexedTrack:
    path: Path
    relative_path: str
    track_id: str
    filename: str
    has_lyrics: bool
    format: Optional[str]
    sync_type: Optional[str]


@lru_cache(maxsize=20000)
def _cached_sync_type(lyrics_path: str, mtime_ns: int, size: int, fmt: LyricsFormat) -> str:
    """Sync type of a lyrics file, memoized by (path, mtime, size): unchanged files are read once."""
    try:
        content = Path(lyrics_path).read_text(encoding="utf-8", errors="replace")[:8192]
        return detect_sync_type(content, fmt).value
    except OSError:
        return LyricsSyncType.UNSYNCED.value


def sync_type_for(lyrics_path: Path, fmt: LyricsFormat) -> str:
    try:
        st = lyrics_path.stat()
    except OSError:
        return LyricsSyncType.UNSYNCED.value
    return _cached_sync_type(str(lyrics_path), st.st_mtime_ns, st.st_size, fmt)


@lru_cache(maxsize=5000)
def _cached_tags(path: str, mtime_ns: int, size: int) -> Optional[Dict[str, Any]]:
    meta = read_track_metadata(Path(path))
    if meta is None:
        return None
    return {"artist": meta.artist, "title": meta.title, "album": meta.album, "duration": meta.duration}


def cached_track_tags(audio_path: Path) -> Optional[Dict[str, Any]]:
    """Title/artist/album/duration of an audio file, parsed once per file version."""
    try:
        st = audio_path.stat()
    except OSError:
        return None
    return _cached_tags(str(audio_path), st.st_mtime_ns, st.st_size)


class _IndexEventHandler(FileSystemEventHandler):
    def __init__(self, index: "LibraryIndex"):
        super().__init__()
        self._index = index

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in ("opened", "closed", "closed_no_write"):
            return
        self._index.notify_path_changed(Path(str(event.src_path)), event.is_directory)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._index.notify_path_changed(Path(str(dest)), event.is_directory)


class LibraryIndex:
    def __init__(
        self,
        get_config: Callable[[], Any],
        encode_id: Callable[[Path, Path], str],
        poll_ttl: float = 15.0,
        watched_full_resync: float = 600.0,
    ):
        self._get_config = get_config
        self._encode_id = encode_id
        self._poll_ttl = poll_ttl
        self._watched_full_resync = watched_full_resync

        self._lock = threading.RLock()
        self._dirs: Dict[Path, List[IndexedTrack]] = {}
        self._dirty: Set[Path] = set()
        self._needs_full = True
        self._built_at = 0.0
        self._signature: Optional[Tuple[str, str]] = None
        self._sorted: Optional[List[IndexedTrack]] = None
        self._observer: Optional[Any] = None
        self._watched_roots: Tuple[Optional[Path], Optional[Path]] = (None, None)

    # -- public API ---------------------------------------------------------

    def tracks(self) -> List[IndexedTrack]:
        """All indexed tracks sorted by path (a shared list: do not mutate)."""
        with self._lock:
            self._ensure_fresh()
            if self._sorted is None:
                self._sorted = sorted(
                    (t for entries in self._dirs.values() for t in entries),
                    key=lambda t: str(t.path),
                )
            return self._sorted

    def stats(self) -> Dict[str, Any]:
        tracks = self.tracks()
        total = len(tracks)
        counts = {LyricsSyncType.WORD_SYNC.value: 0, LyricsSyncType.LINE_SYNC.value: 0, LyricsSyncType.UNSYNCED.value: 0}
        format_counts: Dict[str, int] = {}
        for t in tracks:
            if t.has_lyrics:
                counts[t.sync_type or LyricsSyncType.UNSYNCED.value] += 1
                label = (t.format or "").upper()
                format_counts[label] = format_counts.get(label, 0) + 1
        has_lyrics = sum(counts.values())

        def pct(n: int) -> float:
            return round(n / total * 100, 1) if total else 0.0

        return {
            "total_tracks": total,
            "has_lyrics_count": has_lyrics,
            "coverage_pct": pct(has_lyrics),
            "word_sync_count": counts["word_sync"],
            "word_sync_pct": pct(counts["word_sync"]),
            "line_sync_count": counts["line_sync"],
            "line_sync_pct": pct(counts["line_sync"]),
            "unsynced_count": counts["unsynced"],
            "unsynced_pct": pct(counts["unsynced"]),
            "missing_count": total - has_lyrics,
            "missing_pct": pct(total - has_lyrics),
            "format_counts": format_counts,
        }

    def mark_dir_dirty(self, audio_dir: Path) -> None:
        """Re-evaluate the tracks of one library directory on the next access."""
        with self._lock:
            self._dirty.add(Path(audio_dir))

    def notify_path_changed(self, path: Path, is_directory: bool) -> None:
        """Translate a filesystem event into the library directory that needs rescanning."""
        if is_directory:
            with self._lock:
                self._needs_full = True  # new/removed/renamed folders: rare, rebuild everything
            return
        name = path.name.lower()
        if ".tmp_" in name or not (is_supported_audio_file(path) or name.endswith(_LYRICS_SUFFIXES)):
            return
        root, out = self._watched_roots
        directory = path.parent
        if out is not None:
            try:
                # Sidecar in the mirrored output_dir -> matching library directory
                rel = directory.relative_to(out)
                if root is not None:
                    directory = root / rel
            except ValueError:
                pass
        self.mark_dir_dirty(directory)

    def start_watching(self) -> bool:
        """Start a filesystem watcher on the library (and output_dir). Returns False on failure."""
        cfg = self._get_config()
        root = Path(cfg.music_dir)
        out = Path(cfg.output_dir) if cfg.output_dir else None
        if not root.is_dir():
            return False
        try:
            observer = Observer()
            handler = _IndexEventHandler(self)
            observer.schedule(handler, str(root), recursive=True)
            if out is not None and out.is_dir() and root not in out.parents and out != root:
                observer.schedule(handler, str(out), recursive=True)
            observer.daemon = True
            observer.start()
        except Exception as e:
            logger.warning(f"Library watcher unavailable, falling back to periodic refresh: {e}")
            return False
        with self._lock:
            self._observer = observer
            self._watched_roots = (root, out)
        logger.info(f"Web UI library index is watching {root}")
        return True

    def stop_watching(self) -> None:
        with self._lock:
            observer, self._observer = self._observer, None
        if observer is not None:
            observer.stop()
            observer.join(timeout=5)

    # -- internals ----------------------------------------------------------

    def _watching(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def _ensure_fresh(self) -> None:
        cfg = self._get_config()
        root = Path(cfg.music_dir)
        out = Path(cfg.output_dir) if cfg.output_dir else None
        signature = (str(root), str(out))
        max_age = self._watched_full_resync if self._watching() else self._poll_ttl
        now = time.monotonic()

        if self._needs_full or signature != self._signature or now - self._built_at >= max_age:
            self._full_rebuild(root, out)
            self._signature = signature
            self._built_at = now
            self._needs_full = False
            self._dirty.clear()
            self._sorted = None
        elif self._dirty:
            for directory in list(self._dirty):
                self._rebuild_dir(directory, root, out)
            self._dirty.clear()
            self._sorted = None

    def _full_rebuild(self, root: Path, out: Optional[Path]) -> None:
        dirs: Dict[Path, List[IndexedTrack]] = {}
        if root.is_dir():
            for audio in iter_discover_audio_files(root):
                entry = self._make_entry(audio, root, out)
                if entry is not None:
                    dirs.setdefault(audio.parent, []).append(entry)
        self._dirs = dirs

    def _rebuild_dir(self, directory: Path, root: Path, out: Optional[Path]) -> None:
        entries: List[IndexedTrack] = []
        try:
            children = sorted(directory.iterdir()) if directory.is_dir() else []
        except OSError:
            children = []
        for child in children:
            try:
                if child.is_file() and not child.is_symlink() and is_supported_audio_file(child):
                    entry = self._make_entry(child, root, out)
                    if entry is not None:
                        entries.append(entry)
            except OSError:
                continue
        if entries:
            self._dirs[directory] = entries
        else:
            self._dirs.pop(directory, None)

    def _make_entry(self, audio: Path, root: Path, out: Optional[Path]) -> Optional[IndexedTrack]:
        try:
            relative = str(audio.relative_to(root))
            track_id = self._encode_id(audio, root)
        except ValueError:
            # Symlinked file resolving outside the library: cannot be served safely
            return None
        existing = get_existing_lyrics_file(audio, output_dir=out, music_dir=root)
        return IndexedTrack(
            path=audio,
            relative_path=relative,
            track_id=track_id,
            filename=audio.name,
            has_lyrics=existing is not None,
            format=existing[1].value if existing else None,
            sync_type=sync_type_for(existing[0], existing[1]) if existing else None,
        )
