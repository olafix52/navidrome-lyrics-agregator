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


_DEFAULT_CACHE_DB_PATH: Path = Path("data/lyrics_cache.db")
_ACTIVE_CACHE_DB_PATH: Optional[Path] = None
_SPOTIFY_ID_MEM_CACHE: Dict[str, str] = {}


def set_active_cache_db_path(db_path: Path | str) -> None:
    """Set globally active SQLite database path for convenience helper functions."""
    global _ACTIVE_CACHE_DB_PATH
    _ACTIVE_CACHE_DB_PATH = Path(db_path)


def get_active_cache_db_path() -> Path:
    """Get currently active SQLite database path."""
    return _ACTIVE_CACHE_DB_PATH or _DEFAULT_CACHE_DB_PATH


def _spotify_cache_key(artist: Optional[str], title: Optional[str]) -> str:
    """Generate normalized cache key for track artist and title."""
    a = (artist or "").strip().lower()
    t = (title or "").strip().lower()
    return f"{a}:::{t}"


def clear_spotify_id_mem_cache() -> None:
    """Clear in-memory Spotify ID cache (used primarily for test isolation)."""
    _SPOTIFY_ID_MEM_CACHE.clear()


def get_cached_spotify_id(
    artist: Optional[str] = None,
    title: Optional[str] = None,
    isrc: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[str]:
    """Retrieve Spotify track ID from fast in-memory cache or persistent SQLite database.
    
    Supports resolution by ISRC or (artist, title).
    """
    # 1. Fast in-memory lookup
    if isrc and isrc.strip():
        isrc_key = f"isrc:{isrc.strip().upper()}"
        if isrc_key in _SPOTIFY_ID_MEM_CACHE:
            return _SPOTIFY_ID_MEM_CACHE[isrc_key]

    meta_key = _spotify_cache_key(artist, title)
    if meta_key != ":::" and meta_key in _SPOTIFY_ID_MEM_CACHE:
        return _SPOTIFY_ID_MEM_CACHE[meta_key]

    # 2. SQLite persistent lookup
    target_db = Path(db_path) if db_path else get_active_cache_db_path()
    if not target_db.exists():
        return None

    try:
        conn = sqlite3.connect(str(target_db), timeout=5.0)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spotify_id_cache (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    isrc TEXT,
                    spotify_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            cursor = conn.cursor()
            found_id: Optional[str] = None

            if isrc and isrc.strip():
                cursor.execute(
                    "SELECT spotify_id FROM spotify_id_cache WHERE isrc = ? LIMIT 1;",
                    (isrc.strip().upper(),),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    found_id = str(row[0]).strip()

            if not found_id and meta_key != ":::":
                cursor.execute(
                    "SELECT spotify_id FROM spotify_id_cache WHERE cache_key = ? LIMIT 1;",
                    (meta_key,),
                )
                row = cursor.fetchone()
                if row and row[0]:
                    found_id = str(row[0]).strip()

            if found_id:
                # Populate in-memory cache for subsequent instant hits
                if isrc and isrc.strip():
                    _SPOTIFY_ID_MEM_CACHE[f"isrc:{isrc.strip().upper()}"] = found_id
                if meta_key != ":::":
                    _SPOTIFY_ID_MEM_CACHE[meta_key] = found_id
                return found_id

        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[cache] Failed reading Spotify ID from database {target_db}: {e}")

    return None


def set_cached_spotify_id(
    artist: Optional[str],
    title: Optional[str],
    spotify_id: str,
    isrc: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Store resolved Spotify track ID in both in-memory cache and persistent SQLite database."""
    if not spotify_id or not isinstance(spotify_id, str):
        return
    sp_id = spotify_id.strip()
    if len(sp_id) != 22:
        return

    meta_key = _spotify_cache_key(artist, title)
    norm_isrc = isrc.strip().upper() if isrc and isrc.strip() else None

    # 1. Update in-memory cache
    if norm_isrc:
        _SPOTIFY_ID_MEM_CACHE[f"isrc:{norm_isrc}"] = sp_id
    if meta_key != ":::":
        _SPOTIFY_ID_MEM_CACHE[meta_key] = sp_id

    # 2. Persist in SQLite
    target_db = Path(db_path) if db_path else get_active_cache_db_path()
    try:
        target_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target_db), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spotify_id_cache (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    isrc TEXT,
                    spotify_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_id_isrc ON spotify_id_cache(isrc);")

            now = time.time()
            clean_a = (artist or "").strip()
            clean_t = (title or "").strip()

            conn.execute("""
                INSERT INTO spotify_id_cache (
                    cache_key, artist, title, isrc, spotify_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    spotify_id = excluded.spotify_id,
                    isrc = COALESCE(excluded.isrc, spotify_id_cache.isrc),
                    created_at = excluded.created_at;
            """, (meta_key, clean_a, clean_t, norm_isrc, sp_id, now))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[cache] Failed saving Spotify ID to database {target_db}: {e}")


class LyricsCache:
    """Thread-safe SQLite persistent cache for negative match results and Spotify ID lookups.
    
    Prevents repeated expensive network cascade calls on audio tracks that
    previously yielded no lyrics or redundant Spotify ID search lookups.
    """

    def __init__(self, db_path: Path | str, ttl_days: float = 14.0):
        self.db_path = Path(db_path)
        self.ttl_days = max(0.0, float(ttl_days))
        self._initialized = False
        self._lock = asyncio.Lock()
        set_active_cache_db_path(self.db_path)

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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS spotify_id_cache (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    isrc TEXT,
                    spotify_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spotify_id_isrc ON spotify_id_cache(isrc);")
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

    def get_spotify_id(
        self,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        isrc: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch cached Spotify track ID synchronously from database or memory cache."""
        return get_cached_spotify_id(artist=artist, title=title, isrc=isrc, db_path=self.db_path)

    async def get_spotify_id_async(
        self,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        isrc: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch cached Spotify track ID asynchronously."""
        return await asyncio.to_thread(self.get_spotify_id, artist, title, isrc)

    def set_spotify_id(
        self,
        artist: Optional[str],
        title: Optional[str],
        spotify_id: str,
        isrc: Optional[str] = None,
    ) -> None:
        """Store resolved Spotify track ID in persistent cache and memory cache."""
        set_cached_spotify_id(artist=artist, title=title, spotify_id=spotify_id, isrc=isrc, db_path=self.db_path)

    async def set_spotify_id_async(
        self,
        artist: Optional[str],
        title: Optional[str],
        spotify_id: str,
        isrc: Optional[str] = None,
    ) -> None:
        """Store resolved Spotify track ID asynchronously."""
        await asyncio.to_thread(self.set_spotify_id, artist, title, spotify_id, isrc)

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

            cursor.execute("SELECT COUNT(*) FROM spotify_id_cache")
            total_spotify = cursor.fetchone()[0]

        size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "db_path": str(self.db_path),
            "total_negative_entries": total,
            "active_negative_entries": active,
            "expired_negative_entries": expired,
            "total_spotify_ids": total_spotify,
            "ttl_days": self.ttl_days,
            "db_size_bytes": size_bytes,
            "db_size_kb": round(size_bytes / 1024.0, 1),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Fetch statistical overview of the cache state."""
        await self.initialize()
        return await asyncio.to_thread(self._get_stats_sync)
