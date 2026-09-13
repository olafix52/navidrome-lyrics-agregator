"""Unit tests for sidecar lyrics storage, priority order, and atomic writing."""

from pathlib import Path
import pytest
from src.models import LyricsFormat, LyricsResult, LyricsSyncType
from src.storage import (
    get_existing_lyrics_file,
    save_lyrics_sidecar,
    should_skip_track,
)


def test_get_existing_lyrics_file(tmp_path: Path):
    audio_file = tmp_path / "song.flac"
    audio_file.write_bytes(b"dummy audio content")

    # No lyrics initially
    assert get_existing_lyrics_file(audio_file) is None

    # Add LRC
    lrc_file = tmp_path / "song.lrc"
    lrc_file.write_text("[00:01.00] line", encoding="utf-8")
    existing = get_existing_lyrics_file(audio_file)
    assert existing is not None
    assert existing[1] == LyricsFormat.LRC

    # Add lyricsfile.yaml
    lyricsfile = tmp_path / "song.lyricsfile.yaml"
    lyricsfile.write_text("version: '1.0'", encoding="utf-8")
    existing = get_existing_lyrics_file(audio_file)
    assert existing is not None
    assert existing[1] == LyricsFormat.YAML

    # Add TTML (higher priority)
    ttml_file = tmp_path / "song.ttml"
    ttml_file.write_text("<tt>sample</tt>", encoding="utf-8")
    existing = get_existing_lyrics_file(audio_file)
    assert existing is not None
    assert existing[1] == LyricsFormat.TTML


def test_should_skip_track(tmp_path: Path):
    audio_file = tmp_path / "track.mp3"
    audio_file.write_bytes(b"dummy")

    # No existing file -> should not skip
    skip, _ = should_skip_track(audio_file, overwrite=False, upgrade_quality=True)
    assert skip is False

    # Existing TTML -> should skip
    ttml_file = tmp_path / "track.ttml"
    ttml_file.write_text("<tt>...</tt>", encoding="utf-8")
    skip, reason = should_skip_track(audio_file, overwrite=False, upgrade_quality=True)
    assert skip is True
    assert "TTML" in reason

    # Overwrite=True -> should never skip
    skip, _ = should_skip_track(audio_file, overwrite=True, upgrade_quality=True)
    assert skip is False


def test_save_lyrics_sidecar_atomic(tmp_path: Path):
    audio_file = tmp_path / "01 - Artist - Track.opus"
    audio_file.write_bytes(b"dummy audio")

    # Create old LRC
    lrc_file = tmp_path / "01 - Artist - Track.lrc"
    lrc_file.write_text("[00:01.00] old", encoding="utf-8")

    lyrics_result = LyricsResult(
        content="<tt><s>New TTML content</s></tt>",
        format=LyricsFormat.TTML,
        sync_type=LyricsSyncType.WORD_SYNC,
        provider_name="amll",
    )

    saved_path = save_lyrics_sidecar(
        audio_file,
        lyrics_result,
        dry_run=False,
        remove_lower_quality=True,
    )

    # Test saving Lyricsfile YAML
    yaml_result = LyricsResult(
        content="version: '1.0'",
        format=LyricsFormat.YAML,
        sync_type=LyricsSyncType.WORD_SYNC,
        provider_name="lrclib",
    )
    saved_yaml_path = save_lyrics_sidecar(audio_file, yaml_result, dry_run=False)
    assert saved_yaml_path == tmp_path / "01 - Artist - Track.lyricsfile.yaml"
    assert saved_yaml_path.is_file()
