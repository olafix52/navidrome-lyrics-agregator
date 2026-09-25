"""FastAPI backend application for the Navidrome Lyrics Aggregator Web UI."""

import base64
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.config import AppConfig
from src.matcher import LyricsMatcher
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import clean_artist, clean_title
from src.providers import build_provider_cascade
from src.storage import get_existing_lyrics_file, save_lyrics_for_track
from src.tag_reader import is_supported_audio_file
from src.uncensor import uncensor_lyrics_content
from src.web.library_index import LibraryIndex, cached_track_tags
from src.web.parser import karaoke_to_ttml, parse_lyrics_to_karaoke

logger = logging.getLogger("nla.web")

STATIC_DIR = Path(__file__).parent / "static"

AUTH_COOKIE_NAME = "nla_token"
AUTH_HEADER_NAME = "x-nla-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = ["localhost", "127.0.0.1"]


def is_loopback_host(host: str) -> bool:
    """Return True if the bind address only accepts local connections."""
    return host in ("localhost", "::1") or host.startswith("127.")


def _is_cross_origin(request: Request) -> bool:
    """Detect browser requests issued by a different origin (CSRF from other websites)."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = request.headers.get("origin")
    if not origin:
        return False
    if origin == "null":
        return True
    return urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower()


def _request_token(request: Request) -> Optional[str]:
    """Extract the auth token from header, bearer authorization, or session cookie."""
    token = request.headers.get(AUTH_HEADER_NAME)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(AUTH_COOKIE_NAME)


def _token_matches(provided: Optional[str], expected: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


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


def create_app(
    config: AppConfig,
    matcher: Optional[LyricsMatcher] = None,
    trusted_hosts: Optional[List[str]] = None,
    watch_library: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI web application.

    Security model:
      - No CORS headers are sent, so other websites cannot read API responses.
      - State-changing requests (POST, ...) coming from another origin are rejected.
      - If ``config.web_auth_token`` is set, every request except static assets must carry
        the token (``X-NLA-Token`` header, ``Authorization: Bearer``, or the session cookie
        obtained by opening ``/?token=<token>`` once).
      - ``trusted_hosts`` restricts accepted Host headers (protects loopback-only servers
        against DNS rebinding).

    ``watch_library`` keeps the library index current through a filesystem watcher while
    the server runs (started/stopped by the ASGI lifespan); otherwise it refreshes by TTL.
    """
    library_index = LibraryIndex(lambda: app.state.config, encode_track_id)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if watch_library:
            library_index.start_watching()
        try:
            yield
        finally:
            library_index.stop_watching()
            if app.state.matcher is not None:
                await app.state.matcher.close()

    app = FastAPI(
        title="Navidrome Lyrics Aggregator - Web UI",
        description="Lightweight dashboard for coverage charts, live karaoke player, and provider search",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_guard(request: Request, call_next):
        if request.method not in SAFE_METHODS and _is_cross_origin(request):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected"})

        expected_token = app.state.config.web_auth_token
        path = request.url.path
        if expected_token and not path.startswith("/static/"):
            if path == "/" and request.query_params.get("token") is not None:
                if _token_matches(request.query_params.get("token"), expected_token):
                    response = RedirectResponse(url="/", status_code=303)
                    response.set_cookie(
                        AUTH_COOKIE_NAME,
                        expected_token,
                        httponly=True,
                        samesite="strict",
                        secure=request.url.scheme == "https",
                    )
                    return response
                return PlainTextResponse("Invalid token.", status_code=401)

            if not _token_matches(_request_token(request), expected_token):
                if path == "/":
                    return PlainTextResponse(
                        "Authentication required: open this page as /?token=<your NLA web token>.",
                        status_code=401,
                    )
                return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        return await call_next(request)

    if trusted_hosts:
        # Added last so it runs first (outermost middleware)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    # Ensure static directory exists
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Internal state / lazy matcher
    app.state.config = config
    app.state.matcher = matcher
    app.state.library_index = library_index

    def get_or_create_matcher() -> LyricsMatcher:
        if app.state.matcher is None:
            providers = build_provider_cascade(app.state.config)
            app.state.matcher = LyricsMatcher(app.state.config, providers)
        return app.state.matcher

    # 1. API STATS / AUDIT
    # Handlers doing filesystem/tag I/O are plain ``def`` so FastAPI runs them in its
    # threadpool instead of blocking the event loop (and every other request).
    @app.get("/api/stats")
    def get_stats():
        """Get library coverage and lyrics distribution statistics (from the library index)."""
        return {"music_dir": str(app.state.config.music_dir), **library_index.stats()}

    # 2. API TRACKS LISTING
    @app.get("/api/tracks")
    def get_tracks(
        q: Optional[str] = None,
        filter: Optional[str] = "all",  # all, missing, has_lyrics, word_sync, line_sync
        page: int = Query(1, ge=1),
        limit: int = Query(50, ge=1, le=200),
    ):
        """List music tracks in collection with filtering and pagination."""
        music_dir = Path(app.state.config.music_dir)
        if not music_dir.exists():
            return {"total": 0, "page": page, "limit": limit, "tracks": []}

        query_lower = q.lower() if q else None
        items: List[Dict[str, Any]] = []
        for t in library_index.tracks():
            if filter == "missing" and t.has_lyrics:
                continue
            if filter == "has_lyrics" and not t.has_lyrics:
                continue
            if filter == "word_sync" and t.sync_type != "word_sync":
                continue
            if filter == "line_sync" and t.sync_type != "line_sync":
                continue
            if query_lower and query_lower not in t.filename.lower() and query_lower not in t.relative_path.lower():
                continue
            items.append(t)

        total = len(items)
        start_idx = (page - 1) * limit
        paginated = items[start_idx:start_idx + limit]

        # Enrich only the visible page with tag metadata (memoized per file version)
        tracks: List[Dict[str, Any]] = []
        for t in paginated:
            tags = cached_track_tags(t.path)
            tracks.append({
                "id": t.track_id,
                "filename": t.filename,
                "relative_path": t.relative_path,
                "has_lyrics": t.has_lyrics,
                "format": t.format,
                "sync_type": t.sync_type,
                "artist": tags["artist"] if tags else "",
                "title": tags["title"] if tags else t.path.stem,
                "album": tags["album"] if tags else "",
                "duration": tags["duration"] if tags else 0.0,
            })

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "tracks": tracks,
        }

    # 3. API AUDIO STREAMING WITH HTTP RANGE SUPPORT
    @app.get("/api/tracks/{track_id}/audio")
    def get_audio_stream(track_id: str):
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
    def get_track_lyrics(track_id: str):
        """Retrieve existing lyrics and parsed karaoke timing lines for player visualization."""
        music_dir = Path(app.state.config.music_dir)
        audio_path = decode_track_id(track_id, music_dir)
        if not audio_path or not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        tags = cached_track_tags(audio_path)
        existing = get_existing_lyrics_file(
            audio_path, output_dir=app.state.config.output_dir, music_dir=music_dir
        )

        if not existing:
            return {
                "has_lyrics": False,
                "format": None,
                "sync_type": None,
                "content": "",
                "lines": [],
                "track": {
                    "artist": tags["artist"] if tags else "",
                    "title": tags["title"] if tags else audio_path.stem,
                    "album": tags["album"] if tags else "",
                    "duration": tags["duration"] if tags else 0.0,
                },
                "ttml_content": "",
            }

        lyrics_path, fmt = existing
        content = lyrics_path.read_text(encoding="utf-8", errors="replace")
        from src.models import detect_sync_type
        sync_type = detect_sync_type(content, fmt)

        karaoke_lines = parse_lyrics_to_karaoke(content, fmt)
        track_title = tags["title"] if tags else audio_path.stem
        track_artist = tags["artist"] if tags else ""

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
                "album": tags["album"] if tags else "",
                "duration": tags["duration"] if tags else 0.0,
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
    def save_lyrics(track_id: str, req: SaveLyricsRequest):
        """Save selected lyrics content as the track sidecar file."""
        music_dir = Path(app.state.config.music_dir)
        audio_path = decode_track_id(track_id, music_dir)
        if not audio_path or not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        tags = cached_track_tags(audio_path)
        try:
            fmt = LyricsFormat(req.format.lower())
        except ValueError:
            fmt = LyricsFormat.LRC

        from src.models import detect_sync_type
        content = req.content
        if app.state.config.uncensor_lyrics:
            content, _ = uncensor_lyrics_content(content, fmt)
        sync_type = detect_sync_type(content, fmt)

        lyrics_result = LyricsResult(
            format=fmt,
            sync_type=sync_type,
            content=content,
            title=tags["title"] if tags else audio_path.stem,
            artist=tags["artist"] if tags else "",
            duration=tags["duration"] if tags else 0.0,
            provider_name=req.provider or "manual",
        )

        # Same destination rules as the scanner: storage_mode + mirrored output_dir
        cfg = app.state.config
        saved_path, was_embedded = save_lyrics_for_track(
            audio_path=audio_path,
            lyrics=lyrics_result,
            storage_mode=cfg.storage_mode,
            output_dir=cfg.output_dir,
            music_dir=music_dir,
            dry_run=False,
            remove_lower_quality=True,
            enhanced_lrc=cfg.embed_word_sync,
        )
        library_index.mark_dir_dirty(audio_path.parent)

        return {
            "success": True,
            "saved_file": saved_path.name if saved_path else "audio tags",
            "embedded": was_embedded,
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
        old_matcher = app.state.matcher
        app.state.matcher = LyricsMatcher(app.state.config, providers)
        if old_matcher is not None:
            # Release the HTTP clients of the replaced provider instances
            await old_matcher.close()

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
