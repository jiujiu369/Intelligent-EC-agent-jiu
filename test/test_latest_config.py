import os
import subprocess
import sys


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_project_env_overrides_stale_parent_environment():
    env = os.environ.copy()
    env.update({
        "AGENT_BASE_URL": "https://old.example/v1",
        "AGENT_MODEL_NAME": "old-model",
        "AGENT_RAG_FALLBACK_MODEL": "./models/old-minilm",
    })
    code = (
        "import config; "
        "print(config.get('API', 'base_url')); "
        "print(config.get('API', 'model_name')); "
        "print(config.get('RAG', 'fallback_model'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT_PATH,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    actual = result.stdout.splitlines()
    expected = [
        "https://api.apikl.ai/v1",
        "gpt-5.5",
        "BAAI/bge-small-zh-v1.5",
    ]
    if actual != expected:
        raise AssertionError(f"配置未以项目 .env 为准: {actual!r}")


if __name__ == "__main__":
    test_project_env_overrides_stale_parent_environment()
    print("[PASS] 项目 .env 覆盖父进程旧配置")
