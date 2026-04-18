"""
Redis client — single connection pool shared across the process.
All cache helpers live here: get, set, delete, with JSON serialization.
"""
import json
import redis
from app.core.config import settings

_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


# ── Helpers ────────────────────────────────────────────────────────────────────

def cache_get(key: str):
    """Return deserialized value or None if missing/error."""
    try:
        raw = get_redis().get(key)
        return json.loads(raw) if raw is not None else None
    except Exception:
        return None


def cache_set(key: str, value, ttl_seconds: int) -> None:
    """Serialize and store value with TTL. Silently fails if Redis is down."""
    try:
        get_redis().setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception:
        pass


def cache_delete(*keys: str) -> None:
    """Delete one or more keys. Silently fails if Redis is down."""
    try:
        if keys:
            get_redis().delete(*keys)
    except Exception:
        pass
