"""Genius Lyrics Provider (Search API & HTML Scraper for plain lyrics fallback)."""

import logging
import re
from typing import Optional
from bs4 import BeautifulSoup
from src.models import LyricsFormat, LyricsResult, LyricsSyncType, TrackMetadata
from src.normalizer import calculate_candidate_score, clean_artist, clean_title
from src.providers.base import BaseLyricsProvider

logger = logging.getLogger("nla.providers.genius")


class GeniusProvider(BaseLyricsProvider):
    """Provider for Genius lyrics (plain unsynced lyrics fallback)."""

    name = "genius"
    description = "Genius Database (plain lyrics scraper)"
    supports_word_sync = False
    supports_line_sync = False

    DEFAULT_SEARCH_URL = "https://genius.com/api/search/multi"
    GENIUS_API_URL = "https://api.genius.com/search"

    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        title = track.clean_title or clean_title(track.title)
        artist = track.clean_artist or clean_artist(track.artist)
        query = f"{artist} {title}".strip()

        # 1. Search song
        api_token = self.config.api_key or self.config.extra.get("access_token")
        song_url = None
        song_title = None
        song_artist = None

        if api_token:
            headers = {"Authorization": f"Bearer {api_token}"}
            resp = await self.request_with_retry("GET", self.GENIUS_API_URL, params={"q": query}, headers=headers)
            if resp:
                try:
                    data = resp.json()
                    hits = data.get("response", {}).get("hits", [])
                    best_score = 0.0
                    for hit in hits:
                        if hit.get("type") == "song":
                            res = hit.get("result", {})
                            c_title = res.get("title") or ""
                            c_artist = res.get("primary_artist", {}).get("name") or ""
                            score = calculate_candidate_score(title, artist, c_title, c_artist)
                            if score > best_score and score >= 0.60:
                                best_score = score
                                song_url = res.get("url")
                                song_title = c_title
                                song_artist = c_artist
                except Exception as e:
                    logger.debug(f"[{self.name}] Genius API search error: {e}")
        else:
            # Public multi-search
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://genius.com/",
            }
            resp = await self.request_with_retry("GET", self.DEFAULT_SEARCH_URL, params={"q": query}, headers=headers)
            if resp:
                try:
                    data = resp.json()
                    sections = data.get("response", {}).get("sections", [])
                    best_score = 0.0
                    for section in sections:
                        if section.get("type") in ("song", "top_hit"):
                            hits = section.get("hits", [])
                            for hit in hits:
                                res = hit.get("result", {})
                                c_url = res.get("url")
                                if c_url:
                                    c_title = res.get("title") or ""
                                    c_artist = res.get("primary_artist", {}).get("name") or ""
                                    score = calculate_candidate_score(title, artist, c_title, c_artist)
                                    if score > best_score and score >= 0.60:
                                        best_score = score
                                        song_url = c_url
                                        song_title = c_title
                                        song_artist = c_artist
                except Exception as e:
                    logger.debug(f"[{self.name}] Genius public search error: {e}")

        if not song_url:
            return None

        # 2. Fetch and parse lyrics from the HTML page
        html_resp = await self.request_with_retry(
            "GET",
            song_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        if not html_resp:
            return None

        try:
            soup = BeautifulSoup(html_resp.text, "html.parser")
            containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
            if not containers:
                containers = soup.find_all("div", class_=re.compile(r"Lyrics__Container"))

            if not containers:
                return None

            lyrics_parts = []
            for container in containers:
                # Replace <br> with newlines
                for br in container.find_all("br"):
                    br.replace_with("\n")
                text = container.get_text()
                if text:
                    lyrics_parts.append(text.strip())

            full_lyrics = "\n\n".join(lyrics_parts).strip()
            if not full_lyrics:
                return None

            return LyricsResult(
                content=full_lyrics,
                format=LyricsFormat.TXT,
                sync_type=LyricsSyncType.UNSYNCED,
                provider_name=self.name,
                title=song_title or title,
                artist=song_artist or artist,
                metadata={"url": song_url},
            )

        except Exception as e:
            logger.debug(f"[{self.name}] HTML scraper error: {e}")

        return None
