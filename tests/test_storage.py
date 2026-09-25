"""Unit tests for sidecar lyrics storage, priority order, and atomic writing."""

from pathlib import Path
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


def test_storage_output_dir(tmp_path: Path):
    audio_dir = tmp_path / "music"
    audio_dir.mkdir()
    lyrics_dir = tmp_path / "lyrics"
    lyrics_dir.mkdir()

    audio_file = audio_dir / "track.flac"
    audio_file.write_bytes(b"audio")

    # In audio dir, no lyrics exist
    assert get_existing_lyrics_file(audio_file) is None
    skip, _ = should_skip_track(audio_file, output_dir=lyrics_dir)
    assert skip is False

    # Place lyrics in output_dir
    sidecar = lyrics_dir / "track.ttml"
    sidecar.write_text("<tt>...</tt>")

    # Should find in output_dir
    found = get_existing_lyrics_file(audio_file, output_dir=lyrics_dir)
    assert found is not None
    assert found[0] == sidecar
    assert found[1] == LyricsFormat.TTML

    # should_skip_track should skip because of TTML in output_dir
    skip, reason = should_skip_track(audio_file, output_dir=lyrics_dir)
    assert skip is True
    assert "TTML" in reason


def test_get_existing_lyrics_file_with_output_dir(tmp_path):
    """Regression: get_existing_lyrics_file must search output_dir when provided."""
    from src.storage import get_existing_lyrics_file
    audio_file = tmp_path / "music" / "Artist - Song.flac"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"dummy")
    
    output_dir = tmp_path / "lyrics"
    output_dir.mkdir()
    
    # No lyrics anywhere → None
    assert get_existing_lyrics_file(audio_file, output_dir=output_dir) is None
    
    # Write lyrics to output_dir
    lyrics_file = output_dir / "Artist - Song.ttml"
    lyrics_file.write_text("<tt>test</tt>", encoding="utf-8")
    
    # Should find it in output_dir
    result = get_existing_lyrics_file(audio_file, output_dir=output_dir)
    assert result is not None
    found_path, found_format = result
    assert found_path == lyrics_file
    
    # Should NOT find it when searching audio_path.parent (no output_dir)
    assert get_existing_lyrics_file(audio_file) is None


def test_folder_lyrics_index_caching_and_invalidation(tmp_path: Path):
    """Test FolderLyricsIndex cache lookup, registration, and invalidation."""
    from src.storage import GLOBAL_FOLDER_INDEX

    folder = tmp_path / "album"
    folder.mkdir()
    f1 = folder / "track1.lrc"
    f1.write_text("[00:01.00] hello")

    # Initial get
    entries = GLOBAL_FOLDER_INDEX.get_dir_entries(folder)
    assert "track1.lrc" in entries

    # Direct registration
    GLOBAL_FOLDER_INDEX.register_file(folder / "track2.ttml", 123)
    assert "track2.ttml" in GLOBAL_FOLDER_INDEX.get_dir_entries(folder)

    # Direct unregistration
    GLOBAL_FOLDER_INDEX.unregister_file(folder / "track2.ttml")
    assert "track2.ttml" not in GLOBAL_FOLDER_INDEX.get_dir_entries(folder)

    # Clear/invalidate
    GLOBAL_FOLDER_INDEX.invalidate()
    assert GLOBAL_FOLDER_INDEX._cache == {}
    entries_reloaded = GLOBAL_FOLDER_INDEX.get_dir_entries(folder)
    assert "track1.lrc" in entries_reloaded


