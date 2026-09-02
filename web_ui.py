# web_ui.py
# 电商客服 Agent — Gradio Web UI 入口
# 复用 CLI 全部业务逻辑（run_agent / login_user / 会话管理），仅做界面层封装
# 启动：python web_ui.py  →  浏览器访问 http://localhost:7860
#重启
"""
找到占用 7860 的进程
netstat -ano | findstr :7860

用显示的 PID 杀掉它（把 <PID> 换成实际数字）
taskkill /PID <PID> /F

再重新启动
python web_ui.py

"""
import os
import sys
import multiprocessing
import queue
import threading
import time
import uuid

ROOT_PATH = os.path.abspath(os.path.dirname(__file__))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import gradio as gr

from main_agent import (
    run_agent,
    run_agent_worker,
    list_sessions,
    clear_memory,
    clear_all_memory,
    save_memory,
    load_memory,
    get_recent_chat_records,
    normalize_session_name,
    _next_auto_session,
    DEFAULT_SESSION,
)
from tools.auth_login import (
    init_auth_files,
    login_user,
    register_user,
    get_security_question,
    reset_password,
    change_password,
    set_security_question,
    ROLE_CONSUMER,
    ROLE_MERCHANT,
    SECURITY_QUESTIONS,
)
from utils.logger import get_logger, set_console_logging_enabled
from embedding import rag_pipeline

API_KEY_WARN = """⚠️ 未检测到 API 密钥！请在项目根目录 .env 文件中配置：

AGENT_API_KEY=sk-...

或在启动前设置环境变量（PowerShell）：
$env:AGENT_API_KEY="sk-..."

当前 Agent 只能返回��底回复，无法调用大模型。"""

CONSUMER_GUIDE = """### 👤 消费者使用说明

- **商品查询**：查询商品信息，例如：`查询 SP001 的商品信息`
- **订单查询**：仅查询当前账号自己的订单，例如：`查询 DD001`
- **售后工单**：为订单创建售后工单，例如：`为订单 DD001 创建售后工单，问题类型为退换货`
- **知识库查询**：解答售后政策、活动规则等问题，例如：`七天无理由退货有什么要求`
"""

MERCHANT_GUIDE = """### 🏪 商家使用说明

- **商品管理**：查询商品信息、修改价格/规格/上架状态等，例如：`把 SP001 的售价改为 99 元`
- **库存查询**：查看商品库存数量，例如：`查询 SP001 的库存`
- **订单查询**：根据订单号、商品或时间范围查询订单，例如：`查询 DD001`、`查询本月订单`
- **售后工单**：为订单创建售后工单，例如：`为订单 DD001 创建售后工单，问题类型为退换货`
- **销售报表**：生成指定时间段的销售统计，例如：`导出本月销售报表`
- **知识库查询**：解答售后政策、活动规则等问题，例如：`查询双十一活动规则`
- 运维面板：登录后展开「运维演示」，点击`刷新运维面板`查看主库状态
"""



set_console_logging_enabled(False)
logger = get_logger(__name__)
init_auth_files()

CHAT_TIMEOUT_SECONDS = 60
_CHAT_PROCESS_CONTEXT = multiprocessing.get_context("spawn")
_CHAT_REQUEST_LOCK = threading.Lock()
_ACTIVE_CHAT_REQUESTS = {}
_FINISHED_CHAT_REQUESTS = set()
_CHAT_REQUEST_KEYS = {}

# ====================== 运维演示 ======================

def refresh_ops_panel(state):
    """刷新运维演示面板：ChromaDB 状态、API 配置、RAG 参数。仅商家可查看。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    import config
    if not state or not state.get("username") or state.get("role") != ROLE_MERCHANT:
        return "仅商家角色可查看运维面板"

    lines = []
    primary_ok = not rag_pipeline.chroma_connection_failed and rag_pipeline.collection is not None

    lines.append("### 📊 向量库状态")
    primary_count = None

    if primary_ok:
        try:
            primary_count = rag_pipeline.collection.count()
        except Exception:
            pass
    primary_status = "✅ 正常" if primary_ok and primary_count else "⚠️ 未入库" if primary_ok else "❌ 不可用"
    primary_count_text = f"，{primary_count} 条向量" if primary_count is not None else ""
    lines.append(f"- **主库**: {primary_status}（512 维{primary_count_text}）")

    api_key = config.get("API", "api_key")
    lines.append("")
    lines.append("### 🔧 API 配置")
    lines.append(f"- 模型: `{config.get('API', 'model_name')}`")
    lines.append(f"- 地址: `{config.get('API', 'base_url')}`")
    lines.append(f"- 超时: {config.get('API', 'timeout')}s")
    lines.append(f"- API Key: {'✅ 已配置' if api_key and api_key.strip() not in ('', 'your_api_key_here') else '⚠️ 未配置'}")

    lines.append("")
    lines.append("### 🚦 速率限制")
    lines.append("- API 调用: 30 次 / 60 秒")
    lines.append("- 登录尝试: 10 次 / 60 秒")

    lines.append("")
    lines.append("### 📚 RAG 参数")
    lines.append(f"- 分块大小: {config.get('RAG', 'chunk_size')}")
    lines.append(f"- 重叠: {config.get('RAG', 'chunk_overlap')}")
    lines.append(f"- 距离阈值: {config.get('RAG', 'distance_threshold')}")
    primary_model = os.path.basename(os.path.normpath(config.get("RAG", "embedding_model")))
    lines.append(f"- 主模型: {primary_model} (512 维)")

    return "\n".join(lines)


# ====================== 辅助函数 ======================

def _role_label(role: str) -> str:
    """将内部角色编码转换为界面显示名称。
    :param role: 当前用户角色。
    :return: 返回函数处理得到的结果。
    """
    if role == ROLE_CONSUMER:
        return "消费者"
    if role == ROLE_MERCHANT:
        return "商家"
    return "未知角色"


def _role_from_radio(role_radio: str):
    """将界面角色选项映射为认证模块角色编码。
    :param role_radio: 界面角色选择控件提交的值。
    :return: 返回认证模块使用的角色编码。
    """
    mapping = {
        "消费者": ROLE_CONSUMER,
        "商家": ROLE_MERCHANT,
    }
    return mapping.get(role_radio)


def _account_result(ok: bool, message: str) -> str:
    """为账号安全操作统一补充成功或失败图标。
    :param ok: 认证操作是否成功。
    :param message: 认证模块返回的说明文本。
    :return: 返回可直接展示的界面提示。
    """
    return f"{'✅' if ok else '❌'} {message}"


def _usage_guide(role: str) -> str:
    """返回当前角色可执行操作的简要说明。
    :param role: 当前用户角色。
    :return: 返回对应角色的 Markdown 使用说明。
    """
    if role == ROLE_CONSUMER:
        return CONSUMER_GUIDE
    if role == ROLE_MERCHANT:
        return MERCHANT_GUIDE
    return "❌ 无效角色"


def _load_history(session, username, role, limit=30):
    """加载会话历史，转为 Gradio messages 格式 [{"role":..,"content":..}, ...]。
    :param session: 当前会话名称或会话数据。
    :param username: 用户登录名。
    :param role: 当前用户角色。
    :param limit: 最多读取或返回的记录数量。
    :return: 返回完成读取、构建或转换后的结果。
    """
    records = get_recent_chat_records(session, username=username, role=role, limit=limit)
    return [{"role": r["role"], "content": r["content"]} for r in records]


def _status_text(state):
    """根据登录状态生成界面顶部的状态文本。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    if not state or not state.get("username"):
        return "未登录"
    return (f"角色：{_role_label(state['role'])}　|　"
            f"用户：{state['username']}　|　会话：{state['session']}")


# ====================== 回调函数 ======================

def do_login(role_radio, username, password):
    """登录：成功切换到主界面，失败提示。检测 API Key 是否已配置。
    :param role_radio: 界面角色选择控件提交的值。
    :param username: 用户登录名。
    :param password: 用户提供的登录密码。
    :return: 返回函数处理得到的结果。
    """
    import config
    role = _role_from_radio(role_radio)
    if role is None:
        return (
            gr.update(visible=True), gr.update(visible=False),
            "❌ 请选择有效角色", {}, [], gr.update(choices=[], value=None),
            "未登录", "", "", "", gr.update(visible=False),
        )
    username = (username or "").strip()
    ok, msg, r = login_user(role, username, password or "")
    if not ok:
        return (
            gr.update(visible=True), gr.update(visible=False),
            msg, {}, [], gr.update(choices=[], value=None), "未登录", "", "", "",
            gr.update(visible=False),
        )
    state = {"role": r, "username": username, "session": DEFAULT_SESSION}
    sessions = list_sessions(username, r)
    history = _load_history(DEFAULT_SESSION, username, r)
    # API Key 空值检测
    api_key = config.get("API", "api_key")
    if not api_key or api_key.strip() in ("", "your_api_key_here"):
        warn = API_KEY_WARN
    else:
        warn = ""
    return (
        gr.update(visible=False), gr.update(visible=True),
        f"✅ {msg}", state, history,
        gr.update(choices=sessions, value=DEFAULT_SESSION),
        _status_text(state), warn, _usage_guide(r), "",
        gr.update(visible=(r == ROLE_MERCHANT)),
    )


def do_register(role_radio, username, password, security_question, security_answer):
    """注册新账号。
    :param role_radio: 界面角色选择控件提交的值。
    :param username: 用户登录名。
    :param password: 用户提供的登录密码。
    :param security_question: 用户选定的注册安全问题。
    :param security_answer: 用户填写的注册安全问题答案。
    :return: 返回函数处理得到的结果。
    """
    role = _role_from_radio(role_radio)
    cleared_password = gr.update(value="")
    cleared_answer = gr.update(value="")
    if role is None:
        return "❌ 请选择有效角色", cleared_password, cleared_answer
    username = (username or "").strip()
    ok, msg = register_user(
        role, username, password or "", security_question, security_answer or ""
    )
    return _account_result(ok, msg), cleared_password, cleared_answer


def do_get_security_question(role_radio, username):
    """查询找回密码所需的安全问题。
    :param role_radio: 界面角色选择控件提交的值。
    :param username: 待找回账号的用户名。
    :return: 返回图标化的安全问题或错误提示。
    """
    role = _role_from_radio(role_radio)
    if role is None:
        return "❌ 请选择有效角色"
    ok, message = get_security_question(role, (username or "").strip())
    return _account_result(ok, message)


def do_reset_password(role_radio, username, security_answer, new_password, confirm_password):
    """通过安全问题答案重设未登录账号的密码。
    :param role_radio: 界面角色选择控件提交的值。
    :param username: 待找回账号的用户名。
    :param security_answer: 用户输入的安全问题答案。
    :param new_password: 用户输入的新密码。
    :param confirm_password: 用户再次输入的新密码。
    :return: 返回图标化的密码重设结果。
    """
    cleared = (gr.update(value=""), gr.update(value=""), gr.update(value=""))
    role = _role_from_radio(role_radio)
    if role is None:
        return ("❌ 请选择有效角色", *cleared)
    ok, message = reset_password(
        role,
        (username or "").strip(),
        security_answer or "",
        new_password or "",
        confirm_password or "",
    )
    return (_account_result(ok, message), *cleared)


def do_change_password(old_password, new_password, confirm_password, state):
    """使用当前登录身份修改密码。
    :param old_password: 用户当前密码。
    :param new_password: 用户输入的新密码。
    :param confirm_password: 用户再次输入的新密码。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回图标化的密码修改结果。
    """
    cleared = (gr.update(value=""), gr.update(value=""), gr.update(value=""))
    if not state or not state.get("username"):
        return ("❌ 请先登录", *cleared)
    if state.get("role") not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return ("❌ 无效角色", *cleared)
    ok, message = change_password(
        state["role"],
        state["username"],
        old_password or "",
        new_password or "",
        confirm_password or "",
    )
    return (_account_result(ok, message), *cleared)


def do_set_security_question(current_password, question, answer, state):
    """为当前登录账号设置或更新安全问题。
    :param current_password: 用户当前密码。
    :param question: 选定的安全问题。
    :param answer: 用户输入的安全问题答案。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回图标化的安全问题设置结果。
    """
    cleared = (gr.update(value=""), gr.update(value=""))
    if not state or not state.get("username"):
        return ("❌ 请先登录", *cleared)
    if state.get("role") not in (ROLE_CONSUMER, ROLE_MERCHANT):
        return ("❌ 无效角色", *cleared)
    ok, message = set_security_question(
        state["role"], state["username"], current_password or "", question, answer or ""
    )
    return (_account_result(ok, message), *cleared)


def _terminate_chat_process(process):
    """终止并回收 Agent 工作进程。"""
    if process is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=1)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1)


def _claim_chat_terminal(request_id, status):
    """以原子方式声明请求终态，防止完成、终止和超时重复落地。"""
    with _CHAT_REQUEST_LOCK:
        if request_id in _FINISHED_CHAT_REQUESTS:
            return None
        request = _ACTIVE_CHAT_REQUESTS.pop(request_id, None)
        _FINISHED_CHAT_REQUESTS.add(request_id)
        request_key = None
        if request is not None:
            request_key = request.get("request_key")
        if request_key is None:
            for key, value in list(_CHAT_REQUEST_KEYS.items()):
                if value == request_id:
                    request_key = key
                    break
        if request_key is not None and _CHAT_REQUEST_KEYS.get(request_key) == request_id:
            _CHAT_REQUEST_KEYS.pop(request_key, None)
        if request is not None:
            request["status"] = status
        return request if request is not None else {}


def _append_notice_once(history, notice):
    """仅在聊天记录末尾尚无同一提示时追加助手消息。"""
    history = list(history or [])
    if not history or history[-1].get("role") != "assistant" or history[-1].get("content") != notice:
        history.append({"role": "assistant", "content": notice})
    return history


def _idle_chat_updates(state):
    """生成请求结束后的统一控件状态。"""
    return (
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(interactive=True),
        _status_text(state),
        {},
    )


def start_chat_request(message, history, state):
    """立即显示用户消息并切换到处理中状态。"""
    history = list(history or [])
    if not state or not state.get("username"):
        return (
            history, gr.update(value=message or "", interactive=True),
            gr.update(visible=True), gr.update(visible=False), "请先登录", {},
        )
    message = (message or "").strip()
    if not message:
        return (
            history, gr.update(value="", interactive=True),
            gr.update(visible=True), gr.update(visible=False), _status_text(state), {},
        )
    request_key = (state["role"], state["username"], state["session"])
    with _CHAT_REQUEST_LOCK:
        if request_key in _CHAT_REQUEST_KEYS:
            return (
                history, gr.update(value=message, interactive=False),
                gr.update(visible=False), gr.update(visible=True),
                "⏳ 当前请求仍在处理中，请勿重复提交", {},
            )
        request_id = uuid.uuid4().hex
        _CHAT_REQUEST_KEYS[request_key] = request_id
    request = {
        "request_id": request_id, "message": message, "request_key": request_key,
    }
    return (
        history + [{"role": "user", "content": message}],
        gr.update(value="", interactive=False),
        gr.update(visible=False),
        gr.update(visible=True),
        "⏳ 正在处理当前请求，可点击红色“终止”按钮",
        request,
    )


def _wait_for_chat_worker(message, state, request_id):
    """启动工作进程，并由后端强制执行 60 秒截止。"""
    result_queue = None
    process = None
    with _CHAT_REQUEST_LOCK:
        if request_id in _FINISHED_CHAT_REQUESTS:
            return "cancelled", None
        result_queue = _CHAT_PROCESS_CONTEXT.Queue()
        process = _CHAT_PROCESS_CONTEXT.Process(
            target=run_agent_worker,
            args=(
                result_queue, message, state["session"], state["role"],
                state["username"],
            ),
            daemon=True,
        )
        process.start()
        _ACTIVE_CHAT_REQUESTS[request_id] = {
            "process": process, "queue": result_queue, "status": "running",
            "request_key": (state["role"], state["username"], state["session"]),
        }

    deadline = time.monotonic() + CHAT_TIMEOUT_SECONDS
    try:
        while True:
            with _CHAT_REQUEST_LOCK:
                if request_id in _FINISHED_CHAT_REQUESTS:
                    return "cancelled", None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                request = _claim_chat_terminal(request_id, "timeout")
                if request is not None:
                    _terminate_chat_process(process)
                    return "timeout", None
                return "cancelled", None
            try:
                return result_queue.get(timeout=min(0.05, remaining))
            except queue.Empty:
                if not process.is_alive():
                    return "error", "Agent 工作进程异常退出"
    finally:
        _terminate_chat_process(process)
        if result_queue is not None:
            result_queue.close()
            result_queue.join_thread()


def _persist_completed_exchange(state, message, answer):
    """仅在父进程接受正常结果后提交本轮会话记忆。"""
    messages = load_memory(
        state["session"], username=state["username"], role=state["role"]
    )
    messages.extend([
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer},
    ])
    save_memory(
        messages, state["session"], username=state["username"], role=state["role"]
    )


def run_chat_request(history, state, request):
    """等待 Agent 结果，并统一处理完成、错误、手动终止和超时。"""
    request = request or {}
    request_id = request.get("request_id")
    message = request.get("message", "")
    if not request_id:
        send_update, stop_update, input_update, status, cleared = _idle_chat_updates(state)
        return history, send_update, stop_update, input_update, status, cleared
    try:
        result_type, payload = _wait_for_chat_worker(message, state, request_id)
        if result_type == "timeout":
            history = _append_notice_once(history, "请求已超时，已自动终止")
            status = "⏱️ 请求已超时，工作进程已终止并回收"
        elif result_type == "cancelled":
            history = _append_notice_once(history, "已终止")
            status = "⛔ 当前请求已终止"
        elif result_type == "ok":
            claimed = _claim_chat_terminal(request_id, "completed")
            if claimed is None:
                history = _append_notice_once(history, "已终止")
                status = "⛔ 当前请求已终止"
            else:
                answer = str(payload or "").strip()
                if not answer:
                    answer = "⚠️ 模型返回空内容，请稍后重试；本次请求未生成有效回答。"
                _persist_completed_exchange(state, message, answer)
                history = list(history or []) + [{"role": "assistant", "content": answer}]
                status = _status_text(state)
        else:
            _claim_chat_terminal(request_id, "error")
            error_text = f"⚠️ 处理出错：{payload}"
            history = list(history or []) + [{"role": "assistant", "content": error_text}]
            status = "⚠️ 请求处理失败"
    except Exception as exc:
        _claim_chat_terminal(request_id, "error")
        history = list(history or []) + [
            {"role": "assistant", "content": f"⚠️ 处理出错：{exc}"}
        ]
        status = "⚠️ 请求处理失败"
        logger.exception(f"Web UI chat 异常: {exc}")
    return (
        history, gr.update(visible=True), gr.update(visible=False),
        gr.update(interactive=True), status, {},
    )


def stop_chat_request(history, request):
    """手动终止当前请求并只追加一次终止提示。"""
    request_id = (request or {}).get("request_id")
    claimed = _claim_chat_terminal(request_id, "stopped") if request_id else None
    if claimed is not None:
        _terminate_chat_process(claimed.get("process"))
        history = _append_notice_once(history, "已终止")
    return (
        history, gr.update(visible=True), gr.update(visible=False),
        gr.update(interactive=True), "⛔ 当前请求已终止", {},
    )
def new_session(new_name, state):
    """新建会话（名称留空则自动编号）。
    :param new_name: 准备创建的新会话名称。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    if not state or not state.get("username"):
        return gr.update(), [], "请先登录", state
    name = (new_name or "").strip()
    if name:
        name = normalize_session_name(name)
    else:
        name = _next_auto_session(state["username"], state["role"])
    clear_memory(name, username=state["username"], role=state["role"])
    save_memory([], name, username=state["username"], role=state["role"])
    state = {**state, "session": name}
    sessions = list_sessions(state["username"], state["role"])
    return gr.update(choices=sessions, value=name), [], _status_text(state), state


def switch_session(selected, state):
    """切换到选定的会话。
    :param selected: 用户当前选中的会话名称。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    if not state or not state.get("username"):
        return [], "请先登录", state, gr.update()
    session = selected or DEFAULT_SESSION
    state = {**state, "session": session}
    history = _load_history(session, state["username"], state["role"])
    return history, _status_text(state), state, gr.update(value=session)


def clear_current(state):
    """清空当前会话记忆。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    if not state or not state.get("username"):
        return [], "请先登录", state
    clear_memory(state["session"], username=state["username"], role=state["role"])
    return [], f"已清空会话【{state['session']}】的记忆", state


def clear_all_sessions(state):
    """清空全部会话记忆。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    if not state or not state.get("username"):
        return [], "请先登录", state, gr.update()
    clear_all_memory(username=state["username"], role=state["role"])
    state = {**state, "session": DEFAULT_SESSION}
    sessions = list_sessions(state["username"], state["role"])
    return [], "已清空全部会话记忆", state, gr.update(choices=sessions, value=DEFAULT_SESSION)


def relogin():
    """重新登录：回到登录页，清空状态。
    :return: 返回函数处理得到的结果。
    """
    return (
        gr.update(visible=True), gr.update(visible=False),
        "", {}, [], gr.update(choices=[], value=None), "未登录", "", "", "",
        gr.update(visible=False),
        gr.update(value=""), gr.update(value=SECURITY_QUESTIONS[0]), gr.update(value=""),
        gr.update(value=""), "", gr.update(value=""), gr.update(value=""), gr.update(value=""),
        gr.update(value=""), gr.update(value=""), gr.update(value=""),
        gr.update(value=""), gr.update(value=SECURITY_QUESTIONS[0]), gr.update(value=""),
        "", "", "",
    )


def refresh_sessions(state):
    """刷新会话列表下拉框。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
    if not state or not state.get("username"):
        return gr.update()
    sessions = list_sessions(state["username"], state["role"])
    return gr.update(choices=sessions, value=state.get("session"))


# ====================== UI 构建 ======================

custom_css = """
.gradio-container {max-width: 1200px !important;}
#login-box {max-width: 440px; margin: 30px auto;}
.status-bar {
    font-size: 14px; color: #444; padding: 10px 14px;
    background: #f0f4f8; border-radius: 8px; border-left: 3px solid #3b82f6;
}
.chat-action {min-width: 96px !important;}
"""

with gr.Blocks(title="电商客服 Agent") as demo:
    gr.Markdown("# 🛒 电商客服 Agent")
    state = gr.State({})

    # ===== 登录视图 =====
    with gr.Row(visible=True) as login_view:
        with gr.Column(elem_id="login-box"):
            gr.Markdown("### 登录 / 注册")
            role_radio = gr.Radio(["消费者", "商家"], value="消费者", label="选择角色")
            username_box = gr.Textbox(label="用户名", placeholder="请输入用户名")
            password_box = gr.Textbox(label="密码", type="password", placeholder="请输入密码")
            with gr.Row():
                login_btn = gr.Button("登录", variant="primary")
                register_btn = gr.Button("注册")
            register_question = gr.Dropdown(
                SECURITY_QUESTIONS, value=SECURITY_QUESTIONS[0], label="注册安全问题"
            )
            register_answer = gr.Textbox(label="注册安全问题答案", type="password")
            login_status = gr.Markdown("")
            gr.Markdown("<small>请输入用户名和密码</small>")
            with gr.Accordion("🔐 找回密码", open=False):
                recover_role_radio = gr.Radio(["消费者", "商家"], value="消费者", label="账号角色")
                recover_username = gr.Textbox(label="用户名")
                recover_question = gr.Markdown("")
                recover_question_btn = gr.Button("查询安全问题", size="sm")
                recover_answer = gr.Textbox(label="安全问题答案", type="password")
                recover_new_password = gr.Textbox(label="新密码", type="password")
                recover_confirm_password = gr.Textbox(label="确认新密码", type="password")
                recover_btn = gr.Button("重设密码")
                recover_status = gr.Markdown("")

    # ===== 主界面视图 =====
    with gr.Row(visible=False) as main_view:
        # 左侧聊天区
        with gr.Column(scale=3):
            status_bar = gr.Markdown("未登录", elem_classes=["status-bar"])
            api_key_warn = gr.Markdown("")
            chatbot = gr.Chatbot(label="对话", height=480)
            with gr.Row():
                msg_box = gr.Textbox(
                    placeholder="输入客服问题，回车发送",
                    scale=5, show_label=False, autofocus=True,
                )
                send_btn = gr.Button(
                    "发送", variant="primary", scale=1, elem_classes=["chat-action"]
                )
                stop_btn = gr.Button(
                    "终止", variant="stop", scale=1, visible=False,
                    elem_classes=["chat-action"],
                )
            request_state = gr.State({})
        # 右侧会话管理
        with gr.Column(scale=1, min_width=260):
            with gr.Accordion("📖 使用说明", open=True):
                usage_guide = gr.Markdown("")
            with gr.Accordion("🔐 账号安全", open=False):
                gr.Markdown("#### 修改密码")
                change_old_password = gr.Textbox(label="当前密码", type="password")
                change_new_password = gr.Textbox(label="新密码", type="password")
                change_confirm_password = gr.Textbox(label="确认新密码", type="password")
                change_password_btn = gr.Button("修改密码", size="sm")
                change_password_status = gr.Markdown("")
                gr.Markdown("#### 设置安全问题")
                set_question_password = gr.Textbox(label="当前密码", type="password")
                set_question_dropdown = gr.Dropdown(
                    SECURITY_QUESTIONS, value=SECURITY_QUESTIONS[0], label="安全问题"
                )
                set_question_answer = gr.Textbox(label="安全问题答案", type="password")
                set_question_btn = gr.Button("保存安全问题", size="sm")
                set_question_status = gr.Markdown("")
            gr.Markdown("---")
            gr.Markdown("### 会话管理")
            session_dropdown = gr.Dropdown(label="会话列表", choices=[], interactive=True)
            switch_btn = gr.Button("切换到该会话")
            gr.Markdown("---")
            new_name_box = gr.Textbox(placeholder="新会话名称（可选）", show_label=False)
            new_btn = gr.Button("新建会话")
            refresh_btn = gr.Button("刷新列表")
            clear_cur_btn = gr.Button("清空当前记忆")
            clear_all_btn = gr.Button("清空所有记忆", variant="stop")
            relogin_btn = gr.Button("重新登录")
            gr.Markdown("---")
            ops_accordion = gr.Accordion("🔧 运维演示", visible=False, open=False)
            with ops_accordion:
                ops_content = gr.Markdown("点击刷新加载系统状态")
                ops_refresh_btn = gr.Button("刷新状态", size="sm")

    # ===== 事件绑定 =====
    login_outputs = [
        login_view, main_view, login_status, state,
        chatbot, session_dropdown, status_bar, api_key_warn, usage_guide, password_box,
        ops_accordion,
    ]
    login_btn.click(do_login, [role_radio, username_box, password_box], login_outputs)
    register_btn.click(
        do_register,
        [role_radio, username_box, password_box, register_question, register_answer],
        [login_status, password_box, register_answer],
    )
    recover_question_btn.click(
        do_get_security_question, [recover_role_radio, recover_username], [recover_question]
    )
    recover_btn.click(
        do_reset_password,
        [
            recover_role_radio, recover_username, recover_answer,
            recover_new_password, recover_confirm_password,
        ],
        [recover_status, recover_answer, recover_new_password, recover_confirm_password],
    )

    chat_start_outputs = [
        chatbot, msg_box, send_btn, stop_btn, status_bar, request_state,
    ]
    chat_finish_outputs = [
        chatbot, send_btn, stop_btn, msg_box, status_bar, request_state,
    ]
    send_start_event = send_btn.click(
        start_chat_request,
        [msg_box, chatbot, state],
        chat_start_outputs,
        queue=False,
    )
    send_chat_event = send_start_event.then(
        run_chat_request,
        [chatbot, state, request_state],
        chat_finish_outputs,
    )
    submit_start_event = msg_box.submit(
        start_chat_request,
        [msg_box, chatbot, state],
        chat_start_outputs,
        queue=False,
    )
    submit_chat_event = submit_start_event.then(
        run_chat_request,
        [chatbot, state, request_state],
        chat_finish_outputs,
    )
    chat_events = [send_chat_event, submit_chat_event]
    stop_btn.click(
        stop_chat_request,
        [chatbot, request_state],
        chat_finish_outputs,
        cancels=chat_events,
        queue=False,
    )

    new_btn.click(new_session, [new_name_box, state], [session_dropdown, chatbot, status_bar, state])
    switch_btn.click(switch_session, [session_dropdown, state], [chatbot, status_bar, state, session_dropdown])
    refresh_btn.click(refresh_sessions, [state], [session_dropdown])
    clear_cur_btn.click(clear_current, [state], [chatbot, status_bar, state])
    clear_all_btn.click(clear_all_sessions, [state], [chatbot, status_bar, state, session_dropdown])
    change_password_btn.click(
        do_change_password,
        [change_old_password, change_new_password, change_confirm_password, state],
        [
            change_password_status,
            change_old_password,
            change_new_password,
            change_confirm_password,
        ],
    )
    set_question_btn.click(
        do_set_security_question,
        [set_question_password, set_question_dropdown, set_question_answer, state],
        [set_question_status, set_question_password, set_question_answer],
    )

    relogin_outputs = login_outputs + [
        username_box, register_question, register_answer,
        recover_username, recover_question, recover_answer,
        recover_new_password, recover_confirm_password,
        change_old_password, change_new_password, change_confirm_password,
        set_question_password, set_question_dropdown, set_question_answer,
        recover_status, change_password_status, set_question_status,
    ]
    relogin_btn.click(relogin, outputs=relogin_outputs)

    ops_refresh_btn.click(refresh_ops_panel, [state], [ops_content])


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, inbrowser=True,
                theme=gr.themes.Soft(), css=custom_css)
