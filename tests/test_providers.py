import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
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
from src.providers.musixmatch import (
    MusixmatchProvider,
    convert_richsync_to_ttml,
    extract_musixmatch_writers,
)
from src.providers.netease import NetEaseProvider
from src.providers.qqmusic import QQMusicProvider
from src.providers.rmmrevival import RMMRevivalProvider
from src.providers.spicylyrics import SpicyLyricsProvider
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
async def test_musixmatch_token_caching_and_cold_cooldown(tmp_path):
    token_file = tmp_path / "mxm_token.json"
    provider = MusixmatchProvider(config=ProviderConfig(extra={"token_path": str(token_file)}))

    # 1. First fetch retrieves token and saves to disk
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {
        "message": {"header": {"status_code": 200}, "body": {"user_token": "token_save_test_123"}}
    }

    with patch.object(provider, "request_with_retry", return_value=mock_token_resp) as mock_req:
        token = await provider._get_user_token()
        assert token == "token_save_test_123"
        assert token_file.is_file()
        assert "token_save_test_123" in token_file.read_text()
        assert mock_req.call_count == 1

    # 2. Second instance with same token_file reloads without network request
    provider2 = MusixmatchProvider(config=ProviderConfig(extra={"token_path": str(token_file)}))
    with patch.object(provider2, "request_with_retry") as mock_req2:
        token2 = await provider2._get_user_token()
        assert token2 == "token_save_test_123"
        mock_req2.assert_not_called()

    # 3. 401 response sets cold cooldown
    provider3 = MusixmatchProvider(config=ProviderConfig(extra={"token_path": str(token_file)}))
    mock_401_resp = MagicMock()
    mock_401_resp.json.return_value = {"message": {"header": {"status_code": 401}}}

    with patch.object(provider3, "request_with_retry", return_value=mock_401_resp):
        token3 = await provider3._get_user_token(force=True)
        # Should fallback to static token
        assert token3 == "21051986b9886e2d7bd5d8295b15d605c14e13e33326a3a0e50e1b"
        data = json.loads(token_file.read_text())
        assert "cold" in data


@pytest.mark.asyncio
async def test_musixmatch_spotify_id_query(sample_track):
    sample_track.spotify_id = "4u7EnebtmKWzUH433cf5Qv"
    provider = MusixmatchProvider(config=ProviderConfig())

    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {
        "message": {"header": {"status_code": 200}, "body": {"user_token": "valid_token"}}
    }

    mock_macro_resp = MagicMock()
    mock_macro_resp.json.return_value = {
        "message": {
            "header": {"status_code": 200},
            "body": {
                "macro_calls": {
                    "matcher.track.get": {
                        "message": {
                            "body": {"track": {"track_name": "Bohemian Rhapsody", "artist_name": "Queen"}}
                        }
                    },
                    "track.subtitles.get": {
                        "message": {
                            "header": {"status_code": 200},
                            "body": {"subtitle_list": [{"subtitle": {"subtitle_body": "[00:01.00] Life"}}]},
                        }
                    },
                }
            },
        }
    }

    with patch.object(provider, "request_with_retry", side_effect=[mock_token_resp, mock_macro_resp]) as mock_req:
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        # Verify track_spotify_id was passed in macro request params
        macro_call_kwargs = mock_req.call_args_list[1]
        assert macro_call_kwargs.kwargs["params"]["track_spotify_id"] == "4u7EnebtmKWzUH433cf5Qv"
        assert macro_call_kwargs.kwargs["params"]["richsync_compact_type"] == "words"
        assert "Musixmatch/" in macro_call_kwargs.kwargs["headers"]["X-User-Agent"]


def test_musixmatch_richsync_gap_smoothing_and_zero_repair():
    # Line 1: Word with zero duration (end == start == 1.0) and space token delimiting next word
    # Line 2: Words with gap < 0.4s (smoothed) and gap >= 0.4s (preserved)
    rs_data = [
        {
            "ts": 1.0,
            "te": 2.5,
            "l": [
                {"c": "Hello", "o": 0.0},
                {"c": " ", "o": 0.4},
                {"c": "world", "o": 0.5},
            ],
            "x": "Hello world",
        },
        {
            "ts": 3.0,
            "te": 6.0,
            "l": [
                {"c": "One", "o": 0.0},
                {"c": " ", "o": 0.5},
                {"c": "two", "o": 0.6},
                {"c": " ", "o": 1.0},
                {"c": "three", "o": 2.0},  # gap from 4.0 to 5.0 is 1.0s >= 0.4s
            ],
            "x": "One two three",
        },
    ]

    ttml = convert_richsync_to_ttml(rs_data, title="Song", artist="Artist")
    assert ttml is not None
    assert "<tt" in ttml
    assert 'itunes:timing="Word"' in ttml
    assert "Hello" in ttml
    assert "world" in ttml
    assert "One" in ttml


def test_musixmatch_dewording_fallback():
    # 5 lines with only 1 token per line (pseudo-word-sync)
    rs_pseudo = [
        {"ts": float(i * 3), "te": float(i * 3 + 2), "l": [{"c": f"Line {i}", "o": 0.0}], "x": f"Line {i}"}
        for i in range(5)
    ]
    # convert_richsync_to_ttml should reject dewording (return None)
    res = convert_richsync_to_ttml(rs_pseudo, title="Test", artist="Artist")
    assert res is None


def test_musixmatch_songwriters_extraction():
    copyright_line = "Writer(s): Brian May, Freddie Mercury\nLyrics powered by Musixmatch"
    writers = extract_musixmatch_writers(copyright_line)
    assert writers == ["Brian May", "Freddie Mercury"]

    rs_data = [
        {
            "ts": 1.0,
            "te": 3.0,
            "l": [{"c": "Test", "o": 0.0}, {"c": "song", "o": 0.5}],
            "x": "Test song",
        }
    ]
    ttml = convert_richsync_to_ttml(rs_data, title="Song", artist="Artist", songwriters=writers)
    assert ttml is not None
    assert "<songwriter>Brian May</songwriter>" in ttml
    assert "<songwriter>Freddie Mercury</songwriter>" in ttml


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
    # Regression: minute boundary rounding must not produce 00:60.000
    assert format_ttml_timestamp(60.0) == "01:00.000"
    result_boundary = format_ttml_timestamp(59.9997)
    assert ":60" not in result_boundary, f"Got invalid timestamp: {result_boundary}"
    assert result_boundary == "01:00.000"

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


@pytest.mark.asyncio
async def test_spicylyrics_provider_syllable(sample_track):
    sample_track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Syllable",
            "source": "apple_music",
            "UploadAttribution": {"Maker": "Spicy Community"},
            "SongWriters": ["Freddie Mercury"],
            "EndTime": 354.0,
            "Content": [
                {
                    "Lead": {
                        "StartTime": 1.0,
                        "EndTime": 3.0,
                        "Syllables": [
                            {"Text": "Fa", "StartTime": 1.0, "EndTime": 1.5, "IsPartOfWord": True},
                            {"Text": "ther", "StartTime": 1.6, "EndTime": 2.0, "IsPartOfWord": False},
                            {"Text": "stretch", "StartTime": 2.1, "EndTime": 2.5, "IsPartOfWord": False},
                        ],
                    },
                    "Background": [
                        {
                            "Syllables": [
                                {"Text": "re", "StartTime": 2.6, "EndTime": 2.8, "IsPartOfWord": True},
                                {"Text": "al", "StartTime": 2.8, "EndTime": 3.0, "IsPartOfWord": False},
                            ]
                        }
                    ],
                }
            ],
        },
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        assert result.provider_name == "spicylyrics"
        assert result.duration == 354.0
        assert result.metadata["source"] == "apple_music"
        assert result.metadata["provider"] == "Spicy Lyrics"
        assert result.metadata["spotify_id"] == "4cOdK2wGLETKBW3PvgPWqT"
        assert result.metadata["songwriters"] == ["Freddie Mercury"]
        # Commercial source: provider only, no maker/uploader elements
        assert "<ttm:copyright>Lyrics provided by Spicy Lyrics (Apple Music)</ttm:copyright>" in result.content
        assert '<attribution provider="Spicy Lyrics" source="apple_music" />' in result.content
        assert "<maker" not in result.content
        assert "<uploader" not in result.content
        # Multi-syllable word 'Fa-ther': 'Fa' has no trailing space, 'ther ' has trailing space, zero whitespace between spans
        assert '<span begin="00:01.000" end="00:01.500">Fa</span><span begin="00:01.600" end="00:02.000">ther </span>' in result.content
        # Background vocal enclosed in <span ttm:role="x-bg"> with child spans wrapped in parentheses
        assert '<span ttm:role="x-bg"' in result.content
        assert '<span begin="00:02.600" end="00:02.800">(re</span><span begin="00:02.800" end="00:03.000">al)</span>' in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_community_attribution(sample_track):
    sample_track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Syllable",
            "source": "spicy_lyrics",
            "UploadAttribution": {
                "Maker": {"username": "CoolMaker", "url": "https://spicylyrics.org/user/10", "id": 10},
                "Uploader": {"username": "CoolUploader", "url": "https://spicylyrics.org/user/20", "id": 20},
            },
            "SongWriters": ["Freddie Mercury"],
            "EndTime": 354.0,
            "Content": [
                {
                    "Lead": {
                        "StartTime": 1.0,
                        "EndTime": 3.0,
                        "Syllables": [
                            {"Text": "Hello ", "StartTime": 1.0, "EndTime": 3.0, "IsPartOfWord": False},
                        ],
                    },
                }
            ],
        },
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.metadata["provider"] == "Spicy Lyrics"
        assert result.metadata["source"] == "spicy_lyrics"
        # Community source: copyright and structured attribution contain Maker and Uploader
        assert "Lyrics provided by Spicy Lyrics · Synced by CoolMaker (https://spicylyrics.org/user/10) · Uploaded by CoolUploader (https://spicylyrics.org/user/20)" in result.content
        assert '<attribution provider="Spicy Lyrics" source="spicy_lyrics">' in result.content
        assert '<maker username="CoolMaker" id="10" url="https://spicylyrics.org/user/10" />' in result.content
        assert '<uploader username="CoolUploader" id="20" url="https://spicylyrics.org/user/20" />' in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_duet(sample_track):
    sample_track.artist = "Billie Eilish & Khalid"
    sample_track.title = "lovely"
    sample_track.spotify_id = "0u2P5u6lvoDfwTYjAADbn4"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Syllable",
            "source": "apple_music",
            "UploadAttribution": {"Maker": "Spicy Community"},
            "SongWriters": ["Billie Eilish", "Khalid Robinson"],
            "EndTime": 200.0,
            "Content": [
                {
                    "OppositeAligned": False,
                    "Lead": {
                        "StartTime": 10.0,
                        "EndTime": 12.0,
                        "Syllables": [
                            {"Text": "Thought ", "StartTime": 10.0, "EndTime": 11.0, "IsPartOfWord": False},
                            {"Text": "I ", "StartTime": 11.0, "EndTime": 12.0, "IsPartOfWord": False},
                        ],
                    },
                },
                {
                    "OppositeAligned": True,
                    "Lead": {
                        "StartTime": 13.0,
                        "EndTime": 15.0,
                        "Syllables": [
                            {"Text": "found ", "StartTime": 13.0, "EndTime": 14.0, "IsPartOfWord": False},
                            {"Text": "a ", "StartTime": 14.0, "EndTime": 15.0, "IsPartOfWord": False},
                        ],
                    },
                },
            ],
        },
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TTML
        assert result.sync_type == LyricsSyncType.WORD_SYNC
        # Check agents in metadata
        assert '<ttm:agent type="person" xml:id="v1">Billie Eilish</ttm:agent>' in result.content
        assert '<ttm:agent type="person" xml:id="v2">Khalid</ttm:agent>' in result.content
        # Check agent attributes on paragraphs
        assert 'ttm:agent="v1"' in result.content
        assert 'ttm:agent="v2"' in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_line(sample_track):
    sample_track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Line",
            "source": "spotify",
            "EndTime": 120.0,
            "Content": [
                {"Text": "Is this the real life?", "StartTime": 10.5, "EndTime": 13.0},
                {"Text": "Is this just fantasy?", "StartTime": 13.5, "EndTime": 16.0},
            ],
        },
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.LRC
        assert result.sync_type == LyricsSyncType.LINE_SYNC
        assert result.provider_name == "spicylyrics"
        assert "[00:10.50]Is this the real life?" in result.content
        assert "[00:13.50]Is this just fantasy?" in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_static(sample_track):
    sample_track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Static",
            "source": "spicy_lyrics",
            "Lines": [
                {"Text": "Is this the real life?"},
                {"Text": "Is this just fantasy?"},
            ],
        },
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.format == LyricsFormat.TXT
        assert result.sync_type == LyricsSyncType.UNSYNCED
        assert result.provider_name == "spicylyrics"
        assert "Is this the real life?\nIs this just fantasy?" == result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_no_key(sample_track):
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key=None))
    result = await provider.get_lyrics(sample_track)
    assert result is None


@pytest.mark.asyncio
async def test_spicylyrics_provider_invalid_id(sample_track):
    sample_track.spotify_id = "short_id"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))
    result = await provider.get_lyrics(sample_track)
    assert result is None


@pytest.mark.asyncio
async def test_spicylyrics_provider_spotify_search_resolution(sample_track):
    sample_track.spotify_id = None
    sample_track.isrc = "GBUM71029604"
    provider = SpicyLyricsProvider(
        config=ProviderConfig(
            api_key="sl_sk_test_123",
            extra={
                "spotify_client_id": "test_client_id",
                "spotify_client_secret": "test_client_secret",
            },
        )
    )

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "sp_mock_token", "expires_in": 3600}

    mock_search_resp = MagicMock()
    mock_search_resp.status_code = 200
    mock_search_resp.json.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "4cOdK2wGLETKBW3PvgPWqT",
                    "duration_ms": 354000,
                }
            ]
        }
    }

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_token_resp)
    mock_client.get = AsyncMock(return_value=mock_search_resp)

    mock_spicy_resp = MagicMock()
    mock_spicy_resp.status_code = 200
    mock_spicy_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Line",
            "Content": [{"Text": "Mama, just killed a man", "StartTime": 5.0}],
        },
    }

    with patch.object(provider, "get_client", new_callable=AsyncMock, return_value=mock_client), \
         patch.object(provider, "request_with_retry", return_value=mock_spicy_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.metadata["spotify_id"] == "4cOdK2wGLETKBW3PvgPWqT"
        assert "[00:05.00]Mama, just killed a man" in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_musicbrainz_isrc_resolution(sample_track):
    sample_track.spotify_id = None
    sample_track.isrc = "GBUM71029604"
    # No spotify client id/secret provided
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_mb_resp = MagicMock()
    mock_mb_resp.status_code = 200
    mock_mb_resp.json.return_value = {
        "recordings": [
            {
                "id": "rec-123",
                "relations": [
                    {
                        "url": {
                            "resource": "https://open.spotify.com/track/2OBofMJx94NryV2SK8p8Zf"
                        }
                    }
                ],
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_mb_resp)

    mock_spicy_resp = MagicMock()
    mock_spicy_resp.status_code = 200
    mock_spicy_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Line",
            "Content": [{"Text": "Mama, just killed a man", "StartTime": 5.0}],
        },
    }

    with patch.object(provider, "get_client", new_callable=AsyncMock, return_value=mock_client), \
         patch.object(provider, "request_with_retry", return_value=mock_spicy_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.metadata["spotify_id"] == "2OBofMJx94NryV2SK8p8Zf"
        assert "[00:05.00]Mama, just killed a man" in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_anonymous_isrc_resolution(sample_track):
    sample_track.spotify_id = None
    sample_track.isrc = "PL4K02622961"
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_embed_resp = MagicMock()
    mock_embed_resp.status_code = 200
    mock_embed_resp.text = """
    <html><script id="__NEXT_DATA__" type="application/json">
    {"props": {"pageProps": {"state": {"settings": {"session": {
        "accessToken": "mock_anon_token_123",
        "accessTokenExpirationTimestampMs": 1999999999000
    }}}}}}
    </script></html>
    """

    mock_pathfinder_resp = MagicMock()
    mock_pathfinder_resp.status_code = 200
    mock_pathfinder_resp.json.return_value = {
        "data": {
            "searchV2": {
                "tracksV2": {
                    "items": [
                        {
                            "item": {
                                "data": {
                                    "name": "PRESIDENT",
                                    "uri": "spotify:track:22AyfOBziPxrg9sHz0L6Yw",
                                    "duration": {"totalMilliseconds": 186666},
                                }
                            }
                        }
                    ]
                }
            }
        }
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[mock_embed_resp, mock_pathfinder_resp])

    mock_spicy_resp = MagicMock()
    mock_spicy_resp.status_code = 200
    mock_spicy_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Line",
            "Content": [{"Text": "Mój trzeci Rolex", "StartTime": 12.0}],
        },
    }

    with patch.object(provider, "get_client", new_callable=AsyncMock, return_value=mock_client), \
         patch.object(provider, "request_with_retry", return_value=mock_spicy_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.metadata["spotify_id"] == "22AyfOBziPxrg9sHz0L6Yw"
        assert "[00:12.00]Mój trzeci Rolex" in result.content


@pytest.mark.asyncio
async def test_spicylyrics_provider_anonymous_title_artist_resolution(sample_track):
    sample_track.spotify_id = None
    sample_track.isrc = None
    sample_track.duration = 186.0
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_embed_resp = MagicMock()
    mock_embed_resp.status_code = 200
    mock_embed_resp.text = """
    <html><script id="__NEXT_DATA__" type="application/json">
    {"props": {"pageProps": {"state": {"settings": {"session": {
        "accessToken": "mock_anon_token_123",
        "accessTokenExpirationTimestampMs": 1999999999000
    }}}}}}
    </script></html>
    """

    mock_pathfinder_resp = MagicMock()
    mock_pathfinder_resp.status_code = 200
    mock_pathfinder_resp.json.return_value = {
        "data": {
            "searchV2": {
                "tracksV2": {
                    "items": [
                        {
                            "item": {
                                "data": {
                                    "name": "PRESIDENT",
                                    "uri": "spotify:track:22AyfOBziPxrg9sHz0L6Yw",
                                    "duration": {"totalMilliseconds": 186000},
                                }
                            }
                        }
                    ]
                }
            }
        }
    }

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[mock_embed_resp, mock_pathfinder_resp])

    mock_spicy_resp = MagicMock()
    mock_spicy_resp.status_code = 200
    mock_spicy_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Line",
            "Content": [{"Text": "Mój trzeci Rolex", "StartTime": 12.0}],
        },
    }

    with patch.object(provider, "get_client", new_callable=AsyncMock, return_value=mock_client), \
         patch.object(provider, "request_with_retry", return_value=mock_spicy_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert result.metadata["spotify_id"] == "22AyfOBziPxrg9sHz0L6Yw"


def test_providers_module_annotations():
    """Verify that src.providers annotations can be evaluated without NameError."""
    import src.providers as p
    import typing
    hints = typing.get_type_hints(p)
    assert "PROVIDER_METADATA" in hints


@pytest.mark.asyncio
async def test_base_provider_retry_after_http_date():
    """Verify that BaseLyricsProvider.request_with_retry does not crash on RFC HTTP-date Retry-After."""
    from src.providers.base import BaseLyricsProvider

    class DummyProvider(BaseLyricsProvider):
        name = "dummy"
        async def get_lyrics(self, track):
            return None

    prov = DummyProvider(config=ProviderConfig(timeout_seconds=5.0), max_retries=2)
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.request = AsyncMock(side_effect=[mock_resp_429, mock_resp_200])

    with patch.object(prov, "get_client", new_callable=AsyncMock, return_value=mock_client), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await prov.request_with_retry("GET", "https://example.com/api")
        assert resp == mock_resp_200
        mock_sleep.assert_called_once_with(2.0)


@pytest.mark.asyncio
async def test_spicylyrics_timestamp_minute_boundary(sample_track):
    """Verify that SpicyLyrics Line format near minute boundary does not produce [00:60.00]."""
    sample_track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    sample_track._match_score = 0.88
    provider = SpicyLyricsProvider(config=ProviderConfig(api_key="sl_sk_test_123"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "Status": 200,
        "Type": "object",
        "Body": {
            "Type": "Line",
            "EndTime": 120.0,
            "Content": [
                {"Text": "Boundary line", "StartTime": 59.9997, "EndTime": 62.0},
            ],
        },
    }

    with patch.object(provider, "request_with_retry", return_value=mock_resp):
        result = await provider.get_lyrics(sample_track)
        assert result is not None
        assert ":60" not in result.content
        assert "[01:00.00]Boundary line" in result.content
        assert result.match_score == 0.88



