#!/usr/bin/env python
"""Unit tests for the Sliding Window Rate Limiter."""

from __future__ import annotations

import time
import pytest

from agent.rate_limiter import (
    RateLimiterError,
    RateLimitExceeded,
    SlidingWindowRateLimiter,
)


@pytest.fixture
def limiter():
    return SlidingWindowRateLimiter(window_size=60.0, max_requests=2, ttl=None)


class TestRateLimiterError:
    def test_base_exception_instantiates(self):
        exc = RateLimiterError("base error")
        assert str(exc) == "base error"

    def test_rate_limit_exceeded_instantiates(self):
        exc = RateLimitExceeded("rate limit hit")
        assert str(exc) == "rate limit hit"


class TestSlidingWindowRateLimiterInit:
    def test_defaults(self):
        r = SlidingWindowRateLimiter()
        assert r.window_size == 60.0
        assert r.max_requests == 100
        assert r.ttl is None

    def test_custom_window_size(self):
        r = SlidingWindowRateLimiter(window_size=30.0)
        assert r.window_size == 30.0

    def test_custom_max_requests(self):
        r = SlidingWindowRateLimiter(max_requests=50)
        assert r.max_requests == 50

    def test_custom_ttl(self):
        r = SlidingWindowRateLimiter(ttl=120.0)
        assert r.ttl == 120.0

    def test_invalid_window_size_raises(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(window_size=0)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(window_size=-1)

    def test_invalid_max_requests_raises(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(max_requests=0)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(max_requests=-5)

    def test_invalid_ttl_raises(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(ttl=0)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(ttl=-10)


class TestSlidingWindowRateLimiterAllow:
    def test_first_request_allowed(self, limiter):
        assert limiter.allow("client-1") is True

    def test_second_request_allowed(self, limiter):
        limiter.allow("client-1")
        assert limiter.allow("client-1") is True

    def test_third_request_rejected(self, limiter):
        limiter.allow("client-1")
        limiter.allow("client-1")
        assert limiter.allow("client-1") is False

    def test_different_clients_independent(self, limiter):
        assert limiter.allow("client-a") is True
        assert limiter.allow("client-b") is True
        assert limiter.allow("client-a") is True  # client-a still has its own slot
        assert limiter.allow("client-a") is False  # client-a now exhausted

    def test_returns_bool(self, limiter):
        result = limiter.allow("client-1")
        assert isinstance(result, bool)

    def test_uses_monotonic_time(self, limiter, monkeypatch):
        # Patch time.monotonic to return controlled values
        class MockTime:
            @staticmethod
            def monotonic():
                return 1000.0

        monkeypatch.setattr("time.monotonic", MockTime.monotonic)
        # With window_size=60 and max_requests=2, two requests at t=1000 should be allowed
        assert limiter.allow("client-1") is True
        assert limiter.allow("client-1") is True
        # Third should fail
        assert limiter.allow("client-1") is False


class TestSlidingWindowRateLimiterCheckAlias:
    def test_check_is_allow(self, limiter):
        assert limiter.check("client-1") is True
        limiter.allow("client-1")
        assert limiter.check("client-1") is True


class TestSlidingWindowRateLimiterReset:
    def test_reset_single_client(self, limiter):
        limiter.allow("client-1")
        limiter.allow("client-1")
        limiter.reset("client-1")
        assert limiter.allow("client-1") is True

    def test_reset_all_clients(self, limiter):
        limiter.allow("client-1")
        limiter.allow("client-2")
        limiter.reset()
        assert limiter.allow("client-1") is True
        assert limiter.allow("client-2") is True

    def test_reset_none_clears_everything(self, limiter):
        limiter.allow("client-1")
        limiter.reset(None)
        assert limiter.get_usage("client-1")["used"] == 0


class TestSlidingWindowRateLimiterGetUsage:
    def test_usage_empty(self, limiter):
        usage = limiter.get_usage("client-1")
        assert usage["used"] == 0
        assert usage["remaining"] == 2
        assert usage["limit"] == 2
        assert usage["reset"] is None

    def test_usage_after_requests(self, limiter):
        limiter.allow("client-1")
        limiter.allow("client-1")
        usage = limiter.get_usage("client-1")
        assert usage["used"] == 2
        assert usage["remaining"] == 0
        assert usage["limit"] == 2

    def test_usage_purges_old(self, limiter, monkeypatch):
        class MockTime:
            @staticmethod
            def monotonic():
                return 2000.0

        import time as _time
        _time.monotonic = MockTime.monotonic

        limiter.allow("client-1")
        # Usage should reflect current state after purge
        usage = limiter.get_usage("client-1")
        assert usage["used"] == 1


class TestSlidingWindowRateLimiterTtlCleanup:
    @pytest.fixture
    def limiter_with_ttl(self):
        return SlidingWindowRateLimiter(window_size=60.0, max_requests=100, ttl=1.0)

    def test_ttl_cleanup_removes_stale_client(self, limiter_with_ttl, monkeypatch):
        class MockTime:
            @staticmethod
            def monotonic():
                return 1000.0

        import time as _time
        _time.monotonic = MockTime.monotonic

        limiter_with_ttl.allow("client-1")
        # Simulate TTL expiration by updating last_seen to old time
        limiter_with_ttl._last_seen["client-1"] = 1000.0
        # Now fast-forward time
        _time.monotonic = lambda: 2000.0
        # allow will update last_seen, so test _cleanup_ttl directly
        limiter_with_ttl._cleanup_ttl("client-1", 2000.0)
        # After TTL cleanup, client-1 should be removed
        assert "client-1" not in limiter_with_ttl._windows

    def test_ttl_no_cleanup_within_window(self, limiter_with_ttl):
        # TTL should not remove client that was recently used
        limiter_with_ttl.allow("client-1")
        # last_seen is now current time, so TTL shouldn't trigger
        usage = limiter_with_ttl.get_usage("client-1")
        assert usage["used"] == 1


class TestSlidingWindowRateLimiterEdgeCases:
    def test_window_purges_old_timestamps(self, monkeypatch):
        """Timestamps outside the window are purged, allowing new requests."""
        class MockTime:
            @staticmethod
            def monotonic():
                return 100.0

        import time as _time
        _time.monotonic = MockTime.monotonic

        r = SlidingWindowRateLimiter(window_size=10.0, max_requests=2, ttl=None)
        # Add two requests at t=100
        r.allow("client-1")
        r.allow("client-1")
        # Window is [100, 100], adding another at t=100 should fail
        assert r.allow("client-1") is False
        # Now move time to t=111 (outside window), adding a request should succeed
        _time.monotonic = lambda: 111.0
        # _purge_window runs on allow, so it should purge t=100 timestamps
        assert r.allow("client-1") is True

    def test_get_usage_nonexistent_client(self, limiter):
        usage = limiter.get_usage("unknown-client")
        assert usage["used"] == 0
        assert usage["remaining"] == 2
        assert usage["limit"] == 2
        assert usage["reset"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])