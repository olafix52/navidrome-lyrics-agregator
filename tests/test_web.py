"""Unit tests for Web UI server, API endpoints, and karaoke parser."""

from pathlib import Path
import pytest
import httpx

from src.config import AppConfig
from src.models import LyricsFormat, LyricsResult, LyricsSyncType
from src.web.parser import (
    parse_lyrics_to_karaoke,
    parse_lrc_to_karaoke,
    parse_time_str_to_seconds,
    parse_ttml_to_karaoke,
    parse_yaml_to_karaoke,
)
from src.web.server import create_app, decode_track_id, encode_track_id


def test_parse_time_str_to_seconds():
    """Verify timestamp parsing for various standard and ms/s formats."""
    assert parse_time_str_to_seconds("01:23.456") == pytest.approx(83.456)
    assert parse_time_str_to_seconds("00:01:23.456") == pytest.approx(83.456)
    assert parse_time_str_to_seconds("12.5s") == pytest.approx(12.5)
    assert parse_time_str_to_seconds("1500ms") == pytest.approx(1.5)
    assert parse_time_str_to_seconds("invalid") is None
    assert parse_time_str_to_seconds("") is None


def test_parse_ttml_to_karaoke():
    """Verify TTML word-level and line-level synchronization parsing."""
    ttml = """<?xml version="1.0" encoding="utf-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml" itunes:timing="Word">
      <body>
        <div>
          <p begin="00:10.000" end="00:14.000">
            <span begin="00:10.000" end="00:11.500">Hello </span>
            <span begin="00:11.500" end="00:13.500">World</span>
          </p>
          <p begin="00:15.000" end="00:18.000">Line without spans</p>
        </div>
      </body>
    </tt>
    """
    lines = parse_ttml_to_karaoke(ttml)
    assert len(lines) == 2
    assert lines[0].text == "Hello World"
    assert lines[0].start == 10.0
    assert lines[0].end == 14.0
    assert lines[0].words is not None
    assert len(lines[0].words) == 2
    assert lines[0].words[0].text == "Hello "
    assert lines[0].words[0].start == 10.0
    assert lines[0].words[0].end == 11.5

    assert lines[1].text == "Line without spans"
    assert lines[1].start == 15.0
    assert lines[1].end == 18.0
    assert lines[1].words is None


def test_parse_yaml_to_karaoke():
    """Verify Lyricsfile 1.0 YAML parsing with words and line timings."""
    yaml_content = """
    lines:
      - text: "First line"
        start_ms: 5000
        end_ms: 8000
        words:
          - text: "First "
            start_ms: 5000
            end_ms: 6000
          - text: "line"
            start_ms: 6000
            end_ms: 7500
      - text: "Second line"
        start_ms: 9000
        end_ms: 12000
    """
    lines = parse_yaml_to_karaoke(yaml_content)
    assert len(lines) == 2
    assert lines[0].text == "First line"
    assert lines[0].start == 5.0
    assert lines[0].end == 8.0
    assert len(lines[0].words) == 2
    assert lines[0].words[1].text == "line"
    assert lines[0].words[1].start == 6.0

    assert lines[1].text == "Second line"
    assert lines[1].start == 9.0


def test_parse_lrc_to_karaoke():
    """Verify standard LRC and enhanced word-synced LRC parsing."""
    # Standard LRC
    lrc_standard = "[00:05.50]Hello world\n[00:10.00]Second line"
    lines = parse_lrc_to_karaoke(lrc_standard)
    assert len(lines) == 2
    assert lines[0].text == "Hello world"
    assert lines[0].start == pytest.approx(5.50)
    assert lines[0].end == pytest.approx(10.00)

    # Enhanced LRC
    lrc_enhanced = "[00:05.00]<00:05.00>One <00:06.00>Two"
    lines2 = parse_lrc_to_karaoke(lrc_enhanced)
    assert len(lines2) == 1
    assert lines2[0].words is not None
    assert len(lines2[0].words) == 2
    assert lines2[0].words[0].text == "One "
    assert lines2[0].words[0].start == 5.0


def test_parse_lyrics_to_karaoke_plain():
    """Verify fallback for plain text without timestamps."""
    text = "Line one\nLine two\nLine three"
    lines = parse_lyrics_to_karaoke(text, LyricsFormat.TXT)
    assert len(lines) == 3
    assert lines[0].text == "Line one"
    assert lines[0].start is None


def test_track_id_encoding_and_traversal_guard(tmp_path: Path):
    """Verify base64 track ID encoding and prevention of path traversal attacks."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    song = music_dir / "artist" / "album" / "track.flac"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"audio")

    outside = tmp_path / "secret.flac"
    outside.write_bytes(b"secret")

    # Valid encoding/decoding
    t_id = encode_track_id(song, music_dir)
    decoded = decode_track_id(t_id, music_dir)
    assert decoded == song.resolve()

    # Outside traversal attempt
    outside_id = encode_track_id(outside, tmp_path)
    assert decode_track_id(outside_id, music_dir) is None


@pytest.mark.asyncio
async def test_api_endpoints_stats_and_tracks(tmp_path: Path):
    """Verify GET /api/stats, GET /api/tracks, GET /api/tracks/{id}/lyrics, and GET /api/tracks/{id}/audio."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    # Track 1 with TTML
    t1 = music_dir / "Song1.mp3"
    t1.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00dummy-mp3-content-data")
    t1_lyrics = music_dir / "Song1.ttml"
    t1_lyrics.write_text(
        '<tt xmlns="http://www.w3.org/ns/ttml" itunes:timing="Word"><body><div><p begin="00:01.000" end="00:03.000"><span begin="00:01.000" end="00:02.000">Song</span><span begin="00:02.000" end="00:03.000">One</span></p></div></body></tt>',
        encoding="utf-8",
    )

    # Track 2 without lyrics
    t2 = music_dir / "Song2.flac"
    t2.write_bytes(b"fLaC\x00\x00\x00dummy-flac-data")

    config = AppConfig(music_dir=music_dir)
    app = create_app(config)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test /api/stats
        res = await client.get("/api/stats")
        assert res.status_code == 200
        stats = res.json()
        assert stats["total_tracks"] == 2
        assert stats["has_lyrics_count"] == 1
        assert stats["word_sync_count"] == 1
        assert stats["missing_count"] == 1

        # 2. Test /api/tracks
        res2 = await client.get("/api/tracks")
        assert res2.status_code == 200
        tracks_data = res2.json()
        assert tracks_data["total"] == 2
        assert len(tracks_data["tracks"]) == 2

        # 3. Test filtering by missing
        res_missing = await client.get("/api/tracks?filter=missing")
        assert res_missing.json()["total"] == 1
        assert res_missing.json()["tracks"][0]["filename"] == "Song2.flac"

        # 4. Test lyrics retrieval for Track 1
        t1_id = encode_track_id(t1, music_dir)
        res_lyrics = await client.get(f"/api/tracks/{t1_id}/lyrics")
        assert res_lyrics.status_code == 200
        lyr = res_lyrics.json()
        assert lyr["has_lyrics"] is True
        assert lyr["format"] == "ttml"
        assert lyr["sync_type"] == "word_sync"
        assert len(lyr["lines"]) == 1
        assert lyr["lines"][0]["text"] == "SongOne"
        assert len(lyr["lines"][0]["words"]) == 2

        # 5. Test audio streaming with Range header
        res_audio = await client.get(f"/api/tracks/{t1_id}/audio", headers={"Range": "bytes=0-10"})
        assert res_audio.status_code in (200, 206)
        assert len(res_audio.content) > 0


@pytest.mark.asyncio
async def test_api_save_lyrics_and_search_providers(tmp_path: Path):
    """Verify POST /api/tracks/{id}/save-lyrics and GET /api/search-providers."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    audio = music_dir / "Song.mp3"
    audio.write_bytes(b"audio-data")

    # Mock Matcher
    class MockProvider:
        name = "mock_provider"
        async def get_lyrics(self, track):
            return LyricsResult(
                format=LyricsFormat.TTML,
                sync_type=LyricsSyncType.WORD_SYNC,
                content='<tt xmlns="http://www.w3.org/ns/ttml" itunes:timing="Word"><body><div><p begin="00:01.000">Test</p></div></body></tt>',
                title="Song",
                artist="Artist",
                duration=180.0,
                provider_name="mock_provider",
            )
        async def close(self):
            pass

    from src.matcher import LyricsMatcher
    config = AppConfig(music_dir=music_dir)
    mock_matcher = LyricsMatcher(config, [MockProvider()])

    app = create_app(config, matcher=mock_matcher)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Search providers endpoint
        res = await client.get("/api/search-providers?artist=Artist&title=Song&duration=180.0")
        assert res.status_code == 200
        cands = res.json()["candidates"]
        assert len(cands) == 1
        assert cands[0]["provider"] == "mock_provider"
        assert cands[0]["format"] == "ttml"
        assert cands[0]["sync_type"] == "word_sync"

        # 2. Save selected lyrics to disk
        track_id = encode_track_id(audio, music_dir)
        save_res = await client.post(
            f"/api/tracks/{track_id}/save-lyrics",
            json={
                "content": cands[0]["content"],
                "format": "ttml",
                "sync_type": "word_sync",
                "provider": "mock_provider",
            },
        )
        assert save_res.status_code == 200
        assert save_res.json()["success"] is True

        # Verify sidecar file on disk
        sidecar = music_dir / "Song.ttml"
        assert sidecar.exists()
        assert "<tt" in sidecar.read_text(encoding="utf-8")

        # 3. Test SSE streaming search endpoint
        sse_res = await client.get("/api/search-providers-stream?artist=Artist&title=Song&duration=180.0")
        assert sse_res.status_code == 200
        assert "text/event-stream" in sse_res.headers["content-type"]
        body_text = sse_res.text
        assert "data: " in body_text
        assert "mock_provider" in body_text
        assert '"type": "done"' in body_text


def test_karaoke_to_ttml_conversion():
    """Verify karaoke_to_ttml correctly formats KaraokeLine objects with line and word timings into valid TTML XML."""
    import xml.etree.ElementTree as ET
    from src.web.parser import KaraokeLine, KaraokeWord, karaoke_to_ttml

    lines = [
        KaraokeLine(text="Line 1 without words", start=12.5, end=15.0),
        KaraokeLine(
            text="Line 2 with words",
            start=18.0,
            end=22.0,
            words=[
                KaraokeWord(text="Line ", start=18.0, end=19.0),
                KaraokeWord(text="2 ", start=19.0, end=20.0),
                KaraokeWord(text="with ", start=20.0, end=21.0),
                KaraokeWord(text="words", start=21.0, end=22.0),
            ],
        ),
    ]

    ttml_xml = karaoke_to_ttml(lines, title="Great Song", artist="Top Artist")
    assert "<tt" in ttml_xml
    assert "<songwriter>Top Artist</songwriter>" in ttml_xml
    assert "<ttm:title>Great Song</ttm:title>" in ttml_xml

    # Check XML well-formedness
    root = ET.fromstring(ttml_xml)
    assert root.tag.endswith("tt")
    p_elements = list(root.iter("{http://www.w3.org/ns/ttml}p"))
    assert len(p_elements) == 2
    assert p_elements[0].attrib.get("begin") == "00:12.500"
    assert p_elements[0].attrib.get("end") == "00:15.000"
    assert p_elements[1].attrib.get("begin") == "00:18.000"
    assert p_elements[1].attrib.get("end") == "00:22.000"

    spans_p2 = list(p_elements[1].iter("{http://www.w3.org/ns/ttml}span"))
    assert len(spans_p2) == 4
    assert spans_p2[0].attrib.get("begin") == "00:18.000"
    assert spans_p2[0].text == "Line "


@pytest.mark.asyncio
async def test_api_lyrics_ttml_content(tmp_path: Path):
    """Verify GET /api/tracks/{id}/lyrics returns valid ttml_content for both TTML and LRC tracks."""
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    # Track with LRC
    lrc_audio = music_dir / "TestLRC.mp3"
    lrc_audio.write_bytes(b"audio-data")
    lrc_lyrics = music_dir / "TestLRC.lrc"
    lrc_lyrics.write_text("[00:10.00]Hello world\n[00:15.00]Second line\n", encoding="utf-8")

    config = AppConfig(music_dir=music_dir)
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        track_id = encode_track_id(lrc_audio, music_dir)
        res = await client.get(f"/api/tracks/{track_id}/lyrics")
        assert res.status_code == 200
        data = res.json()
        assert data["has_lyrics"] is True
        assert data["format"] == "lrc"
        assert "ttml_content" in data
        assert "<tt" in data["ttml_content"]
        assert 'begin="00:10.000"' in data["ttml_content"]

