"""Global test configuration and fixtures."""

from pathlib import Path
import pytest
from src.config import AppConfig


@pytest.fixture(autouse=True)
def isolate_test_cache(tmp_path, monkeypatch):
    """Ensure all tests use an isolated temporary SQLite database for lyrics cache."""
    orig_init = AppConfig.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        if self.cache.db_path == Path("data/lyrics_cache.db"):
            self.cache.db_path = tmp_path / "test_lyrics_cache.db"

    monkeypatch.setattr(AppConfig, "__init__", patched_init)
    yield
