import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

from utils.api_monitor import llm_client    
if __name__ == "__main__":
    resp = llm_client.chat_completion(
        messages=[{"role":"user","content":"你好"}],
        tools=None
    )
    print(resp["choices"][0]["message"]["content"])
