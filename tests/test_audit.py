"""Unit tests for library audit, upgrade, and prune tools."""

import json
import csv
from pathlib import Path
import pytest

from src.audit import (
    AuditReport,
    LibraryAuditor,
    LibraryPruner,
    TrackAuditItem,
    get_track_stem_and_format,
)
from src.config import AppConfig
from src.models import LyricsFormat, LyricsSyncType
from src.main import run_audit_command, run_upgrade_command, run_prune_command


def test_get_track_stem_and_format():
    """Verify stem and format parsing from lyrics sidecar filenames."""
    assert get_track_stem_and_format(Path("Song.ttml")) == ("Song", LyricsFormat.TTML)
    assert get_track_stem_and_format(Path("Song.lyricsfile.yaml")) == ("Song", LyricsFormat.YAML)
    assert get_track_stem_and_format(Path("Song.yaml")) == ("Song", LyricsFormat.YAML)
    assert get_track_stem_and_format(Path("Song.lrc")) == ("Song", LyricsFormat.LRC)
    assert get_track_stem_and_format(Path("Song.txt")) == ("Song", LyricsFormat.TXT)
    assert get_track_stem_and_format(Path("Song.mp3")) is None


def test_library_auditor_audit_and_metrics(tmp_path: Path):
    """Test offline auditing of audio directory with different lyrics formats."""
    # Track 1: Word-sync TTML
    t1_audio = tmp_path / "01 - Intro.mp3"
    t1_audio.write_bytes(b"dummy mp3")
    t1_lyrics = tmp_path / "01 - Intro.ttml"
    t1_lyrics.write_text(
        '<tt xmlns="http://www.w3.org/ns/ttml" itunes:timing="Word"><body><div><p begin="00:01.000"><span begin="00:01.000" end="00:02.000">Intro</span></p></div></body></tt>',
        encoding="utf-8",
    )

    # Track 2: Line-sync LRC
    t2_audio = tmp_path / "02 - Song Two.flac"
    t2_audio.write_bytes(b"dummy flac")
    t2_lyrics = tmp_path / "02 - Song Two.lrc"
    t2_lyrics.write_text("[00:15.20]Just a line of lyrics\n[00:20.00]Second line", encoding="utf-8")

    # Track 3: Unsynced TXT
    t3_audio = tmp_path / "03 - Acoustic.m4a"
    t3_audio.write_bytes(b"dummy m4a")
    t3_lyrics = tmp_path / "03 - Acoustic.txt"
    t3_lyrics.write_text("Plain text with no timestamps at all", encoding="utf-8")

    # Track 4: Missing lyrics
    t4_audio = tmp_path / "04 - Outro.opus"
    t4_audio.write_bytes(b"dummy opus")

    auditor = LibraryAuditor()
    report = auditor.audit_library(tmp_path)

    assert report.total_tracks == 4
    assert report.word_sync_count == 1
    assert report.line_sync_count == 1
    assert report.unsynced_count == 1
    assert report.missing_count == 1
    assert report.has_lyrics_count == 3
    assert report.coverage_pct == 75.0
    assert report.word_sync_pct == 25.0
    assert report.line_sync_pct == 25.0
    assert report.missing_pct == 25.0
    assert report.format_counts == {"TTML": 1, "LRC": 1, "TXT": 1}

    # Verify display does not crash
    auditor.display_report(report, show_missing_limit=5)


def test_library_auditor_export_missing_and_report(tmp_path: Path):
    """Test exporting missing tracks and full audit report to JSON and CSV."""
    t1_audio = tmp_path / "Track1.mp3"
    t1_audio.write_bytes(b"dummy mp3")
    (tmp_path / "Track1.lrc").write_text("[00:01.00]Hello", encoding="utf-8")

    t2_audio = tmp_path / "Track2.mp3"
    t2_audio.write_bytes(b"dummy mp3")

    auditor = LibraryAuditor()
    report = auditor.audit_library(tmp_path)

    # 1. Export missing to JSON
    missing_json = tmp_path / "missing.json"
    auditor.export_report(report, missing_json, missing_only=True)
    assert missing_json.exists()
    with open(missing_json, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) == 1
        assert "Track2.mp3" in data[0]["audio_path"]

    # 2. Export missing to CSV
    missing_csv = tmp_path / "missing.csv"
    auditor.export_report(report, missing_csv, missing_only=True)
    assert missing_csv.exists()
    with open(missing_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == ["audio_path", "artist", "title", "album"]
        assert len(reader) == 2
        assert "Track2.mp3" in reader[1][0]

    # 3. Export full report to JSON
    report_json = tmp_path / "report.json"
    auditor.export_report(report, report_json, missing_only=False)
    assert report_json.exists()
    with open(report_json, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["summary"]["total_tracks"] == 2
        assert data["summary"]["missing_count"] == 1
        assert len(data["tracks"]) == 2

    # 4. Export full report to CSV
    report_csv = tmp_path / "report.csv"
    auditor.export_report(report, report_csv, missing_only=False)
    assert report_csv.exists()
    with open(report_csv, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert "has_lyrics" in reader[0]
        assert len(reader) == 3


def test_library_pruner_orphans_and_duplicates(tmp_path: Path):
    """Test detection and deletion of orphaned lyrics files and duplicate obsolete formats."""
    # Active track with TTML and an obsolete LRC duplicate
    t1_audio = tmp_path / "Song1.mp3"
    t1_audio.write_bytes(b"audio")
    t1_ttml = tmp_path / "Song1.ttml"
    t1_ttml.write_text("<tt>TTML</tt>", encoding="utf-8")
    t1_lrc = tmp_path / "Song1.lrc"
    t1_lrc.write_text("[00:01.00]Old LRC", encoding="utf-8")

    # Orphaned sidecars (audio was deleted or moved)
    orphan1 = tmp_path / "DeletedSong.lrc"
    orphan1.write_text("[00:01.00]Orphan", encoding="utf-8")
    orphan2 = tmp_path / "DeletedSong2.lyricsfile.yaml"
    orphan2.write_text("lines: []", encoding="utf-8")

    pruner = LibraryPruner()

    # Find orphans
    orphans = pruner.find_orphaned_sidecars(tmp_path)
    assert orphan1 in orphans
    assert orphan2 in orphans
    assert t1_lrc not in orphans
    assert t1_ttml not in orphans

    # Find duplicates
    duplicates = pruner.find_duplicate_sidecars(tmp_path)
    assert len(duplicates) == 1
    dup_file, kept_file = duplicates[0]
    assert dup_file == t1_lrc
    assert kept_file == t1_ttml

    # Summary display test
    pruner.display_prune_summary(orphans, duplicates, dry_run=True)

    # Dry-run prune execution
    all_to_prune = list(orphans) + [t1_lrc]
    pruned_count = pruner.execute_prune(all_to_prune, dry_run=True)
    assert pruned_count == 3
    assert orphan1.exists()
    assert orphan2.exists()
    assert t1_lrc.exists()

    # Actual prune execution
    pruned_count = pruner.execute_prune(all_to_prune, dry_run=False)
    assert pruned_count == 3
    assert not orphan1.exists()
    assert not orphan2.exists()
    assert not t1_lrc.exists()
    assert t1_ttml.exists()
    assert t1_audio.exists()


@pytest.mark.asyncio
async def test_run_audit_command(tmp_path: Path):
    """Test run_audit_command CLI handler."""
    t_audio = tmp_path / "Track.flac"
    t_audio.write_bytes(b"audio")

    class FakeArgs:
        path = str(tmp_path)
        music_dir = None
        export_missing = str(tmp_path / "miss.json")
        export_report = str(tmp_path / "full.json")
        show_missing = 10

    config = AppConfig(music_dir=tmp_path)
    await run_audit_command(FakeArgs(), config)

    assert Path(FakeArgs.export_missing).exists()
    assert Path(FakeArgs.export_report).exists()


@pytest.mark.asyncio
async def test_run_prune_command(tmp_path: Path):
    """Test run_prune_command CLI handler with dry-run and force."""
    orphan = tmp_path / "Old.lrc"
    orphan.write_text("[00:00.00]test", encoding="utf-8")

    class FakeArgsDry:
        path = str(tmp_path)
        music_dir = None
        orphans_only = False
        duplicates_only = False
        force = False
        dry_run = True

    config = AppConfig(music_dir=tmp_path)
    await run_prune_command(FakeArgsDry(), config)
    assert orphan.exists()

    class FakeArgsForce:
        path = str(tmp_path)
        music_dir = None
        orphans_only = False
        duplicates_only = False
        force = True
        dry_run = False

    await run_prune_command(FakeArgsForce(), config)
    assert not orphan.exists()


@pytest.mark.asyncio
async def test_run_upgrade_command_skips_ttml(tmp_path: Path):
    """Test run_upgrade_command discovers only non-TTML tracks."""
    t1_audio = tmp_path / "AlreadyGood.mp3"
    t1_audio.write_bytes(b"audio")
    t1_ttml = tmp_path / "AlreadyGood.ttml"
    t1_ttml.write_text("<tt>TTML</tt>", encoding="utf-8")

    class FakeArgs:
        path = str(tmp_path)
        music_dir = None
        only_lrc = False
        only_missing = False
        force = False
        dry_run = True
        allow_plain = False
        concurrency = 1
        no_progress = True

    config = AppConfig(music_dir=tmp_path)
    # With dry-run, if no candidates exist, it prints nothing to upgrade and exits cleanly
    await run_upgrade_command(FakeArgs(), config)
