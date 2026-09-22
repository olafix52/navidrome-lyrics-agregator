"""Unit tests for Navidrome / Subsonic API client and scan triggering."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.models import SubsonicTrack
from src.subsonic import SubsonicClient


def test_subsonic_client_url_cleanup():
    c1 = SubsonicClient("http://localhost:4533/", "user", "pass")
    assert c1.base_url == "http://localhost:4533"

    c2 = SubsonicClient("http://localhost:4533/rest/", "user", "pass")
    assert c2.base_url == "http://localhost:4533"

    c3 = SubsonicClient("http://localhost:4533/rest", "user", "pass")
    assert c3.base_url == "http://localhost:4533"


def test_subsonic_auth_params():
    client = SubsonicClient("http://localhost:4533", "testuser", "secret")
    params = client._get_auth_params()

    assert params["u"] == "testuser"
    assert params["v"] == "1.16.1"
    assert params["c"] == "NavidromeLyricsAggregator"
    assert params["f"] == "json"
    assert "s" in params
    assert "t" in params

    # Verify md5(password + salt) == token
    expected_token = hashlib.md5(("secret" + params["s"]).encode("utf-8")).hexdigest()
    assert params["t"] == expected_token


@pytest.mark.asyncio
async def test_subsonic_ping_success():
    client = SubsonicClient("http://localhost:4533", "user", "pass")
    mock_resp = {
        "subsonic-response": {
            "status": "ok",
            "version": "1.16.1",
        }
    }

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp["subsonic-response"]
        ok = await client.ping()
        assert ok is True
        mock_get.assert_awaited_once_with("ping.view")
    await client.close()


@pytest.mark.asyncio
async def test_subsonic_ping_failure():
    client = SubsonicClient("http://localhost:4533", "user", "pass")
    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = RuntimeError("Auth failed")
        ok = await client.ping()
        assert ok is False
    await client.close()


@pytest.mark.asyncio
async def test_subsonic_start_scan():
    client = SubsonicClient("http://localhost:4533", "user", "pass")
    mock_status = {"scanning": True, "count": 100}

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"status": "ok", "scanStatus": mock_status}

        # Incremental scan
        res = await client.start_scan(full_scan=False)
        assert res == mock_status
        mock_get.assert_awaited_with("startScan.view", extra_params={})

        # Full scan
        res_full = await client.start_scan(full_scan=True)
        assert res_full == mock_status
        mock_get.assert_awaited_with("startScan.view", extra_params={"fullScan": "true"})

    await client.close()


@pytest.mark.asyncio
async def test_subsonic_get_all_tracks_search3():
    client = SubsonicClient("http://localhost:4533", "user", "pass")
    mock_songs = [
        {
            "id": "song-1",
            "title": "Song One",
            "artist": "Artist One",
            "album": "Album One",
            "duration": 180,
            "path": "Artist One/Album One/01 - Song One.mp3",
            "suffix": "mp3",
            "lyricsPresent": True,
        },
        {
            "id": "song-2",
            "title": "Song Two",
            "artist": "Artist Two",
            "album": "Album Two",
            "duration": 210,
            "path": "Artist Two/Album Two/02 - Song Two.flac",
            "suffix": "flac",
            "lyricsPresent": False,
        },
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "status": "ok",
            "searchResult3": {
                "song": mock_songs,
            },
        }

        tracks = await client.get_all_tracks(batch_size=500)
        assert len(tracks) == 2
        assert isinstance(tracks[0], SubsonicTrack)
        assert tracks[0].id == "song-1"
        assert tracks[0].title == "Song One"
        assert tracks[0].suffix == "mp3"
        assert tracks[0].lyrics_present is True
        assert tracks[1].suffix == "flac"
        assert tracks[1].lyrics_present is False

    await client.close()


@pytest.mark.asyncio
async def test_subsonic_get_all_tracks_fallback_to_albums():
    client = SubsonicClient("http://localhost:4533", "user", "pass")

    async def fake_get(endpoint: str, extra_params=None):
        if endpoint == "search3.view":
            raise RuntimeError("search3 not supported")
        elif endpoint == "getAlbumList2.view":
            return {
                "status": "ok",
                "albumList2": {
                    "album": [{"id": "alb-1", "name": "Album 1"}],
                },
            }
        elif endpoint == "getAlbum.view":
            return {
                "status": "ok",
                "album": {
                    "song": [
                        {
                            "id": "song-fallback",
                            "title": "Fallback Song",
                            "artist": "Fallback Artist",
                            "duration": 200,
                            "path": "a/b/c.mp3",
                            "suffix": "mp3",
                        }
                    ]
                },
            }
        return {"status": "ok"}

    with patch.object(client, "_get", side_effect=fake_get):
        tracks = await client.get_all_tracks(batch_size=500)
        assert len(tracks) == 1
        assert tracks[0].id == "song-fallback"
        assert tracks[0].title == "Fallback Song"

    await client.close()


@pytest.mark.asyncio
async def test_subsonic_get_all_tracks_fallback_multiple_albums_concurrent():
    client = SubsonicClient("http://localhost:4533", "user", "pass")

    async def fake_get(endpoint: str, extra_params=None):
        if endpoint == "search3.view":
            raise RuntimeError("search3 not supported")
        elif endpoint == "getAlbumList2.view":
            return {
                "status": "ok",
                "albumList2": {
                    "album": [
                        {"id": "alb-1", "name": "Album 1"},
                        {"id": "alb-2", "name": "Album 2"},
                        {"id": "alb-3", "name": "Album 3"},
                    ],
                },
            }
        elif endpoint == "getAlbum.view":
            alb_id = extra_params.get("id", "")
            return {
                "status": "ok",
                "album": {
                    "song": [
                        {
                            "id": f"song-{alb_id}",
                            "title": f"Song in {alb_id}",
                            "artist": "Test Artist",
                            "duration": 180,
                            "path": f"path/{alb_id}.mp3",
                            "suffix": "mp3",
                        }
                    ]
                },
            }
        return {"status": "ok"}

    with patch.object(client, "_get", side_effect=fake_get):
        tracks = await client.get_all_tracks(batch_size=500)
        assert len(tracks) == 3
        ids = [t.id for t in tracks]
        assert ids == ["song-alb-1", "song-alb-2", "song-alb-3"]

    await client.close()


@pytest.mark.asyncio
async def test_subsonic_error_handling():
    client = SubsonicClient("http://localhost:4533", "user", "pass")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "subsonic-response": {
            "status": "failed",
            "error": {
                "code": 40,
                "message": "Wrong username or password",
            },
        }
    }

    mock_http_client = AsyncMock()
    mock_http_client.get.return_value = mock_resp

    with patch.object(client, "_get_client", return_value=mock_http_client):
        with pytest.raises(RuntimeError, match="Subsonic error 40: Wrong username or password"):
            await client._get("ping.view")

    await client.close()


@pytest.mark.asyncio
async def test_scanner_auto_trigger_navidrome_scan():
    from src.config import AppConfig, NavidromeConfig
    from src.scanner import LibraryScanner
    from src.matcher import LyricsMatcher

    config = AppConfig(
        navidrome=NavidromeConfig(
            url="http://localhost:4533",
            user="admin",
            password="secretpassword",
            auto_scan=True,
            full_scan=False,
        )
    )
    matcher = LyricsMatcher(config, [])
    scanner = LibraryScanner(config=config, matcher=matcher)

    with patch("src.subsonic.SubsonicClient.start_scan", new_callable=AsyncMock) as mock_scan:
        # success_count = 0 -> should not trigger
        await scanner._maybe_trigger_navidrome_scan(0)
        mock_scan.assert_not_called()

        # success_count = 2 -> should trigger
        await scanner._maybe_trigger_navidrome_scan(2)
        mock_scan.assert_awaited_once_with(full_scan=False)

    await matcher.close()


@pytest.mark.asyncio
async def test_subsonic_scan_path_traversal_prevention(tmp_path):
    from src.config import AppConfig, NavidromeConfig
    from src.scanner import LibraryScanner
    from src.matcher import LyricsMatcher
    from pathlib import Path

    output_dir = tmp_path / "lyrics_out"
    config = AppConfig(
        output_dir=output_dir,
        navidrome=NavidromeConfig(url="http://mock:4533", user="u", password="p"),
    )
    matcher = LyricsMatcher(config, [])
    scanner = LibraryScanner(config=config, matcher=matcher)

    # Subsonic track with absolute leading slashes
    track_with_slash = SubsonicTrack(
        id="1",
        title="Song",
        artist="Artist",
        path="/Artist/Album/Song.flac",
        suffix="flac",
    )

    with patch("src.subsonic.SubsonicClient.ping", new_callable=AsyncMock, return_value=True), \
         patch("src.subsonic.SubsonicClient.get_all_tracks", new_callable=AsyncMock, return_value=[track_with_slash]), \
         patch.object(scanner, "process_metadata_batch", new_callable=AsyncMock, return_value=[]) as mock_batch:
        await scanner.scan_subsonic_library()
        mock_batch.assert_called_once()
        metadata_list = mock_batch.call_args[0][0]
        assert len(metadata_list) == 1
        # The path should be rooted inside output_dir, not /Artist/...
        assert metadata_list[0].file_path == output_dir / "Artist/Album/Song.flac"
        assert metadata_list[0].file_path != Path("/Artist/Album/Song.flac")

    await matcher.close()

