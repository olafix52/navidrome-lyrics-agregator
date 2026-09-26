"""Navidrome / Subsonic API client for library scanning, track discovery, and scan triggers."""

import asyncio
import hashlib
import logging
import secrets
from typing import Any, Dict, List, Optional, Set

import httpx

from src.models import SubsonicTrack

logger = logging.getLogger("nla.subsonic")


class SubsonicClient:
    """Async client interacting with Subsonic API endpoints (Navidrome, Airsonic, Gonic)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client_name: str = "NavidromeLyricsAggregator",
        api_version: str = "1.16.1",
        timeout: float = 20.0,
    ):
        clean_url = base_url.strip().rstrip("/")
        if clean_url.endswith("/rest"):
            clean_url = clean_url[:-5]
        self.base_url = clean_url
        self.username = username
        self.password = password
        self.client_name = client_name
        self.api_version = api_version
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _get_auth_params(self) -> Dict[str, str]:
        """Generate Subsonic token authentication parameters: md5(password + salt)."""
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return {
            "u": self.username,
            "t": token,
            "s": salt,
            "v": self.api_version,
            "c": self.client_name,
            "f": "json",
        }

    async def _get(self, endpoint: str, extra_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send authenticated GET request to a Subsonic endpoint."""
        client = self._get_client()
        url = f"{self.base_url}/rest/{endpoint.lstrip('/')}"
        params = self._get_auth_params()
        if extra_params:
            params.update(extra_params)

        resp = await client.get(url, params=params)
        resp.raise_for_status()

        data = resp.json()
        sub_resp = data.get("subsonic-response", {})
        if sub_resp.get("status") != "ok":
            error = sub_resp.get("error", {})
            err_msg = error.get("message", "Unknown Subsonic error")
            err_code = error.get("code", "N/A")
            raise RuntimeError(f"Subsonic error {err_code}: {err_msg}")

        return sub_resp

    async def ping(self) -> bool:
        """Test connection and authentication with the Subsonic server."""
        try:
            resp = await self._get("ping.view")
            return resp.get("status") == "ok"
        except Exception as e:
            logger.warning(f"Subsonic ping failed for {self.base_url}: {e}")
            return False

    async def start_scan(self, full_scan: bool = False) -> Dict[str, Any]:
        """Trigger library scan in Navidrome.
        
        Args:
            full_scan: If True, request full rescan instead of quick incremental scan.
            
        Returns:
            Dict representing scanStatus (e.g. {'scanning': True, 'count': 0}).
        """
        params: Dict[str, Any] = {}
        if full_scan:
            params["fullScan"] = "true"

        resp = await self._get("startScan.view", extra_params=params)
        scan_status = resp.get("scanStatus", {})
        logger.info(f"Triggered Navidrome scan at {self.base_url} (fullScan={full_scan}): {scan_status}")
        return scan_status

    async def get_scan_status(self) -> Dict[str, Any]:
        """Check current scan progress on the Subsonic server."""
        resp = await self._get("getScanStatus.view")
        return resp.get("scanStatus", {})

    async def get_all_tracks(self, batch_size: int = 10000) -> List[SubsonicTrack]:
        """Fetch all music tracks from the library.

        Tries the Navidrome native API first (one request for the whole library, real file
        paths), then Subsonic search3 and finally the album listing for other servers.
        """
        logger.info(f"Fetching track catalog from Navidrome: {self.base_url}")

        # Method 1: Navidrome native REST API. Unlike the Subsonic API, which by default
        # reports made-up "Artist/Album/NN - Title.ext" paths, it returns the real paths.
        try:
            tracks = await self._get_all_tracks_native(batch_size)
            if tracks:
                logger.info(f"Retrieved {len(tracks)} tracks via Navidrome native API")
                return tracks
        except Exception as e:
            logger.debug(f"Navidrome native API unavailable, falling back to Subsonic search3: {e}")

        # Method 2: search3.view with empty query (Navidrome natively supports this for pagination).
        # Pages are requested until an empty one: some servers cap songCount below batch_size.
        tracks = []
        try:
            seen: Set[str] = set()
            offset = 0
            while True:
                resp = await self._get(
                    "search3.view",
                    extra_params={
                        "query": "",
                        "songCount": batch_size,
                        "songOffset": offset,
                        "artistCount": 0,
                        "albumCount": 0,
                    },
                )
                search_res = resp.get("searchResult3", {})
                songs = search_res.get("song", [])
                new_songs = [s for s in songs if str(s.get("id", "")) not in seen]
                if not new_songs:
                    # Empty page, or a server ignoring songOffset and repeating itself
                    break

                for s in new_songs:
                    seen.add(str(s.get("id", "")))
                    tracks.append(self._parse_song_to_track(s))
                offset += len(songs)

            if tracks:
                logger.info(f"Retrieved {len(tracks)} tracks via Subsonic search3.view")
                logger.warning(
                    "Subsonic API paths are virtual unless the server reports real paths "
                    "(Navidrome: enable 'Report Real Path' for this client)"
                )
                return tracks

        except Exception as e:
            logger.debug(f"Subsonic search3 query='' returned exception, falling back to album listing: {e}")

        # Method 3: Album listing fallback (getAlbumList2.view -> getAlbum.view).
        # search3 may have failed half-way through pagination: start from scratch so
        # the partial results are not duplicated by the full album listing.
        tracks = []
        try:
            album_offset = 0
            album_batch = 200
            while True:
                resp = await self._get(
                    "getAlbumList2.view",
                    extra_params={
                        "type": "alphabetical",
                        "size": album_batch,
                        "offset": album_offset,
                    },
                )
                album_list = resp.get("albumList2", {}).get("album", [])
                if not album_list:
                    break

                sem = asyncio.Semaphore(10)

                async def _fetch_album_songs(alb_id: str) -> List[Dict[str, Any]]:
                    async with sem:
                        try:
                            alb_resp = await self._get("getAlbum.view", extra_params={"id": alb_id})
                            return alb_resp.get("album", {}).get("song", [])
                        except Exception as err:
                            logger.debug(f"Could not load album {alb_id}: {err}")
                            return []

                album_ids = [alb.get("id") for alb in album_list if alb.get("id")]
                albums_songs = await asyncio.gather(*(_fetch_album_songs(aid) for aid in album_ids))
                for songs in albums_songs:
                    for s in songs:
                        tracks.append(self._parse_song_to_track(s))

                if len(album_list) < album_batch:
                    break
                album_offset += len(album_list)

            logger.info(f"Retrieved {len(tracks)} tracks via Subsonic getAlbumList2 fallback")
            return tracks

        except Exception as e:
            logger.error(f"Failed to retrieve tracks from Subsonic server: {e}")
            raise

    async def _native_login(self) -> str:
        """Log in to the Navidrome native API and return its JWT."""
        client = self._get_client()
        resp = await client.post(
            f"{self.base_url}/auth/login",
            json={"username": self.username, "password": self.password},
        )
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            raise RuntimeError("Navidrome login response contained no token")
        return token

    async def _get_all_tracks_native(self, batch_size: int) -> List[SubsonicTrack]:
        """Fetch all songs through the Navidrome native API (``/api/song``)."""
        token = await self._native_login()
        client = self._get_client()
        headers = {"x-nd-authorization": f"Bearer {token}"}

        tracks: List[SubsonicTrack] = []
        library_paths: Set[str] = set()
        start = 0
        while True:
            resp = await client.get(
                f"{self.base_url}/api/song",
                params={"_start": start, "_end": start + batch_size, "_sort": "id", "_order": "ASC"},
                headers=headers,
            )
            resp.raise_for_status()
            songs = resp.json()
            if not isinstance(songs, list):
                raise RuntimeError(f"Unexpected /api/song response: {type(songs).__name__}")

            for s in songs:
                if s.get("missing"):
                    continue
                if s.get("libraryPath"):
                    library_paths.add(str(s["libraryPath"]))
                tracks.append(self._parse_native_song(s))

            start += len(songs)
            total = resp.headers.get("x-total-count")
            if not songs or (start >= int(total) if total else len(songs) < batch_size):
                break

        if len(library_paths) > 1:
            logger.warning(
                f"Navidrome has {len(library_paths)} libraries ({', '.join(sorted(library_paths))}); "
                "track paths are resolved against music_dir only"
            )
        return tracks

    def _parse_native_song(self, s: Dict[str, Any]) -> SubsonicTrack:
        """Parse a Navidrome native API song object into a SubsonicTrack.

        ``lyrics_present`` is left unset: Navidrome only knows embedded lyrics from its last
        scan, so the per-file checks in the matcher stay the source of truth.
        """
        path = str(s.get("path") or "")
        library_path = str(s.get("libraryPath") or "").rstrip("/")
        # Paths are relative to the library root; strip it should a server report them absolute
        if library_path and path.startswith(library_path + "/"):
            path = path[len(library_path) + 1:]
        return SubsonicTrack(
            id=str(s.get("id", "")),
            title=str(s.get("title", "")),
            artist=str(s.get("artist", "")),
            album=s.get("album"),
            duration=float(s.get("duration") or 0.0),
            path=path,
            suffix=str(s.get("suffix") or "mp3").lower(),
            year=s.get("year") or None,
            track_number=s.get("trackNumber") or None,
            disc_number=s.get("discNumber") or None,
        )

    def _parse_song_to_track(self, s: Dict[str, Any]) -> SubsonicTrack:
        """Parse raw Subsonic song JSON object into typed SubsonicTrack model."""
        return SubsonicTrack(
            id=str(s.get("id", "")),
            title=str(s.get("title", "")),
            artist=str(s.get("artist", "")),
            album=s.get("album"),
            duration=float(s.get("duration", 0.0)),
            path=str(s.get("path", "")),
            suffix=str(s.get("suffix", "mp3")).lower(),
            lyrics_present=bool(s.get("lyricsPresent", False)),
            year=s.get("year"),
            track_number=s.get("track"),
            disc_number=s.get("discNumber"),
        )
