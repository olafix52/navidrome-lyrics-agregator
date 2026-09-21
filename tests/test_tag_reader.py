"""Unit tests for tag reader and audio file detection."""

from pathlib import Path
from src.tag_reader import (
    _extract_spotify_id,
    _fallback_parse_filename,
    is_supported_audio_file,
)


def test_is_supported_audio_file():
    assert is_supported_audio_file(Path("song.flac")) is True
    assert is_supported_audio_file(Path("song.mp3")) is True
    assert is_supported_audio_file(Path("song.m4a")) is True
    assert is_supported_audio_file(Path("song.opus")) is True
    assert is_supported_audio_file(Path("song.ogg")) is True
    assert is_supported_audio_file(Path("song.wav")) is True
    assert is_supported_audio_file(Path("image.jpg")) is False
    assert is_supported_audio_file(Path("lyrics.lrc")) is False


def test_fallback_parse_filename():
    artist, title = _fallback_parse_filename(Path("/music/Queen - Bohemian Rhapsody.flac"))
    assert artist == "Queen"
    assert title == "Bohemian Rhapsody"

    artist, title = _fallback_parse_filename(Path("/music/Pink Floyd/The Wall/01 - In The Flesh.mp3"))
    assert title == "In The Flesh"


def test_extract_spotify_id():
    # Valid 22-char ID
    valid_id = "4cOdK2wGLETKBW3PvgPWqT"
    assert _extract_spotify_id(valid_id) == valid_id
    assert _extract_spotify_id(f"  {valid_id}  ") == valid_id

    # Spotify URL
    assert _extract_spotify_id(f"https://open.spotify.com/track/{valid_id}?si=123") == valid_id
    assert _extract_spotify_id(f"http://spotify.com/track/{valid_id}") == valid_id

    # Spotify URI
    assert _extract_spotify_id(f"spotify:track:{valid_id}") == valid_id

    # In comments or surrounded by text
    assert _extract_spotify_id(f"Track URL: https://open.spotify.com/track/{valid_id}") == valid_id

    # Invalid values
    assert _extract_spotify_id(None) is None
    assert _extract_spotify_id("") is None
    assert _extract_spotify_id("invalid-id-short") is None
    assert _extract_spotify_id("4cOdK2wGLETKBW3PvgPWqT-extra-chars") is None


def test_get_first_tag_value_decoding():
    from src.tag_reader import _get_first_tag_value

    # String list (Vorbis / ID3)
    assert _get_first_tag_value({"ISRC": ["GBUM71029604"]}, ["ISRC"]) == "GBUM71029604"
    # Bytes list (MP4 custom atoms)
    assert _get_first_tag_value({"----:com.apple.iTunes:ISRC": [b"USAT21700684"]}, ["----:com.apple.iTunes:ISRC"]) == "USAT21700684"
    # Bare bytes
    assert _get_first_tag_value({"ISRC": b"PLF242616109"}, ["ISRC"]) == "PLF242616109"
    # Missing / None
    assert _get_first_tag_value({}, ["ISRC"]) is None
    assert _get_first_tag_value({"OTHER": "val"}, ["ISRC"]) is None


def test_isrc_normalization_on_real_flac(tmp_path: Path):
    import struct
    from mutagen.flac import FLAC
    from src.tag_reader import read_track_metadata

    flac_file = tmp_path / "song.flac"
    # Create minimal FLAC header
    min_block, max_block, sample_rate, channels, bps, total_samples = 4096, 4096, 44100, 1, 15, 44100
    b1 = (sample_rate << 12) | (channels << 9) | (bps << 4) | (total_samples >> 32)
    b2 = total_samples & 0xFFFFFFFF
    streaminfo = struct.pack(">HH3s3sII16s", min_block, max_block, b"\x00\x00\x00", b"\x00\x00\x00", b1, b2, b"\x00" * 16)
    flac_file.write_bytes(b"fLaC\x80\x00\x00\x22" + streaminfo)

    audio = FLAC(str(flac_file))
    audio["title"] = ["Bohemian Rhapsody"]
    audio["artist"] = ["Queen"]
    # Test with hyphens that should be normalized
    audio["ISRC"] = ["GB-UM7-10-29604"]
    audio.save()

    meta = read_track_metadata(flac_file)
    assert meta is not None
    assert meta.isrc == "GBUM71029604"

