"""Lyricsify Community LRC Provider."""

import logging
import re
import urllib.parse
from bs4 import BeautifulSoup
from typing import Dict, Optional
from src.models import LyricsFormat, LyricsResult, TrackMetadata, detect_sync_type
from src.normalizer import calculate_candidate_score, clean_artist, clean_title
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.lyricsify")


class LyricsifyProvider(BaseLyricsProvider):
    """Provider for Lyricsify community synchronized LRC lyrics."""

    name = "lyricsify"
    description = "Lyricsify Community Database (synced LRC lyrics)"
    supports_word_sync = False

    DEFAULT_BASE_URL = "https://www.lyricsify.com"

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.lyricsify.com/",
        }

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch HTML directly or via FlareSolverr if configured."""
        flaresolverr_url = None
        if self.config.extra and isinstance(self.config.extra, dict):
            flaresolverr_url = self.config.extra.get("flaresolverr_url")
        if not flaresolverr_url:
            import os
            flaresolverr_url = os.environ.get("NLA_FLARESOLVERR_URL") or os.environ.get("FLARESOLVERR_URL")

        # 1. Try FlareSolverr if configured
        if flaresolverr_url:
            try:
                base = flaresolverr_url.rstrip("/")
                fs_endpoint = base if base.endswith("/v1") else f"{base}/v1"
                payload = {
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": 60000,
                }
                fs_resp = await self.request_with_retry("POST", fs_endpoint, json=payload)
                if fs_resp and fs_resp.status_code == 200:
                    data = fs_resp.json()
                    if data.get("status") == "ok":
                        solution = data.get("solution", {})
                        if solution.get("status") == 200:
                            logger.debug(f"[{self.name}] Successfully bypassed Cloudflare via FlareSolverr for {url}")
                            return solution.get("response", "")
            except Exception as e:
                logger.debug(f"[{self.name}] FlareSolverr request error: {e}")

        # 2. Direct HTTP request fallback
        headers = self._get_headers()
        resp = await self.request_with_retry("GET", url, headers=headers)
        if not resp:
            return None

        if resp.status_code in (403, 503) or "Just a moment..." in resp.text or "challenge-platform" in resp.text:
            logger.warning(
                f"[{self.name}] Cloudflare protection blocked direct request to {url} (HTTP {resp.status_code}). "
                f"Ensure FlareSolverr is running on {flaresolverr_url or 'http://localhost:8191/v1'} to bypass Cloudflare."
            )
            return None

        return resp.text

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        query = f"{artist} {title}".strip()

        base_url = (self.config.custom_url or self.DEFAULT_BASE_URL).rstrip("/")
        search_url = f"{base_url}/search?q={urllib.parse.quote_plus(query)}"

        search_html = await self._fetch_html(search_url)
        if not search_html:
            return None

        try:
            soup = BeautifulSoup(search_html, "html.parser")
            # Lyricsify search results have links in format /lrc/... or /lyric/...
            candidate_links = soup.find_all("a", href=re.compile(r"/(?:lrc|lyric)/"))
            if not candidate_links:
                logger.debug(f"[{self.name}] No candidate links found for: {query}")
                return None

            best_link = None
            best_score = 0.0

            for link in candidate_links:
                text = link.get_text(strip=True)
                # Text usually is "Artist - Title" or "TITLE - ARTIST"
                cand_artist, cand_title = "", text
                if " - " in text:
                    parts = text.split(" - ", 1)
                    cand_artist, cand_title = parts[0], parts[1]

                score = calculate_candidate_score(
                    target_title=title,
                    target_artist=artist,
                    candidate_title=cand_title,
                    candidate_artist=cand_artist,
                )

                if score > best_score:
                    best_score = score
                    best_link = link

            if not best_link or best_score < 0.6:
                logger.debug(f"[{self.name}] Low match score ({best_score:.2f}) for: {query}")
                return None

            href = best_link.get("href", "")
            if not href.startswith("http"):
                href = f"{base_url}{href}"

            page_html = await self._fetch_html(href)
            if not page_html:
                return None

            page_soup = BeautifulSoup(page_html, "html.parser")
            # Lyrics container in Lyricsify is in div with id lyrics_display or lyrics_
            lrc_container = (
                page_soup.find("div", id="lyrics_display")
                or page_soup.find("div", id=re.compile(r"lyrics_"))
                or page_soup.find("div", class_="content")
                or page_soup.find("textarea")
            )
            if not lrc_container:
                return None

            lrc_text = lrc_container.get_text(separator="\n").strip()
            if not lrc_text or "[" not in lrc_text:
                return None

            sync_type = detect_sync_type(lrc_text, LyricsFormat.LRC)

            return LyricsResult(
                content=lrc_text,
                format=LyricsFormat.LRC,
                sync_type=sync_type,
                provider_name=self.name,
                title=title,
                artist=artist,
                metadata={"url": href, "match_score": best_score},
            )

        except Exception as e:
            logger.debug(f"[{self.name}] Error searching or parsing Lyricsify: {e}")
            return None
