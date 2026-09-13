"""Unit tests for all 9 lyrics providers with mocked HTTP requests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from src.config import ProviderConfig
from src.models import LyricsFormat, LyricsSyncType, TrackMetadata
from src.providers.amll import AMLLProvider
from src.providers.apple_music import AppleMusicProvider
from src.providers.binilyrics import BiniLyricsProvider
from src.providers.genius import GeniusProvider
from src.providers.kugou import KugouProvider
from src.providers.kuwo import KuwoProvider
from src.providers.lrclib import LrclibProvider
from src.providers.lyricsify import LyricsifyProvider
from src.providers.musixmatch import MusixmatchProvider
from src.providers.netease import NetEaseProvider
from src.providers.qqmusic import QQMusicProvider
from src.providers.rmmrevival import RMMRevivalProvider
from src.providers.unison import UnisonProvider


@pytest.fixture
def sample_track() -> TrackMetadata:
    return TrackMetadata(
        file_path=Path("/music/Queen - Bohemian Rhapsody.flac"),
        title="Bohemian Rhapsody",
        artist="Queen",
        album="A Night at the Opera",
        duration=354.0,
        clean_title="Bohemian Rhapsody",
        clean_artist="Queen",
    )


@pytest.mark.asyncio
async def test_amll_provider(sample_track):
    provider = AMLLProvider(config=ProviderConfig())
    mock_search = MagicMock()
    mock_search.json.return_value = [{"id": 12345, "trackName": "Bohemian Rhapsody", "artistName": "Queen"}]
    mock_search.status_code = 200

    mock_get = MagicMock()
    mock_get.json.return_value = {
        "id": 12345,
        "ttml": "<tt xmlns='http://www.w3.org/ns/ttml'><body><div><p><span begin='00:01.00' end='00:02.00'>Mama</span></p></div></body></tt>",
        "trackName": "Bohemian Rhapsody",
        "artistName": "Queen",
    }
    mock_get.status_code = 200

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_get]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "<tt" in result.content
        assert result.provider_name == "amll"


@pytest.mark.asyncio
async def test_lrclib_provider_yaml(sample_track):
    provider = LrclibProvider(config=ProviderConfig())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "id": 999,
        "trackName": "Bohemian Rhapsody",
        "artistName": "Queen",
        "duration": 354.0,
        "yaml": "version: '1.0'\nlines:\n  - text: Mama\n    words:\n      - text: Mama\n",
    }
    mock_resp.status_code = 200

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.YAML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "lines:" in result.content


@pytest.mark.asyncio
async def test_lrclib_provider_lrc(sample_track):
    provider = LrclibProvider(config=ProviderConfig())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "id": 1001,
        "trackName": "Bohemian Rhapsody",
        "artistName": "Queen",
        "duration": 354.0,
        "syncedLyrics": "[00:10.00] Is this the real life?\n[00:13.50] Is this just fantasy?",
    }
    mock_resp.status_code = 200

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert "[00:10.00]" in result.content


@pytest.mark.asyncio
async def test_musixmatch_provider(sample_track):
    provider = MusixmatchProvider(config=ProviderConfig())
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"message": {"body": {"user_token": "valid_dummy_token_12345"}}}

    mock_macro_resp = MagicMock()
    mock_macro_resp.status_code = 200
    mock_macro_resp.json.return_value = {
        "message": {
            "header": {"status_code": 200},
            "body": {
                "macro_calls": {
                    "matcher.track.get": {
                        "message": {
                            "body": {
                                "track": {
                                    "track_id": 54321,
                                    "track_name": "Bohemian Rhapsody",
                                    "artist_name": "Queen",
                                    "track_length": 354.0,
                                }
                            }
                        }
                    },
                    "track.subtitles.get": {
                        "message": {
                            "header": {"status_code": 200},
                            "body": {
                                "subtitle_list": [
                                    {
                                        "subtitle": {
                                            "subtitle_id": 54321,
                                            "subtitle_length": 354.0,
                                            "subtitle_body": "[00:01.00] Is this the real life?\n[00:05.00] Is this just fantasy?",
                                        }
                                    }
                                ]
                            },
                        }
                    },
                }
            },
        }
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_token_resp, mock_macro_resp]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert "[00:01.00]" in result.content


@pytest.mark.asyncio
async def test_musixmatch_richsync_provider(sample_track):
    provider = MusixmatchProvider(config=ProviderConfig())
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"message": {"body": {"user_token": "valid_dummy_token_12345"}}}

    mock_macro_resp = MagicMock()
    mock_macro_resp.status_code = 200
    mock_macro_resp.json.return_value = {
        "message": {
            "header": {"status_code": 200},
            "body": {
                "macro_calls": {
                    "matcher.track.get": {
                        "message": {
                            "body": {
                                "track": {
                                    "track_id": 99999,
                                    "track_name": "Bohemian Rhapsody",
                                    "artist_name": "Queen",
                                    "track_length": 354.0,
                                }
                            }
                        }
                    },
                    "track.richsync.get": {
                        "message": {
                            "header": {"status_code": 200},
                            "body": {
                                "richsync": {
                                    "richsync_body": [
                                        {
                                            "ts": 1.0,
                                            "te": 4.0,
                                            "l": [
                                                {"c": "Is", "o": 0.0},
                                                {"c": "this", "o": 0.5},
                                                {"c": "the", "o": 1.0},
                                                {"c": "real", "o": 1.5},
                                                {"c": "life?", "o": 2.0},
                                            ],
                                            "x": "Is this the real life?",
                                        }
                                    ]
                                }
                            },
                        }
                    },
                }
            },
        }
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_token_resp, mock_macro_resp]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "<tt" in result.content
        assert "real" in result.content
        assert 'itunes:timing="Word"' in result.content
        assert "<span" in result.content
        assert "life?" in result.content


@pytest.mark.asyncio
async def test_netease_provider(sample_track):
    provider = NetEaseProvider(config=ProviderConfig())
    mock_search_resp = MagicMock()
    mock_search_resp.json.return_value = {
        "result": {
            "songs": [
                {
                    "id": 123456,
                    "name": "Bohemian Rhapsody",
                    "duration": 354000,
                    "artists": [{"name": "Queen"}],
                }
            ]
        }
    }

    mock_lyric_resp = MagicMock()
    mock_lyric_resp.json.return_value = {
        "lrc": {
            "lyric": "[00:01.00] Is this the real life?\n[00:05.00] Is this just fantasy?",
        }
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_search_resp, mock_lyric_resp]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert "[00:01.00]" in result.content


@pytest.mark.asyncio
async def test_netease_provider_yrc_word_sync(sample_track):
    provider = NetEaseProvider(config=ProviderConfig())
    mock_search_resp = MagicMock()
    mock_search_resp.json.return_value = {
        "result": {
            "songs": [
                {
                    "id": 123456,
                    "name": "Bohemian Rhapsody",
                    "duration": 354000,
                    "artists": [{"name": "Queen"}],
                }
            ]
        }
    }

    mock_lyric_resp = MagicMock()
    mock_lyric_resp.json.return_value = {
        "yrc": {
            "lyric": (
                "[0,1000](0,1000,0)Mama\n"
                "[6190,4440](6190,2190,0)Hello(8380,540,0), (8920,360,0)it's (9280,1350,0)me\n"
            ),
        },
        "lrc": {
            "lyric": "[00:01.00] Fallback line sync",
        },
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_search_resp, mock_lyric_resp]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "<tt" in result.content
        assert 'itunes:timing="Word"' in result.content
        assert '<span begin="00:06.190" end="00:08.380">Hello</span>' in result.content
        assert result.metadata.get("source_format") == "yrc"


@pytest.mark.asyncio
async def test_kugou_provider(sample_track):
    import base64
    provider = KugouProvider(config=ProviderConfig())

    mock_search = MagicMock()
    mock_search.json.return_value = {
        "data": {
            "info": [
                {
                    "hash": "abc123hash",
                    "duration": 354,
                    "songname": "Bohemian Rhapsody",
                    "singername": "Queen",
                }
            ]
        }
    }

    mock_candidate = MagicMock()
    mock_candidate.json.return_value = {
        "candidates": [{"id": "cand_1", "accesskey": "key_1"}]
    }

    encoded_lrc = base64.b64encode(b"[00:01.00] Bohemian Rhapsody text").decode("ascii")
    mock_download = MagicMock()
    mock_download.json.return_value = {"content": encoded_lrc}

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_candidate, mock_download]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert "[00:01.00]" in result.content


@pytest.mark.asyncio
async def test_kugou_provider_krc_word_sync(sample_track):
    import base64
    import zlib
    from src.providers.kugou import KRC_KEY

    provider = KugouProvider(config=ProviderConfig())

    mock_search = MagicMock()
    mock_search.json.return_value = {
        "data": {
            "info": [
                {
                    "hash": "abc123hash",
                    "duration": 354,
                    "songname": "Bohemian Rhapsody",
                    "singername": "Queen",
                }
            ]
        }
    }

    mock_candidate = MagicMock()
    mock_candidate.json.return_value = {
        "candidates": [{"id": "cand_1", "accesskey": "key_1"}]
    }

    krc_plain = (
        "[offset:0]\n"
        "[1000,3000]<0,1000,0>Mama <1000,2000,0>just killed a man\n"
    )
    compressed = zlib.compress(krc_plain.encode("utf-8"))
    xored = bytes(b ^ KRC_KEY[i % len(KRC_KEY)] for i, b in enumerate(compressed))
    raw_payload = b"krc1" + xored
    b64_krc = base64.b64encode(raw_payload).decode("ascii")

    mock_download = MagicMock()
    mock_download.json.return_value = {"content": b64_krc}

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_candidate, mock_download]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "<tt" in result.content
        assert 'itunes:timing="Word"' in result.content
        assert '<span begin="00:01.000" end="00:02.000">Mama </span>' in result.content
        assert '<span begin="00:02.000" end="00:04.000">just killed a man</span>' in result.content
        assert result.metadata.get("source_format") == "krc"


@pytest.mark.asyncio
async def test_kugou_provider_picks_best_candidate_over_irrelevant_first():
    import base64
    provider = KugouProvider(config=ProviderConfig())
    track = TrackMetadata(file_path="test.mp3", title="Ghost Town", artist="Kanye West", duration=0.0)

    # Search returns Flashing Lights first, Ghost Town second
    mock_search = MagicMock()
    mock_search.json.return_value = {
        "data": {
            "info": [
                {
                    "hash": "hash_wrong",
                    "duration": 237,
                    "songname": "Flashing Lights",
                    "singername": "Ye (侃爷)",
                },
                {
                    "hash": "hash_correct",
                    "duration": 271,
                    "songname": "Ghost Town (Explicit)",
                    "singername": "Ye (侃爷)、PARTYNEXTDOOR",
                },
            ]
        }
    }

    mock_candidate = MagicMock()
    mock_candidate.json.return_value = {
        "candidates": [{"id": "cand_correct", "accesskey": "key_correct"}]
    }

    encoded_lrc = base64.b64encode(b"[00:01.00] Some day, some day").decode("ascii")
    mock_download = MagicMock()
    mock_download.json.return_value = {"content": encoded_lrc}

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_candidate, mock_download]):
        result = await provider.get_lyrics(track)
        assert result is not None
        assert result.title == "Ghost Town (Explicit)"
        assert result.metadata.get("hash") == "hash_correct"


@pytest.mark.asyncio
async def test_unison_provider(sample_track):
    provider = UnisonProvider(config=ProviderConfig())
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {
        "success": True,
        "data": {
            "id": 123,
            "song": "Bohemian Rhapsody",
            "artist": "Queen",
            "format": "ttml",
            "syncType": "word_sync",
            "lyrics": "<tt xmlns='http://www.w3.org/ns/ttml'><body><p>Mama</p></body></tt>",
        }
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC


@pytest.mark.asyncio
async def test_binilyrics_provider(sample_track):
    provider = BiniLyricsProvider(config=ProviderConfig())
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "ttml": "<tt><s>Test Bini</s></tt>",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert "<tt>" in result.content


@pytest.mark.asyncio
async def test_genius_provider(sample_track):
    provider = GeniusProvider(config=ProviderConfig())
    mock_search = MagicMock()
    mock_search.json.return_value = {
        "response": {
            "sections": [
                {
                    "type": "song",
                    "hits": [
                        {
                            "result": {
                                "url": "https://genius.com/Queen-bohemian-rhapsody-lyrics",
                                "title": "Bohemian Rhapsody",
                                "primary_artist": {"name": "Queen"},
                            }
                        }
                    ],
                }
            ]
        }
    }

    mock_html = MagicMock()
    mock_html.text = """
    <html><body>
      <div data-lyrics-container="true">
        Is this the real life?<br/>Is this just fantasy?
      </div>
    </body></html>
    """

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_html]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TXT
        assert "Is this the real life?" in result.content


@pytest.mark.asyncio
async def test_qqmusic_provider(sample_track):
    provider = QQMusicProvider(config=ProviderConfig())
    mock_search = MagicMock()
    mock_search.status_code = 200
    mock_search.json.return_value = {
        "data": {
            "song": {
                "list": [
                    {
                        "songname": "Bohemian Rhapsody",
                        "songmid": "00123abc",
                        "singer": [{"name": "Queen"}],
                    }
                ]
            }
        }
    }

    mock_lyric = MagicMock()
    mock_lyric.status_code = 200
    mock_lyric.json.return_value = {
        "retcode": 0,
        "lyric": "[00:01.00] Is this the real life?\n[00:05.00] Is this just fantasy?",
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_lyric]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert "[00:01.00]" in result.content


@pytest.mark.asyncio
async def test_qqmusic_provider_qrc_word_sync(sample_track):
    import zlib
    from src.providers.qqmusic import triple_des_ecb, QRC_KEY, ENCRYPT

    provider = QQMusicProvider(config=ProviderConfig())
    mock_search = MagicMock()
    mock_search.status_code = 200
    mock_search.json.return_value = {
        "data": {
            "song": {
                "list": [
                    {
                        "songname": "Bohemian Rhapsody",
                        "songmid": "00123abc",
                        "singer": [{"name": "Queen"}],
                    }
                ]
            }
        }
    }

    qrc_xml = """<?xml version="1.0" encoding="utf-8"?>
<Lyric_1 LyricType="1" LyricContent="[ti:Bohemian Rhapsody]
[6591,3110]Hello (6591,2660)it's (9251,150)me(9401,300)
" />"""
    compressed = zlib.compress(qrc_xml.encode("utf-8"))
    pad_len = 8 - (len(compressed) % 8)
    if pad_len != 8:
        compressed += b"\x00" * pad_len
    enc_hex = triple_des_ecb(compressed, QRC_KEY, ENCRYPT).hex()

    mock_musicu = MagicMock()
    mock_musicu.status_code = 200
    mock_musicu.json.return_value = {
        "code": 0,
        "req": {
            "code": 0,
            "data": {
                "qrc": 1,
                "crypt": 1,
                "lyric": enc_hex,
            },
        },
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_musicu]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "<tt" in result.content
        assert 'itunes:timing="Word"' in result.content
        assert '<span begin="00:06.591" end="00:09.251">Hello </span>' in result.content
        assert result.metadata.get("source_format") == "qrc"


@pytest.mark.asyncio
async def test_kuwo_provider(sample_track):
    import zlib
    provider = KuwoProvider(config=ProviderConfig())
    mock_search = MagicMock()
    mock_search.status_code = 200
    mock_search.text = "{'abslist': [{'SONGNAME': 'Bohemian Rhapsody', 'ARTIST': 'Queen', 'MUSICRID': 'MUSIC_12345'}]}"

    lrc_content = b"[00:01.00] Is this the real life?\n[00:05.00] Is this just fantasy?"
    compressed = zlib.compress(lrc_content)
    raw_response = b"tp=content\r\nscore=5\r\n\r\n" + compressed

    mock_lyric = MagicMock()
    mock_lyric.status_code = 200
    mock_lyric.content = raw_response

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_lyric]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert "[00:01.00]" in result.content


@pytest.mark.asyncio
async def test_kuwo_provider_rejects_mismatched_artist():
    provider = KuwoProvider(config=ProviderConfig())
    track = TrackMetadata(file_path="test.mp3", title="Ghost Town", artist="Kanye West", duration=0.0)

    mock_search = MagicMock()
    mock_search.status_code = 200
    # Search returns Ghost Town by KOO's
    mock_search.text = "{'abslist': [{'SONGNAME': 'Ghost Town', 'ARTIST': \"KOO's\", 'MUSICRID': 'MUSIC_99999'}]}"

    with patch.object(provider, "request_with_retry", return_value=mock_search):
        result = await provider.get_lyrics(track)
        assert result is None


@pytest.mark.asyncio
async def test_lyricsify_provider_success(sample_track):
    provider = LyricsifyProvider(config=ProviderConfig())
    mock_search = MagicMock()
    mock_search.status_code = 200
    mock_search.text = """
    <html><body>
      <a class="title" href="/lyric/queen/bohemian-rhapsody">Queen - Bohemian Rhapsody</a>
    </body></html>
    """

    mock_lyric = MagicMock()
    mock_lyric.status_code = 200
    mock_lyric.text = """
    <html><body>
      <div id="lyrics_content">[00:01.00] Is this the real life?\n[00:05.00] Is this just fantasy?</div>
    </body></html>
    """

    with patch.object(provider, "request_with_retry", side_effect=[mock_search, mock_lyric]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert "[00:01.00]" in result.content


@pytest.mark.asyncio
async def test_lyricsify_provider_cloudflare_fallback(sample_track):
    provider = LyricsifyProvider(config=ProviderConfig())
    mock_cf = MagicMock()
    mock_cf.status_code = 403
    mock_cf.text = "<html><body><title>Just a moment...</title></body></html>"

    with patch.object(provider, "request_with_retry", return_value=mock_cf):
        result = await provider.get_lyrics(sample_track)
        assert result is None


@pytest.mark.asyncio
async def test_lyricsify_provider_flaresolverr_success(sample_track):
    provider = LyricsifyProvider(
        config=ProviderConfig(extra={"flaresolverr_url": "http://localhost:8191/v1"})
    )
    mock_fs_search = MagicMock()
    mock_fs_search.status_code = 200
    mock_fs_search.json.return_value = {
        "status": "ok",
        "solution": {
            "status": 200,
            "response": '<html><body><a href="/lrc/queen-bohemian-rhapsody/12345">Queen - Bohemian Rhapsody</a></body></html>',
        },
    }

    mock_fs_page = MagicMock()
    mock_fs_page.status_code = 200
    mock_fs_page.json.return_value = {
        "status": "ok",
        "solution": {
            "status": 200,
            "response": '<html><body><div id="lyrics_display">[00:01.00] Mama, just killed a man</div></body></html>',
        },
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_fs_search, mock_fs_page]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert "[00:01.00] Mama" in result.content


@pytest.mark.asyncio
async def test_musixmatch_zero_token_handling():
    provider = MusixmatchProvider(config=ProviderConfig())
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "body": {
                "user_token": "00000000000000000000000000000000000000000000000000000000"
            }
        }
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        token = await provider._get_user_token()
        # Should NOT use the zero token, but fallback to static token
        assert token != "00000000000000000000000000000000000000000000000000000000"
        assert token == "21051986b9886e2d7bd5d8295b15d605c14e13e33326a3a0e50e1b"


@pytest.mark.asyncio
async def test_musixmatch_mismatched_candidate_rejected(sample_track):
    """Ensure Musixmatch honeypot/mismatched track (e.g. Drake - NOKIA) is rejected when Queen is requested."""
    provider = MusixmatchProvider(config=ProviderConfig())
    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {
        "message": {"body": {"user_token": "valid_token_123"}}
    }

    mock_macro_resp = MagicMock()
    mock_macro_resp.json.return_value = {
        "message": {
            "header": {"status_code": 200},
            "body": {
                "macro_calls": {
                    "matcher.track.get": {
                        "message": {
                            "body": {
                                "track": {
                                    "track_name": "NOKIA",
                                    "artist_name": "Drake",
                                }
                            }
                        }
                    },
                    "track.subtitles.get": {
                        "message": {
                            "header": {"status_code": 200},
                            "body": {
                                "subtitle_list": [
                                    {"subtitle": {"subtitle_body": "[00:12.00] Real lyrics"}}
                                ]
                            },
                        }
                    },
                }
            },
        }
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_token_resp, mock_macro_resp]):
        result = await provider.get_lyrics(sample_track)
        # Queen - Bohemian Rhapsody requested, but Drake - NOKIA returned -> MUST be rejected
        assert result is None


@pytest.mark.asyncio
async def test_musixmatch_poisoned_lyrics_rejected(sample_track):
    """Ensure Musixmatch dummy honeypot lyrics ('Wob gopini den...') are detected and rejected."""
    provider = MusixmatchProvider(config=ProviderConfig())
    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {
        "message": {"body": {"user_token": "valid_token_123"}}
    }

    mock_macro_resp = MagicMock()
    mock_macro_resp.json.return_value = {
        "message": {
            "header": {"status_code": 200},
            "body": {
                "macro_calls": {
                    "matcher.track.get": {
                        "message": {
                            "body": {
                                "track": {
                                    "track_name": "Bohemian Rhapsody",
                                    "artist_name": "Queen",
                                }
                            }
                        }
                    },
                    "track.subtitles.get": {
                        "message": {
                            "header": {"status_code": 200},
                            "body": {
                                "subtitle_list": [
                                    {
                                        "subtitle": {
                                            "subtitle_body": "[00:12.00]Wob gopini den\n[00:16.00]Tefe woxica fero"
                                        }
                                    }
                                ]
                            },
                        }
                    },
                }
            },
        }
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_token_resp, mock_macro_resp]):
        result = await provider.get_lyrics(sample_track)
        # Poisoned lyrics detected -> MUST be rejected
        assert result is None



@pytest.mark.asyncio
async def test_rmmrevival_provider_ttml_word_sync(sample_track):
    provider = RMMRevivalProvider(config=ProviderConfig())

    mock_itunes_resp = MagicMock()
    mock_itunes_resp.status_code = 200
    mock_itunes_resp.json.return_value = {
        "results": [
            {
                "trackId": 1440650711,
                "trackName": "Bohemian Rhapsody",
                "artistName": "Queen",
            }
        ]
    }

    mock_lyrics_resp = MagicMock()
    mock_lyrics_resp.status_code = 200
    mock_lyrics_resp.json.return_value = {
        "id": "1440650711",
        "name": "Bohemian Rhapsody",
        "artist": "Queen",
        "duration": 354,
        "ttmlTiming": "word",
        "ttml": """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal" itunes:timing="Word">
          <body><div><p begin="00:01.00" end="00:05.00"><span begin="00:01.00" end="00:03.00">Mama</span></p></div></body>
        </tt>""",
        "syncedLyrics": "[00:01.00] Mama",
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_itunes_resp, mock_lyrics_resp]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert "Mama" in result.content


@pytest.mark.asyncio
async def test_rmmrevival_provider_lrc_fallback(sample_track):
    provider = RMMRevivalProvider(config=ProviderConfig())

    mock_itunes_resp = MagicMock()
    mock_itunes_resp.status_code = 200
    mock_itunes_resp.json.return_value = {
        "results": [
            {
                "trackId": 1440650711,
                "trackName": "Bohemian Rhapsody",
                "artistName": "Queen",
            }
        ]
    }

    mock_lyrics_resp = MagicMock()
    mock_lyrics_resp.status_code = 200
    mock_lyrics_resp.json.return_value = {
        "id": "1440650711",
        "name": "Bohemian Rhapsody",
        "artist": "Queen",
        "duration": 354,
        "ttml": None,
        "syncedLyrics": "[00:01.00] Is this the real life?",
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_itunes_resp, mock_lyrics_resp]):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert "[00:01.00]" in result.content


def test_ttml_builder_and_timestamps():
    from src.ttml import build_ttml, format_ttml_timestamp

    assert format_ttml_timestamp(-5.0) == "00:00.000"
    assert format_ttml_timestamp(0.0) == "00:00.000"
    assert format_ttml_timestamp(65.432) == "01:05.432"

    lines = [
        {
            "start_s": 1.0,
            "end_s": 3.5,
            "tokens": [
                {"start_s": 1.0, "end_s": 2.0, "text": "Hello "},
                {"start_s": 2.0, "end_s": 3.5, "text": "world"},
            ],
        },
        {
            "start_s": 4.0,
            "end_s": 6.0,
            "text": "Fallback plain line",
        },
    ]
    xml = build_ttml(lines, title="Test Song", artist="Test Artist")
    assert "<ttm:title>Test Song</ttm:title>" in xml
    assert "Test Artist" in xml
    assert 'itunes:timing="Word"' in xml
    assert '<span begin="00:01.000" end="00:02.000">Hello </span>' in xml
    assert '<span begin="00:04.000" end="00:06.000">Fallback plain line</span>' in xml

