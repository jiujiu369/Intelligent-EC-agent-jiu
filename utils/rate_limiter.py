import functools
import hashlib
import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class TokenBucketRateLimiter:
    def __init__(
        self,
        max_calls: int = 30,
        window_seconds: int = 60,
        time_func: Callable[[], float] = time.time,
        sleep_func: Callable[[float], None] = time.sleep,
    ):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.time_func = time_func
        self.sleep_func = sleep_func
        self._lock = threading.Lock()
        self._window_start = self.time_func()
        self._used = 0

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self.time_func()
                elapsed = now - self._window_start
                if elapsed >= self.window_seconds:
                    self._window_start = now
                    self._used = 0
                if self._used < self.max_calls:
                    self._used += 1
                    return
                wait_seconds = max(0.0, self.window_seconds - elapsed)
            self.sleep_func(wait_seconds)


_API_RATE_LIMITER = TokenBucketRateLimiter(max_calls=30, window_seconds=60)


def rate_limit(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _API_RATE_LIMITER.acquire()
        return func(*args, **kwargs)

    return wrapper


# 登录专用限流器：比 API 更严格，防止公网暴力破解
# 60 秒内最多 10 次登录尝试（含成功与失败），超出则阻塞到下一个窗口
_LOGIN_RATE_LIMITER = TokenBucketRateLimiter(max_calls=10, window_seconds=60)


def rate_limit_login(func: Callable) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _LOGIN_RATE_LIMITER.acquire()
        return func(*args, **kwargs)

    return wrapper


def _make_cache_key(func_name: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Tuple[str, str]:
    raw = json.dumps({"args": args, "kwargs": kwargs}, ensure_ascii=False, sort_keys=True, default=str)
    return func_name, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ttl_cache(ttl_seconds: int, time_func: Callable[[], float] = time.time) -> Callable:
    def decorator(func: Callable) -> Callable:
        store: Dict[Tuple[str, str], Tuple[float, Any]] = {}
        lock = threading.Lock()

        @functools.lru_cache(maxsize=512)
        def _cached(key: Tuple[str, str]) -> Any:
            return store[key][1]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = _make_cache_key(func.__name__, args, kwargs)
            now = time_func()
            with lock:
                cached = store.get(key)
                if cached and now - cached[0] <= ttl_seconds:
                    return _cached(key)
            result = func(*args, **kwargs)
            with lock:
                store[key] = (now, result)
                _cached.cache_clear()
            return result

        wrapper.cache_clear = lambda: _clear_ttl_cache(store, _cached, lock)  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _clear_ttl_cache(store: Dict, cached_func: Callable, lock: threading.Lock) -> None:
    with lock:
        store.clear()
        cached_func.cache_clear()


cache_query_goods = ttl_cache(ttl_seconds=5 * 60)
cache_rag_search = ttl_cache(ttl_seconds=10 * 60)


class RepeatedQueryDeduplicator:
    def __init__(self, ttl_seconds: int = 5, time_func: Callable[[], float] = time.time):
        self.ttl_seconds = ttl_seconds
        self.time_func = time_func
        self._lock = threading.Lock()
        self._items: Dict[Tuple[str, str], Tuple[float, str]] = {}

    def get(self, session_name: str, query: str) -> Optional[str]:
        key = self._key(session_name, query)
        now = self.time_func()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            created_at, answer = item
            if now - created_at > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            return answer

    def remember(self, session_name: str, query: str, answer: str) -> None:
        key = self._key(session_name, query)
        with self._lock:
            self._items[key] = (self.time_func(), answer)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @staticmethod
    def _key(session_name: str, query: str) -> Tuple[str, str]:
        return session_name or "-", (query or "").strip()


_QUERY_DEDUP = RepeatedQueryDeduplicator(ttl_seconds=5)


def get_repeated_query_answer(session_name: str, query: str) -> Optional[str]:
    return _QUERY_DEDUP.get(session_name, query)


def remember_query_answer(session_name: str, query: str, answer: str) -> None:
    _QUERY_DEDUP.remember(session_name, query, answer)


def clear_all_caches() -> None:
    _QUERY_DEDUP.clear()
