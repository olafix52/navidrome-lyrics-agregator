"""Unit tests for Lyricsfile 1.0 format parser, converter, and validator."""

from src.lyricsfile import (
    convert_lyricsfile_result,
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


def test_ttml_timestamp_minute_boundary():
    """Regression: format_ttml_timestamp must not produce 00:60.000."""
    from src.ttml import format_ttml_timestamp
    # Exact minute boundary
    assert format_ttml_timestamp(60.0) == "01:00.000"
    # Just below minute boundary that rounds up
    result = format_ttml_timestamp(59.9997)
    assert ":60" not in result  # Must not produce 00:60.000
    assert result == "01:00.000"  # Should round up to next minute
    # Normal values
    assert format_ttml_timestamp(0.0) == "00:00.000"
    assert format_ttml_timestamp(30.5) == "00:30.500"
    assert format_ttml_timestamp(125.123) == "02:05.123"
    # Negative clamped to zero
    assert format_ttml_timestamp(-5.0) == "00:00.000"


def _yaml_result(content: str, sync_type):
    from src.models import LyricsFormat, LyricsResult
    return LyricsResult(content=content, format=LyricsFormat.YAML, sync_type=sync_type, provider_name="lrclib", title="T", artist="A")


def test_convert_word_synced_lyricsfile_to_ttml_keeps_spacing():
    from src.models import LyricsFormat, LyricsSyncType, detect_sync_type
    from src.web.parser import parse_ttml_to_karaoke

    yaml_doc = """version: '1.0'
metadata: {title: T, artist: A}
lines:
  - text: Stay until the end
    start_ms: 4200
    end_ms: 6800
    words:
      - {text: 'Stay', start_ms: 4200, end_ms: 4900}
      - {text: 'until', start_ms: 4900, end_ms: 5400}
      - {text: 'the ', start_ms: 5400, end_ms: 5800}
      - {text: 'end', start_ms: 5800, end_ms: 6800}
  - text: Second line
    start_ms: 7000
"""
    res = convert_lyricsfile_result(_yaml_result(yaml_doc, LyricsSyncType.WORD_SYNC))
    assert res.format == LyricsFormat.TTML
    assert res.sync_type == LyricsSyncType.WORD_SYNC
    assert detect_sync_type(res.content, res.format) == LyricsSyncType.WORD_SYNC
    assert res.provider_name == "lrclib"

    lines = parse_ttml_to_karaoke(res.content)
    assert [line.text for line in lines] == ["Stay until the end", "Second line"]
    assert [w.text for w in lines[0].words] == ["Stay ", "until ", "the ", "end"]
    assert (lines[0].start, lines[0].end) == (4.2, 6.8)
    assert lines[1].start == 7.0


def test_convert_line_synced_lyricsfile_to_lrc():
    from src.models import LyricsFormat, LyricsSyncType

    yaml_doc = "version: '1.0'\nlines:\n  - text: Hello there\n    start_ms: 1000\n  - text: Bye\n    start_ms: 61500\n"
    res = convert_lyricsfile_result(_yaml_result(yaml_doc, LyricsSyncType.LINE_SYNC))
    assert res.format == LyricsFormat.LRC
    assert res.sync_type == LyricsSyncType.LINE_SYNC
    assert res.content == "[00:01.00]Hello there\n[01:01.50]Bye"


def test_convert_plain_lyricsfile_to_txt():
    from src.models import LyricsFormat, LyricsSyncType

    res = convert_lyricsfile_result(_yaml_result("version: '1.0'\nplain: |\n  Line one\n  Line two\n", LyricsSyncType.UNSYNCED))
    assert res.format == LyricsFormat.TXT
    assert res.content == "Line one\nLine two"


def test_convert_leaves_other_formats_and_unparsable_yaml_unchanged():
    from src.models import LyricsFormat, LyricsResult, LyricsSyncType

    lrc = LyricsResult(content="[00:01.00]Hi", format=LyricsFormat.LRC, sync_type=LyricsSyncType.LINE_SYNC, provider_name="p")
    assert convert_lyricsfile_result(lrc) is lrc
    broken = _yaml_result("::: not yaml [", LyricsSyncType.LINE_SYNC)
    assert convert_lyricsfile_result(broken) is broken
