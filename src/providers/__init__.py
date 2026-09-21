"""Providers package and registry for lyrics providers."""

from typing import Dict, List, Type
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
    "build_provider_cascade",
]
