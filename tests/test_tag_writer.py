"""Unit tests for tag writer (ID3 USLT/SYLT, Vorbis LYRICS, MP4 ©lyr) and embedded lyrics detection."""

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.models import LyricsFormat, LyricsResult, LyricsSyncType
from src.tag_writer import (
    _karaoke_lines_to_lrc,
    _karaoke_lines_to_plain,
    _lyrics_to_tag_payloads,
    embed_lyrics_in_audio,
    extract_embedded_lyrics,
    has_embedded_lyrics,
)
from src.web.parser import KaraokeLine


def _create_minimal_flac(path: Path) -> None:
    """Helper to generate a minimal valid FLAC stream that mutagen can parse and write."""
    min_block = 4096
    max_block = 4096
    sample_rate = 44100
    channels = 2 - 1
    bps = 16 - 1
    total_samples = 44100
    b1 = (sample_rate << 12) | (channels << 9) | (bps << 4) | (total_samples >> 32)
    b2 = total_samples & 0xFFFFFFFF
    md5 = b"\x00" * 16
    streaminfo = struct.pack(">HH3s3sII16s", min_block, max_block, b"\x00\x00\x00", b"\x00\x00\x00", b1, b2, md5)
    header = b"fLaC\x80\x00\x00\x22" + streaminfo
    path.write_bytes(header)


def _create_minimal_mp3(path: Path) -> None:
    """Helper to generate a dummy MP3 file."""
    path.write_bytes(b"\xff\xfb\x90\x44" + b"\x00" * 1000)


def test_karaoke_lines_to_lrc():
    lines = [
        KaraokeLine(text="Hello world", start=65.5, end=68.0),
        KaraokeLine(text="Second line", start=120.0, end=125.0),
        KaraokeLine(text="Unsynced line", start=None, end=None),
    ]
    lrc = _karaoke_lines_to_lrc(lines)
    assert "[01:05.50]Hello world" in lrc
    assert "[02:00.00]Second line" in lrc
    assert "Unsynced line" in lrc


def test_karaoke_lines_to_plain():
    lines = [
        KaraokeLine(text="  Hello world  ", start=5.0),
        KaraokeLine(text="", start=7.0),
        KaraokeLine(text="Second line", start=10.0),
    ]
    plain = _karaoke_lines_to_plain(lines)
    assert plain == "Hello world\nSecond line"


def test_lyrics_to_tag_payloads_synced():
    lyrics = LyricsResult(
        content="[00:05.50]First line\n[00:10.00]Second line",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )
    lrc_text, plain_text, sylt_entries = _lyrics_to_tag_payloads(lyrics)
    assert "[00:05.50]First line" in lrc_text
    assert "First line\nSecond line" == plain_text
    assert len(sylt_entries) == 2
    assert sylt_entries[0] == ("First line", 5500)
    assert sylt_entries[1] == ("Second line", 10000)


def test_lyrics_to_tag_payloads_unsynced():
    lyrics = LyricsResult(
        content="First line\nSecond line",
        format=LyricsFormat.TXT,
        sync_type=LyricsSyncType.UNSYNCED,
        provider_name="test",
    )
    lrc_text, plain_text, sylt_entries = _lyrics_to_tag_payloads(lyrics)
    assert lrc_text == "First line\nSecond line"
    assert plain_text == "First line\nSecond line"
    assert sylt_entries == []


def test_has_and_extract_embedded_lyrics_missing_and_empty(tmp_path: Path):
    non_existent = tmp_path / "missing.mp3"
    assert has_embedded_lyrics(non_existent) is False
    assert extract_embedded_lyrics(non_existent) is None

    empty_file = tmp_path / "empty.flac"
    empty_file.write_bytes(b"")
    assert has_embedded_lyrics(empty_file) is False
    assert extract_embedded_lyrics(empty_file) is None


def test_embed_lyrics_mp3(tmp_path: Path):
    mp3_file = tmp_path / "track.mp3"
    _create_minimal_mp3(mp3_file)

    lyrics = LyricsResult(
        content="[00:05.00]Line 1\n[00:10.00]Line 2",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )

    # Initially no lyrics
    assert has_embedded_lyrics(mp3_file) is False

    # Dry run should not write
    assert embed_lyrics_in_audio(mp3_file, lyrics, dry_run=True) is True
    assert has_embedded_lyrics(mp3_file) is False

    # Actual embedding
    assert embed_lyrics_in_audio(mp3_file, lyrics, dry_run=False) is True
    assert has_embedded_lyrics(mp3_file) is True

    extracted = extract_embedded_lyrics(mp3_file)
    assert extracted is not None
    assert "[00:05.00]Line 1" in extracted

    # Verify SYLT and TXXX frames
    from mutagen.id3 import ID3
    tags = ID3(str(mp3_file))
    assert tags.getall("USLT")
    assert tags.getall("SYLT")
    assert tags.getall("TXXX:LYRICS")


def test_embed_lyrics_flac(tmp_path: Path):
    flac_file = tmp_path / "track.flac"
    _create_minimal_flac(flac_file)

    lyrics = LyricsResult(
        content="[00:02.50]FLAC synced line\n[00:05.00]Second line",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )

    assert has_embedded_lyrics(flac_file) is False

    # Embed in FLAC
    assert embed_lyrics_in_audio(flac_file, lyrics, dry_run=False) is True
    assert has_embedded_lyrics(flac_file) is True

    extracted = extract_embedded_lyrics(flac_file)
    assert extracted is not None
    assert "[00:02.50]FLAC synced line" in extracted

    # Verify Vorbis comment fields
    from mutagen.flac import FLAC
    audio = FLAC(str(flac_file))
    assert "LYRICS" in audio
    assert "UNSYNCEDLYRICS" in audio
    assert audio["UNSYNCEDLYRICS"] == ["FLAC synced line\nSecond line"]


def test_embed_lyrics_m4a_mock(tmp_path: Path):
    m4a_file = tmp_path / "track.m4a"
    m4a_file.write_bytes(b"dummy m4a content")

    lyrics = LyricsResult(
        content="[00:01.00]M4A Lyrics",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )

    mock_mp4 = MagicMock()
    mock_mp4.__getitem__.side_effect = lambda k: ["\xa9lyr test content"]
    mock_mp4.__contains__.side_effect = lambda k: k == "\xa9lyr"

    with patch("src.tag_writer.MP4", return_value=mock_mp4):
        ok = embed_lyrics_in_audio(m4a_file, lyrics)
        assert ok is True
        mock_mp4.save.assert_called_once()
        assert "\xa9lyr" in mock_mp4.__setitem__.call_args[0][0]


def test_embed_lyrics_ogg_opus_mock(tmp_path: Path):
    opus_file = tmp_path / "track.opus"
    opus_file.write_bytes(b"dummy opus content")

    lyrics = LyricsResult(
        content="[00:01.00]Opus line",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        provider_name="test",
    )

    mock_audio = MagicMock()
    mock_audio.tags = {}
    with patch("mutagen.File", return_value=mock_audio):
        ok = embed_lyrics_in_audio(opus_file, lyrics)
        assert ok is True
        assert "LYRICS" in mock_audio.tags
        mock_audio.save.assert_called_once()
