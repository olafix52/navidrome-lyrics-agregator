"""Library scanner engine for asynchronous audio scanning and batch lyrics processing."""

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from src.config import AppConfig
from src.logger import console
from src.matcher import LyricsMatcher
from src.models import MatchStatus, ProcessResult
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

    async def process_files(
        self,
        audio_files: List[Path],
        show_progress: bool = True,
    ) -> List[ProcessResult]:
        """Process a list of audio files concurrently and download missing/upgraded lyrics."""
        total_files = len(audio_files)
        if total_files == 0:
            return []

        results: List[ProcessResult] = []

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

            with progress:
                task_id = progress.add_task("[cyan]Processing tracks...", total=total_files)

                async def _worker(file_path: Path) -> ProcessResult:
                    async with self.semaphore:
                        res = await self._process_single_file(file_path)
                        progress.advance(task_id, 1)
                        return res

                tasks = [_worker(fp) for fp in audio_files]
                results = await asyncio.gather(*tasks)
        else:
            async def _worker_no_prog(file_path: Path) -> ProcessResult:
                async with self.semaphore:
                    return await self._process_single_file(file_path)

            tasks = [_worker_no_prog(fp) for fp in audio_files]
            results = await asyncio.gather(*tasks)

        self.display_summary(results)
        return results

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
