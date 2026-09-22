"""FastAPI backend application for the Navidrome Lyrics Aggregator Web UI."""

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.audit import LibraryAuditor
from src.config import AppConfig
from src.matcher import LyricsMatcher
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import clean_artist, clean_title
from src.providers import build_provider_cascade
from src.storage import get_existing_lyrics_file, save_lyrics_sidecar
from src.tag_reader import is_supported_audio_file, read_track_metadata
from src.web.parser import karaoke_to_ttml, parse_lyrics_to_karaoke

logger = logging.getLogger("nla.web")

STATIC_DIR = Path(__file__).parent / "static"


def encode_track_id(audio_path: Path, music_dir: Path) -> str:
    """Encode relative path to base64url track ID."""
    rel = audio_path.resolve().relative_to(music_dir.resolve())
    return base64.urlsafe_b64encode(str(rel).encode("utf-8")).decode("utf-8")


def decode_track_id(track_id: str, music_dir: Path) -> Optional[Path]:
    """Safely decode track ID to absolute audio Path within music_dir."""
    try:
        rel_str = base64.urlsafe_b64decode(track_id.encode("utf-8")).decode("utf-8")
        path = (music_dir.resolve() / rel_str).resolve()
        # Verify resolved path is strictly within music_dir
        if music_dir.resolve() in path.parents or path == music_dir.resolve():
            if path.is_file() and is_supported_audio_file(path):
                return path
    except Exception:
        pass
    return None


class SaveLyricsRequest(BaseModel):
    content: str
    format: str
    sync_type: Optional[str] = "line_sync"
    provider: Optional[str] = "manual"


class UpdateProvidersRequest(BaseModel):
    enabled_providers: List[str]
    persist: Optional[bool] = True


def create_app(config: AppConfig, matcher: Optional[LyricsMatcher] = None) -> FastAPI:
    """Create and configure the FastAPI web application."""
    app = FastAPI(
        title="Navidrome Lyrics Aggregator - Web UI",
        description="Lightweight dashboard for coverage charts, live karaoke player, and provider search",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Ensure static directory exists
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Internal state / lazy matcher
    app.state.config = config
    app.state.matcher = matcher
    app.state.auditor = LibraryAuditor()

    def get_or_create_matcher() -> LyricsMatcher:
        if app.state.matcher is None:
            providers = build_provider_cascade(app.state.config)
            app.state.matcher = LyricsMatcher(app.state.config, providers)
        return app.state.matcher

    # 1. API STATS / AUDIT
    @app.get("/api/stats")
    async def get_stats():
        """Get library coverage and lyrics distribution statistics."""
        music_dir = Path(app.state.config.music_dir)
        report = app.state.auditor.audit_library(music_dir, load_metadata_for_missing=False)
        return {
            "music_dir": str(music_dir),
            "total_tracks": report.total_tracks,
            "has_lyrics_count": report.has_lyrics_count,
            "coverage_pct": round(report.coverage_pct, 1),
            "word_sync_count": report.word_sync_count,
            "word_sync_pct": round(report.word_sync_pct, 1),
            "line_sync_count": report.line_sync_count,
            "line_sync_pct": round(report.line_sync_pct, 1),
            "unsynced_count": report.unsynced_count,
            "unsynced_pct": round(report.unsynced_pct, 1),
            "missing_count": report.missing_count,
            "missing_pct": round(report.missing_pct, 1),
            "format_counts": report.format_counts,
        }

    # 2. API TRACKS LISTING
    @app.get("/api/tracks")
    async def get_tracks(
        q: Optional[str] = None,
        filter: Optional[str] = "all",  # all, missing, has_lyrics, word_sync, line_sync
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
    ):
        """List music tracks in collection with filtering and pagination."""
        music_dir = Path(app.state.config.music_dir)
        if not music_dir.exists():
            return {"total": 0, "page": page, "limit": limit, "tracks": []}

        all_audio = sorted([p for p in music_dir.rglob("*") if p.is_file() and is_supported_audio_file(p)])

        items: List[Dict[str, Any]] = []
        for p in all_audio:
            rel_path = str(p.relative_to(music_dir))
            track_id = encode_track_id(p, music_dir)

            existing = get_existing_lyrics_file(p, output_dir=config.output_dir)
            has_lyrics = existing is not None
            fmt_str = existing[1].value if existing else None

            # Detect sync type if lyrics exist
            sync_type_str = None
            if existing:
                lyrics_path, fmt = existing
                try:
                    content = lyrics_path.read_text(encoding="utf-8", errors="replace")[:4096]
                    from src.models import detect_sync_type
                    sync_type_str = detect_sync_type(content, fmt).value
                except Exception:
                    sync_type_str = "unsynced"

            # Apply filter
            if filter == "missing" and has_lyrics:
                continue
            if filter == "has_lyrics" and not has_lyrics:
                continue
            if filter == "word_sync" and sync_type_str != "word_sync":
                continue
            if filter == "line_sync" and sync_type_str != "line_sync":
                continue

            # Apply search query
            if q:
                query_lower = q.lower()
                if query_lower not in p.name.lower() and query_lower not in rel_path.lower():
                    continue

            items.append({
                "id": track_id,
                "filename": p.name,
                "relative_path": rel_path,
                "has_lyrics": has_lyrics,
                "format": fmt_str,
                "sync_type": sync_type_str,
            })

        total = len(items)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated = items[start_idx:end_idx]

        # Enrich paginated items with tag metadata
        for it in paginated:
            decoded_p = decode_track_id(it["id"], music_dir)
            if decoded_p:
                meta = read_track_metadata(decoded_p)
                it["artist"] = meta.artist if meta else ""
                it["title"] = meta.title if meta else decoded_p.stem
                it["album"] = meta.album if meta else ""
                it["duration"] = meta.duration if meta else 0.0

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "tracks": paginated,
        }

    # 3. API AUDIO STREAMING WITH HTTP RANGE SUPPORT
    @app.get("/api/tracks/{track_id}/audio")
    async def get_audio_stream(track_id: str):
        """Stream audio file with standard HTTP Range support for seeking in HTML5 audio player."""
        music_dir = Path(app.state.config.music_dir)
        audio_path = decode_track_id(track_id, music_dir)
        if not audio_path or not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        # Determine media type
        suffix = audio_path.suffix.lower()
        mime_map = {
            ".mp3": "audio/mpeg",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".mp4": "audio/mp4",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
            ".wav": "audio/wav",
            ".aiff": "audio/x-aiff",
            ".aif": "audio/x-aiff",
        }
        media_type = mime_map.get(suffix, "application/octet-stream")

        return FileResponse(
            path=audio_path,
            media_type=media_type,
            filename=audio_path.name,
        )

    # 4. API LYRICS & KARAOKE PARSING
    @app.get("/api/tracks/{track_id}/lyrics")
    async def get_track_lyrics(track_id: str):
        """Retrieve existing lyrics and parsed karaoke timing lines for player visualization."""
        music_dir = Path(app.state.config.music_dir)
        audio_path = decode_track_id(track_id, music_dir)
        if not audio_path or not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        meta = read_track_metadata(audio_path)
        existing = get_existing_lyrics_file(audio_path, output_dir=config.output_dir)

        if not existing:
            return {
                "has_lyrics": False,
                "format": None,
                "sync_type": None,
                "content": "",
                "lines": [],
                "track": {
                    "artist": meta.artist if meta else "",
                    "title": meta.title if meta else audio_path.stem,
                    "album": meta.album if meta else "",
                    "duration": meta.duration if meta else 0.0,
                },
                "ttml_content": "",
            }

        lyrics_path, fmt = existing
        content = lyrics_path.read_text(encoding="utf-8", errors="replace")
        from src.models import detect_sync_type
        sync_type = detect_sync_type(content, fmt)

        karaoke_lines = parse_lyrics_to_karaoke(content, fmt)
        track_title = meta.title if meta else audio_path.stem
        track_artist = meta.artist if meta else ""

        if fmt == LyricsFormat.TTML:
            ttml_content = content
        else:
            ttml_content = karaoke_to_ttml(karaoke_lines, title=track_title, artist=track_artist)

        attribution_data: Dict[str, Any] = {
            "provider": None,
            "source": None,
            "maker": None,
            "uploader": None,
            "copyright_text": None,
            "songwriters": None,
        }

        if fmt == LyricsFormat.TTML or "<tt" in content:
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(content)
                attr_el = next((el for el in root.iter() if el.tag.split("}")[-1].lower() == "attribution"), None)
                copy_el = next((el for el in root.iter() if el.tag.split("}")[-1].lower() == "copyright"), None)
                sw_elements = [el.text.strip() for el in root.iter() if el.tag.split("}")[-1].lower() == "songwriter" and el.text]

                if attr_el is not None:
                    attribution_data["provider"] = attr_el.attrib.get("provider")
                    attribution_data["source"] = attr_el.attrib.get("source")
                    for ch in attr_el:
                        t = ch.tag.split("}")[-1].lower()
                        if t == "maker":
                            attribution_data["maker"] = {
                                "username": ch.attrib.get("username", ""),
                                "url": ch.attrib.get("url", ""),
                                "id": ch.attrib.get("id", ""),
                            }
                        elif t == "uploader":
                            attribution_data["uploader"] = {
                                "username": ch.attrib.get("username", ""),
                                "url": ch.attrib.get("url", ""),
                                "id": ch.attrib.get("id", ""),
                            }

                if copy_el is not None and copy_el.text:
                    attribution_data["copyright_text"] = copy_el.text.strip()
                if sw_elements:
                    attribution_data["songwriters"] = sw_elements
            except Exception:
                pass
        elif fmt == LyricsFormat.LRC:
            for line in content.splitlines()[:15]:
                line_s = line.strip()
                if line_s.startswith("# Provider:"):
                    attribution_data["provider"] = line_s.replace("# Provider:", "").strip()
                elif line_s.startswith("# Synced by:"):
                    raw = line_s.replace("# Synced by:", "").strip()
                    attribution_data["maker"] = {"username": raw}
                elif line_s.startswith("# Uploaded by:"):
                    raw = line_s.replace("# Uploaded by:", "").strip()
                    attribution_data["uploader"] = {"username": raw}

        return {
            "has_lyrics": True,
            "format": fmt.value,
            "sync_type": sync_type.value,
            "filename": lyrics_path.name,
            "content": content,
            "ttml_content": ttml_content,
            "attribution": attribution_data,
            "lines": [line.model_dump() for line in karaoke_lines],
            "track": {
                "artist": track_artist,
                "title": track_title,
                "album": meta.album if meta else "",
                "duration": meta.duration if meta else 0.0,
            },
        }

    # 5. API MANUAL SEARCH ACROSS ALL PROVIDERS (JSON AND SSE STREAMING)
    @app.get("/api/search-providers")
    async def search_providers(
        artist: str = Query(...),
        title: str = Query(...),
        album: Optional[str] = Query(None),
        duration: Optional[float] = Query(0.0),
        timeout: Optional[float] = Query(3.5),
    ):
        """Query all enabled lyrics providers concurrently and return candidate versions."""
        matcher = get_or_create_matcher()

        track = TrackMetadata(
            file_path=Path(f"/tmp/{artist} - {title}.mp3"),
            artist=artist,
            title=title,
            album=album,
            duration=duration or 0.0,
            clean_artist=clean_artist(artist),
            clean_title=clean_title(title),
        )

        candidates = await matcher.search_all_providers(track, timeout_per_provider=timeout or 3.5)
        return {"query": {"artist": artist, "title": title}, "candidates": candidates}

    @app.get("/api/search-providers-stream")
    async def search_providers_stream(
        artist: str = Query(...),
        title: str = Query(...),
        album: Optional[str] = Query(None),
        duration: Optional[float] = Query(0.0),
        timeout: Optional[float] = Query(3.5),
    ):
        """Stream provider search results as Server-Sent Events (SSE) in real time as each provider responds."""
        matcher = get_or_create_matcher()

        track = TrackMetadata(
            file_path=Path(f"/tmp/{artist} - {title}.mp3"),
            artist=artist,
            title=title,
            album=album,
            duration=duration or 0.0,
            clean_artist=clean_artist(artist),
            clean_title=clean_title(title),
        )

        async def sse_generator():
            count = 0
            async for cand in matcher.stream_provider_search(track, timeout_per_provider=timeout or 3.5):
                count += 1
                payload = json.dumps({"type": "candidate", "candidate": cand})
                yield f"data: {payload}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'total': count})}\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 6. API SAVE SELECTED LYRICS VERSION TO DISK
    @app.post("/api/tracks/{track_id}/save-lyrics")
    async def save_lyrics(track_id: str, req: SaveLyricsRequest):
        """Save selected lyrics content as the track sidecar file."""
        music_dir = Path(app.state.config.music_dir)
        audio_path = decode_track_id(track_id, music_dir)
        if not audio_path or not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        meta = read_track_metadata(audio_path)
        try:
            fmt = LyricsFormat(req.format.lower())
        except ValueError:
            fmt = LyricsFormat.LRC

        from src.models import detect_sync_type
        sync_type = detect_sync_type(req.content, fmt)

        lyrics_result = LyricsResult(
            format=fmt,
            sync_type=sync_type,
            content=req.content,
            title=meta.title if meta else audio_path.stem,
            artist=meta.artist if meta else "",
            duration=meta.duration if meta else 0.0,
            provider_name=req.provider or "manual",
        )

        saved_path = save_lyrics_sidecar(
            audio_path=audio_path,
            lyrics=lyrics_result,
            dry_run=False,
            remove_lower_quality=True,
        )

        return {
            "success": True,
            "saved_file": saved_path.name,
            "format": fmt.value,
            "sync_type": sync_type.value,
        }

    # 7. API PROVIDERS MANAGEMENT
    @app.get("/api/providers")
    async def get_providers():
        """Get all lyrics providers, their status, cascade priority, and metadata."""
        from src.providers import AVAILABLE_PROVIDERS, PROVIDER_METADATA

        enabled_set = set(p.lower() for p in app.state.config.enabled_providers)
        result: List[Dict[str, Any]] = []

        # 1. Enabled providers in cascade priority order
        priority = 1
        for p_id in app.state.config.enabled_providers:
            p_lower = p_id.lower()
            meta = PROVIDER_METADATA.get(p_lower, {})
            result.append({
                "id": p_lower,
                "name": meta.get("name", p_id),
                "description": meta.get("description", ""),
                "formats": meta.get("formats", ["LRC (Line-sync)"]),
                "requires_api_key": meta.get("requires_api_key", False),
                "enabled": True,
                "priority": priority,
            })
            priority += 1

        # 2. Disabled providers
        for p_id in AVAILABLE_PROVIDERS:
            if p_id not in enabled_set:
                meta = PROVIDER_METADATA.get(p_id, {})
                result.append({
                    "id": p_id,
                    "name": meta.get("name", p_id),
                    "description": meta.get("description", ""),
                    "formats": meta.get("formats", ["LRC (Line-sync)"]),
                    "requires_api_key": meta.get("requires_api_key", False),
                    "enabled": False,
                    "priority": None,
                })

        return {
            "providers": result,
            "total": len(AVAILABLE_PROVIDERS),
            "enabled_count": len(app.state.config.enabled_providers),
        }

    @app.post("/api/providers")
    async def update_providers(req: UpdateProvidersRequest):
        """Update active providers cascade priority and enabled state."""
        from src.config import save_enabled_providers
        from src.providers import AVAILABLE_PROVIDERS

        clean_list = []
        for p in req.enabled_providers:
            p_lower = p.strip().lower()
            if p_lower in AVAILABLE_PROVIDERS and p_lower not in clean_list:
                clean_list.append(p_lower)

        app.state.config.enabled_providers = clean_list

        for p_id in AVAILABLE_PROVIDERS:
            if p_id in app.state.config.providers:
                app.state.config.providers[p_id].enabled = (p_id in clean_list)

        providers = build_provider_cascade(app.state.config)
        app.state.matcher = LyricsMatcher(app.state.config, providers)

        saved_file = None
        if req.persist:
            try:
                saved_path = save_enabled_providers(app.state.config.enabled_providers)
                saved_file = str(saved_path)
            except Exception as e:
                logger.warning(f"Failed to persist enabled providers to file: {e}")

        return {
            "success": True,
            "enabled_providers": app.state.config.enabled_providers,
            "persisted_to": saved_file,
        }

    # 8. CACHE MANAGEMENT ENDPOINTS
    @app.get("/api/cache")
    async def get_cache_info():
        """Fetch statistics of the persistent SQLite negative lyrics cache."""
        matcher = get_or_create_matcher()
        if not matcher.cache:
            return {"enabled": False}
        stats = await matcher.cache.get_stats()
        stats["enabled"] = True
        return stats

    @app.post("/api/cache/clear")
    async def clear_cache_info(expired_only: bool = False):
        """Clear cache entries, optionally only expired ones."""
        matcher = get_or_create_matcher()
        if not matcher.cache:
            return {"enabled": False, "deleted": 0}
        deleted = await matcher.cache.clear(expired_only=expired_only)
        return {"enabled": True, "deleted": deleted}

    # 9. SERVE STATIC ASSETS AND SPA INDEX
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(
                index_file,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return {"message": "Navidrome Lyrics Aggregator Web UI API is running."}

    return app
