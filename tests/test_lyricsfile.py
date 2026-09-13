"""Unit tests for Lyricsfile 1.0 format parser, converter, and validator."""

from src.lyricsfile import (
    LyricsfileDocument,
    lrc_to_lyricsfile,
    parse_lrc_timestamp_to_ms,
    validate_lyricsfile_yaml,
)


def test_parse_lrc_timestamp_to_ms():
    assert parse_lrc_timestamp_to_ms("01:23.45") == (1 * 60 + 23) * 1000 + 450
    assert parse_lrc_timestamp_to_ms("00:00.00") == 0
    assert parse_lrc_timestamp_to_ms("02:10.500") == (2 * 60 + 10) * 1000 + 500
    assert parse_lrc_timestamp_to_ms("invalid") is None


def test_validate_lyricsfile_yaml():
    valid_yaml = """
version: '1.0'
metadata:
  title: 'Small Hours'
  artist: 'Example Artist'
  language: 'en'
lines:
  - text: 'Stay until the morning'
    start_ms: 4200
    end_ms: 6800
    words:
      - text: 'Stay '
        start_ms: 4200
        end_ms: 4900
      - text: 'until '
        start_ms: 4900
        end_ms: 5400
      - text: 'the '
        start_ms: 5400
        end_ms: 5700
      - text: 'morning'
        start_ms: 5700
        end_ms: 6800
plain: |
  Stay until the morning
"""
    doc = validate_lyricsfile_yaml(valid_yaml)
    assert doc is not None
    assert doc.version == "1.0"
    assert doc.metadata.title == "Small Hours"
    assert doc.metadata.artist == "Example Artist"
    assert doc.lines is not None
    assert len(doc.lines) == 1
    assert len(doc.lines[0].words or []) == 4


def test_lrc_to_lyricsfile():
    lrc = """
[00:12.50]First line of lyrics
[00:16.00]Second line of lyrics
"""
    doc = lrc_to_lyricsfile(
        lrc_content=lrc,
        title="My Song",
        artist="My Artist",
        album="My Album",
        duration_ms=180000,
    )
    assert doc.metadata.title == "My Song"
    assert doc.metadata.artist == "My Artist"
    assert doc.lines is not None
    assert len(doc.lines) == 2
    assert doc.lines[0].start_ms == 12500
    assert doc.lines[0].text == "First line of lyrics"
    assert doc.lines[1].start_ms == 16000

    yaml_output = doc.to_yaml()
    assert "version: '1.0'" in yaml_output or "version: 1.0" in yaml_output
    assert "First line of lyrics" in yaml_output


def test_detect_sync_type():
    from src.models import LyricsFormat, LyricsSyncType, detect_sync_type

    # TTML with syllables
    ttml_word = "<tt><body><div><p begin='01:00.00' end='01:05.00'><span begin='01:00.00' end='01:02.00'>Hello</span></p></div></body></tt>"
    assert detect_sync_type(ttml_word, LyricsFormat.TTML) == LyricsSyncType.WORD_SYNC

    # TTML line only
    ttml_line = "<tt><body><div><p begin='01:00.00' end='01:05.00'>Hello world</p></div></body></tt>"
    assert detect_sync_type(ttml_line, LyricsFormat.TTML) == LyricsSyncType.LINE_SYNC

    # YAML with words
    yaml_word = "version: '1.0'\nlines:\n  - text: Hello\n    words:\n      - text: Hello\n"
    assert detect_sync_type(yaml_word, LyricsFormat.YAML) == LyricsSyncType.WORD_SYNC

    # YAML without words (e.g. Sentino from LRCLIB)
    yaml_line = "version: '1.0'\nlines:\n  - text: Hello\n    start_ms: 1000\n"
    assert detect_sync_type(yaml_line, LyricsFormat.YAML) == LyricsSyncType.LINE_SYNC

    # Standard LRC
    lrc_line = "[00:12.50] Hello world"
    assert detect_sync_type(lrc_line, LyricsFormat.LRC) == LyricsSyncType.LINE_SYNC

    # Syllable LRC
    lrc_word = "[00:12.50]<00:12.50>Hello <00:13.00>world"
    assert detect_sync_type(lrc_word, LyricsFormat.LRC) == LyricsSyncType.WORD_SYNC

    # Unsynced plain text
    assert detect_sync_type("Just plain text\nline 2", LyricsFormat.TXT) == LyricsSyncType.UNSYNCED
