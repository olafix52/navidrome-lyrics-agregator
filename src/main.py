"""Main CLI entry point for Navidrome Lyrics Aggregator."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from src.audit import LibraryAuditor, LibraryPruner
from src.config import load_config
from src.logger import console, setup_logger
from src.matcher import LyricsMatcher
from src.models import LyricsFormat, TrackMetadata
from src.normalizer import clean_artist, clean_title
from src.providers import build_provider_cascade
from src.scanner import LibraryScanner
from src.storage import get_existing_lyrics_file
from src.watcher import DirectoryWatcher

logger = logging.getLogger("nla.main")


async def run_scan_command(args: argparse.Namespace, config) -> None:
    """Execute one-time library scan."""
    if getattr(args, "force", False):
        config.overwrite = True
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "allow_plain", False):
        config.allow_plain_lyrics = True
    if getattr(args, "concurrency", None):
        config.concurrency = args.concurrency
    if getattr(args, "music_dir", None):
        config.music_dir = Path(args.music_dir)
    if getattr(args, "storage_mode", None):
        config.storage_mode = args.storage_mode
    if getattr(args, "output_dir", None):
        config.output_dir = Path(args.output_dir)
    if getattr(args, "navidrome_url", None):
        config.navidrome.url = args.navidrome_url
    if getattr(args, "navidrome_user", None):
        config.navidrome.user = args.navidrome_user
    if getattr(args, "navidrome_password", None):
        config.navidrome.password = args.navidrome_password
    if getattr(args, "auto_scan", False):
        config.navidrome.auto_scan = True

    target_str = getattr(args, "path", None) or getattr(args, "target", None) or getattr(args, "music_dir", None)
    target_path = Path(target_str) if target_str else config.music_dir

    providers = build_provider_cascade(config)
    matcher = LyricsMatcher(config, providers)
    scanner = LibraryScanner(config, matcher)

    try:
        if getattr(args, "subsonic", False):
            console.print(f"[bold cyan]Scanning library via Navidrome Subsonic API:[/bold cyan] {config.navidrome.url}")
            await scanner.scan_subsonic_library(show_progress=not getattr(args, "no_progress", False))
        else:
            await scanner.scan_and_process(target_path, show_progress=not getattr(args, "no_progress", False))
    finally:
        await matcher.close()


async def run_trigger_scan_command(args: argparse.Namespace, config) -> None:
    """Trigger library scan on the Navidrome server."""
    from src.subsonic import SubsonicClient
    url = getattr(args, "navidrome_url", None) or config.navidrome.url
    user = getattr(args, "navidrome_user", None) or config.navidrome.user
    pw = getattr(args, "navidrome_password", None) or config.navidrome.password
    if not url:
        console.print("[bold red]Error: Navidrome URL is not set. Use --navidrome-url or set NAVIDROME_URL in config/env.[/bold red]")
        return

    client = SubsonicClient(base_url=url, username=user or "", password=pw or "")
    try:
        res = await client.start_scan(full_scan=getattr(args, "full", False))
        console.print(f"[bold green]✓ Successfully triggered Navidrome scan at {url}:[/bold green] {res}")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to trigger Navidrome scan: {e}[/bold red]")
    finally:
        await client.close()


async def run_ping_navidrome_command(args: argparse.Namespace, config) -> None:
    """Test connection and authentication to Navidrome Subsonic API."""
    from src.subsonic import SubsonicClient
    url = getattr(args, "navidrome_url", None) or config.navidrome.url
    user = getattr(args, "navidrome_user", None) or config.navidrome.user
    pw = getattr(args, "navidrome_password", None) or config.navidrome.password
    if not url:
        console.print("[bold red]Error: Navidrome URL is not set. Use --navidrome-url or set NAVIDROME_URL in config/env.[/bold red]")
        return

    client = SubsonicClient(base_url=url, username=user or "", password=pw or "")
    try:
        ok = await client.ping()
        if ok:
            console.print(f"[bold green]✓ Successfully connected and authenticated with Navidrome at {url}[/bold green]")
        else:
            console.print(f"[bold red]✗ Failed to connect/authenticate with Navidrome at {url}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]✗ Error connecting to Navidrome: {e}[/bold red]")
    finally:
        await client.close()


async def run_daemon_command(args: argparse.Namespace, config) -> None:
    """Execute continuous daemon mode with scheduled scans and optional watcher."""
    if getattr(args, "music_dir", None):
        config.music_dir = Path(args.music_dir)
    if getattr(args, "interval", None):
        config.scan_interval = args.interval
    if getattr(args, "allow_plain", False):
        config.allow_plain_lyrics = True
    if getattr(args, "storage_mode", None):
        config.storage_mode = args.storage_mode
    if getattr(args, "output_dir", None):
        config.output_dir = Path(args.output_dir)
    if getattr(args, "navidrome_url", None):
        config.navidrome.url = args.navidrome_url
    if getattr(args, "navidrome_user", None):
        config.navidrome.user = args.navidrome_user
    if getattr(args, "navidrome_password", None):
        config.navidrome.password = args.navidrome_password
    if getattr(args, "auto_scan", False):
        config.navidrome.auto_scan = True

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
            try:
                await scanner.scan_and_process(show_progress=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Periodic scan failed, will retry next interval: {e}", exc_info=True)
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
        spotify_id=getattr(args, "spotify_id", None),
        isrc=getattr(args, "isrc", None),
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


async def run_audit_command(args: argparse.Namespace, config) -> None:
    """Execute offline library audit and coverage reporting."""
    target_str = getattr(args, "path", None) or getattr(args, "music_dir", None)
    target_path = Path(target_str) if target_str else config.music_dir

    console.print(f"[bold cyan]Running offline lyrics audit on:[/bold cyan] {target_path}")
    auditor = LibraryAuditor()
    show_missing_limit = getattr(args, "show_missing", 0) or 0
    needs_meta = bool(getattr(args, "export_missing", None) or (show_missing_limit > 0))
    report = auditor.audit_library(target_path, load_metadata_for_missing=needs_meta)
    auditor.display_report(report, show_missing_limit=show_missing_limit)

    if getattr(args, "export_missing", None):
        auditor.export_report(report, Path(args.export_missing), missing_only=True)
        console.print(f"[bold green]✓ Exported missing tracks to:[/bold green] {args.export_missing}")

    if getattr(args, "export_report", None):
        auditor.export_report(report, Path(args.export_report), missing_only=False)
        console.print(f"[bold green]✓ Exported full audit report to:[/bold green] {args.export_report}")


async def run_upgrade_command(args: argparse.Namespace, config) -> None:
    """Scan and upgrade tracks with missing or lower-quality lyrics (to TTML word-sync)."""
    if getattr(args, "force", False):
        config.overwrite = True
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "allow_plain", False):
        config.allow_plain_lyrics = True
    if getattr(args, "concurrency", None):
        config.concurrency = args.concurrency
    if getattr(args, "music_dir", None):
        config.music_dir = Path(args.music_dir)
    if getattr(args, "storage_mode", None):
        config.storage_mode = args.storage_mode
    if getattr(args, "output_dir", None):
        config.output_dir = Path(args.output_dir)
    if getattr(args, "auto_scan", False):
        config.navidrome.auto_scan = True

    target_str = getattr(args, "path", None) or getattr(args, "music_dir", None)
    target_path = Path(target_str) if target_str else config.music_dir

    providers = build_provider_cascade(config)
    matcher = LyricsMatcher(config, providers)
    scanner = LibraryScanner(config, matcher)

    try:
        all_audio = scanner.discover_audio_files(target_path)
        console.print(f"[bold cyan]Discovered {len(all_audio)} total tracks in:[/bold cyan] {target_path}")

        candidates = []
        for audio_path in all_audio:
            existing = get_existing_lyrics_file(audio_path, output_dir=config.output_dir)
            if existing:
                _, fmt = existing
                if not args.force and fmt in (LyricsFormat.TTML, LyricsFormat.YAML):
                    # Already top-tier word-sync lyrics, skip
                    continue
                if args.only_missing:
                    # Track already has lyrics, skip because only_missing requested
                    continue
                candidates.append(audio_path)
            else:
                if args.only_lrc:
                    # Track has no lyrics, skip because only_lrc requested
                    continue
                candidates.append(audio_path)

        console.print(f"[bold green]Tracks targeted for upgrade/fetch:[/bold green] {len(candidates)} of {len(all_audio)}")
        if not candidates:
            console.print("[green]All tracks already have top quality lyrics! Nothing to upgrade.[/green]")
            return

        await scanner.process_files(candidates, show_progress=not args.no_progress)
    finally:
        await matcher.close()


async def run_prune_command(args: argparse.Namespace, config) -> None:
    """Execute library pruning of orphaned lyrics and obsolete lower-quality duplicates."""
    target_str = getattr(args, "path", None) or getattr(args, "music_dir", None)
    target_path = Path(target_str) if target_str else config.music_dir

    pruner = LibraryPruner()
    orphans = [] if args.duplicates_only else pruner.find_orphaned_sidecars(target_path)
    duplicates = [] if args.orphans_only else pruner.find_duplicate_sidecars(target_path)

    dry_run = not args.force
    if args.dry_run:
        dry_run = True

    pruner.display_prune_summary(orphans, duplicates, dry_run=dry_run)

    if not dry_run:
        files_to_delete = list(orphans) + [dup for dup, _ in duplicates]
        if files_to_delete:
            count = pruner.execute_prune(files_to_delete, dry_run=False)
            console.print(f"[bold green]Successfully deleted {count} obsolete/orphaned files.[/bold green]\n")


async def run_web_command(args: argparse.Namespace, config) -> None:
    """Launch the Web UI dashboard and live karaoke player server."""
    import uvicorn
    from src.web.server import create_app

    if getattr(args, "music_dir", None):
        config.music_dir = Path(args.music_dir)
    elif getattr(args, "path", None):
        config.music_dir = Path(args.path)

    host = getattr(args, "host", "0.0.0.0") or "0.0.0.0"
    port = getattr(args, "port", 8080) or 8080

    app = create_app(config)

    console.print(f"[bold green]Starting Web UI & Karaoke Dashboard at:[/bold green] http://{host}:{port}")
    console.print(f"[dim]Serving music library from:[/dim] {config.music_dir}")

    server_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level=config.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(server_config)
    await server.serve()




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
    scan_p.add_argument("-f", "--force", "--overwrite", dest="force", action="store_true", help="Force re-fetching and overwrite existing lyrics")
    scan_p.add_argument("--dry-run", action="store_true", help="Simulate scan without writing files")
    scan_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    scan_p.add_argument("--concurrency", type=int, help="Number of concurrent download tasks")
    scan_p.add_argument("--no-progress", action="store_true", help="Disable rich progress bar")
    scan_p.add_argument("--storage-mode", type=str, choices=["sidecar", "embedded", "both"], help="Storage destination: sidecar (default), embedded (tags), or both")
    scan_p.add_argument("--output-dir", type=str, help="Custom output directory for saved sidecars")
    scan_p.add_argument("--subsonic", action="store_true", help="Fetch tracks from Navidrome Subsonic API instead of scanning local disk")
    scan_p.add_argument("--navidrome-url", type=str, help="Navidrome server URL (e.g. http://localhost:4533)")
    scan_p.add_argument("--navidrome-user", type=str, help="Navidrome username")
    scan_p.add_argument("--navidrome-password", type=str, help="Navidrome password")
    scan_p.add_argument("--auto-scan", action="store_true", help="Auto-trigger Navidrome scan after downloading new lyrics")

    # DAEMON subcommand
    daemon_p = subparsers.add_parser("daemon", help="Run in daemon mode with periodic scans")
    daemon_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    daemon_p.add_argument("-i", "--interval", type=str, help="Scan interval (e.g. '1h', '30m', '3600')")
    daemon_p.add_argument("-w", "--with-watch", action="store_true", help="Enable real-time watchdog along with periodic scans")
    daemon_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    daemon_p.add_argument("--storage-mode", type=str, choices=["sidecar", "embedded", "both"], help="Storage destination: sidecar, embedded, or both")
    daemon_p.add_argument("--output-dir", type=str, help="Custom output directory for saved sidecars")
    daemon_p.add_argument("--navidrome-url", type=str, help="Navidrome server URL")
    daemon_p.add_argument("--navidrome-user", type=str, help="Navidrome username")
    daemon_p.add_argument("--navidrome-password", type=str, help="Navidrome password")
    daemon_p.add_argument("--auto-scan", action="store_true", help="Auto-trigger Navidrome scan after downloading new lyrics")

    # WATCH subcommand
    watch_p = subparsers.add_parser("watch", help="Watch music directory and fetch lyrics on file events")
    watch_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    watch_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    watch_p.add_argument("--storage-mode", type=str, choices=["sidecar", "embedded", "both"], help="Storage destination: sidecar, embedded, or both")

    # TEST-TRACK subcommand
    test_p = subparsers.add_parser("test-track", help="Test query against all providers for a single track")
    test_p.add_argument("-t", "--title", type=str, required=True, help="Track title")
    test_p.add_argument("-a", "--artist", type=str, required=True, help="Track artist")
    test_p.add_argument("--album", type=str, help="Track album name")
    test_p.add_argument("--duration", type=float, default=0.0, help="Track duration in seconds")
    test_p.add_argument("--spotify-id", type=str, help="Spotify Track ID or URL")
    test_p.add_argument("--isrc", type=str, help="ISRC code (e.g. GBUM71029604)")

    # AUDIT / STATS subcommand
    for cmd_name in ["audit", "stats"]:
        audit_p = subparsers.add_parser(cmd_name, help="Analyze library lyrics coverage and formats offline")
        audit_p.add_argument("path", nargs="?", type=str, help="Target folder or file to audit (positional)")
        audit_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
        audit_p.add_argument("--export-missing", type=str, help="Export missing tracks to JSON or CSV file")
        audit_p.add_argument("--export-report", type=str, help="Export full audit report to JSON or CSV file")
        audit_p.add_argument(
            "--show-missing",
            nargs="?",
            const=20,
            type=int,
            default=0,
            help="Show missing tracks sample in terminal (default: 20)",
        )

    # UPGRADE subcommand
    upgrade_p = subparsers.add_parser("upgrade", help="Fetch word-sync TTML lyrics for tracks lacking them")
    upgrade_p.add_argument("path", nargs="?", type=str, help="Target folder or file to upgrade (positional)")
    upgrade_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    upgrade_p.add_argument("--only-lrc", action="store_true", help="Only upgrade tracks that already have line-sync/plain lyrics")
    upgrade_p.add_argument("--only-missing", action="store_true", help="Only download lyrics for tracks with no lyrics at all")
    upgrade_p.add_argument("-f", "--force", action="store_true", help="Force re-fetching even if TTML already exists")
    upgrade_p.add_argument("--dry-run", action="store_true", help="Simulate upgrade without writing files")
    upgrade_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    upgrade_p.add_argument("--concurrency", type=int, help="Number of concurrent download tasks")
    upgrade_p.add_argument("--no-progress", action="store_true", help="Disable rich progress bar")
    upgrade_p.add_argument("--storage-mode", type=str, choices=["sidecar", "embedded", "both"], help="Storage destination: sidecar, embedded, or both")
    upgrade_p.add_argument("--output-dir", type=str, help="Custom output directory for saved sidecars")
    upgrade_p.add_argument("--auto-scan", action="store_true", help="Auto-trigger Navidrome scan after upgrading lyrics")

    # PRUNE subcommand
    prune_p = subparsers.add_parser("prune", help="Clean up orphaned sidecars and obsolete duplicate formats")
    prune_p.add_argument("path", nargs="?", type=str, help="Target folder to prune (positional)")
    prune_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
    prune_p.add_argument("--dry-run", action="store_true", help="Simulate prune without deleting files (default)")
    prune_p.add_argument("-f", "--force", action="store_true", help="Perform actual deletion of files")
    prune_p.add_argument("--orphans-only", action="store_true", help="Only delete orphaned sidecars without audio")
    prune_p.add_argument("--duplicates-only", action="store_true", help="Only delete duplicate lower-quality sidecars")

    # TRIGGER-SCAN subcommand
    trigger_p = subparsers.add_parser("trigger-scan", help="Trigger a library scan on the Navidrome server")
    trigger_p.add_argument("--full", action="store_true", help="Request full rescan instead of quick scan")
    trigger_p.add_argument("--navidrome-url", type=str, help="Navidrome server URL")
    trigger_p.add_argument("--navidrome-user", type=str, help="Navidrome username")
    trigger_p.add_argument("--navidrome-password", type=str, help="Navidrome password")

    # PING-NAVIDROME subcommand
    ping_p = subparsers.add_parser("ping-navidrome", help="Test connection and authentication to Navidrome Subsonic API")
    ping_p.add_argument("--navidrome-url", type=str, help="Navidrome server URL")
    ping_p.add_argument("--navidrome-user", type=str, help="Navidrome username")
    ping_p.add_argument("--navidrome-password", type=str, help="Navidrome password")

    # WEB / DASHBOARD subcommand
    for cmd_name in ["web", "dashboard"]:
        web_p = subparsers.add_parser(cmd_name, help="Launch lightweight Web UI dashboard and live karaoke player")
        web_p.add_argument("path", nargs="?", type=str, help="Root music directory (positional)")
        web_p.add_argument("-d", "--music-dir", type=str, help="Root music directory (overrides config)")
        web_p.add_argument("-p", "--port", type=int, default=8080, help="Web server port (default: 8080)")
        web_p.add_argument("--host", type=str, default="0.0.0.0", help="Web server host (default: 0.0.0.0)")

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
        elif command in ("audit", "stats"):
            await run_audit_command(args, config)
        elif command == "upgrade":
            await run_upgrade_command(args, config)
        elif command == "prune":
            await run_prune_command(args, config)
        elif command in ("web", "dashboard"):
            await run_web_command(args, config)
        elif command == "trigger-scan":
            await run_trigger_scan_command(args, config)
        elif command == "ping-navidrome":
            await run_ping_navidrome_command(args, config)
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
