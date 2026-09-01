# run_all_test.py
# 一键批量运行 test 目录下的所有 test*.py 文件。
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DIR = PROJECT_ROOT / "test"


def _configure_output() -> None:
    """配置测试运行时的终端字符编码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def discover_tests(test_dir: Optional[os.PathLike] = None) -> List[Path]:
    """按稳定顺序发现目录中的 test*.py 测试脚本。"""
    target_dir = Path(test_dir) if test_dir is not None else TEST_DIR
    if not target_dir.exists():
        return []
    return sorted(
        path for path in target_dir.glob("test*.py")
        if path.is_file()
    )


def run_test_file(test_file: os.PathLike, cwd: Optional[os.PathLike] = None) -> Dict[str, object]:
    """在独立子进程中运行单个测试脚本并收集结果。"""
    path = Path(test_file)
    workdir = Path(cwd) if cwd is not None else PROJECT_ROOT
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    start_time = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.perf_counter() - start_time, 2)
    return {
        "file": str(path),
        "name": path.name,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "returncode": completed.returncode,
        "duration": duration,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def print_result(result: Dict[str, object]) -> None:
    """打印单个测试脚本的运行结果。"""
    print("=" * 70)
    print(f"[{result['status']}] {result['name']}  耗时: {result['duration']}s")
    print("=" * 70)
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    if stdout:
        print(stdout)
    if stderr:
        print("\n[stderr]")
        print(stderr)


def main() -> int:
    """发现并依次执行全部测试，最后输出汇总。"""
    _configure_output()
    tests = discover_tests(TEST_DIR)
    if not tests:
        print(f"未找到测试文件: {TEST_DIR}")
        return 1

    print(f"发现 {len(tests)} 个测试文件，开始批量执行。")
    results = []
    for test_file in tests:
        result = run_test_file(test_file, cwd=PROJECT_ROOT)
        results.append(result)
        print_result(result)

    passed = sum(1 for item in results if item["status"] == "PASS")
    failed = len(results) - passed
    print("=" * 70)
    print("批量测试汇总")
    print("=" * 70)
    for item in results:
        print(f"{item['status']:4}  {item['name']}  {item['duration']}s")
    print("-" * 70)
    print(f"总计: {len(results)}  通过: {passed}  失败: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
