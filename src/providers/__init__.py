"""Providers package and registry for lyrics providers."""

from typing import Any, Dict, List, Type
from src.config import AppConfig
from src.providers.amll import AMLLProvider
from src.providers.apple_music import AppleMusicProvider
from src.providers.base import BaseLyricsProvider
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
from src.providers.spicylyrics import SpicyLyricsProvider
from src.providers.unison import UnisonProvider

AVAILABLE_PROVIDERS: Dict[str, Type[BaseLyricsProvider]] = {
    "amll": AMLLProvider,
    "apple_music": AppleMusicProvider,
    "rmmrevival": RMMRevivalProvider,
    "unison": UnisonProvider,
    "spicylyrics": SpicyLyricsProvider,
    "binilyrics": BiniLyricsProvider,
    "lrclib": LrclibProvider,
    "musixmatch": MusixmatchProvider,
    "qqmusic": QQMusicProvider,
    "kuwo": KuwoProvider,
    "netease": NetEaseProvider,
    "kugou": KugouProvider,
    "lyricsify": LyricsifyProvider,
    "genius": GeniusProvider,
}

PROVIDER_METADATA: Dict[str, Dict[str, Any]] = {
    "spicylyrics": {
        "name": "Spicy Lyrics",
        "description": "Developer API providing syllable-level TTML word-sync and synced LRC",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": True,
        "default_priority": 1,
    },
    "amll": {
        "name": "AMLL",
        "description": "Apple Music-Like Lyrics database with rich syllable TTML sync",
        "formats": ["TTML (Word-sync)"],
        "requires_api_key": False,
        "default_priority": 2,
    },
    "apple_music": {
        "name": "Apple Music",
        "description": "Apple Music catalog API / AMLL bridge for high-fidelity TTML lyrics",
        "formats": ["TTML (Word-sync)"],
        "requires_api_key": False,
        "default_priority": 3,
    },
    "rmmrevival": {
        "name": "RMM Revival",
        "description": "Apple Music Worker and synced lyric archives with TTML and LRC",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 4,
    },
    "unison": {
        "name": "Unison",
        "description": "Crowdsourced synchronized lyrics supporting TTML, YAML, and LRC",
        "formats": ["TTML (Word-sync)", "YAML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 5,
    },
    "binilyrics": {
        "name": "BiniLyrics",
        "description": "Aligned REST lyrics database with word-level TTML and LRC support",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 6,
    },
    "lrclib": {
        "name": "LRCLIB",
        "description": "Large community lyrics database with word-sync YAML and line-sync LRC",
        "formats": ["YAML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 7,
    },
    "musixmatch": {
        "name": "Musixmatch",
        "description": "Desktop RichSync API providing word-synced TTML and line-synced LRC",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 8,
    },
    "qqmusic": {
        "name": "QQ Music",
        "description": "Tencent QQ Music database with QRC word-sync and LRC line-sync",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 9,
    },
    "kuwo": {
        "name": "Kuwo Music",
        "description": "Kuwo Music database with line-synchronized LRC",
        "formats": ["LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 10,
    },
    "netease": {
        "name": "NetEase Cloud Music",
        "description": "NetEase Cloud Music with YRC word-sync and LRC line-sync",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 11,
    },
    "kugou": {
        "name": "Kugou Music",
        "description": "Kugou Music database with KRC word-sync and LRC line-sync",
        "formats": ["TTML (Word-sync)", "LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 12,
    },
    "lyricsify": {
        "name": "Lyricsify",
        "description": "Lyricsify community database with line-synced LRC (supports FlareSolverr)",
        "formats": ["LRC (Line-sync)"],
        "requires_api_key": False,
        "default_priority": 13,
    },
    "genius": {
        "name": "Genius",
        "description": "Genius lyrics database (fallback for unsynchronized plain text lyrics)",
        "formats": ["Plain"],
        "requires_api_key": False,
        "default_priority": 14,
    },
}


def build_provider_cascade(config: AppConfig) -> List[BaseLyricsProvider]:
    """Instantiate and return the configured cascade list of providers."""
    providers: List[BaseLyricsProvider] = []

    for name in config.enabled_providers:
        name_lower = name.lower().strip()
        provider_cls = AVAILABLE_PROVIDERS.get(name_lower)
        if not provider_cls:
            continue

        prov_config = config.providers.get(name_lower)
        if prov_config and not prov_config.enabled:
            continue

        instance = provider_cls(
            config=prov_config,
            user_agent=config.user_agent,
            default_timeout=config.network_timeout,
            max_retries=config.max_retries,
        )
        providers.append(instance)

    return providers


__all__ = [
    "BaseLyricsProvider",
    "AMLLProvider",
    "AppleMusicProvider",
    "RMMRevivalProvider",
    "UnisonProvider",
    "SpicyLyricsProvider",
    "BiniLyricsProvider",
    "LrclibProvider",
    "MusixmatchProvider",
    "QQMusicProvider",
    "KuwoProvider",
    "NetEaseProvider",
    "KugouProvider",
    "LyricsifyProvider",
    "GeniusProvider",
    "AVAILABLE_PROVIDERS",
    "PROVIDER_METADATA",
    "build_provider_cascade",
]
