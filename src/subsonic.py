"""Navidrome / Subsonic API client for library scanning, track discovery, and scan triggers."""

import asyncio
import hashlib
import logging
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

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

    async def get_all_tracks(self, batch_size: int = 500) -> List[SubsonicTrack]:
        """Fetch all music tracks from the Navidrome library using Subsonic search or album pagination."""
        tracks: List[SubsonicTrack] = []
        logger.info(f"Fetching track catalog from Navidrome via Subsonic API: {self.base_url}")

        # Method 1: search3.view with empty query (Navidrome natively supports this for pagination)
        try:
            offset = 0
            while True:
                resp = await self._get(
                    "search3.view",
                    extra_params={
                        "query": "",
                        "songCount": batch_size,
                        "songOffset": offset,
                    },
                )
                search_res = resp.get("searchResult3", {})
                songs = search_res.get("song", [])
                if not songs:
                    break

                for s in songs:
                    tracks.append(self._parse_song_to_track(s))

                if len(songs) < batch_size:
                    break
                offset += len(songs)

            if tracks:
                logger.info(f"Retrieved {len(tracks)} tracks via Subsonic search3.view")
                return tracks

        except Exception as e:
            logger.debug(f"Subsonic search3 query='' returned exception, falling back to album listing: {e}")

        # Method 2: Album listing fallback (getAlbumList2.view -> getAlbum.view).
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
