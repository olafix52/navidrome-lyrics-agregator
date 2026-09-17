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


def test_save_lyrics_for_track_modes(tmp_path: Path):
    from src.storage import save_lyrics_for_track
    from src.tag_writer import has_embedded_lyrics

    audio_file = tmp_path / "song.mp3"
    audio_file.write_bytes(b"\xff\xfb\x90\x44" + b"\x00" * 1000)

    lyrics = LyricsResult(
        content="[00:01.00]Sidecar line",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )

    # 1. Mode: sidecar
    sidecar_path, was_embedded = save_lyrics_for_track(
        audio_file, lyrics, storage_mode="sidecar"
    )
    assert sidecar_path is not None
    assert sidecar_path.is_file()
    assert was_embedded is False
    assert has_embedded_lyrics(audio_file) is False

    # 2. Mode: embedded
    lyrics2 = LyricsResult(
        content="[00:02.00]Embedded line",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )
    sidecar_path2, was_embedded2 = save_lyrics_for_track(
        audio_file, lyrics2, storage_mode="embedded"
    )
    assert sidecar_path2 is None
    assert was_embedded2 is True
    assert has_embedded_lyrics(audio_file) is True

    # 3. Mode: both with custom output_dir
    custom_out = tmp_path / "custom_lyrics_folder"
    lyrics3 = LyricsResult(
        content="[00:03.00]Both line",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )
    sidecar_path3, was_embedded3 = save_lyrics_for_track(
        audio_file, lyrics3, storage_mode="both", output_dir=custom_out
    )
    assert sidecar_path3 == custom_out / "song.lrc"
    assert sidecar_path3.is_file()
    assert was_embedded3 is True


def test_should_skip_track_storage_modes(tmp_path: Path):
    audio_file = tmp_path / "song_skip.mp3"
    audio_file.write_bytes(b"\xff\xfb\x90\x44" + b"\x00" * 1000)

    # Mode: embedded, no embedded lyrics -> should not skip
    skip, _ = should_skip_track(audio_file, storage_mode="embedded")
    assert skip is False

    # Mode: both, has sidecar but no embedded -> should not skip
    lrc_file = tmp_path / "song_skip.lrc"
    lrc_file.write_text("[00:01.00] text")
    skip, _ = should_skip_track(audio_file, storage_mode="both")
    assert skip is False

    # Embed lyrics
    from src.tag_writer import embed_lyrics_in_audio
    lyrics = LyricsResult(
        content="[00:01.00] Embedded text",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )
    embed_lyrics_in_audio(audio_file, lyrics)

    # Now mode embedded -> should skip
    skip, reason = should_skip_track(audio_file, storage_mode="embedded")
    assert skip is True
    assert "embedded" in reason.lower()

    # Mode both -> has both LRC sidecar and embedded, with upgrade_quality=False -> should skip
    skip, reason = should_skip_track(audio_file, storage_mode="both", upgrade_quality=False)
    assert skip is True

    # Mode both -> with TTML sidecar -> should skip even with upgrade_quality=True
    ttml_file = tmp_path / "song_skip.ttml"
    ttml_file.write_text("<tt>...</tt>")
    skip, reason = should_skip_track(audio_file, storage_mode="both", upgrade_quality=True)
    assert skip is True

