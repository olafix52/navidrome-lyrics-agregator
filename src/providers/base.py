"""Abstract Base Class for Lyrics Providers."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import httpx

from src.config import ProviderConfig
from src.models import LyricsResult, TrackMetadata
from src.rate_limiter import AsyncRateLimiter

logger = logging.getLogger("nla.providers")


class BaseLyricsProvider(ABC):
    """Base abstract class for all lyrics providers."""

    name: str = "base"
    description: str = "Base lyrics provider"
    supports_word_sync: bool = True
    supports_line_sync: bool = True

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        user_agent: str = "NavidromeLyricsAggregator/1.0",
        default_timeout: float = 10.0,
        max_retries: int = 3,
    ):
        self.config = config or ProviderConfig()
        self.user_agent = user_agent
        self.timeout = self.config.timeout_seconds or default_timeout
        self.max_retries = max_retries
        self.rate_limiter = AsyncRateLimiter(rate_per_second=self.config.rate_limit_per_second)
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or initialize the persistent async HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[httpx.Response]:
        """Perform an HTTP request with rate limiting and exponential backoff retry."""
        client = await self.get_client()

        for attempt in range(1, self.max_retries + 1):
            await self.rate_limiter.acquire()
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    json=json,
                    headers=headers,
                )

                # If rate limited by remote server (429), wait and retry
                if response.status_code == 429:
                    raw_retry = response.headers.get("Retry-After", "")
                    try:
                        retry_after = float(raw_retry)
                    except (ValueError, TypeError):
                        retry_after = 2.0 * attempt
                    logger.warning(f"[{self.name}] Rate limited (429), waiting {retry_after}s...")
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as e:
                logger.debug(f"[{self.name}] HTTP status {e.response.status_code} on {url}: {e}")
                if attempt == self.max_retries:
                    return None
            except (httpx.RequestError, httpx.TimeoutException) as e:
                logger.debug(f"[{self.name}] Network error on {url} (attempt {attempt}/{self.max_retries}): {e}")
                if attempt == self.max_retries:
                    return None
                await asyncio.sleep(1.0 * attempt)
            except Exception as e:
                logger.warning(f"[{self.name}] Unexpected error on {url}: {e}")
                return None

        return None

    @abstractmethod
    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        """Search and retrieve lyrics for the given audio track.
        
        Must return LyricsResult if found and valid, or None if not found.
        """
        pass
