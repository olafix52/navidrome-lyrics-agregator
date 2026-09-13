"""Asynchronous Token Bucket rate limiter for managing API request frequency."""

import asyncio
import time


class AsyncRateLimiter:
    """Token bucket rate limiter for asyncio tasks."""

    def __init__(self, rate_per_second: float = 2.0, max_tokens: float = 5.0):
        self.rate = max(0.1, float(rate_per_second))
        self.capacity = max(1.0, float(max_tokens))
        self.tokens = self.capacity
        self.last_updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available to proceed."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_updated
                self.last_updated = now

                # Add tokens based on elapsed time
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

                # Calculate wait time needed for next token
                needed = 1.0 - self.tokens
                wait_time = needed / self.rate
                await asyncio.sleep(wait_time)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
