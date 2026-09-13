"""Filesystem watcher using Watchdog to process new and modified audio files in real-time."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, Optional, Set
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from src.config import AppConfig
from src.matcher import LyricsMatcher
from src.models import MatchStatus
from src.tag_reader import is_supported_audio_file, read_track_metadata

logger = logging.getLogger("nla.watcher")


class AudioFileEventHandler(FileSystemEventHandler):
    """Handles watchdog filesystem events for audio files with debouncing."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        matcher: LyricsMatcher,
        debounce_seconds: float = 3.0,
    ):
        super().__init__()
        self.loop = loop
        self.matcher = matcher
        self.debounce_seconds = debounce_seconds
        self._pending_files: Dict[Path, float] = {}
        self._lock = asyncio.Lock()
        self._processing: Set[Path] = set()

    def _schedule_event(self, path_str: str) -> None:
        file_path = Path(path_str)
        if is_supported_audio_file(file_path):
            self.loop.call_soon_threadsafe(
                asyncio.create_task,
                self._debounce_and_process(file_path),
            )

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_event(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._schedule_event(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            if hasattr(event, "dest_path") and event.dest_path:
                self._schedule_event(event.dest_path)

    async def _debounce_and_process(self, file_path: Path) -> None:
        """Wait until file stops changing, then process."""
        self._pending_files[file_path] = time.monotonic()

        # Wait debounce time
        await asyncio.sleep(self.debounce_seconds)

        # If a newer event arrived, let the newer task handle it
        last_event_time = self._pending_files.get(file_path, 0)
        if time.monotonic() - last_event_time < self.debounce_seconds:
            return

        if file_path in self._processing:
            return

        self._processing.add(file_path)
        try:
            self._pending_files.pop(file_path, None)
            if not file_path.is_file() or not is_supported_audio_file(file_path):
                return

            logger.info(f"[WATCHER EVENT] Detected audio file: {file_path.name}")
            metadata = read_track_metadata(file_path)
            if metadata:
                result = await self.matcher.process_track(metadata)
                if result.status == MatchStatus.SUCCESS:
                    logger.info(f"[WATCHER] Saved lyrics for {metadata.display_name()}")
        except Exception as e:
            logger.error(f"[WATCHER] Error processing {file_path.name}: {e}")
        finally:
            self._processing.discard(file_path)


class DirectoryWatcher:
    """Watches music directory and triggers automatic lyrics fetching on new audio files."""

    def __init__(self, config: AppConfig, matcher: LyricsMatcher):
        self.config = config
        self.matcher = matcher
        self.observer: Optional[Observer] = None

    async def start(self) -> None:
        """Start the watchdog observer."""
        root_dir = self.config.music_dir
        if not root_dir.exists():
            logger.error(f"Music directory to watch does not exist: {root_dir}")
            return

        loop = asyncio.get_running_loop()
        handler = AudioFileEventHandler(
            loop=loop,
            matcher=self.matcher,
            debounce_seconds=self.config.watch_debounce_seconds,
        )

        self.observer = Observer()
        self.observer.schedule(handler, str(root_dir), recursive=True)
        self.observer.start()

        logger.info(f"Started real-time file watcher on: {root_dir}")

        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the watchdog observer."""
        if self.observer and self.observer.is_alive():
            logger.info("Stopping filesystem watcher...")
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
