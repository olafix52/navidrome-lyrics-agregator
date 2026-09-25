"""Tests for restoring masked explicit words in lyrics."""

from pathlib import Path

import pytest

from src.config import AppConfig
from src.matcher import LyricsMatcher
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, MatchStatus, TrackMetadata
from src.providers.base import BaseLyricsProvider
from src.uncensor import has_masked_words, restore_from_references, uncensor_lyrics_content, uncensor_text


@pytest.mark.parametrize("masked, expected", [
    ("f**k", "fuck"), ("F**k it", "Fuck it"), ("b**ches", "bitches"), ("B***H", "BITCH"),
    ("ni**a", "nigga"), ("n***as", "niggas"), ("n-gga", "nigga"), ("N-ggas", "Niggas"),
    ("sh*t", "shit"), ("godd**n", "goddamn"), ("p**sy", "pussy"), ("f**kin'", "fuckin'"),
])
def test_pattern_masks_are_restored(masked, expected):
    assert uncensor_text(masked)[0] == expected


@pytest.mark.parametrize("text", [
    "tippy-toes", "ta-tatted", "du-du", "k-kenny", "hip-hop", "x-ray", "a-s", "**** you", "#hashtag",
])
def test_non_masks_and_unknowable_masks_are_untouched(text):
    assert uncensor_text(text) == (text, 0)


def test_mask_split_over_ttml_word_spans_keeps_timing():
    ttml = ('<tt><body><div><p begin="1" end="3"><span begin="1" end="1.5">My </span>'
            '<span begin="1.5" end="1.6">n-</span><span begin="1.6" end="2">ggas </span>'
            '<span begin="2" end="3">out</span></p></div></body></tt>')
    out, count = uncensor_lyrics_content(ttml, LyricsFormat.TTML)
    assert count == 1
    assert '<span begin="1.5" end="1.6">ni</span><span begin="1.6" end="2">ggas </span>' in out


REF = ("[00:10.00]Fuck niggas, I don't fuck with them\n"
       "[00:12.00]I don't trust no nigga, why not?\n"
       "[00:14.00]'Cause they pussy niggas, they snake niggas")


def test_fully_masked_words_filled_from_reference_lrc():
    lrc = ("[00:10.00]Fuck ****, I don't fuck with them\n"
           "[00:12.00]I don't trust no ****, why not?\n"
           "[00:14.00]'Cause they pussy ****, they snake ****\n"
           "[00:16.00]Some unrelated **** line here")
    out, count = restore_from_references(lrc, LyricsFormat.LRC, [REF])
    assert count == 4
    assert out.splitlines()[:3] == REF.splitlines()
    assert out.splitlines()[3] == "[00:16.00]Some unrelated **** line here"  # no matching reference line
    assert has_masked_words(out)


def test_reference_words_outside_explicit_list_are_never_used():
    lrc = "[00:12.00]I don't trust no ****, why not?"
    ref = "I don't trust no Bob, why not?"
    assert restore_from_references(lrc, LyricsFormat.LRC, [ref]) == (lrc, 0)


def test_fully_masked_word_filled_inside_ttml_span():
    ttml = ('<tt><body><div><p begin="1" end="2"><span begin="1" end="1.2">I </span>'
            '<span begin="1.2" end="1.4">don\'t </span><span begin="1.4" end="1.6">trust </span>'
            '<span begin="1.6" end="1.8">no </span><span begin="1.8" end="2">****, </span>'
            '<span begin="2" end="2.2">why </span><span begin="2.2" end="2.4">not?</span></p></div></body></tt>')
    out, count = restore_from_references(ttml, LyricsFormat.TTML, [REF])
    assert count == 1
    assert '<span begin="1.8" end="2">nigga, </span>' in out


class _Provider(BaseLyricsProvider):
    def __init__(self, name, result):
        super().__init__()
        self.name = name
        self.result = result
        self.calls = 0

    async def get_lyrics(self, track):
        self.calls += 1
        return self.result


def _result(provider, content, sync=LyricsSyncType.LINE_SYNC, fmt=LyricsFormat.LRC):
    return LyricsResult(content=content, format=fmt, sync_type=sync, provider_name=provider,
                        title="My Song", artist="Artist", duration=200.0)


@pytest.mark.asyncio
async def test_matcher_restores_masks_before_saving(tmp_path: Path):
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"x")
    track = TrackMetadata(file_path=audio, title="My Song", artist="Artist", duration=200.0)

    apple = _Provider("spicylyrics", _result("spicylyrics", "[00:12.00]I don't trust no ****, why not?\n[00:14.00]F**k it"))
    genius = _Provider("genius", _result("genius", "I don't trust no nigga, why not?\nFuck it", LyricsSyncType.UNSYNCED, LyricsFormat.TXT))

    config = AppConfig(music_dir=tmp_path, cache={"enabled": False})
    res = await LyricsMatcher(config, [apple, genius]).process_track(track)
    assert res.status == MatchStatus.SUCCESS and res.provider == "spicylyrics"
    saved = (tmp_path / "song.lrc").read_text(encoding="utf-8")
    assert saved == "[00:12.00]I don't trust no nigga, why not?\n[00:14.00]Fuck it\n"


@pytest.mark.asyncio
async def test_uncensoring_can_be_disabled_and_skips_lookups_when_nothing_masked(tmp_path: Path):
    audio = tmp_path / "song.flac"
    audio.write_bytes(b"x")
    track = TrackMetadata(file_path=audio, title="My Song", artist="Artist", duration=200.0)
    reference = _Provider("genius", _result("genius", "Fuck it", LyricsSyncType.UNSYNCED, LyricsFormat.TXT))
    # plain-lyrics source: skipped by the cascade, so calls count only uncensor lookups
    reference.supports_line_sync = reference.supports_word_sync = False

    masked = _Provider("spicylyrics", _result("spicylyrics", "[00:14.00]F**k ****"))
    config = AppConfig(music_dir=tmp_path, cache={"enabled": False}, uncensor_lyrics=False)
    await LyricsMatcher(config, [masked, reference]).process_track(track)
    assert (tmp_path / "song.lrc").read_text(encoding="utf-8") == "[00:14.00]F**k ****\n"

    clean = _Provider("spicylyrics", _result("spicylyrics", "[00:14.00]F**k it"))
    config = AppConfig(music_dir=tmp_path, cache={"enabled": False}, overwrite=True)
    await LyricsMatcher(config, [clean, reference]).process_track(track)
    assert (tmp_path / "song.lrc").read_text(encoding="utf-8") == "[00:14.00]Fuck it\n"
    assert reference.calls == 0  # pattern restoration needed no reference lookup


def test_reference_with_bracketed_adlibs_and_possessive_masks():
    lrc = ("[00:01.00]Fuck ****, I don't fuck with them\n"
           "[00:02.00]Fuck on any ****'s bitch, shit, that's every day")
    ref = ("Fuck niggas (fuck you), I don't fuck with them (no)\n"
           "Fuck on any nigga bitch, shit, that's every day (let's get it)")
    out, count = restore_from_references(lrc, LyricsFormat.LRC, [ref])
    assert count == 2
    assert out == ("[00:01.00]Fuck niggas, I don't fuck with them\n"
                   "[00:02.00]Fuck on any nigga's bitch, shit, that's every day")
