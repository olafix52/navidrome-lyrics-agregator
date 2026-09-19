"""Unit tests for tag reader and audio file detection."""

from pathlib import Path
from src.tag_reader import (
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
