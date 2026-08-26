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
        """初始化对象所需的状态和依赖。
        :param max_calls: 指定时间窗口内允许的最大调用次数。
        :param window_seconds: 限流统计窗口的秒数。
        :param time_func: 传入 ``time_func`` 的业务数据。
        :param sleep_func: 传入 ``sleep_func`` 的业务数据。
        """
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.time_func = time_func
        self.sleep_func = sleep_func
        self._lock = threading.Lock()
        self._window_start = self.time_func()
        self._used = 0

    def acquire(self) -> None:
        """检查限流窗口并等待，直至本次调用获得执行许可。"""
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
    """为函数添加通用调用频率限制。
    :param func: 需要调用或装饰的函数。
    :return: 返回函数处理得到的结果。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        """执行 ``wrapper`` 对应的项目处理逻辑。
        :param args: 传递给被包装函数的位置参数。
        :param kwargs: 传递给被包装函数的关键字参数。
        :return: 返回函数处理得到的结果。
        """
        _API_RATE_LIMITER.acquire()
        return func(*args, **kwargs)

    return wrapper


# 登录专用限流器：比 API 更严格，防止公网暴力破解
# 60 秒内最多 10 次登录尝试（含成功与失败），超出则阻塞到下一个窗口
_LOGIN_RATE_LIMITER = TokenBucketRateLimiter(max_calls=10, window_seconds=60)


def rate_limit_login(func: Callable) -> Callable:
    """为登录函数添加独立的调用频率限制。
    :param func: 需要调用或装饰的函数。
    :return: 返回函数处理得到的结果。
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        """执行 ``wrapper`` 对应的项目处理逻辑。
        :param args: 传递给被包装函数的位置参数。
        :param kwargs: 传递给被包装函数的关键字参数。
        :return: 返回函数处理得到的结果。
        """
        _LOGIN_RATE_LIMITER.acquire()
        return func(*args, **kwargs)

    return wrapper


def _make_cache_key(func_name: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Tuple[str, str]:
    """根据函数调用参数生成稳定的缓存键。
    :param func_name: 工具或函数名称。
    :param args: 传递给被包装函数的位置参数。
    :param kwargs: 传递给被包装函数的关键字参数。
    :return: 返回函数处理得到的结果。
    """
    raw = json.dumps({"args": args, "kwargs": kwargs}, ensure_ascii=False, sort_keys=True, default=str)
    return func_name, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ttl_cache(ttl_seconds: int, time_func: Callable[[], float] = time.time) -> Callable:
    """为函数返回值添加带过期时间的内存缓存。
    :param ttl_seconds: 缓存结果的有效秒数。
    :param time_func: 传入 ``time_func`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    def decorator(func: Callable) -> Callable:
        """执行 ``decorator`` 对应的项目处理逻辑。
        :param func: 需要调用或装饰的函数。
        :return: 返回函数处理得到的结果。
        """
        store: Dict[Tuple[str, str], Tuple[float, Any]] = {}
        lock = threading.Lock()

        @functools.lru_cache(maxsize=512)
        def _cached(key: Tuple[str, str]) -> Any:
            """执行 ``_cached`` 对应的项目处理逻辑。
            :param key: 用于定位配置、缓存或数据项的键。
            :return: 返回函数处理得到的结果。
            """
            return store[key][1]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """执行 ``wrapper`` 对应的项目处理逻辑。
            :param args: 传递给被包装函数的位置参数。
            :param kwargs: 传递给被包装函数的关键字参数。
            :return: 返回函数处理得到的结果。
            """
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
    """清空指定函数关联的 TTL 缓存。
    :param store: 传入 ``store`` 的业务数据。
    :param cached_func: 传入 ``cached_func`` 的业务数据。
    :param lock: 传入 ``lock`` 的业务数据。
    """
    with lock:
        store.clear()
        cached_func.cache_clear()


cache_query_goods = ttl_cache(ttl_seconds=5 * 60)
cache_rag_search = ttl_cache(ttl_seconds=10 * 60)


class RepeatedQueryDeduplicator:
    def __init__(self, ttl_seconds: int = 5, time_func: Callable[[], float] = time.time):
        """初始化对象所需的状态和依赖。
        :param ttl_seconds: 缓存结果的有效秒数。
        :param time_func: 传入 ``time_func`` 的业务数据。
        """
        self.ttl_seconds = ttl_seconds
        self.time_func = time_func
        self._lock = threading.Lock()
        self._items: Dict[Tuple[str, str], Tuple[float, str]] = {}

    def get(self, session_name: str, query: str) -> Optional[str]:
        """根据键读取配置项、缓存项或集合数据。
        :param session_name: 用于隔离上下文的会话名称。
        :param query: 传入 ``query`` 的业务数据。
        :return: 返回函数处理得到的结果。
        """
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
        """将问题和回答写入当前会话的重复查询缓存。
        :param session_name: 用于隔离上下文的会话名称。
        :param query: 传入 ``query`` 的业务数据。
        :param answer: 待检查或处理的回答文本。
        """
        key = self._key(session_name, query)
        with self._lock:
            self._items[key] = (self.time_func(), answer)

    def clear(self) -> None:
        """执行 ``clear`` 对应的项目处理逻辑。"""
        with self._lock:
            self._items.clear()

    @staticmethod
    def _key(session_name: str, query: str) -> Tuple[str, str]:
        """生成规范化的重复问题缓存键。
        :param session_name: 用于隔离上下文的会话名称。
        :param query: 传入 ``query`` 的业务数据。
        :return: 返回函数处理得到的结果。
        """
        return session_name or "-", (query or "").strip()


_QUERY_DEDUP = RepeatedQueryDeduplicator(ttl_seconds=5)


def get_repeated_query_answer(session_name: str, query: str) -> Optional[str]:
    """读取同一会话中重复问题的缓存回答。
    :param session_name: 用于隔离上下文的会话名称。
    :param query: 传入 ``query`` 的业务数据。
    :return: 返回完成读取、构建或转换后的结果。
    """
    return _QUERY_DEDUP.get(session_name, query)


def remember_query_answer(session_name: str, query: str, answer: str) -> None:
    """缓存指定会话的最新问题与回答。
    :param session_name: 用于隔离上下文的会话名称。
    :param query: 传入 ``query`` 的业务数据。
    :param answer: 待检查或处理的回答文本。
    """
    _QUERY_DEDUP.remember(session_name, query, answer)


def clear_all_caches() -> None:
    """清空限流模块维护的全部查询缓存。"""
    _QUERY_DEDUP.clear()
