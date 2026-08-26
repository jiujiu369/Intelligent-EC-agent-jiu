import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

import importlib


def _assert(condition, label):
    """记录测试断言结果，并输出对应的通过或失败信息。
    :param condition: 断言是否成立的布尔条件。
    :param label: 用于日志或测试输出的说明标签。
    :return: 返回函数处理得到的结果。
    """
    if condition:
        print(f"  [PASS] {label}")
        return 0
    print(f"  [FAIL] {label}")
    return 1


failures = 0

rate_limiter = importlib.import_module("utils.rate_limiter")
rate_limiter.clear_all_caches()


class FakeClock:
    def __init__(self):
        """初始化对象所需的状态和依赖。"""
        self.now = 1000.0
        self.slept = []

    def time(self):
        """执行 ``time`` 对应的项目处理逻辑。
        :return: 返回函数处理得到的结果。
        """
        return self.now

    def sleep(self, seconds):
        """执行 ``sleep`` 对应的项目处理逻辑。
        :param seconds: 传入 ``seconds`` 的业务数据。
        """
        self.slept.append(round(seconds, 3))
        self.now += seconds


clock = FakeClock()
limiter = rate_limiter.TokenBucketRateLimiter(max_calls=2, window_seconds=60, time_func=clock.time, sleep_func=clock.sleep)
limiter.acquire()
limiter.acquire()
limiter.acquire()
failures += _assert(clock.slept == [60.0], "令牌桶无令牌时等待到窗口刷新")


calls = {"count": 0}


@rate_limiter.ttl_cache(ttl_seconds=10, time_func=clock.time)
def cached_query(value):
    """执行 ``cached_query`` 对应的项目处理逻辑。
    :param value: 需要转换、缓存或检查的值。
    :return: 返回函数处理得到的结果。
    """
    calls["count"] += 1
    return {"value": value, "count": calls["count"]}


first = cached_query("SP001")
second = cached_query("SP001")
failures += _assert(first == second and calls["count"] == 1, "TTL 内相同参数返回缓存")
clock.now += 11
third = cached_query("SP001")
failures += _assert(third["count"] == 2, "TTL 过期后重新计算")


llm_calls = {"count": 0}


def ask_llm(query):
    """执行 ``ask_llm`` 对应的项目处理逻辑。
    :param query: 传入 ``query`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    llm_calls["count"] += 1
    return f"answer-{llm_calls['count']}:{query}"


rate_limiter._QUERY_DEDUP.time_func = clock.time
rate_limiter._QUERY_DEDUP.clear()
dedup_1 = rate_limiter.get_repeated_query_answer("对话一", "查订单")
if dedup_1 is None:
    dedup_1 = ask_llm("查订单")
    rate_limiter.remember_query_answer("对话一", "查订单", dedup_1)
dedup_2 = rate_limiter.get_repeated_query_answer("对话一", "查订单")
failures += _assert(dedup_1 == dedup_2 and llm_calls["count"] == 1, "5 秒内重复 query 命中缓存")
clock.now += 6
failures += _assert(rate_limiter.get_repeated_query_answer("对话一", "查订单") is None, "重复 query 超过 5 秒后失效")


print("=" * 60)
print(f"  通过: {5 - failures}  失败: {failures}  总计: 5")
raise SystemExit(1 if failures else 0)
