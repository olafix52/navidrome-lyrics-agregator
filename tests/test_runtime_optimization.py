"""Tests for performance optimizations: uvloop, HTTP/2, and SingleFlight."""

import asyncio
import sys
import pytest
from src.matcher import SingleFlight
from src.providers.base import _HTTP2_AVAILABLE


def test_http2_support_enabled():
    """Verify that h2 is available and HTTP/2 is enabled in BaseLyricsProvider."""
    assert _HTTP2_AVAILABLE is True


@pytest.mark.skipif(sys.platform == "win32", reason="uvloop not supported on Windows")
def test_uvloop_installation():
    """Verify uvloop can be loaded and creates uvloop event loop instances."""
    import uvloop
    policy = uvloop.EventLoopPolicy()
    loop = policy.new_event_loop()
    try:
        assert isinstance(loop, uvloop.Loop)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_single_flight_exception_handling():
    """Verify that when a leader raises an exception, all waiting coroutines receive it and key is cleared."""
    sf = SingleFlight()

    async def _failing_task():
        await asyncio.sleep(0.02)
        raise ValueError("Simulated network timeout")

    results = await asyncio.gather(
        sf.execute("fail_key", _failing_task),
        sf.execute("fail_key", _failing_task),
        return_exceptions=True,
    )

    assert len(results) == 2
    assert isinstance(results[0], ValueError)
    assert isinstance(results[1], ValueError)
    assert str(results[0]) == "Simulated network timeout"
    # Ensure key was cleaned up so subsequent calls can retry
    assert "fail_key" not in sf._in_flight
