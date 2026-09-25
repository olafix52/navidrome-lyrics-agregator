"""Comprehensive test suite for the 5 mild-lyrics blend providers and blending engine."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.blender import (
    _key,
    _recut,
    _unlump,
    _unsplit,
    blend_karaoke_lines,
    blend_lyrics,
    pair_lines,
    relay_word_timings,
)
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.providers.blends import (
    AppleKugouBlendProvider,
    AppleNetEaseBlendProvider,
    AppleNetEaseKugouBlendProvider,
    AppleNetEaseQQBlendProvider,
    AppleQQBlendProvider,
)
from src.config import ProviderConfig
from src.web.parser import KaraokeLine, KaraokeWord, parse_ttml_to_karaoke
from pathlib import Path


def test_blender_key_normalization():
    assert _key("Hello, World!") == "helloworld"
    assert _key("Don't  Stop  Believin'") == "dontstopbelievin"
    assert _key("") == ""
    assert _key("123-ABC") == "123abc"


def test_blender_pair_lines():
    base = [
        KaraokeLine(text="Is this the real life?"),
        KaraokeLine(text="Is this just fantasy?"),
        KaraokeLine(text="Caught in a landslide,"),
        KaraokeLine(text="No escape from reality."),
    ]
    # Donor has slight variations in punctuation and casing
    donor = [
        KaraokeLine(text="is this the real life"),
        KaraokeLine(text="is this just fantasy"),
        KaraokeLine(text="caught in a landslide"),
        KaraokeLine(text="no escape from reality!"),
    ]

    pairs = pair_lines(base, donor)
    assert pairs is not None
    assert pairs == {0: 0, 1: 1, 2: 2, 3: 3}


def test_blender_pair_lines_rejects_unrelated_song():
    base = [
        KaraokeLine(text="Yesterday all my troubles seemed so far away"),
        KaraokeLine(text="Now it looks as though they're here to stay"),
    ]
    donor = [
        KaraokeLine(text="We will we will rock you"),
        KaraokeLine(text="Singing we will we will rock you"),
    ]
    assert pair_lines(base, donor) is None


def test_blender_relay_word_timings_uncensoring():
    """Verify that censored words (e.g. f***) in donor receive timing onto clean words (fucking)."""
    base_text = "I am fucking back now"
    donor_words = [
        KaraokeWord(text="I", start=1.0, end=1.2),
        KaraokeWord(text="am", start=1.2, end=1.5),
        KaraokeWord(text="f***", start=1.5, end=2.0),
        KaraokeWord(text="back", start=2.0, end=2.3),
        KaraokeWord(text="now", start=2.3, end=2.6),
    ]

    relayed = relay_word_timings(base_text, donor_words)
    assert relayed is not None
    assert len(relayed) == 5
    assert [w.text for w in relayed] == ["I", "am", "fucking", "back", "now"]
    assert relayed[2].text == "fucking"
    assert relayed[2].start == 1.5
    assert relayed[2].end == 2.0


def test_blender_relay_word_timings_slang_difference():
    """Verify that slang difference (e.g. nothin' vs nothing) is reconciled accurately."""
    base_text = "There is nothing left"
    donor_words = [
        KaraokeWord(text="There", start=0.5, end=0.8),
        KaraokeWord(text="is", start=0.8, end=1.0),
        KaraokeWord(text="nothin'", start=1.0, end=1.4),
        KaraokeWord(text="left", start=1.4, end=1.8),
    ]

    relayed = relay_word_timings(base_text, donor_words)
    assert relayed is not None
    assert [w.text for w in relayed] == ["There", "is", "nothing", "left"]
    assert relayed[2].text == "nothing"
    assert relayed[2].start == 1.0
    assert relayed[2].end == 1.4


def test_blender_unsplit_contractions():
    """Verify that accidental cuts at apostrophes are recombined."""
    syls = [
        {"Text": "It'", "StartTime": 0.0, "EndTime": 0.2, "IsPartOfWord": True},
        {"Text": "s", "StartTime": 0.2, "EndTime": 0.4, "IsPartOfWord": False},
        {"Text": "fine", "StartTime": 0.5, "EndTime": 0.8, "IsPartOfWord": False},
    ]
    unsplit = _unsplit(syls)
    assert len(unsplit) == 2
    assert unsplit[0]["Text"] == "It's"
    assert unsplit[0]["EndTime"] == 0.4
    assert unsplit[1]["Text"] == "fine"


def test_blender_unlump_multiword_tokens():
    """Verify that donor tokens grouping multiple words are distributed proportionally."""
    syls = [
        {"Text": "Let me go", "StartTime": 1.0, "EndTime": 2.2, "IsPartOfWord": False},
    ]
    unlumped = _unlump(syls)
    assert len(unlumped) == 3
    texts = [w["Text"].strip() for w in unlumped]
    assert texts == ["Let", "me", "go"]
    assert unlumped[0]["StartTime"] == 1.0
    assert unlumped[-1]["EndTime"] == 2.2


def test_blender_three_way_blend_gap_fill():
    """Verify triblend/kutriblend logic: NetEase missed a line, spare donor (QQ) fills it."""
    base_lines = [
        KaraokeLine(text="Line one of song", start=1.0, end=3.0),
        KaraokeLine(text="Line two of song", start=3.0, end=5.0),
        KaraokeLine(text="Line three of song", start=5.0, end=7.0),
    ]

    # Primary donor (NetEase) has line 1 and line 3, missing line 2
    netease_lines = [
        KaraokeLine(
            text="Line one of song",
            start=1.1,
            end=2.9,
            words=[
                KaraokeWord(text="Line", start=1.1, end=1.5),
                KaraokeWord(text="one", start=1.5, end=1.9),
                KaraokeWord(text="of", start=1.9, end=2.2),
                KaraokeWord(text="song", start=2.2, end=2.9),
            ],
        ),
        KaraokeLine(
            text="Line three of song",
            start=5.1,
            end=6.9,
            words=[
                KaraokeWord(text="Line", start=5.1, end=5.5),
                KaraokeWord(text="three", start=5.5, end=6.0),
                KaraokeWord(text="of", start=6.0, end=6.3),
                KaraokeWord(text="song", start=6.3, end=6.9),
            ],
        ),
    ]

    # Spare donor (QQ Music) has line 2 with word timings
    qq_lines = [
        KaraokeLine(
            text="Line two of song",
            start=3.1,
            end=4.9,
            words=[
                KaraokeWord(text="Line", start=3.1, end=3.5),
                KaraokeWord(text="two", start=3.5, end=4.0),
                KaraokeWord(text="of", start=4.0, end=4.3),
                KaraokeWord(text="song", start=4.3, end=4.9),
            ],
        ),
    ]

    blended_lines = blend_karaoke_lines(base_lines, netease_lines, spare_lines=qq_lines)
    assert blended_lines is not None
    assert len(blended_lines) == 3

    # All three lines should now have 4 word tokens!
    for line in blended_lines:
        assert len(line["tokens"]) == 4

    assert [t["text"].strip() for t in blended_lines[1]["tokens"]] == ["Line", "two", "of", "song"]
    assert blended_lines[1]["tokens"][1]["text"].strip() == "two"
    assert blended_lines[1]["start_s"] == 3.1


def test_blend_lyrics_produces_valid_ttml():
    base = LyricsResult(
        provider_name="apple_music",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        content="[00:01.00]Hello world\n[00:03.00]Second line",
        title="Test Song",
        artist="Test Artist",
    )
    donor = LyricsResult(
        provider_name="qqmusic",
        format=LyricsFormat.TTML,
        sync_type=LyricsSyncType.WORD_SYNC,
        content="""<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">
  <body>
    <div>
      <p begin="00:01.000" end="00:02.500">
        <span begin="00:01.000" end="00:01.600">Hello</span>
        <span begin="00:01.600" end="00:02.500">world</span>
      </p>
      <p begin="00:03.000" end="00:04.500">
        <span begin="00:03.000" end="00:03.700">Second</span>
        <span begin="00:03.700" end="00:04.500">line</span>
      </p>
    </div>
  </body>
</tt>""",
        title="Test Song",
        artist="Test Artist",
    )

    result = blend_lyrics(base, donor, blend_name="blend", donor_label="QQ Music")
    assert result is not None
    assert result.format == LyricsFormat.TTML
    assert result.sync_type == LyricsSyncType.WORD_SYNC
    assert result.provider_name == "blend"
    assert 'itunes:timing="Word"' in result.content
    assert "Apple Music + QQ Music" in result.content

    # Parse resulting TTML and ensure words were preserved
    parsed = parse_ttml_to_karaoke(result.content)
    assert len(parsed) == 2
    assert parsed[0].text == "Hello world"
    assert parsed[0].words is not None
    assert len(parsed[0].words) == 2
    assert parsed[0].words[0].text == "Hello "
    assert parsed[0].words[1].text == "world"


@pytest.mark.asyncio
async def test_apple_qq_blend_provider_mocked():
    provider = AppleQQBlendProvider(config=ProviderConfig())
    track = TrackMetadata(file_path=Path("dummy.mp3"), title="Song", artist="Artist", duration=180.0)

    # Base returns line sync
    mock_base = LyricsResult(
        provider_name="apple_music",
        format=LyricsFormat.LRC,
        sync_type=LyricsSyncType.LINE_SYNC,
        content="[00:05.00]Testing blend lines",
        title="Song",
        artist="Artist",
    )
    # Donor returns word sync
    mock_donor = LyricsResult(
        provider_name="qqmusic",
        format=LyricsFormat.TTML,
        sync_type=LyricsSyncType.WORD_SYNC,
        content="""<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml">
  <body>
    <div>
      <p begin="00:05.000" end="00:08.000">
        <span begin="00:05.000" end="00:05.800">Testing</span>
        <span begin="00:05.800" end="00:06.500">blend</span>
        <span begin="00:06.500" end="00:08.000">lines</span>
      </p>
    </div>
  </body>
</tt>""",
        title="Song",
        artist="Artist",
    )

    with patch.object(provider, "_fetch_base", return_value=mock_base), \
         patch.object(provider, "_fetch_donor", return_value=mock_donor):
        res = await provider.get_lyrics(track)
        assert res is not None
        assert res.provider_name == "blend"
        assert res.sync_type == LyricsSyncType.WORD_SYNC
        assert res.format == LyricsFormat.TTML
        parsed_out = parse_ttml_to_karaoke(res.content)
        assert len(parsed_out) == 1
        assert parsed_out[0].text == "Testing blend lines"

    await provider.close()


@pytest.mark.asyncio
async def test_blend_provider_skips_when_base_already_word_sync():
    """Verify that if Apple Music already has word-sync TTML, it is returned without unnecessary blending."""
    provider = AppleKugouBlendProvider(config=ProviderConfig())
    track = TrackMetadata(file_path=Path("dummy.mp3"), title="Song", artist="Artist", duration=180.0)

    mock_base_word_sync = LyricsResult(
        provider_name="apple_music",
        format=LyricsFormat.TTML,
        sync_type=LyricsSyncType.WORD_SYNC,
        content="<tt>already word synced</tt>",
        title="Song",
        artist="Artist",
    )
    mock_donor = LyricsResult(
        provider_name="kugou",
        format=LyricsFormat.TTML,
        sync_type=LyricsSyncType.WORD_SYNC,
        content="<tt>kugou timing</tt>",
        title="Song",
        artist="Artist",
    )

    with patch.object(provider, "_fetch_base", return_value=mock_base_word_sync), \
         patch.object(provider, "_fetch_donor", return_value=mock_donor):
        res = await provider.get_lyrics(track)
        assert res is not None
        assert res.content == "<tt>already word synced</tt>"
        assert res.provider_name == "apple_music"

    await provider.close()


@pytest.mark.asyncio
async def test_triblend_and_kutriblend_instantiation():
    """Verify all 5 blend providers instantiate and declare correct names and donor labels."""
    b1 = AppleQQBlendProvider(config=ProviderConfig())
    b2 = AppleKugouBlendProvider(config=ProviderConfig())
    b3 = AppleNetEaseBlendProvider(config=ProviderConfig())
    b4 = AppleNetEaseQQBlendProvider(config=ProviderConfig())
    b5 = AppleNetEaseKugouBlendProvider(config=ProviderConfig())

    assert b1.name == "blend"
    assert b1.donor_label == "QQ Music"
    assert b2.name == "kublend"
    assert b2.donor_label == "Kugou"
    assert b3.name == "neblend"
    assert b3.donor_label == "NetEase"
    assert b4.name == "triblend"
    assert b4.donor_label == "NetEase"
    assert b4.spare_label == "QQ Music"
    assert b5.name == "kutriblend"
    assert b5.donor_label == "NetEase"
    assert b5.spare_label == "Kugou"

    for b in (b1, b2, b3, b4, b5):
        await b.close()


def test_parse_ttml_ignores_translation_and_romanization():
    """Verify that auxiliary spans (x-translation, x-roman) do not pollute lyrics text or word spans."""
    ttml = """<?xml version="1.0" encoding="utf-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" itunes:timing="Word">
      <body>
        <div>
          <p begin="00:01.000" end="00:03.000">
            <span begin="00:01.000" end="00:01.800">Hello </span>
            <span begin="00:01.800" end="00:02.500">world</span>
            <span ttm:role="x-translation" xml:lang="pl">Witaj świecie</span>
            <span ttm:role="x-roman">he-lo werd</span>
          </p>
        </div>
      </body>
    </tt>
    """
    lines = parse_ttml_to_karaoke(ttml)
    assert len(lines) == 1
    assert lines[0].text == "Hello world"
    assert lines[0].words is not None
    assert len(lines[0].words) == 2
    assert lines[0].words[0].text == "Hello "
    assert lines[0].words[1].text == "world"



# ---------------------------------------------------------------------------
# Regression tests: blended text must reproduce the base line exactly, and base
# line order must survive donors timed against a different master.
# ---------------------------------------------------------------------------

import xml.etree.ElementTree as _ET

from src.providers.kugou import convert_krc_to_ttml
from src.providers.netease import convert_yrc_to_ttml

_TT = "{http://www.w3.org/ns/ttml}"


def _lrc(lines):
    return "\n".join(f"[{int(t // 60):02d}:{t % 60:05.2f}]{x}" for t, x in lines)


def _yrc(lines):
    out = []
    for ls, words in lines:
        end = max(s + d for _, s, d in words)
        out.append(f"[{int(ls * 1000)},{int((end - ls) * 1000)}]"
                   + "".join(f"({int(s * 1000)},{int(d * 1000)},0){t}" for t, s, d in words))
    return "\n".join(out)


def _krc(lines):
    out = []
    for ls, words in lines:
        end = max(s + d for _, s, d in words)
        out.append(f"[{int(ls * 1000)},{int((end - ls) * 1000)}]"
                   + "".join(f"<{int((s - ls) * 1000)},{int(d * 1000)},0>{t}" for t, s, d in words))
    return "\n".join(out)


def _res(name, content, fmt, sync):
    return LyricsResult(provider_name=name, content=content, format=fmt, sync_type=sync,
                        title="Song", artist="Artist", duration=200.0)


def _base(lines):
    return _res("apple_music", _lrc(lines), LyricsFormat.LRC, LyricsSyncType.LINE_SYNC)


def _donor(ttml, name="netease"):
    return _res(name, ttml, LyricsFormat.TTML, LyricsSyncType.WORD_SYNC)


def _lines(result):
    """[(begin_seconds, rendered_text, [span texts])] of a blended TTML result."""
    out = []
    for p in _ET.fromstring(result.content).iter(_TT + "p"):
        spans = [s for s in p if s.tag == _TT + "span"]
        begin = p.get("begin")
        m, sec = begin.split(":")
        out.append((int(m) * 60 + float(sec), "".join(s.text or "" for s in spans), [s.text for s in spans]))
    return out


def test_blend_syllable_donor_keeps_words_intact():
    base = [(20.0, "You are beautiful tonight")]
    donor = _krc([(20.0, [("You ", 20.0, .3), ("are ", 20.3, .3), ("beau", 20.6, .3), ("ti", 20.9, .2),
                          ("ful ", 21.1, .4), ("to", 21.5, .3), ("night", 21.8, .6)])])
    lines = _lines(blend_lyrics(_base(base), _donor(convert_krc_to_ttml(donor), "kugou")))
    assert lines[0][1] == "You are beautiful tonight"
    assert lines[0][2] == ["You ", "are ", "beau", "ti", "ful ", "to", "night"]


def test_blend_leading_punctuation_stays_with_its_word():
    base = [(30.0, 'She said "go" (go now!)')]
    donor = _yrc([(30.0, [("She ", 30.0, .3), ("said ", 30.3, .3), ("go ", 30.6, .4), ("go ", 31.0, .4), ("now", 31.4, .5)])])
    lines = _lines(blend_lyrics(_base(base), _donor(convert_yrc_to_ttml(donor))))
    assert lines[0][1] == 'She said "go" (go now!)'
    assert lines[0][2] == ["She ", "said ", '"go" ', "(go ", "now!)"]


def test_blend_keeps_base_order_with_offset_donor_and_missing_line():
    base = [(10.0, "First line here"), (13.0, "Middle line only in Apple"),
            (16.0, "Third line here"), (19.0, "Fourth line here")]
    donor = _yrc([(16.0, [("First ", 16.0, .5), ("line ", 16.5, .5), ("here", 17.0, .5)]),
                  (22.0, [("Third ", 22.0, .5), ("line ", 22.5, .5), ("here", 23.0, .5)]),
                  (25.0, [("Fourth ", 25.0, .5), ("line ", 25.5, .5), ("here", 26.0, .5)])])
    lines = _lines(blend_lyrics(_base(base), _donor(convert_yrc_to_ttml(donor))))
    assert [t for _, t, _ in lines] == [t for _, t in base]
    # The Apple-only line is moved onto the donor timeline (+6 s)
    assert lines[1][0] == pytest.approx(19.0)
    assert [b for b, _, _ in lines] == sorted(b for b, _, _ in lines)


def test_triblend_spare_donor_is_moved_onto_primary_timeline():
    base = [(10.0, "Line one"), (14.0, "Line two only in spare"), (18.0, "Line three")]
    primary = _yrc([(12.0, [("Line ", 12.0, .4), ("one", 12.4, .6)]), (20.0, [("Line ", 20.0, .4), ("three", 20.4, .6)])])
    spare = _krc([(11.0, [("Line ", 11.0, .4), ("one", 11.4, .6)]),
                  (15.0, [("Line ", 15.0, .3), ("two ", 15.3, .3), ("only ", 15.6, .3), ("in ", 15.9, .2), ("spare", 16.1, .5)]),
                  (19.0, [("Line ", 19.0, .4), ("three", 19.4, .6)])])
    lines = _lines(blend_lyrics(_base(base), _donor(convert_yrc_to_ttml(primary)),
                                spare_donor=_donor(convert_krc_to_ttml(spare), "kugou")))
    assert [t for _, t, _ in lines] == [t for _, t in base]
    assert [b for b, _, _ in lines] == pytest.approx([12.0, 16.0, 20.0])


def test_blend_partial_donor_coverage_keeps_word_separator():
    base = [(1.0, "Hello there world"), (5.0, "Second line")]
    donor = _yrc([(1.0, [("Hello ", 1.0, .5), ("there", 1.5, .5)]), (5.0, [("Second ", 5.0, .5), ("line", 5.5, .5)])])
    lines = _lines(blend_lyrics(_base(base), _donor(convert_yrc_to_ttml(donor))))
    assert [t for _, t, _ in lines] == ["Hello there world", "Second line"]


def test_blend_cjk_and_censored_words():
    base = [(1.0, "This is the first line"), (5.0, "What the fuck is going on"), (9.0, "我爱你中国")]
    donor = _yrc([(1.0, [("This ", 1.0, .3), ("is ", 1.3, .2), ("the ", 1.5, .2), ("first ", 1.7, .4), ("line", 2.1, .5)]),
                  (5.0, [("What ", 5.0, .3), ("the ", 5.3, .2), ("f*** ", 5.5, .4), ("is ", 5.9, .2), ("going ", 6.1, .4), ("on", 6.5, .5)]),
                  (9.0, [("我", 9.0, .3), ("爱", 9.3, .3), ("你", 9.6, .3), ("中", 9.9, .3), ("国", 10.2, .5)])])
    lines = _lines(blend_lyrics(_base(base), _donor(convert_yrc_to_ttml(donor))))
    assert [t for _, t, _ in lines] == [t for _, t in base]
    assert "fuck " in lines[1][2]
    assert lines[2][2] == ["我", "爱", "你", "中", "国"]


@pytest.mark.asyncio
async def test_blend_provider_keeps_word_synced_base_even_with_word_synced_donor():
    """Apple's own word timing is never replaced by donor timing."""
    provider = AppleNetEaseBlendProvider(config=ProviderConfig())
    track = TrackMetadata(file_path=Path("dummy.mp3"), title="Song", artist="Artist", duration=180.0)
    base = LyricsResult(provider_name="apple_music", format=LyricsFormat.TTML, sync_type=LyricsSyncType.WORD_SYNC,
                        content="<tt>apple word timing</tt>", title="Song", artist="Artist")
    donor = LyricsResult(provider_name="netease", format=LyricsFormat.TTML, sync_type=LyricsSyncType.WORD_SYNC,
                         content="<tt>netease word timing</tt>", title="Song", artist="Artist")

    with patch.object(provider, "_fetch_base", return_value=base), \
         patch.object(provider, "_fetch_donor", return_value=donor):
        res = await provider.get_lyrics(track)
    assert res is not None and res.content == "<tt>apple word timing</tt>"

    # ...also when no donor was found at all
    with patch.object(provider, "_fetch_base", return_value=base), \
         patch.object(provider, "_fetch_donor", return_value=None):
        res = await provider.get_lyrics(track)
    assert res is not None and res.content == "<tt>apple word timing</tt>"

    await provider.close()
