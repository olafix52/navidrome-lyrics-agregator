"""Abstract Base Class for Lyrics Providers."""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional
import httpx

from src.config import ProviderConfig
from src.models import LyricsResult, TrackMetadata
from src.rate_limiter import AsyncRateLimiter

logger = logging.getLogger("nla.providers")

# Transient HTTP statuses worth retrying; every other 4xx fails immediately
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
# Longest server-requested 429 back-off we are willing to wait inside a request
MAX_RETRY_AFTER_SECONDS = 60.0


def _backoff_seconds(attempt: int) -> float:
    """Exponential back-off: 1s, 2s, 4s, ... capped at 8s."""
    return min(8.0, float(2 ** (attempt - 1)))


def _parse_retry_after(value: Optional[str], default: float) -> float:
    """Parse a Retry-After header given as delay-seconds or HTTP-date."""
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, IndexError):
        return default

try:
    import h2
    _HTTP2_AVAILABLE = True
except ImportError:
    _HTTP2_AVAILABLE = False


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
        """Get or initialize the persistent async HTTP client with HTTP/2 support."""
        if self._client is None or self._client.is_closed:
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "*/*",
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                follow_redirects=True,
                http2=_HTTP2_AVAILABLE,
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
        """Perform an HTTP request with rate limiting and exponential backoff retry.

        Only transient failures are retried: network errors, 408/425/429 and 5xx responses.
        Other client errors (400, 401, 403, ...) cannot succeed on retry and return None
        immediately. A 429 whose Retry-After exceeds ``MAX_RETRY_AFTER_SECONDS`` gives up
        instead of blocking a worker for (potentially) hours.
        """
        client = await self.get_client()

        for attempt in range(1, self.max_retries + 1):
            is_last = attempt == self.max_retries
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
            except httpx.RequestError as e:
                logger.debug(f"[{self.name}] Network error on {url} (attempt {attempt}/{self.max_retries}): {e}")
                if is_last:
                    return None
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            except Exception as e:
                logger.warning(f"[{self.name}] Unexpected error on {url}: {e}")
                return None

            status = response.status_code
            if 200 <= status < 300:
                return response
            if status == 404:
                return None

            if status == 429:
                wait = _parse_retry_after(response.headers.get("Retry-After"), default=2.0 * attempt)
                if wait > MAX_RETRY_AFTER_SECONDS:
                    logger.warning(
                        f"[{self.name}] Rate limited (429) with Retry-After {wait:.0f}s, skipping this request"
                    )
                    return None
                logger.warning(f"[{self.name}] Rate limited (429), waiting {wait:.1f}s...")
            elif status in RETRYABLE_STATUS_CODES:
                wait = _backoff_seconds(attempt)
                logger.debug(f"[{self.name}] HTTP {status} on {url} (attempt {attempt}/{self.max_retries})")
            else:
                logger.debug(f"[{self.name}] HTTP {status} on {url}, not retrying")
                return None

            if is_last:
                return None
            await asyncio.sleep(wait)

        return None

    @abstractmethod
    async def get_lyrics(self, track: TrackMetadata) -> Optional[LyricsResult]:
        """Search and retrieve lyrics for the given audio track.
        
        Must return LyricsResult if found and valid, or None if not found.
        """
        pass
