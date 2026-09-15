"""Library audit, diagnostics, and pruning tools for Navidrome Lyrics Aggregator."""

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from rich.table import Table

from src.logger import console
from src.models import LyricsFormat, LyricsSyncType, detect_sync_type
from src.storage import LYRICS_EXTENSIONS_PRIORITY, get_existing_lyrics_file
from src.tag_reader import SUPPORTED_AUDIO_EXTENSIONS, is_supported_audio_file, read_track_metadata

logger = logging.getLogger("nla.audit")

# All recognized lyrics extensions
ALL_LYRICS_EXTENSIONS = [".ttml", ".lyricsfile.yaml", ".yaml", ".lrc", ".txt"]


def get_track_stem_and_format(path: Path) -> Optional[Tuple[str, LyricsFormat]]:
    """Extract audio track base stem and lyrics format from a lyrics sidecar file."""
    name = path.name
    if name.endswith(".lyricsfile.yaml"):
        return name[:-16], LyricsFormat.YAML
    suffix = path.suffix.lower()
    if suffix == ".ttml":
        return path.stem, LyricsFormat.TTML
    elif suffix == ".yaml":
        return path.stem, LyricsFormat.YAML
    elif suffix == ".lrc":
        return path.stem, LyricsFormat.LRC
    elif suffix == ".txt":
        return path.stem, LyricsFormat.TXT
    return None


class TrackAuditItem(BaseModel):
    """Audit result for an individual audio file."""
    audio_path: Path
    has_lyrics: bool
    lyrics_path: Optional[Path] = None
    format: Optional[LyricsFormat] = None
    sync_type: Optional[LyricsSyncType] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None


class AuditReport(BaseModel):
    """Aggregated library audit report and statistics."""
    total_tracks: int = 0
    word_sync_count: int = 0
    line_sync_count: int = 0
    unsynced_count: int = 0
    missing_count: int = 0
    format_counts: Dict[str, int] = Field(default_factory=dict)
    tracks: List[TrackAuditItem] = Field(default_factory=list)

    @property
    def has_lyrics_count(self) -> int:
        return self.total_tracks - self.missing_count

    @property
    def coverage_pct(self) -> float:
        return (self.has_lyrics_count / self.total_tracks * 100) if self.total_tracks else 0.0

    @property
    def word_sync_pct(self) -> float:
        return (self.word_sync_count / self.total_tracks * 100) if self.total_tracks else 0.0

    @property
    def line_sync_pct(self) -> float:
        return (self.line_sync_count / self.total_tracks * 100) if self.total_tracks else 0.0

    @property
    def unsynced_pct(self) -> float:
        return (self.unsynced_count / self.total_tracks * 100) if self.total_tracks else 0.0

    @property
    def missing_pct(self) -> float:
        return (self.missing_count / self.total_tracks * 100) if self.total_tracks else 0.0


class LibraryAuditor:
    """Performs offline analysis of the music library without external network requests."""

    def audit_library(
        self,
        root_dir: Path,
        load_metadata_for_missing: bool = False,
    ) -> AuditReport:
        """Scan target directory and analyze sidecars and synchronization types offline."""
        report = AuditReport()
        if not root_dir.exists():
            logger.error(f"Directory does not exist: {root_dir}")
            return report

        if root_dir.is_file():
            audio_files = [root_dir] if is_supported_audio_file(root_dir) else []
        else:
            audio_files = sorted([p for p in root_dir.rglob("*") if p.is_file() and is_supported_audio_file(p)])

        report.total_tracks = len(audio_files)

        for audio_path in audio_files:
            existing = get_existing_lyrics_file(audio_path)
            if existing:
                lyrics_path, fmt = existing
                sync_type = LyricsSyncType.UNSYNCED
                try:
                    content = lyrics_path.read_text(encoding="utf-8", errors="replace")[:8192]
                    sync_type = detect_sync_type(content, fmt)
                except Exception as e:
                    logger.debug(f"Could not inspect content of {lyrics_path}: {e}")

                if sync_type == LyricsSyncType.WORD_SYNC:
                    report.word_sync_count += 1
                elif sync_type == LyricsSyncType.LINE_SYNC:
                    report.line_sync_count += 1
                else:
                    report.unsynced_count += 1

                fmt_label = fmt.value.upper()
                report.format_counts[fmt_label] = report.format_counts.get(fmt_label, 0) + 1

                report.tracks.append(
                    TrackAuditItem(
                        audio_path=audio_path,
                        has_lyrics=True,
                        lyrics_path=lyrics_path,
                        format=fmt,
                        sync_type=sync_type,
                    )
                )
            else:
                report.missing_count += 1
                item = TrackAuditItem(
                    audio_path=audio_path,
                    has_lyrics=False,
                )
                if load_metadata_for_missing:
                    meta = read_track_metadata(audio_path)
                    if meta:
                        item.title = meta.title
                        item.artist = meta.artist
                        item.album = meta.album
                report.tracks.append(item)

        return report

    def display_report(self, report: AuditReport, show_missing_limit: int = 0) -> None:
        """Display Rich formatted tables in terminal for coverage, sync types, and formats."""
        table = Table(title="📊 Music Library Lyrics Audit 📊", border_style="cyan")
        table.add_column("Metric / Quality Level", style="bold white", justify="left")
        table.add_column("Tracks", style="bold green", justify="right")
        table.add_column("Coverage %", justify="right")

        table.add_row("Total Audio Tracks", str(report.total_tracks), "100.0%")
        table.add_row(
            "[bold green]Tracks with Lyrics (Total)[/bold green]",
            str(report.has_lyrics_count),
            f"[bold green]{report.coverage_pct:.1f}%[/bold green]",
        )
        table.add_row(
            "  [green]Word-level Sync (TTML / YAML)[/green]",
            str(report.word_sync_count),
            f"{report.word_sync_pct:.1f}%",
        )
        table.add_row(
            "  [yellow]Line-level Sync (LRC)[/yellow]",
            str(report.line_sync_count),
            f"{report.line_sync_pct:.1f}%",
        )
        table.add_row(
            "  [magenta]Unsynced (Plain Text)[/magenta]",
            str(report.unsynced_count),
            f"{report.unsynced_pct:.1f}%",
        )
        table.add_row(
            "[bold red]Missing Lyrics[/bold red]",
            str(report.missing_count),
            f"[bold red]{report.missing_pct:.1f}%[/bold red]",
        )

        console.print()
        console.print(table)

        if report.format_counts:
            fmt_table = Table(title="Existing Formats Breakdown", border_style="blue")
            fmt_table.add_column("Format", style="bold yellow")
            fmt_table.add_column("Count", style="green", justify="right")
            fmt_table.add_column("Share", justify="right")
            for fmt_name, count in sorted(report.format_counts.items(), key=lambda x: x[1], reverse=True):
                share = (count / report.has_lyrics_count * 100) if report.has_lyrics_count else 0.0
                fmt_table.add_row(fmt_name, str(count), f"{share:.1f}%")
            console.print(fmt_table)

        if show_missing_limit > 0 and report.missing_count > 0:
            missing_tracks = [t for t in report.tracks if not t.has_lyrics]
            display_count = min(len(missing_tracks), show_missing_limit)

            miss_table = Table(
                title=f"Sample Missing Tracks (Showing {display_count} of {report.missing_count})",
                border_style="red",
            )
            miss_table.add_column("File / Name", style="white")
            miss_table.add_column("Directory", style="dim")

            for item in missing_tracks[:display_count]:
                label = f"{item.artist} - {item.title}" if (item.artist and item.title) else item.audio_path.name
                miss_table.add_row(label, str(item.audio_path.parent))

            console.print(miss_table)
        console.print()

    def export_report(
        self,
        report: AuditReport,
        output_path: Path,
        missing_only: bool = False,
    ) -> None:
        """Export audit report or missing tracks to JSON or CSV."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        is_csv = output_path.suffix.lower() == ".csv"

        # Populate missing metadata if needed
        for t in report.tracks:
            if (not t.has_lyrics or not missing_only) and (not t.artist or not t.title):
                meta = read_track_metadata(t.audio_path)
                if meta:
                    t.artist = meta.artist
                    t.title = meta.title
                    t.album = meta.album

        if missing_only:
            missing_tracks = [t for t in report.tracks if not t.has_lyrics]
            if is_csv:
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["audio_path", "artist", "title", "album"])
                    for t in missing_tracks:
                        writer.writerow([str(t.audio_path), t.artist or "", t.title or "", t.album or ""])
            else:
                data = [
                    {
                        "audio_path": str(t.audio_path),
                        "artist": t.artist or "",
                        "title": t.title or "",
                        "album": t.album or "",
                    }
                    for t in missing_tracks
                ]
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            if is_csv:
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "audio_path",
                        "has_lyrics",
                        "format",
                        "sync_type",
                        "lyrics_path",
                        "artist",
                        "title",
                        "album",
                    ])
                    for t in report.tracks:
                        writer.writerow([
                            str(t.audio_path),
                            t.has_lyrics,
                            t.format.value if t.format else "",
                            t.sync_type.value if t.sync_type else "",
                            str(t.lyrics_path) if t.lyrics_path else "",
                            t.artist or "",
                            t.title or "",
                            t.album or "",
                        ])
            else:
                data = {
                    "summary": {
                        "total_tracks": report.total_tracks,
                        "has_lyrics_count": report.has_lyrics_count,
                        "coverage_pct": round(report.coverage_pct, 2),
                        "word_sync_count": report.word_sync_count,
                        "word_sync_pct": round(report.word_sync_pct, 2),
                        "line_sync_count": report.line_sync_count,
                        "line_sync_pct": round(report.line_sync_pct, 2),
                        "unsynced_count": report.unsynced_count,
                        "unsynced_pct": round(report.unsynced_pct, 2),
                        "missing_count": report.missing_count,
                        "missing_pct": round(report.missing_pct, 2),
                        "format_counts": report.format_counts,
                    },
                    "tracks": [
                        {
                            "audio_path": str(t.audio_path),
                            "has_lyrics": t.has_lyrics,
                            "format": t.format.value if t.format else None,
                            "sync_type": t.sync_type.value if t.sync_type else None,
                            "lyrics_path": str(t.lyrics_path) if t.lyrics_path else None,
                            "artist": t.artist or "",
                            "title": t.title or "",
                            "album": t.album or "",
                        }
                        for t in report.tracks
                    ],
                }
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Exported audit report to {output_path}")


class LibraryPruner:
    """Detects and deletes orphaned lyrics files and obsolete duplicate sidecars."""

    def find_orphaned_sidecars(self, root_dir: Path) -> List[Path]:
        """Find lyrics files whose associated audio file no longer exists."""
        orphans: List[Path] = []
        if not root_dir.exists():
            return orphans

        for candidate in root_dir.rglob("*"):
            if not candidate.is_file():
                continue
            parsed = get_track_stem_and_format(candidate)
            if not parsed:
                continue
            stem, _ = parsed
            # Check if any supported audio file with this stem exists in the same directory
            has_audio = any(
                (candidate.parent / f"{stem}{audio_ext}").is_file()
                for audio_ext in SUPPORTED_AUDIO_EXTENSIONS
            )
            if not has_audio:
                orphans.append(candidate)

        return sorted(orphans)

    def find_duplicate_sidecars(self, root_dir: Path) -> List[Tuple[Path, Path]]:
        """Find lower-quality duplicate sidecar files where a higher quality sidecar exists for the same track.

        Returns:
            List of (obsolete_file_to_delete, kept_higher_quality_file)
        """
        duplicates: List[Tuple[Path, Path]] = []
        if not root_dir.exists():
            return duplicates

        audio_files = [p for p in root_dir.rglob("*") if p.is_file() and is_supported_audio_file(p)]

        for audio_path in audio_files:
            # Collect all sidecars present for this audio file
            sidecars: List[Tuple[Path, LyricsFormat]] = []
            for fmt, exts in LYRICS_EXTENSIONS_PRIORITY:
                for ext in exts:
                    candidate = audio_path.parent / f"{audio_path.stem}{ext}"
                    if candidate.is_file() and candidate.stat().st_size > 0:
                        sidecars.append((candidate, fmt))

            if len(sidecars) > 1:
                # Highest priority is sidecars[0] because LYRICS_EXTENSIONS_PRIORITY is descending
                kept_file, _ = sidecars[0]
                for dup_file, _ in sidecars[1:]:
                    if dup_file != kept_file:
                        duplicates.append((dup_file, kept_file))

        return duplicates

    def execute_prune(self, files_to_delete: List[Path], dry_run: bool = True) -> int:
        """Prune specified files. If dry_run is True, only logs actions."""
        count = 0
        for f in files_to_delete:
            if dry_run:
                logger.info(f"[DRY RUN] Would delete: {f}")
                count += 1
            else:
                try:
                    f.unlink()
                    logger.info(f"Deleted: {f}")
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to delete {f}: {e}")
        return count

    def display_prune_summary(
        self,
        orphans: List[Path],
        duplicates: List[Tuple[Path, Path]],
        dry_run: bool,
    ) -> None:
        """Display Rich summary of orphaned and duplicate files found/pruned."""
        mode_str = "[yellow]DRY RUN (no files deleted)[/yellow]" if dry_run else "[bold red]LIVE DELETION[/bold red]"
        console.print(f"\n[bold cyan]🧹 Library Pruning Summary - {mode_str}[/bold cyan]\n")

        if orphans:
            t = Table(title=f"Orphaned Lyrics Files Found ({len(orphans)})", border_style="red")
            t.add_column("Orphaned File", style="red")
            t.add_column("Directory", style="dim")
            for f in orphans[:20]:
                t.add_row(f.name, str(f.parent))
            if len(orphans) > 20:
                t.add_row(f"... and {len(orphans) - 20} more", "")
            console.print(t)
        else:
            console.print("[green]✓ No orphaned lyrics files found.[/green]")

        if duplicates:
            t2 = Table(title=f"Duplicate Obsolete Sidecars Found ({len(duplicates)})", border_style="yellow")
            t2.add_column("File to Remove (Obsolete)", style="yellow")
            t2.add_column("Kept High-Quality File", style="green")
            for dup, kept in duplicates[:20]:
                t2.add_row(dup.name, kept.name)
            if len(duplicates) > 20:
                t2.add_row(f"... and {len(duplicates) - 20} more", "")
            console.print(t2)
        else:
            console.print("[green]✓ No duplicate obsolete sidecars found.[/green]")

        total_files = len(orphans) + len(duplicates)
        action_word = "would be deleted" if dry_run else "were deleted"
        console.print(f"\n[bold]Total files that {action_word}: {total_files}[/bold]\n")
        if dry_run and total_files > 0:
            console.print("[dim]Run with [bold cyan]--force[/bold cyan] to perform actual deletion.[/dim]\n")
