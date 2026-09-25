"""Configuration management using Pydantic Settings with YAML and Environment variable support."""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field, field_validator


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


def default_provider_configs() -> Dict[str, ProviderConfig]:
    """Built-in per-provider settings (rate limits, timeouts, extra options)."""
    return {
        "amll": ProviderConfig(rate_limit_per_second=5.0),
        "apple_music": ProviderConfig(rate_limit_per_second=2.0),
        "rmmrevival": ProviderConfig(rate_limit_per_second=2.0, timeout_seconds=15.0),
        "unison": ProviderConfig(rate_limit_per_second=3.0),
        "spicylyrics": ProviderConfig(rate_limit_per_second=3.0),
        "binilyrics": ProviderConfig(rate_limit_per_second=3.0),
        "lrclib": ProviderConfig(rate_limit_per_second=4.0),
        "musixmatch": ProviderConfig(
            rate_limit_per_second=2.0,
            extra={"token_path": "data/musixmatch_token.json"},
        ),
        "blend": ProviderConfig(rate_limit_per_second=2.0),
        "kublend": ProviderConfig(rate_limit_per_second=2.0),
        "neblend": ProviderConfig(rate_limit_per_second=2.0),
        "triblend": ProviderConfig(rate_limit_per_second=2.0),
        "kutriblend": ProviderConfig(rate_limit_per_second=2.0),
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


class NavidromeConfig(BaseModel):
    """Navidrome / Subsonic server connection configuration."""
    url: Optional[str] = Field(default=None, description="Navidrome server URL, e.g. http://localhost:4533")
    user: Optional[str] = Field(default=None, description="Navidrome / Subsonic username")
    password: Optional[str] = Field(default=None, description="Navidrome / Subsonic password")
    auto_scan: bool = Field(default=False, description="Automatically trigger Navidrome scan after lyrics update")
    full_scan: bool = Field(default=False, description="Trigger full library scan in Navidrome instead of fast scan")


class CacheConfig(BaseModel):
    """Configuration for SQLite persistent lyrics cache."""
    enabled: bool = Field(default=True, description="Enable persistent caching for unfound lyrics lookups")
    db_path: Path = Field(default=Path("data/lyrics_cache.db"), description="Path to SQLite cache database")
    negative_ttl_days: float = Field(default=14.0, description="Days to remember tracks with no lyrics found")


class AppConfig(BaseModel):
    """Main application configuration."""

    # General Settings
    music_dir: Path = Field(default=Path("/music"), description="Root directory of audio files")
    output_dir: Optional[Path] = Field(default=None, description="Optional custom destination directory for saved sidecars")
    scan_interval: str = Field(default="1h", description="Periodic scan interval for daemon mode (e.g. 1h, 30m, 3600s)")
    watch_debounce_seconds: float = Field(default=3.0, description="Debounce delay for filesystem watcher events")
    duration_tolerance_seconds: float = Field(default=2.5, description="Allowed deviation in song duration")
    min_similarity_score: float = Field(default=0.75, description="Minimum string similarity for fuzzy matching")
    
    # Operation modes
    storage_mode: str = Field(
        default="sidecar",
        description="Lyrics storage destination: 'sidecar' (companion files), 'embedded' (audio tags), or 'both'",
    )
    embed_word_sync: bool = Field(
        default=True,
        description="Embed word/syllable-level timing (Enhanced LRC <mm:ss.xx> and SYLT words) into audio tags when available",
    )
    overwrite: bool = Field(default=False, description="Force overwrite existing lyrics sidecar files or tags")
    upgrade_quality: bool = Field(default=True, description="Upgrade from LRC to TTML/YAML if higher quality is found")
    dry_run: bool = Field(default=False, description="Scan and search without writing files to disk")
    allow_plain_lyrics: bool = Field(default=False, description="Allow falling back to unsynced plain lyrics if no synced found")
    early_exit_on_line_sync: bool = Field(
        default=False,
        description="Stop cascade immediately when a verified line-sync match is found without searching for word-sync",
    )
    word_sync_search_budget: Optional[int] = Field(
        default=None,
        description="Maximum additional word-sync providers to query after a verified line-sync match is found (None for unlimited)",
    )
    cache: CacheConfig = Field(
        default_factory=CacheConfig,
        description="Persistent caching settings for negative lookups",
    )
    ignore_cache: bool = Field(
        default=False,
        description="Bypass cache lookups and force fresh provider queries",
    )

    # Navidrome server integration
    navidrome: NavidromeConfig = Field(
        default_factory=NavidromeConfig,
        description="Navidrome / Subsonic server integration settings",
    )

    # Concurrency and Network
    concurrency: int = Field(default=4, description="Maximum concurrent track processing tasks")
    network_timeout: float = Field(default=10.0, description="Default HTTP request timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retries for failed network requests")
    user_agent: str = Field(
        default="NavidromeLyricsAggregator/1.0 (https://github.com/navidrome-lyrics-aggregator)",
        description="HTTP User-Agent header",
    )

    # Web UI
    web_auth_token: Optional[str] = Field(
        default=None,
        description="Shared secret required by the Web UI/API (open /?token=<token> once in the browser)",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")
    log_file: Optional[Path] = Field(default=None, description="Optional path to log file")

    # Providers order and cascade
    enabled_providers: List[str] = Field(
        default=[
            "spicylyrics",
            "amll",
            "apple_music",
            "rmmrevival",
            "unison",
            "binilyrics",
            "lrclib",
            "musixmatch",
            "neblend",
            "triblend",
            "kutriblend",
            "netease",
            "blend",
            "qqmusic",
            "kublend",
            "kugou",
            "kuwo",
            "lyricsify",
            "genius",
        ],
        description="Ordered list of active providers (priority order)",
    )

    # Provider specific configurations. User-supplied entries (YAML / env) are deep-merged
    # on top of these defaults instead of replacing the whole mapping.
    providers: Dict[str, ProviderConfig] = Field(default_factory=lambda: default_provider_configs())

    @field_validator("providers", mode="before")
    @classmethod
    def _merge_provider_defaults(cls, value: Any) -> Any:
        if value is None:
            return default_provider_configs()
        if not isinstance(value, dict):
            return value

        merged: Dict[str, Any] = {
            name: cfg.model_dump() for name, cfg in default_provider_configs().items()
        }
        for name, override in value.items():
            key = str(name).strip().lower()
            if override is None:
                override = {}
            elif isinstance(override, ProviderConfig):
                override = override.model_dump(exclude_unset=True)

            if isinstance(override, dict) and isinstance(merged.get(key), dict):
                merged[key] = _deep_merge(merged[key], override)
            else:
                merged[key] = override
        return merged

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
        "NLA_STORAGE_MODE": "storage_mode",
        "STORAGE_MODE": "storage_mode",
        "NLA_EMBED_WORD_SYNC": ("embed_word_sync", lambda v: v.lower() in ("true", "1", "yes")),
        "EMBED_WORD_SYNC": ("embed_word_sync", lambda v: v.lower() in ("true", "1", "yes")),
        "NLA_OUTPUT_DIR": ("output_dir", lambda v: Path(v)),
        "OUTPUT_DIR": ("output_dir", lambda v: Path(v)),
        "NLA_IGNORE_CACHE": ("ignore_cache", lambda v: v.lower() in ("true", "1", "yes")),
        "IGNORE_CACHE": ("ignore_cache", lambda v: v.lower() in ("true", "1", "yes")),
        "NLA_WEB_TOKEN": "web_auth_token",
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

    # Cache env settings
    cache_data = data.setdefault("cache", {})
    if not isinstance(cache_data, dict):
        cache_data = {}
        data["cache"] = cache_data

    cache_enabled = os.environ.get("NLA_CACHE_ENABLED") or os.environ.get("CACHE_ENABLED")
    if cache_enabled:
        cache_data["enabled"] = cache_enabled.lower() in ("true", "1", "yes")

    cache_path = os.environ.get("NLA_CACHE_DB_PATH") or os.environ.get("CACHE_DB_PATH")
    if cache_path:
        cache_data["db_path"] = Path(cache_path)

    cache_ttl = os.environ.get("NLA_CACHE_NEGATIVE_TTL_DAYS") or os.environ.get("CACHE_NEGATIVE_TTL_DAYS")
    if cache_ttl:
        try:
            cache_data["negative_ttl_days"] = float(cache_ttl)
        except ValueError:
            pass

    # Navidrome env settings
    navidrome_data = data.setdefault("navidrome", {})
    if not isinstance(navidrome_data, dict):
        navidrome_data = {}
        data["navidrome"] = navidrome_data

    nd_url = os.environ.get("NLA_NAVIDROME_URL") or os.environ.get("NAVIDROME_URL")
    if nd_url:
        navidrome_data["url"] = nd_url
    nd_user = os.environ.get("NLA_NAVIDROME_USER") or os.environ.get("NAVIDROME_USER")
    if nd_user:
        navidrome_data["user"] = nd_user
    nd_pass = os.environ.get("NLA_NAVIDROME_PASSWORD") or os.environ.get("NAVIDROME_PASSWORD")
    if nd_pass:
        navidrome_data["password"] = nd_pass
    nd_auto = os.environ.get("NLA_NAVIDROME_AUTO_SCAN") or os.environ.get("NAVIDROME_AUTO_SCAN")
    if nd_auto:
        navidrome_data["auto_scan"] = nd_auto.lower() in ("true", "1", "yes")
    nd_full = os.environ.get("NLA_NAVIDROME_FULL_SCAN") or os.environ.get("NAVIDROME_FULL_SCAN")
    if nd_full:
        navidrome_data["full_scan"] = nd_full.lower() in ("true", "1", "yes")

    # Providers list from env (comma separated)
    if "NLA_ENABLED_PROVIDERS" in os.environ:
        providers_str = os.environ["NLA_ENABLED_PROVIDERS"]
        data["enabled_providers"] = [p.strip() for p in providers_str.split(",") if p.strip()]

    # FlareSolverr URL override from env
    fs_url = os.environ.get("NLA_FLARESOLVERR_URL") or os.environ.get("FLARESOLVERR_URL")
    if fs_url:
        data.setdefault("providers", {}).setdefault("lyricsify", {}).setdefault("extra", {})["flaresolverr_url"] = fs_url

    # Spicy Lyrics API key & Spotify credentials
    spicy_key = os.environ.get("SPICY_LYRICS_SECRET_KEY") or os.environ.get("NLA_SPICY_LYRICS_API_KEY")
    if spicy_key:
        data.setdefault("providers", {}).setdefault("spicylyrics", {})["api_key"] = spicy_key

    sp_client_id = os.environ.get("SPOTIFY_CLIENT_ID") or os.environ.get("NLA_SPOTIFY_CLIENT_ID")
    if sp_client_id:
        data.setdefault("providers", {}).setdefault("spicylyrics", {}).setdefault("extra", {})["spotify_client_id"] = sp_client_id

    sp_client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or os.environ.get("NLA_SPOTIFY_CLIENT_SECRET")
    if sp_client_secret:
        data.setdefault("providers", {}).setdefault("spicylyrics", {}).setdefault("extra", {})["spotify_client_secret"] = sp_client_secret


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge override dict into base dict. Override values take precedence."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load configuration from YAML file and override with environment variables.
    
    If config.local.yaml exists, it is deep-merged on top of config.yaml,
    so users only need to specify overrides in the local file.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    data: Dict[str, Any] = {}

    # If explicit path provided or via env var, use it directly
    explicit = config_path or (Path(os.environ["NLA_CONFIG"]) if os.environ.get("NLA_CONFIG") else None)
    if explicit and explicit.is_file():
        try:
            with open(explicit, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
                if isinstance(content, dict):
                    data = content
        except Exception as e:
            print(f"Warning: Failed to load config from {explicit}: {e}")
    else:
        # Load base config.yaml
        base_candidates = [
            Path("/config/config.yaml"),
            Path("./config.yaml"),
            Path("./config/config.yaml"),
        ]
        for candidate in base_candidates:
            if candidate.is_file():
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        content = yaml.safe_load(f)
                        if isinstance(content, dict):
                            data = content
                            break
                except Exception as e:
                    print(f"Warning: Failed to load config from {candidate}: {e}")

        # Merge config.local.yaml on top if it exists
        local_path = Path("./config.local.yaml")
        if local_path.is_file():
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    local_content = yaml.safe_load(f)
                    if isinstance(local_content, dict):
                        data = _deep_merge(data, local_content)
            except Exception as e:
                print(f"Warning: Failed to load local config from {local_path}: {e}")

    _apply_env_overrides(data)
    return AppConfig(**data)


def save_enabled_providers(
    enabled_list: List[str], config_path: Optional[Path] = None
) -> Path:
    """Save or update the enabled_providers list in the configuration file."""
    # Priority for target file:
    # 1. Explicit config_path if provided
    # 2. config.local.yaml if it exists
    # 3. config.yaml if it exists
    # 4. Fallback to ./config.yaml
    if config_path and Path(config_path).is_file():
        target = Path(config_path)
    elif Path("./config.local.yaml").is_file():
        target = Path("./config.local.yaml")
    elif Path("./config.yaml").is_file():
        target = Path("./config.yaml")
    elif Path("/config/config.yaml").is_file():
        target = Path("/config/config.yaml")
    else:
        target = Path("./config.yaml")

    new_block_lines = ["enabled_providers:\n"]
    for p in enabled_list:
        new_block_lines.append(f'  - "{p}"\n')
    new_block = "".join(new_block_lines)

    if target.exists():
        content = target.read_text(encoding="utf-8")
        pattern = re.compile(r"^enabled_providers:\s*(?:\n\s*-\s*[^\n]*)+", re.MULTILINE)
        if pattern.search(content):
            updated_content = pattern.sub(new_block.rstrip("\n"), content)
        elif "enabled_providers:" in content:
            pattern_inline = re.compile(r"^enabled_providers:.*$", re.MULTILINE)
            updated_content = pattern_inline.sub(new_block.rstrip("\n"), content)
        else:
            updated_content = content + "\n\n" + new_block
        target.write_text(updated_content, encoding="utf-8")
    else:
        target.write_text(new_block, encoding="utf-8")

    return target

