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
