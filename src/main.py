"""Main CLI entry point for Navidrome Lyrics Aggregator."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from src.config import load_config
from src.logger import console, setup_logger
from src.matcher import LyricsMatcher
from src.models import TrackMetadata
from src.normalizer import clean_artist, clean_title
from src.providers import build_provider_cascade
from src.scanner import LibraryScanner
from src.watcher import DirectoryWatcher


async def run_scan_command(args: argparse.Namespace, config) -> None:
    """Execute one-time library scan."""
    if args.force:
        config.overwrite = True
    if args.dry_run:
        config.dry_run = True
    if args.allow_plain:
        config.allow_plain_lyrics = True
    if args.concurrency:
        config.concurrency = args.concurrency
    if args.music_dir:
        config.music_dir = Path(args.music_dir)

    target_str = getattr(args, "path", None) or getattr(args, "target", None) or getattr(args, "music_dir", None)
    target_path = Path(target_str) if target_str else config.music_dir

    providers = build_provider_cascade(config)
    matcher = LyricsMatcher(config, providers)
    scanner = LibraryScanner(config, matcher)

    try:
        await scanner.scan_and_process(target_path, show_progress=not args.no_progress)
    finally:
        await matcher.close()


async def run_daemon_command(args: argparse.Namespace, config) -> None:
    """Execute continuous daemon mode with scheduled scans and optional watcher."""
    if args.music_dir:
        config.music_dir = Path(args.music_dir)
    if args.interval:
        config.scan_interval = args.interval
    if args.allow_plain:
        config.allow_plain_lyrics = True

    providers = build_provider_cascade(config)
    matcher = LyricsMatcher(config, providers)
    scanner = LibraryScanner(config, matcher)
    watcher = DirectoryWatcher(config, matcher) if args.with_watch else None

    interval_sec = config.scan_interval_seconds
    console.print(
        f"[bold green]Starting daemon mode for:[/bold green] {config.music_dir} "
        f"(Interval: {config.scan_interval} / {interval_sec}s, Realtime watcher: {bool(args.with_watch)})"
    )

    watcher_task = None
    if watcher:
        watcher_task = asyncio.create_task(watcher.start())

    try:
        while True:
            console.print(f"[bold cyan]>>> Running periodic library scan at {Path(config.music_dir)}[/bold cyan]")
            await scanner.scan_and_process(show_progress=False)
            console.print(f"[yellow]Sleeping for {interval_sec} seconds until next scan...[/yellow]")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        console.print("[bold red]Daemon received cancellation, shutting down...[/bold red]")
    finally:
        if watcher:
            watcher.stop()
        if watcher_task:
            watcher_task.cancel()
        await matcher.close()


async def run_watch_command(args: argparse.Namespace, config) -> None:
    """Execute real-time filesystem watcher mode."""
    if args.music_dir:
        config.music_dir = Path(args.music_dir)
    if args.allow_plain:
        config.allow_plain_lyrics = True

    providers = build_provider_cascade(config)
    matcher = LyricsMatcher(config, providers)
    watcher = DirectoryWatcher(config, matcher)

    console.print(f"[bold green]Starting real-time file watcher on:[/bold green] {config.music_dir}")

    try:
        await watcher.start()
    except asyncio.CancelledError:
        console.print("[bold red]Watcher stopped by signal[/bold red]")
    finally:
        watcher.stop()
        await matcher.close()


async def run_test_track_command(args: argparse.Namespace, config) -> None:
    """Test searching all providers for a specific track directly from command line."""
    providers = build_provider_cascade(config)
    matcher = LyricsMatcher(config, providers)

    fake_path = Path(f"/tmp/{args.artist} - {args.title}.mp3")
    metadata = TrackMetadata(
        file_path=fake_path,
        title=args.title,
        artist=args.artist,
        album=args.album,
        duration=float(args.duration or 0),
        clean_title=clean_title(args.title),
        clean_artist=clean_artist(args.artist),
    )

    console.print(f"[bold cyan]Testing track lookup:[/bold cyan] {metadata.display_name()} ({metadata.duration}s)")
    console.print(f"[dim]Cleaned: {metadata.clean_artist} - {metadata.clean_title}[/dim]")
    console.print()

    for provider in providers:
        console.print(f"[bold yellow]Querying provider '{provider.name}'...[/bold yellow]")
        try:
            res = await provider.get_lyrics(metadata)
            if res:
                console.print(
                    f"  [bold green]HIT![/bold green] Format: [magenta]{res.format.value.upper()}[/magenta] "
                    f"| Sync: [cyan]{res.sync_type.value}[/cyan] | Title: {res.title} | Artist: {res.artist}"
                )
                console.print("--- Content Preview (first 10 lines) ---")
                lines = res.content.splitlines()[:10]
                for line in lines:
                    console.print(f"  {line}")
                if len(res.content.splitlines()) > 10:
                    console.print(f"  ... (+{len(res.content.splitlines()) - 10} more lines)")
                console.print("----------------------------------------\n")
            else:
                console.print("  [dim]No lyrics found on this provider[/dim]\n")
        except Exception as e:
            console.print(f"  [bold red]Error querying {provider.name}:[/bold red] {e}\n")

    await matcher.close()


def build_parser() -> argparse.ArgumentParser:
    """Build command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="navidrome-lyrics-aggregator",
        description="Sidecar lyrics aggregator service for Navidrome music server",
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        help="Path to custom config.yaml file",
    )
    parser.add_argument(
        "-d", "--music-dir",
        type=str,
        help="Root music directory (overrides config)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity level",
    )

    subparsers = parser.add_subparsers(dest="command", help="Operational mode")

    # SCAN subcommand
    scan_p = subparsers.add_parser("scan", help="Run a one-time scan of the music library")
    scan_p.add_argument("path", nargs="?", type=str, help="Target folder or file to scan (positional)")
    scan_p.add_argument("-t", "--target", type=str, help="Specific target folder or audio file to scan")
    scan_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    scan_p.add_argument("-f", "--force", action="store_true", help="Force re-fetching and overwrite existing lyrics")
    scan_p.add_argument("--dry-run", action="store_true", help="Simulate scan without writing files")
    scan_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    scan_p.add_argument("--concurrency", type=int, help="Number of concurrent download tasks")
    scan_p.add_argument("--no-progress", action="store_true", help="Disable rich progress bar")

    # DAEMON subcommand
    daemon_p = subparsers.add_parser("daemon", help="Run in daemon mode with periodic scans")
    daemon_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    daemon_p.add_argument("-i", "--interval", type=str, help="Scan interval (e.g. '1h', '30m', '3600')")
    daemon_p.add_argument("-w", "--with-watch", action="store_true", help="Enable real-time watchdog along with periodic scans")
    daemon_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")

    # WATCH subcommand
    watch_p = subparsers.add_parser("watch", help="Watch music directory and fetch lyrics on file events")
    watch_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    watch_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")

    # TEST-TRACK subcommand
    test_p = subparsers.add_parser("test-track", help="Test query against all providers for a single track")
    test_p.add_argument("-t", "--title", type=str, required=True, help="Track title")
    test_p.add_argument("-a", "--artist", type=str, required=True, help="Track artist")
    test_p.add_argument("--album", type=str, help="Track album name")
    test_p.add_argument("--duration", type=float, default=0.0, help="Track duration in seconds")

    return parser


def main() -> None:
    """Main CLI execution."""
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)

    if args.log_level:
        config.log_level = args.log_level

    setup_logger(config.log_level, config.log_file)

    # Default to scan mode if no subcommand is given
    command = args.command or "scan"

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task: Optional[asyncio.Task] = None

    def _signal_handler():
        if main_task and not main_task.done():
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Signal handling on Windows
            pass

    async def _async_main():
        if command == "scan":
            # For default scan when no subcommand was given, set default attributes
            if not hasattr(args, "force"):
                args.force = False
            if not hasattr(args, "dry_run"):
                args.dry_run = False
            if not hasattr(args, "allow_plain"):
                args.allow_plain = False
            if not hasattr(args, "concurrency"):
                args.concurrency = None
            if not hasattr(args, "target"):
                args.target = None
            if not hasattr(args, "no_progress"):
                args.no_progress = False
            await run_scan_command(args, config)
        elif command == "daemon":
            await run_daemon_command(args, config)
        elif command == "watch":
            await run_watch_command(args, config)
        elif command == "test-track":
            await run_test_track_command(args, config)
        else:
            parser.print_help()

    try:
        main_task = loop.create_task(_async_main())
        loop.run_until_complete(main_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
