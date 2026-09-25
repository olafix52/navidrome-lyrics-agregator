"""Unit tests for CLI provider management and argument parsing."""

from pathlib import Path
from unittest.mock import MagicMock

from src.config import AppConfig, save_enabled_providers
from src.main import build_parser, run_providers_command


def test_build_parser_provider_flags():
    """Verify CLI parser supports --providers, --disable-providers, and --enable-providers."""
    parser = build_parser()
    
    # 1. Comma-separated providers override
    args = parser.parse_args(["scan", "--providers", "spicylyrics,lrclib"])
    assert args.providers == "spicylyrics,lrclib"

    # 2. Short flag -P
    args2 = parser.parse_args(["scan", "-P", "amll,netease"])
    assert args2.providers == "amll,netease"

    # 3. Disable providers flag
    args3 = parser.parse_args(["scan", "--disable-providers", "genius,lyricsify"])
    assert args3.disable_providers == "genius,lyricsify"

    # 4. Enable providers flag
    args4 = parser.parse_args(["scan", "--enable-providers", "kugou"])
    assert args4.enable_providers == "kugou"


def test_save_enabled_providers(tmp_path: Path):
    """Verify save_enabled_providers correctly updates the YAML file."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""# Initial config
music_dir: "/music"
enabled_providers:
  - "spicylyrics"
  - "amll"
scan_interval: "1h"
""", encoding="utf-8")

    save_enabled_providers(["lrclib", "musixmatch", "binilyrics"], config_path=config_file)

    content = config_file.read_text(encoding="utf-8")
    assert 'music_dir: "/music"' in content
    assert 'scan_interval: "1h"' in content
    assert '- "lrclib"' in content
    assert '- "musixmatch"' in content
    assert '- "binilyrics"' in content
    assert '- "spicylyrics"' not in content


def test_run_providers_command_list():
    """Verify run_providers_command with list action executes without errors."""
    config = AppConfig(enabled_providers=["spicylyrics", "lrclib"])
    args = MagicMock()
    args.provider_action = "list"
    # Should not raise exception
    run_providers_command(args, config, config_path=None)


def test_run_providers_command_enable_and_disable(tmp_path: Path):
    """Verify run_providers_command enable and disable actions update config and file."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("enabled_providers:\n  - \"spicylyrics\"\n", encoding="utf-8")
    
    config = AppConfig(enabled_providers=["spicylyrics"])

    # 1. Enable 'lrclib'
    enable_args = MagicMock()
    enable_args.provider_action = "enable"
    enable_args.names = ["lrclib"]
    enable_args.no_save = False

    run_providers_command(enable_args, config, config_path=config_file)
    assert "lrclib" in config.enabled_providers
    assert "spicylyrics" in config.enabled_providers

    content = config_file.read_text(encoding="utf-8")
    assert '- "lrclib"' in content
    assert '- "spicylyrics"' in content

    # 2. Disable 'spicylyrics'
    disable_args = MagicMock()
    disable_args.provider_action = "disable"
    disable_args.names = ["spicylyrics"]
    disable_args.no_save = False

    run_providers_command(disable_args, config, config_path=config_file)
    assert "spicylyrics" not in config.enabled_providers
    assert "lrclib" in config.enabled_providers

    content2 = config_file.read_text(encoding="utf-8")
    assert '- "spicylyrics"' not in content2
    assert '- "lrclib"' in content2


def _isolate_from_dotenv(monkeypatch) -> None:
    """load_config() calls load_dotenv(), which would import the developer's real .env
    (API keys) into os.environ for the rest of the test session."""
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    for var in ("SPICY_LYRICS_SECRET_KEY", "NLA_SPICY_LYRICS_API_KEY", "NLA_CONFIG", "NLA_ENABLED_PROVIDERS"):
        monkeypatch.delenv(var, raising=False)


def test_global_options_before_subcommand_are_kept():
    """Options given before the subcommand must not be reset by the subcommand's defaults."""
    parser = build_parser()

    args = parser.parse_args(["-P", "lrclib", "-d", "/music", "scan"])
    assert args.providers == "lrclib"
    assert args.music_dir == "/music"

    args = parser.parse_args(["--disable-providers", "genius", "daemon"])
    assert args.disable_providers == "genius"

    # Subcommand-level values still work and win
    args = parser.parse_args(["-d", "/a", "audit", "-d", "/b"])
    assert args.music_dir == "/b"

    # Nothing given -> attributes behave as unset
    args = parser.parse_args(["scan"])
    assert getattr(args, "providers", None) is None
    assert args.music_dir is None


def test_provider_config_overrides_merge_with_defaults(monkeypatch, tmp_path: Path):
    """Configuring one provider (YAML or env) must not wipe other providers' defaults."""
    from src.config import ProviderConfig, load_config

    monkeypatch.chdir(tmp_path)  # no config.yaml / config.local.yaml
    _isolate_from_dotenv(monkeypatch)
    monkeypatch.setenv("SPICY_LYRICS_SECRET_KEY", "sl_sk_test")
    config = load_config()
    assert config.providers["spicylyrics"].api_key == "sl_sk_test"
    assert config.providers["spicylyrics"].rate_limit_per_second == 3.0
    assert config.providers["lyricsify"].extra["flaresolverr_url"] == "http://localhost:8191/v1"
    assert config.providers["musixmatch"].extra["token_path"] == "data/musixmatch_token.json"
    assert config.providers["rmmrevival"].timeout_seconds == 15.0

    partial = AppConfig(providers={
        "lrclib": {"rate_limit_per_second": 1.0},
        "lyricsify": {"extra": {"cookie": "x"}},
        "genius": None,
        "amll": ProviderConfig(enabled=False),
    })
    assert partial.providers["lrclib"].rate_limit_per_second == 1.0
    assert partial.providers["lyricsify"].extra == {"flaresolverr_url": "http://localhost:8191/v1", "cookie": "x"}
    assert partial.providers["genius"].rate_limit_per_second == 1.5
    assert partial.providers["amll"].enabled is False
    assert partial.providers["amll"].rate_limit_per_second == 5.0
    assert "kugou" in partial.providers


def test_missing_explicit_config_file_is_an_error(tmp_path: Path, monkeypatch):
    import pytest
    from src.config import ConfigFileError, load_config

    monkeypatch.chdir(tmp_path)
    _isolate_from_dotenv(monkeypatch)
    with pytest.raises(ConfigFileError):
        load_config(tmp_path / "typo.yaml")

    # NLA_CONFIG (Docker default) pointing to an unmounted file falls back to defaults
    monkeypatch.setenv("NLA_CONFIG", str(tmp_path / "not-mounted.yaml"))
    assert load_config().storage_mode == "sidecar"


def test_invalid_storage_mode_and_scan_interval_rejected(tmp_path: Path, monkeypatch):
    import pytest
    from pydantic import ValidationError
    from src.config import ConfigFileError, load_config

    for bad in ({"storage_mode": "embed"}, {"scan_interval": "0"}, {"scan_interval": "soon"}, {"scan_interval": "30s"}):
        with pytest.raises(ValidationError):
            AppConfig(**bad)
    assert AppConfig(storage_mode="Both").storage_mode == "both"
    assert AppConfig(scan_interval="2h").scan_interval_seconds == 7200

    monkeypatch.chdir(tmp_path)
    _isolate_from_dotenv(monkeypatch)
    (tmp_path / "cfg.yaml").write_text("storage_mode: embed\n", encoding="utf-8")
    with pytest.raises(ConfigFileError):
        load_config(tmp_path / "cfg.yaml")
