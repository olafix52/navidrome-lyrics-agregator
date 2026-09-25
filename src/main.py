from __future__ import annotations

"""Main CLI entry point for Navidrome Lyrics Aggregator."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from src.audit import LibraryAuditor, LibraryPruner
from src.config import AppConfig, load_config
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
        config.ignore_cache = True
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "allow_plain", False):
        config.allow_plain_lyrics = True
    if getattr(args, "no_cache", False):
        config.ignore_cache = True
    if getattr(args, "cache_db", None):
        config.cache.db_path = Path(args.cache_db)
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
    if getattr(args, "fast_line_sync", False):
        config.early_exit_on_line_sync = True
    if getattr(args, "word_sync_budget", None) is not None:
        config.word_sync_search_budget = args.word_sync_budget

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
    if getattr(args, "no_cache", False):
        config.ignore_cache = True
    if getattr(args, "cache_db", None):
        config.cache.db_path = Path(args.cache_db)
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
    if getattr(args, "fast_line_sync", False):
        config.early_exit_on_line_sync = True
    if getattr(args, "word_sync_budget", None) is not None:
        config.word_sync_search_budget = args.word_sync_budget

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
    if getattr(args, "allow_plain", False):
        config.allow_plain_lyrics = True
    if getattr(args, "no_cache", False):
        config.ignore_cache = True
    if getattr(args, "cache_db", None):
        config.cache.db_path = Path(args.cache_db)

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

    if not metadata.spotify_id:
        from src.cache import get_cached_spotify_id
        cached_sp = get_cached_spotify_id(
            artist=metadata.clean_artist or metadata.artist,
            title=metadata.clean_title or metadata.title,
            isrc=metadata.isrc,
        )
        if cached_sp:
            metadata.spotify_id = cached_sp
            console.print(f"[dim cyan]Reusing cached Spotify ID: {cached_sp}[/dim cyan]")

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


async def run_cache_command(args: argparse.Namespace, config) -> None:
    """Manage SQLite persistent negative lyrics cache."""
    from src.cache import LyricsCache
    from rich.table import Table

    cache_path = getattr(args, "cache_db", None) or config.cache.db_path
    cache = LyricsCache(db_path=cache_path, ttl_days=config.cache.negative_ttl_days)

    if getattr(args, "clear", False):
        deleted = await cache.clear(expired_only=False)
        console.print(f"[bold green]✓ Cleared negative cache:[/bold green] removed {deleted} entries.")
        return

    if getattr(args, "prune", False):
        deleted = await cache.clear(expired_only=True)
        console.print(f"[bold green]✓ Pruned expired negative cache entries:[/bold green] removed {deleted} entries.")
        return

    # Default action: show stats
    stats = await cache.get_stats()
    table = Table(title="SQLite Persistent Lyrics Cache Statistics", show_header=True)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="bold green")

    table.add_row("Database File", str(stats["db_path"]))
    table.add_row("File Size", f"{stats['db_size_kb']} KB ({stats['db_size_bytes']} bytes)")
    table.add_row("Negative TTL", f"{stats['ttl_days']} days")
    table.add_row("Total Negative Entries", str(stats["total_negative_entries"]))
    table.add_row("Active Entries (within TTL)", str(stats["active_negative_entries"]))
    table.add_row("Expired Entries", str(stats["expired_negative_entries"]))
    table.add_row("Cached Spotify IDs", str(stats.get("total_spotify_ids", 0)))

    console.print(table)


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
        config.ignore_cache = True
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "allow_plain", False):
        config.allow_plain_lyrics = True
    if getattr(args, "no_cache", False):
        config.ignore_cache = True
    if getattr(args, "cache_db", None):
        config.cache.db_path = Path(args.cache_db)
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
    if getattr(args, "word_sync_budget", None) is not None:
        config.word_sync_search_budget = args.word_sync_budget

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
    from src.web.server import LOOPBACK_HOSTS, create_app, is_loopback_host

    if getattr(args, "music_dir", None):
        config.music_dir = Path(args.music_dir)
    elif getattr(args, "path", None):
        config.music_dir = Path(args.path)
    if getattr(args, "token", None):
        config.web_auth_token = args.token

    host = getattr(args, "host", None) or "127.0.0.1"
    port = getattr(args, "port", 8080) or 8080
    loopback = is_loopback_host(host)

    if not loopback and not config.web_auth_token and not getattr(args, "allow_unauthenticated", False):
        console.print(
            f"[bold red]Refusing to expose the Web UI on {host} without authentication.[/bold red]\n"
            "The API can overwrite lyrics files and your config. Set a token with [cyan]--token[/cyan] "
            "(or NLA_WEB_TOKEN), or pass [cyan]--allow-unauthenticated[/cyan] if the network is trusted."
        )
        return

    # Without a token, a loopback-only server must also reject foreign Host headers (DNS rebinding)
    trusted_hosts = LOOPBACK_HOSTS if (loopback and not config.web_auth_token) else None
    app = create_app(config, trusted_hosts=trusted_hosts)

    console.print(f"[bold green]Starting Web UI & Karaoke Dashboard at:[/bold green] http://{host}:{port}")
    if config.web_auth_token:
        console.print("[dim]Authentication enabled - open[/dim] [cyan]/?token=<token>[/cyan] [dim]once to sign in.[/dim]")
    elif not loopback:
        console.print("[bold yellow]Warning: Web UI is reachable from the network without authentication.[/bold yellow]")
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


def run_providers_command(args, config: AppConfig, config_path: Optional[Path]) -> None:
    """Handle the 'providers' CLI command for listing, enabling, and disabling providers."""
    from rich.console import Console
    from rich.table import Table
    from src.config import save_enabled_providers
    from src.providers import AVAILABLE_PROVIDERS, PROVIDER_METADATA

    console = Console()
    action = getattr(args, "provider_action", None) or "list"

    if action == "list":
        table = Table(
            title="Lyrics Providers - Status & Cascade Priority",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Priorytet", justify="center", style="bold")
        table.add_column("ID", style="yellow")
        table.add_column("Nazwa", style="white")
        table.add_column("Status", justify="center")
        table.add_column("Formaty", style="green")
        table.add_column("Klucz API", justify="center")
        table.add_column("Opis", style="dim")

        enabled_set = set(p.lower() for p in config.enabled_providers)

        # 1. Enabled providers in order
        for idx, p_id in enumerate(config.enabled_providers, start=1):
            meta = PROVIDER_METADATA.get(p_id, {})
            name = meta.get("name", p_id)
            fmts = ", ".join(meta.get("formats", [])) or "LRC/TTML"
            req_key = "[yellow]Wymagany[/]" if meta.get("requires_api_key") else "[dim]Nie[/]"
            desc = meta.get("description", "")
            table.add_row(
                str(idx),
                p_id,
                name,
                "[bold green]✓ WŁĄCZONY[/]",
                fmts,
                req_key,
                desc,
            )

        # 2. Disabled providers
        for p_id in AVAILABLE_PROVIDERS:
            if p_id not in enabled_set:
                meta = PROVIDER_METADATA.get(p_id, {})
                name = meta.get("name", p_id)
                fmts = ", ".join(meta.get("formats", [])) or "LRC/TTML"
                req_key = "[yellow]Wymagany[/]" if meta.get("requires_api_key") else "[dim]Nie[/]"
                desc = meta.get("description", "")
                table.add_row(
                    "-",
                    p_id,
                    name,
                    "[bold red]✗ WYŁĄCZONY[/]",
                    fmts,
                    req_key,
                    desc,
                )

        console.print(table)
        console.print(
            f"\n[dim]Aktywnych dostawców:[/] [bold]{len(config.enabled_providers)}[/] / {len(AVAILABLE_PROVIDERS)}. "
            f"[dim]Użyj [cyan]providers enable <id>[/] lub [cyan]providers disable <id>[/] aby zarządzać.[/]\n"
        )
        return

    if action == "enable":
        names_to_add = [n.strip().lower() for n in args.names]
        changed = []
        for n in names_to_add:
            if n not in AVAILABLE_PROVIDERS:
                console.print(f"[bold red]Błąd:[/] Nieznany provider: '{n}'. Dostępne: {', '.join(AVAILABLE_PROVIDERS.keys())}")
                continue
            if n not in config.enabled_providers:
                config.enabled_providers.append(n)
                changed.append(n)
            if n in config.providers:
                config.providers[n].enabled = True

        if changed:
            if not getattr(args, "no_save", False):
                saved_to = save_enabled_providers(config.enabled_providers, config_path)
                console.print(f"[bold green]Sukces![/] Włączono dostawców: {', '.join(changed)}. Zapisano w [cyan]{saved_to}[/].")
            else:
                console.print(f"[bold green]Sukces![/] Włączono dostawców (bez zapisu): {', '.join(changed)}.")
        else:
            console.print("[yellow]Wszyscy podani dostawcy byli już włączeni.[/]")

    elif action == "disable":
        names_to_remove = set(n.strip().lower() for n in args.names)
        changed = []
        new_enabled = []
        for p in config.enabled_providers:
            if p.lower() in names_to_remove:
                changed.append(p)
                if p.lower() in config.providers:
                    config.providers[p.lower()].enabled = False
            else:
                new_enabled.append(p)

        if changed:
            config.enabled_providers = new_enabled
            if not getattr(args, "no_save", False):
                saved_to = save_enabled_providers(config.enabled_providers, config_path)
                console.print(f"[bold green]Sukces![/] Wyłączono dostawców: {', '.join(changed)}. Zapisano w [cyan]{saved_to}[/].")
            else:
                console.print(f"[bold green]Sukces![/] Wyłączono dostawców (bez zapisu): {', '.join(changed)}.")
        else:
            console.print("[yellow]Żaden z podanych dostawców nie był aktywny.[/]")


def build_parser() -> argparse.ArgumentParser:
    """Build command line argument parser.

    Options shared between the top-level parser and subcommands use ``argparse.SUPPRESS``
    as default: otherwise the subparser's default would overwrite a value given before
    the subcommand (e.g. ``-P lrclib scan`` or ``-d /music scan``).
    """
    provider_parent = argparse.ArgumentParser(add_help=False)
    provider_parent.add_argument(
        "--providers", "-P",
        type=str,
        default=argparse.SUPPRESS,
        help="Comma-separated list of enabled providers (e.g. 'spicylyrics,lrclib')",
    )
    provider_parent.add_argument(
        "--disable-providers",
        type=str,
        default=argparse.SUPPRESS,
        help="Comma-separated list of providers to disable (e.g. 'genius,lyricsify')",
    )
    provider_parent.add_argument(
        "--enable-providers",
        type=str,
        default=argparse.SUPPRESS,
        help="Comma-separated list of providers to enable",
    )

    cache_parent = argparse.ArgumentParser(add_help=False)
    cache_parent.add_argument(
        "--no-cache",
        "--ignore-cache",
        dest="no_cache",
        action="store_true",
        help="Bypass negative cache and force querying all providers",
    )
    cache_parent.add_argument(
        "--cache-db",
        type=str,
        default=None,
        help="Path to SQLite cache database file",
    )

    parser = argparse.ArgumentParser(
        prog="navidrome-lyrics-aggregator",
        description="Sidecar lyrics aggregator service for Navidrome music server",
        parents=[provider_parent],
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
    scan_p = subparsers.add_parser("scan", parents=[provider_parent, cache_parent], help="Run a one-time scan of the music library")
    scan_p.add_argument("path", nargs="?", type=str, help="Target folder or file to scan (positional)")
    scan_p.add_argument("-t", "--target", type=str, help="Specific target folder or audio file to scan")
    scan_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
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
    scan_p.add_argument("--fast-line-sync", "--early-exit-line-sync", dest="fast_line_sync", action="store_true", help="Exit cascade immediately upon matching line-synced lyrics without searching for word-sync")
    scan_p.add_argument("--word-sync-budget", type=int, default=None, help="Maximum additional word-sync providers to check after line-sync is found")

    # DAEMON subcommand
    daemon_p = subparsers.add_parser("daemon", parents=[provider_parent, cache_parent], help="Run in daemon mode with periodic scans")
    daemon_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
    daemon_p.add_argument("-i", "--interval", type=str, help="Scan interval (e.g. '1h', '30m', '3600')")
    daemon_p.add_argument("-w", "--with-watch", action="store_true", help="Enable real-time watchdog along with periodic scans")
    daemon_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    daemon_p.add_argument("--storage-mode", type=str, choices=["sidecar", "embedded", "both"], help="Storage destination: sidecar, embedded, or both")
    daemon_p.add_argument("--output-dir", type=str, help="Custom output directory for saved sidecars")
    daemon_p.add_argument("--navidrome-url", type=str, help="Navidrome server URL")
    daemon_p.add_argument("--navidrome-user", type=str, help="Navidrome username")
    daemon_p.add_argument("--navidrome-password", type=str, help="Navidrome password")
    daemon_p.add_argument("--auto-scan", action="store_true", help="Auto-trigger Navidrome scan after downloading new lyrics")
    daemon_p.add_argument("--fast-line-sync", "--early-exit-line-sync", dest="fast_line_sync", action="store_true", help="Exit cascade immediately upon matching line-synced lyrics without searching for word-sync")
    daemon_p.add_argument("--word-sync-budget", type=int, default=None, help="Maximum additional word-sync providers to check after line-sync is found")

    # WATCH subcommand
    watch_p = subparsers.add_parser("watch", parents=[provider_parent, cache_parent], help="Watch music directory and fetch lyrics on file events")
    watch_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
    watch_p.add_argument("--allow-plain", action="store_true", help="Allow fallback to plain lyrics")
    watch_p.add_argument("--storage-mode", type=str, choices=["sidecar", "embedded", "both"], help="Storage destination: sidecar, embedded, or both")
    watch_p.add_argument("--fast-line-sync", "--early-exit-line-sync", dest="fast_line_sync", action="store_true", help="Exit cascade immediately upon matching line-synced lyrics without searching for word-sync")
    watch_p.add_argument("--word-sync-budget", type=int, default=None, help="Maximum additional word-sync providers to check after line-sync is found")

    # TEST-TRACK subcommand
    test_p = subparsers.add_parser("test-track", parents=[provider_parent], help="Test query against all providers for a single track")
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
        audit_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
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
    upgrade_p = subparsers.add_parser("upgrade", parents=[provider_parent, cache_parent], help="Fetch word-sync TTML lyrics for tracks lacking them")
    upgrade_p.add_argument("path", nargs="?", type=str, help="Target folder or file to upgrade (positional)")
    upgrade_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
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
    upgrade_p.add_argument("--word-sync-budget", type=int, default=None, help="Maximum additional word-sync providers to check after line-sync is found")

    # CACHE subcommand
    cache_p = subparsers.add_parser("cache", parents=[cache_parent], help="Manage SQLite persistent negative lyrics cache")
    cache_p.add_argument("--stats", action="store_true", help="Display cache statistics (total, active, expired, size)")
    cache_p.add_argument("--clear", action="store_true", help="Clear all entries from the negative cache")
    cache_p.add_argument("--prune", action="store_true", help="Prune only expired entries from the negative cache")

    # PRUNE subcommand
    prune_p = subparsers.add_parser("prune", help="Clean up orphaned sidecars and obsolete duplicate formats")
    prune_p.add_argument("path", nargs="?", type=str, help="Target folder to prune (positional)")
    prune_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
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
        web_p = subparsers.add_parser(cmd_name, parents=[provider_parent], help="Launch lightweight Web UI dashboard and live karaoke player")
        web_p.add_argument("path", nargs="?", type=str, help="Root music directory (positional)")
        web_p.add_argument("-d", "--music-dir", type=str, default=argparse.SUPPRESS, help="Root music directory (overrides config)")
        web_p.add_argument("-p", "--port", type=int, default=8080, help="Web server port (default: 8080)")
        web_p.add_argument(
            "--host",
            type=str,
            default="127.0.0.1",
            help="Web server bind address (default: 127.0.0.1). Non-loopback addresses require --token.",
        )
        web_p.add_argument(
            "--token",
            type=str,
            default=None,
            help="Access token for the Web UI/API (or NLA_WEB_TOKEN). Open /?token=<token> once in the browser.",
        )
        web_p.add_argument(
            "--allow-unauthenticated",
            action="store_true",
            help="Allow binding to a non-loopback address without a token (anyone on the network gets full access)",
        )

    # PROVIDERS subcommand
    providers_p = subparsers.add_parser("providers", help="List, enable, or disable lyrics providers")
    providers_sub = providers_p.add_subparsers(dest="provider_action", help="Provider action (list, enable, disable)")
    providers_sub.add_parser("list", help="List all available providers and their status (default)")

    enable_p = providers_sub.add_parser("enable", help="Enable one or more lyrics providers")
    enable_p.add_argument("names", nargs="+", help="Names of providers to enable (e.g. spicylyrics lrclib)")
    enable_p.add_argument("--no-save", action="store_true", help="Do not persist changes to config file")

    disable_p = providers_sub.add_parser("disable", help="Disable one or more lyrics providers")
    disable_p.add_argument("names", nargs="+", help="Names of providers to disable (e.g. genius lyricsify)")
    disable_p.add_argument("--no-save", action="store_true", help="Do not persist changes to config file")

    return parser


def main() -> None:
    """Main CLI execution."""
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)

    # Apply CLI provider overrides to config
    if getattr(args, "providers", None):
        config.enabled_providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    if getattr(args, "disable_providers", None):
        disabled = {p.strip().lower() for p in args.disable_providers.split(",") if p.strip()}
        config.enabled_providers = [p for p in config.enabled_providers if p.lower() not in disabled]
    if getattr(args, "enable_providers", None):
        to_enable = [p.strip().lower() for p in args.enable_providers.split(",") if p.strip()]
        for p in to_enable:
            if p not in config.enabled_providers:
                config.enabled_providers.append(p)

    if args.log_level:
        config.log_level = args.log_level

    setup_logger(config.log_level, config.log_file)

    # Default to scan mode if no subcommand is given
    command = args.command or "scan"

    if command == "providers":
        run_providers_command(args, config, config_path)
        return

    if sys.platform != "win32":
        try:
            import uvloop
            uvloop.install()
            logger.debug("High-performance uvloop event loop policy installed")
        except ImportError:
            pass

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
        elif command == "cache":
            await run_cache_command(args, config)
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
