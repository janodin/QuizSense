"""
Redis-backed progress bridge for SSE streaming.

Celery tasks publish progress events to Redis; SSE views subscribe and stream
them to the browser. Falls back to in-memory dict if Redis is unavailable.
"""
import json
import logging
import threading
import time

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis
            url = getattr(settings, 'REDIS_URL', 'redis://localhost:6379/0')
            _redis_client = redis.from_url(url, decode_responses=True, socket_timeout=2)
            _redis_client.ping()
            return _redis_client
        except Exception as exc:
            logger.debug("Redis unavailable for SSE bridge: %s", exc)
            _redis_client = False
            return _redis_client


_in_memory_store: dict = {}
_in_memory_lock = threading.Lock()


def publish_progress(key: str, event_type: str, data: dict):
    """Publish a progress event. Uses Redis pub/sub if available, else in-memory."""
    payload = json.dumps({"type": event_type, "data": data, "ts": time.time()})
    r = _get_redis()
    if r:
        try:
            r.publish(f"qsse:{key}", payload)
            r.setex(f"qsse:latest:{key}", 120, payload)
            return
        except Exception as exc:
            logger.warning("Redis publish failed, falling back to in-memory: %s", exc)
    with _in_memory_lock:
        if key not in _in_memory_store:
            _in_memory_store[key] = []
        _in_memory_store[key].append(payload)
        _in_memory_store[key] = _in_memory_store[key][-50]


def get_latest_progress(key: str) -> dict | None:
    """Get the latest progress event for a key."""
    r = _get_redis()
    if r:
        try:
            val = r.get(f"qsse:latest:{key}")
            if val:
                return json.loads(val)
        except Exception:
            pass
    with _in_memory_lock:
        events = _in_memory_store.get(key, [])
        if events:
            return json.loads(events[-1])
    return None


def subscribe_progress(key: str, timeout: float = 120):
    """
    Generator that yields progress events for *key*.
    Yields a "heartbeat" every 15s to keep the SSE connection alive.
    """
    r = _get_redis()
    if r:
        try:
            pubsub = r.pubsub()
            pubsub.subscribe(f"qsse:{key}")
            start = time.time()
            last_heartbeat = start
            for message in pubsub.listen():
                if time.time() - start > timeout:
                    break
                if message and message.get("type") == "message":
                    yield message["data"]
                    last_heartbeat = time.time()
                elif time.time() - last_heartbeat > 15:
                    yield json.dumps({"type": "heartbeat", "data": {}, "ts": time.time()})
                    last_heartbeat = time.time()
            pubsub.unsubscribe(f"qsse:{key}")
            pubsub.close()
            return
        except Exception as exc:
            logger.warning("Redis subscribe failed, falling back to in-memory: %s", exc)

    yield json.dumps({"type": "fallback", "data": {"message": "Streaming via fallback channel"}, "ts": time.time()})

    start = time.time()
    last_heartbeat = start
    last_index = 0
    while time.time() - start < timeout:
        with _in_memory_lock:
            events = _in_memory_store.get(key, [])
        for i in range(last_index, len(events)):
            yield events[i]
            last_index = i + 1
            last_heartbeat = time.time()
        if time.time() - last_heartbeat > 15:
            yield json.dumps({"type": "heartbeat", "data": {}, "ts": time.time()})
            last_heartbeat = time.time()
        time.sleep(0.5)
