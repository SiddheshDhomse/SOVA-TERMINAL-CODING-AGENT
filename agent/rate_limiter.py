"""In-memory Sliding Window Rate Limiter with per-client request quotas and TTL expirations."""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, Optional


class RateLimiterError(Exception):
    """Base exception raised by the rate limiter."""


class RateLimitExceeded(RateLimiterError):
    """Raised when a client exceeds its rate limit."""


class SlidingWindowRateLimiter:
    """Rate limiter using a sliding window algorithm.

    Each client has its own window of timestamps. Requests outside the
    configured window duration are purged. If the number of requests in
    the current window exceeds the quota, the request is rejected.

    Parameters
    ----------
    window_size : float
        Duration of the sliding window in seconds. Defaults to 60.
    max_requests : int
        Maximum number of requests allowed per window per client. Defaults to 100.
    ttl : float, optional
        Time-to-live in seconds for stale window entries. If None, entries
        are never expired. Defaults to None.
    """

    def __init__(
        self,
        window_size: float = 60.0,
        max_requests: int = 100,
        ttl: Optional[float] = None,
    ) -> None:
        if window_size <= 0:
            raise ValueError("window_size must be a positive number")
        if max_requests <= 0:
            raise ValueError("max_requests must be a positive integer")
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be a positive number if provided")

        self.window_size: float = window_size
        self.max_requests: int = max_requests
        self.ttl: Optional[float] = ttl
        # client_id -> deque of request timestamps (floats)
        self._windows: Dict[str, Deque[float]] = {}
        # client_id -> last seen timestamp (for TTL cleanup)
        self._last_seen: Dict[str, float] = {}

    def _purge_window(self, client_id: str, now: float) -> None:
        """Remove timestamps older than the window from the client's window."""
        window = self._windows.get(client_id)
        if window is None:
            return
        # Remove timestamps that are outside the window
        cutoff = now - self.window_size
        while window and window[0] <= cutoff:
            window.popleft()

    def _cleanup_ttl(self, client_id: str, now: float) -> None:
        """Remove entire window entry if it hasn't been seen within TTL."""
        last = self._last_seen.get(client_id)
        if last is not None and now - last > self.ttl:
            self._windows.pop(client_id, None)
            self._last_seen.pop(client_id, None)

    def allow(self, client_id: str) -> bool:
        """Check if a request from *client_id* is allowed.

        Updates the internal window with the current timestamp.
        Returns ``True`` if the request is within the rate limit, otherwise
        ``False`` (the :class:`RateLimitExceeded` exception may be raised
        by callers).

        Parameters
        ----------
        client_id : str
            Identifier of the client making the request.

        Returns
        -------
        bool
            ``True`` if the request is allowed, ``False`` otherwise.
        """
        now = time.monotonic()
        self._last_seen[client_id] = now

        # TTL cleanup of stale entries
        if self.ttl is not None:
            self._cleanup_ttl(client_id, now)

        # Purge old timestamps from the window
        self._purge_window(client_id, now)

        window = self._windows.setdefault(client_id, deque())
        # Check if adding this request would exceed the quota
        if len(window) >= self.max_requests:
            return False
        window.append(now)
        return True

    def check(self, client_id: str) -> bool:
        """Check if a request from *client_id* is allowed without consuming quota.

        This method inspects the current window state and returns ``True`` if
        the request would be allowed, but does **not** add a timestamp to the
        window. Use :meth:`allow` to actually record a request.

        Parameters
        ----------
        client_id : str
            Identifier of the client making the request.

        Returns
        -------
        bool
            ``True`` if the request is within the rate limit, ``False`` otherwise.
        """
        now = time.monotonic()
        # Purge old timestamps without modifying the window
        window = self._windows.get(client_id)
        if window is None:
            return True
        cutoff = now - self.window_size
        # Count entries within the window
        count = sum(1 for ts in window if ts > cutoff)
        return count < self.max_requests

    def reset(self, client_id: Optional[str] = None) -> None:
        """Reset the rate limit state.

        If *client_id* is provided, only that client's window is reset.
        Otherwise, all windows are cleared.
        """
        if client_id is None:
            self._windows.clear()
            self._last_seen.clear()
        else:
            self._windows.pop(client_id, None)
            self._last_seen.pop(client_id, None)

    def get_usage(self, client_id: str) -> dict:
        """Return current window usage statistics for *client_id*.

        Returns a dictionary with:
        - ``"limit"``: maximum requests per window
        - ``"used"``: number of requests currently in the window
        - ``"remaining"``: ``limit - used``
        - ``"reset"``: timestamp when the current window resets (earliest
          timestamp in the window, or ``None`` if the window is empty)
        """
        window = self._windows.get(client_id)
        if window is None:
            return {
                "limit": self.max_requests,
                "used": 0,
                "remaining": self.max_requests,
                "reset": None,
            }
        self._purge_window(client_id, time.monotonic())
        used = len(window)
        reset_time = window[0] if window else None
        return {
            "limit": self.max_requests,
            "used": used,
            "remaining": max(0, self.max_requests - used),
            "reset": reset_time,
        }