# test_api_monitor.py
# LLM API 异常分类测试：HTTP 状态码、Retry-After、退避重试、超时降级
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
import requests

import utils.api_monitor as api_monitor
from utils.api_monitor import CloudLLMClient


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


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        """初始化对象所需的状态和依赖。
        :param status_code: 传入 ``status_code`` 的业务数据。
        :param payload: 传入 ``payload`` 的业务数据。
        :param headers: 传入 ``headers`` 的业务数据。
        """
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        """执行 ``raise_for_status`` 对应的项目处理逻辑。"""
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        """执行 ``json`` 对应的项目处理逻辑。
        :return: 返回函数处理得到的结果。
        """
        return self._payload


def _content(result):
    """执行 ``_content`` 对应的项目处理逻辑。
    :param result: 工具调用或业务处理结果。
    :return: 返回函数处理得到的结果。
    """
    return result["choices"][0]["message"]["content"]


def _run_with(fake_post, max_retry=2):
    """执行 ``_run_with`` 对应的项目处理逻辑。
    :param fake_post: 传入 ``fake_post`` 的业务数据。
    :param max_retry: 传入 ``max_retry`` 的业务数据。
    :return: 返回函数处理得到的结果。
    """
    old_post = api_monitor.requests.post
    old_sleep = api_monitor.time.sleep
    sleeps = []
    api_monitor.requests.post = fake_post
    api_monitor.time.sleep = lambda seconds: sleeps.append(seconds)
    try:
        client = CloudLLMClient("key", "https://example.test/v1", "model", timeout=1, max_retry=max_retry)
        result = client.chat_completion([{"role": "user", "content": "hi"}])
        return result, sleeps
    finally:
        api_monitor.requests.post = old_post
        api_monitor.time.sleep = old_sleep


failures = 0

result, _ = _run_with(lambda **kwargs: FakeResponse(401), max_retry=1)
failures += _assert("密钥" in _content(result), "401 返回密钥失效提示")

result, _ = _run_with(lambda **kwargs: FakeResponse(402), max_retry=1)
failures += _assert("余额" in _content(result), "402 返回余额不足提示")

responses = [
    FakeResponse(429, headers={"Retry-After": "0"}),
    FakeResponse(200, payload={"choices": [{"message": {"content": "ok"}}]}),
]
result, sleeps = _run_with(lambda **kwargs: responses.pop(0), max_retry=2)
failures += _assert(_content(result) == "ok", "429 按 Retry-After 等待后重试成功")
failures += _assert(sleeps == [0], "429 使用 Retry-After 作为等待时间")

result, sleeps = _run_with(lambda **kwargs: FakeResponse(500), max_retry=3)
failures += _assert(_content(result) == "系统繁忙，请稍后再试", "5xx 超过重试上限返回系统繁忙")
failures += _assert(sleeps == [1, 2, 4], "5xx 使用 1/2/4 秒指数退避")

result, _ = _run_with(lambda **kwargs: (_ for _ in ()).throw(requests.exceptions.Timeout()), max_retry=1)
failures += _assert(_content(result) == "系统繁忙，请稍后再试", "网络超时独立捕获并降级")

print("=" * 60)
print(f"  通过: {7 - failures}  失败: {failures}  总计: 7")
raise SystemExit(1 if failures else 0)
