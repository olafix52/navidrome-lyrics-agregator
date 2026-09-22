"""SQLite-backed persistent cache for negative lyrics lookups and lookup metadata."""

import asyncio
from dataclasses import dataclass, field
import hashlib
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from src.models import TrackMetadata

logger = logging.getLogger("nla.cache")


@dataclass
class NegativeCacheHit:
    """Details of an active negative cache entry."""
    cache_key: str
    remaining_seconds: float
    remaining_days: float
    failure_count: int
    last_checked_at: float
    last_providers: List[str] = field(default_factory=list)


class LyricsCache:
    """Thread-safe SQLite persistent cache for negative match results.
    
    Prevents repeated expensive network cascade calls on audio tracks that
    previously yielded no lyrics across any enabled provider.
    """

    def __init__(self, db_path: Path | str, ttl_days: float = 14.0):
        self.db_path = Path(db_path)
        self.ttl_days = max(0.0, float(ttl_days))
        self._initialized = False
        self._lock = asyncio.Lock()

    def _get_raw_connection(self) -> sqlite3.Connection:
        """Create a configured SQLite connection with WAL journal mode and busy timeout."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA mmap_size = 268435456;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -8000;")
        return conn

    def _init_db_sync(self) -> None:
        """Create tables and indexes synchronously."""
        if self._initialized:
            return

        with self._get_raw_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS negative_cache (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    duration INTEGER,
                    file_path TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    failure_count INTEGER DEFAULT 1,
                    last_providers TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_negative_expires ON negative_cache(expires_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_negative_file_path ON negative_cache(file_path);")
            conn.commit()

        self._initialized = True

    async def initialize(self) -> None:
        """Ensure database and tables are ready without blocking event loop."""
        async with self._lock:
            if not self._initialized:
                await asyncio.to_thread(self._init_db_sync)

    def get_cache_key(self, track: TrackMetadata) -> str:
        """Generate a deterministic hash key from normalized track metadata."""
        artist = (track.clean_artist or track.artist or "").strip().lower()
        title = (track.clean_title or track.title or "").strip().lower()
        dur = int(round(track.duration)) if track.duration else 0
        raw = f"{artist}:{title}:{dur}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _is_negative_hit_sync(self, cache_key: str) -> Optional[NegativeCacheHit]:
        self._init_db_sync()
        now = time.time()
        with self._get_raw_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cache_key, updated_at, expires_at, failure_count, last_providers
                FROM negative_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            key, updated_at, expires_at, failure_count, last_providers_str = row
            if expires_at <= now:
                # Expired
                return None

            remaining_sec = expires_at - now
            providers = [p.strip() for p in (last_providers_str or "").split(",") if p.strip()]

            return NegativeCacheHit(
                cache_key=key,
                remaining_seconds=remaining_sec,
                remaining_days=round(remaining_sec / 86400.0, 1),
                failure_count=failure_count or 1,
                last_checked_at=updated_at,
                last_providers=providers,
            )

    async def is_negative_hit(self, track: TrackMetadata) -> Optional[NegativeCacheHit]:
        """Check if track was recorded as having no lyrics within active TTL."""
        await self.initialize()
        key = self.get_cache_key(track)
        return await asyncio.to_thread(self._is_negative_hit_sync, key)

    def _record_negative_sync(
        self,
        cache_key: str,
        artist: str,
        title: str,
        duration: int,
        file_path: Optional[str],
        providers_checked: List[str],
    ) -> None:
        self._init_db_sync()
        now = time.time()
        expires_at = now + (self.ttl_days * 86400.0)
        providers_str = ",".join(providers_checked)

        with self._get_raw_connection() as conn:
            conn.execute(
                """
                INSERT INTO negative_cache (
                    cache_key, artist, title, duration, file_path, created_at, updated_at, expires_at, failure_count, last_providers
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at,
                    failure_count = failure_count + 1,
                    last_providers = excluded.last_providers,
                    file_path = COALESCE(excluded.file_path, negative_cache.file_path);
                """,
                (cache_key, artist, title, duration, file_path, now, now, expires_at, providers_str),
            )
            conn.commit()

    async def record_negative(
        self,
        track: TrackMetadata,
        providers_checked: Optional[List[str]] = None,
    ) -> None:
        """Store or refresh negative lookup record for an unfound track."""
        await self.initialize()
        key = self.get_cache_key(track)
        artist = track.clean_artist or track.artist or ""
        title = track.clean_title or track.title or ""
        duration = int(round(track.duration)) if track.duration else 0
        file_path_str = str(track.file_path) if track.file_path else None
        providers = providers_checked or []

        await asyncio.to_thread(
            self._record_negative_sync,
            key,
            artist,
            title,
            duration,
            file_path_str,
            providers,
        )

    def _remove_sync(self, cache_key: str) -> bool:
        self._init_db_sync()
        with self._get_raw_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM negative_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return cursor.rowcount > 0

    async def remove(self, track: TrackMetadata) -> bool:
        """Remove a track from negative cache (e.g. when lyrics are found or upgraded)."""
        await self.initialize()
        key = self.get_cache_key(track)
        return await asyncio.to_thread(self._remove_sync, key)

    def _clear_sync(self, expired_only: bool = False) -> int:
        self._init_db_sync()
        now = time.time()
        with self._get_raw_connection() as conn:
            cursor = conn.cursor()
            if expired_only:
                cursor.execute("DELETE FROM negative_cache WHERE expires_at <= ?", (now,))
            else:
                cursor.execute("DELETE FROM negative_cache;")
            conn.commit()
            return cursor.rowcount

    async def clear(self, expired_only: bool = False) -> int:
        """Clear cache entries, optionally only expired ones. Returns count of deleted entries."""
        await self.initialize()
        return await asyncio.to_thread(self._clear_sync, expired_only)

    def _get_stats_sync(self) -> Dict[str, Any]:
        self._init_db_sync()
        now = time.time()
        with self._get_raw_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM negative_cache")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM negative_cache WHERE expires_at > ?", (now,))
            active = cursor.fetchone()[0]

            expired = total - active

        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "db_path": str(self.db_path),
            "total_negative_entries": total,
            "active_negative_entries": active,
            "expired_negative_entries": expired,
            "ttl_days": self.ttl_days,
            "db_size_bytes": size_bytes,
            "db_size_kb": round(size_bytes / 1024.0, 1),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Fetch statistical overview of the cache state."""
        await self.initialize()
        return await asyncio.to_thread(self._get_stats_sync)
