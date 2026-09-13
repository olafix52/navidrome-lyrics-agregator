"""Configuration management using Pydantic Settings with YAML and Environment variable support."""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


def parse_time_string_to_seconds(time_str: str | int | float) -> int:
    """Convert human readable time string (e.g. '1h', '30m', '3600s', '1d') to seconds."""
    if isinstance(time_str, (int, float)):
        return int(time_str)

    s = str(time_str).strip().lower()
    if s.isdigit():
        return int(s)

    units = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([smhd])?$", s)
    if match:
        val, unit = match.groups()
        multiplier = units.get(unit or "s", 1)
        return int(float(val) * multiplier)

    try:
        return int(s)
    except ValueError:
        return 3600


class ProviderConfig(BaseModel):
    """Specific settings for a single lyrics provider."""
    enabled: bool = True
    rate_limit_per_second: float = 2.0
    timeout_seconds: float = 10.0
    api_key: Optional[str] = None
    custom_url: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class AppConfig(BaseModel):
    """Main application configuration."""

    # General Settings
    music_dir: Path = Field(default=Path("/music"), description="Root directory of audio files")
    scan_interval: str = Field(default="1h", description="Periodic scan interval for daemon mode (e.g. 1h, 30m, 3600s)")
    watch_debounce_seconds: float = Field(default=3.0, description="Debounce delay for filesystem watcher events")
    duration_tolerance_seconds: float = Field(default=2.5, description="Allowed deviation in song duration")
    min_similarity_score: float = Field(default=0.75, description="Minimum string similarity for fuzzy matching")
    
    # Operation modes
    overwrite: bool = Field(default=False, description="Force overwrite existing lyrics sidecar files")
    upgrade_quality: bool = Field(default=True, description="Upgrade from LRC to TTML/YAML if higher quality is found")
    dry_run: bool = Field(default=False, description="Scan and search without writing files to disk")
    allow_plain_lyrics: bool = Field(default=False, description="Allow falling back to unsynced plain lyrics if no synced found")

    # Concurrency and Network
    concurrency: int = Field(default=4, description="Maximum concurrent track processing tasks")
    network_timeout: float = Field(default=10.0, description="Default HTTP request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retries for failed network requests")
    user_agent: str = Field(
        default="NavidromeLyricsAggregator/1.0 (https://github.com/navidrome-lyrics-aggregator)",
        description="HTTP User-Agent header",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")
    log_file: Optional[Path] = Field(default=None, description="Optional path to log file")

    # Providers order and cascade
    enabled_providers: List[str] = Field(
        default=[
            "amll",
            "apple_music",
            "rmmrevival",
            "unison",
            "binilyrics",
            "lrclib",
            "musixmatch",
            "qqmusic",
            "kuwo",
            "netease",
            "kugou",
            "lyricsify",
            "genius",
        ],
        description="Ordered list of active providers (priority order)",
    )

    # Provider specific configurations
    providers: Dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "amll": ProviderConfig(rate_limit_per_second=5.0),
            "apple_music": ProviderConfig(rate_limit_per_second=2.0),
            "rmmrevival": ProviderConfig(rate_limit_per_second=2.0, timeout_seconds=15.0),
            "unison": ProviderConfig(rate_limit_per_second=3.0),
            "binilyrics": ProviderConfig(rate_limit_per_second=3.0),
            "lrclib": ProviderConfig(rate_limit_per_second=4.0),
            "musixmatch": ProviderConfig(rate_limit_per_second=2.0),
            "qqmusic": ProviderConfig(rate_limit_per_second=3.0),
            "kuwo": ProviderConfig(rate_limit_per_second=3.0),
            "netease": ProviderConfig(rate_limit_per_second=3.0),
            "kugou": ProviderConfig(rate_limit_per_second=3.0),
            "lyricsify": ProviderConfig(
                rate_limit_per_second=1.0,
                timeout_seconds=30.0,
                extra={"flaresolverr_url": "http://localhost:8191/v1"},
            ),
            "genius": ProviderConfig(rate_limit_per_second=1.5),
        }
    )

    @property
    def scan_interval_seconds(self) -> int:
        return parse_time_string_to_seconds(self.scan_interval)


def _apply_env_overrides(data: Dict[str, Any]) -> None:
    """Read environment variables with NLA_ or standard prefixes and apply to config data."""
    env_mapping = {
        "MUSIC_DIR": "music_dir",
        "NLA_MUSIC_DIR": "music_dir",
        "NLA_SCAN_INTERVAL": "scan_interval",
        "NLA_WATCH_DEBOUNCE_SECONDS": ("watch_debounce_seconds", float),
        "NLA_DURATION_TOLERANCE_SECONDS": ("duration_tolerance_seconds", float),
        "NLA_MIN_SIMILARITY_SCORE": ("min_similarity_score", float),
        "NLA_OVERWRITE": ("overwrite", lambda v: v.lower() in ("true", "1", "yes")),
        "NLA_UPGRADE_QUALITY": ("upgrade_quality", lambda v: v.lower() in ("true", "1", "yes")),
        "NLA_DRY_RUN": ("dry_run", lambda v: v.lower() in ("true", "1", "yes")),
        "NLA_ALLOW_PLAIN_LYRICS": ("allow_plain_lyrics", lambda v: v.lower() in ("true", "1", "yes")),
        "NLA_CONCURRENCY": ("concurrency", int),
        "NLA_NETWORK_TIMEOUT": ("network_timeout", float),
        "NLA_MAX_RETRIES": ("max_retries", int),
        "NLA_LOG_LEVEL": "log_level",
        "NLA_LOG_FILE": "log_file",
    }

    for env_var, target in env_mapping.items():
        val = os.environ.get(env_var)
        if val is not None:
            if isinstance(target, tuple):
                field_name, converter = target
                try:
                    data[field_name] = converter(val)
                except Exception:
                    pass
            else:
                data[target] = val

    # Providers list from env (comma separated)
    if "NLA_ENABLED_PROVIDERS" in os.environ:
        providers_str = os.environ["NLA_ENABLED_PROVIDERS"]
        data["enabled_providers"] = [p.strip() for p in providers_str.split(",") if p.strip()]

    # FlareSolverr URL override from env
    fs_url = os.environ.get("NLA_FLARESOLVERR_URL") or os.environ.get("FLARESOLVERR_URL")
    if fs_url:
        data.setdefault("providers", {}).setdefault("lyricsify", {}).setdefault("extra", {})["flaresolverr_url"] = fs_url


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load configuration from YAML file and override with environment variables."""
    data: Dict[str, Any] = {}

    candidates = [
        Path(config_path) if config_path else None,
        Path(os.environ.get("NLA_CONFIG", "")) if os.environ.get("NLA_CONFIG") else None,
        Path("/config/config.yaml"),
        Path("./config.local.yaml"),
        Path("./config.yaml"),
        Path("./config/config.yaml"),
    ]

    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, dict):
                        data = content
                        break
            except Exception as e:
                print(f"Warning: Failed to load config from {candidate}: {e}")

    _apply_env_overrides(data)
    return AppConfig(**data)
