"""Re-export shared rate limiter (used by BybitConnector)."""

from matrix_shared.rate_limiter import TokenBucket

__all__ = ["TokenBucket"]
