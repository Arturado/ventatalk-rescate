from cachetools import TTLCache
from threading import Lock

_hospitales_cache: TTLCache = TTLCache(maxsize=64, ttl=300)  # 5 minutos
_stats_cache: TTLCache = TTLCache(maxsize=16, ttl=60)        # 1 minuto
_lock = Lock()


def cache_get(cache: TTLCache, key: str):
    with _lock:
        return cache.get(key)


def cache_set(cache: TTLCache, key: str, value):
    with _lock:
        cache[key] = value


def cache_clear(cache: TTLCache):
    with _lock:
        cache.clear()
