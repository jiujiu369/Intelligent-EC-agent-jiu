"""在可强制终止的独立进程中执行一次 Agent 请求。"""


def run_agent_process(result_queue, message, session, role, username):
    """执行 Agent，并将成功结果或异常文本写入进程队列。"""
    try:
        from agent.main_agent import run_agent

        answer = run_agent(
            message,
            session_name=session,
            current_role=role,
            current_username=username,
        )
        result_queue.put(("ok", answer))
    except Exception as exc:
        result_queue.put(("error", str(exc)))
