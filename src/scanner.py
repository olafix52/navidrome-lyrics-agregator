"""Library scanner engine for asynchronous audio scanning and batch lyrics processing."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from src.config import AppConfig
from src.logger import console
from src.matcher import LyricsMatcher
from src.models import MatchStatus, ProcessResult, TrackMetadata
from src.normalizer import clean_artist, clean_title
from src.subsonic import SubsonicClient
from src.tag_reader import is_supported_audio_file, read_track_metadata

logger = logging.getLogger("nla.scanner")


class LibraryScanner:
    """Recursively scans music directories and processes tracks concurrently."""

    def __init__(self, config: AppConfig, matcher: LyricsMatcher):
        self.config = config
        self.matcher = matcher
        self.semaphore = asyncio.Semaphore(max(1, config.concurrency))

    def discover_audio_files(self, root_dir: Path) -> List[Path]:
        """Find all supported audio files in the target directory recursively."""
        if not root_dir.exists():
            logger.error(f"Music directory does not exist: {root_dir}")
            return []

        audio_files: List[Path] = []
        if root_dir.is_file():
            if is_supported_audio_file(root_dir):
                return [root_dir]
            return []

        for p in root_dir.rglob("*"):
            if p.is_file() and is_supported_audio_file(p):
                audio_files.append(p)

        return sorted(audio_files)

    async def _process_items_bounded(
        self,
        items: List[Any],
        process_fn: Callable[[Any], Any],
        description: str,
        show_progress: bool = True,
    ) -> List[ProcessResult]:
        """Process a list of items using a bounded worker pool for constant memory footprint."""
        total = len(items)
        if total == 0:
            return []

        queue: asyncio.Queue[Any] = asyncio.Queue()
        for item in items:
            queue.put_nowait(item)

        results: List[ProcessResult] = []
        results_lock = asyncio.Lock()
        worker_count = min(max(1, self.config.concurrency), total)

        progress: Optional[Progress] = None
        task_id = None

        if show_progress:
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("({task.completed}/{task.total})"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=console,
            )

        async def _worker() -> None:
            while True:
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    res = await process_fn(item)
                    if isinstance(res, ProcessResult):
                        async with results_lock:
                            results.append(res)
                except Exception as e:
                    logger.error(f"Task failed: {e}")
                finally:
                    if progress and task_id is not None:
                        progress.advance(task_id, 1)
                    queue.task_done()

        if progress:
            with progress:
                task_id = progress.add_task(description, total=total)
                workers = [asyncio.create_task(_worker()) for _ in range(worker_count)]
                await asyncio.gather(*workers)
        else:
            workers = [asyncio.create_task(_worker()) for _ in range(worker_count)]
            await asyncio.gather(*workers)

        self.display_summary(results)

        # Trigger Navidrome scan if new lyrics were downloaded
        success_count = sum(1 for r in results if r.status == MatchStatus.SUCCESS)
        await self._maybe_trigger_navidrome_scan(success_count)

        return results

    async def process_files(
        self,
        audio_files: List[Path],
        show_progress: bool = True,
    ) -> List[ProcessResult]:
        """Process a list of audio files concurrently and download missing/upgraded lyrics."""
        return await self._process_items_bounded(
            items=audio_files,
            process_fn=self._process_single_file,
            description="[cyan]Processing tracks...",
            show_progress=show_progress,
        )

    async def process_metadata_batch(
        self,
        metadata_list: List[TrackMetadata],
        show_progress: bool = True,
    ) -> List[ProcessResult]:
        """Process a list of TrackMetadata objects concurrently (e.g. from Subsonic API)."""
        return await self._process_items_bounded(
            items=metadata_list,
            process_fn=self.matcher.process_track,
            description="[cyan]Processing Subsonic tracks...",
            show_progress=show_progress,
        )

    async def _maybe_trigger_navidrome_scan(self, success_count: int) -> None:
        """Trigger library scan on Navidrome server if configured and new lyrics were saved."""
        nd_cfg = getattr(self.config, "navidrome", None)
        if not nd_cfg or not nd_cfg.url:
            return

        if not nd_cfg.auto_scan or success_count <= 0 or self.config.dry_run:
            return

        logger.info(f"Auto-triggering Navidrome library scan ({success_count} new lyrics downloaded)...")
        try:
            client = SubsonicClient(
                base_url=nd_cfg.url,
                username=nd_cfg.user or "",
                password=nd_cfg.password or "",
            )
            try:
                res = await client.start_scan(full_scan=nd_cfg.full_scan)
                console.print(f"[bold green]✓ Navidrome scan triggered successfully:[/bold green] {res}")
            finally:
                await client.close()
        except Exception as e:
            logger.warning(f"Failed to auto-trigger Navidrome scan: {e}")

    async def scan_subsonic_library(
        self,
        show_progress: bool = True,
    ) -> List[ProcessResult]:
        """Fetch track list from Navidrome via Subsonic API and download lyrics."""
        nd_cfg = getattr(self.config, "navidrome", None)
        if not nd_cfg or not nd_cfg.url:
            raise ValueError("Navidrome URL is not configured. Specify --navidrome-url or NLA_NAVIDROME_URL.")

        client = SubsonicClient(
            base_url=nd_cfg.url,
            username=nd_cfg.user or "",
            password=nd_cfg.password or "",
        )
        try:
            connected = await client.ping()
            if not connected:
                raise ConnectionError(f"Could not authenticate or connect to Subsonic server at {nd_cfg.url}")

            sub_tracks = await client.get_all_tracks()
            logger.info(f"Loaded {len(sub_tracks)} total tracks from Subsonic server")

            metadata_list: List[TrackMetadata] = []
            for st in sub_tracks:
                if st.lyrics_present and not self.config.overwrite:
                    continue

                dest_base = self.config.output_dir if self.config.output_dir else self.config.music_dir
                track_file = dest_base / st.path.lstrip("/\\") if st.path else dest_base / f"{st.artist} - {st.title}.{st.suffix}"

                meta = TrackMetadata(
                    file_path=track_file,
                    title=st.title,
                    artist=st.artist,
                    album=st.album,
                    duration=st.duration,
                    track_number=st.track_number,
                    disc_number=st.disc_number,
                    clean_title=clean_title(st.title),
                    clean_artist=clean_artist(st.artist),
                )
                metadata_list.append(meta)

            logger.info(f"Tracks needing lyrics check: {len(metadata_list)}")
            if not metadata_list:
                console.print("[green]All tracks on Navidrome already have lyrics![/green]")
                return []

            return await self.process_metadata_batch(metadata_list, show_progress=show_progress)
        finally:
            await client.close()

    async def scan_and_process(
        self,
        target_dir: Optional[Path] = None,
        show_progress: bool = True,
    ) -> List[ProcessResult]:
        """Scan directory and download missing lyrics for all discovered audio files."""
        root = target_dir or self.config.music_dir
        logger.info(f"Scanning directory for audio tracks: {root}")

        audio_files = self.discover_audio_files(root)
        logger.info(f"Discovered {len(audio_files)} audio files")

        return await self.process_files(audio_files, show_progress=show_progress)

    async def _process_single_file(self, file_path: Path) -> ProcessResult:
        """Read metadata and invoke lyrics matcher on a single file."""
        metadata = read_track_metadata(file_path)
        if not metadata:
            return ProcessResult(
                file_path=file_path,
                status=MatchStatus.ERROR,
                error_message="Could not read metadata tags",
            )

        return await self.matcher.process_track(metadata)

    def display_summary(self, results: List[ProcessResult]) -> None:
        """Display a structured Rich summary table of scan results."""
        counts: Dict[MatchStatus, int] = {
            MatchStatus.SUCCESS: 0,
            MatchStatus.SKIPPED: 0,
            MatchStatus.NOT_FOUND: 0,
            MatchStatus.ERROR: 0,
        }
        by_provider: Dict[str, int] = {}
        by_format: Dict[str, int] = {}

        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
            if r.status == MatchStatus.SUCCESS:
                if r.provider:
                    by_provider[r.provider] = by_provider.get(r.provider, 0) + 1
                if r.format:
                    by_format[r.format.value.upper()] = by_format.get(r.format.value.upper(), 0) + 1

        total = len(results)

        table = Table(title="🎵 Navidrome Lyrics Aggregator - Scan Summary 🎵", border_style="cyan")
        table.add_column("Metric", style="bold white", justify="left")
        table.add_column("Count", style="bold green", justify="right")
        table.add_column("Percentage", justify="right")

        table.add_row("Total Files Scanned", str(total), "100.0%")
        table.add_row(
            "[green]Lyrics Downloaded (Success)[/green]",
            str(counts[MatchStatus.SUCCESS]),
            f"{(counts[MatchStatus.SUCCESS] / total * 100):.1f}%" if total else "0%",
        )
        table.add_row(
            "[yellow]Already Had Lyrics (Skipped)[/yellow]",
            str(counts[MatchStatus.SKIPPED]),
            f"{(counts[MatchStatus.SKIPPED] / total * 100):.1f}%" if total else "0%",
        )
        table.add_row(
            "[magenta]Lyrics Not Found[/magenta]",
            str(counts[MatchStatus.NOT_FOUND]),
            f"{(counts[MatchStatus.NOT_FOUND] / total * 100):.1f}%" if total else "0%",
        )
        table.add_row(
            "[red]Errors[/red]",
            str(counts[MatchStatus.ERROR]),
            f"{(counts[MatchStatus.ERROR] / total * 100):.1f}%" if total else "0%",
        )

        console.print()
        console.print(table)

        if by_format:
            fmt_table = Table(title="Downloaded Formats Breakdown", border_style="blue")
            fmt_table.add_column("Format", style="bold yellow")
            fmt_table.add_column("Count", style="green", justify="right")
            for fmt_name, count in sorted(by_format.items(), key=lambda x: x[1], reverse=True):
                fmt_table.add_row(fmt_name, str(count))
            console.print(fmt_table)

        if by_provider:
            prov_table = Table(title="Provider Hits Breakdown", border_style="magenta")
            prov_table.add_column("Provider", style="bold cyan")
            prov_table.add_column("Hits", style="green", justify="right")
            for prov_name, count in sorted(by_provider.items(), key=lambda x: x[1], reverse=True):
                prov_table.add_row(prov_name, str(count))
            console.print(prov_table)
        console.print()
