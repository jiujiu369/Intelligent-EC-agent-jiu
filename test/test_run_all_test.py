# test_run_all_test.py
# 批量测试启动脚本的发现、执行和汇总逻辑测试。
import os
import sys
import tempfile

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import run_all_test


_pass, _fail = 0, 0


def _assert(condition, label):
    """记录测试断言结果。"""
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}")


def test_discover_tests_sorted():
    """验证只发现 test*.py 并按文件名排序。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir = os.path.join(tmpdir, "test")
        os.makedirs(test_dir, exist_ok=True)
        for filename in ("test_b.py", "demo.py", "test_a.py"):
            with open(os.path.join(test_dir, filename), "w", encoding="utf-8") as f:
                f.write("print('ok')\n")
        found = [os.path.basename(path) for path in run_all_test.discover_tests(test_dir)]
        _assert(found == ["test_a.py", "test_b.py"], "只发现 test*.py 且按文件名排序")


def test_run_test_file_status():
    """验证子测试脚本退出码会转换为 PASS 或 FAIL。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        pass_file = os.path.join(tmpdir, "test_pass.py")
        fail_file = os.path.join(tmpdir, "test_fail.py")
        with open(pass_file, "w", encoding="utf-8") as f:
            f.write("print('pass case')\n")
        with open(fail_file, "w", encoding="utf-8") as f:
            f.write("raise SystemExit(3)\n")

        pass_result = run_all_test.run_test_file(pass_file, cwd=tmpdir)
        fail_result = run_all_test.run_test_file(fail_file, cwd=tmpdir)

        _assert(pass_result["status"] == "PASS", "退出码 0 标记为 PASS")
        _assert(fail_result["status"] == "FAIL", "非 0 退出码标记为 FAIL")


print("=" * 60)
print("  test_run_all_test.py")
print("=" * 60)
test_discover_tests_sorted()
test_run_test_file_status()
print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
