"""Global test configuration and fixtures."""

from pathlib import Path
import pytest
from src.config import AppConfig
from src.cache import clear_spotify_id_mem_cache, close_cache_connections, set_active_cache_db_path


@pytest.fixture(autouse=True)
def isolate_test_cache(tmp_path, monkeypatch):
    """Ensure all tests use an isolated temporary SQLite database for lyrics cache."""
    clear_spotify_id_mem_cache()
    test_db = tmp_path / "test_lyrics_cache.db"
    set_active_cache_db_path(test_db)

    orig_init = AppConfig.__init__

    def patched_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        if self.cache.db_path == Path("data/lyrics_cache.db"):
            self.cache.db_path = test_db

    monkeypatch.setattr(AppConfig, "__init__", patched_init)
    yield
    clear_spotify_id_mem_cache()
    close_cache_connections()  # pooled SQLite connections must not outlive the test's tmp dir

