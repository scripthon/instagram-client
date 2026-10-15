"""Typed errors so callers (the scraper) can map them to the contract."""

from __future__ import annotations


class ClientError(Exception):
    """Base error for the Instagram client."""


class NotFoundError(ClientError):
    """The requested object (user, post, media) does not exist on Instagram."""


class StatusError(ClientError):
    """Instagram answered with a non-2xx status the client did not expect.

    The scraper maps ``status`` through the contract: 401/403 -> 503
    UPSTREAM_BLOCKED, 429 -> 429 UPSTREAM_RATE_LIMITED, the rest -> 502.
    """

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"Instagram returned HTTP {status}")
        self.status = status
