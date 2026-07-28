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

ROOT_PATH = os.path.abspath(os.path.dirname(__file__))
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

import gradio as gr

from agent.main_agent import (
    run_agent,
    list_sessions,
    clear_memory,
    clear_all_memory,
    save_memory,
    get_recent_chat_records,
    normalize_session_name,
    _next_auto_session,
    DEFAULT_SESSION,
)
from tools.auth_login import (
    init_auth_files,
    login_user,
    register_user,
    ROLE_CONSUMER,
    ROLE_MERCHANT,
)
from utils.logger import get_logger, set_console_logging_enabled
from embedding import rag_pipeline

HELP_TEXT = """已有对话 或 历史对话：    查看已有会话列表
新建对话 <名字>：         创建并切换到新会话（可省略空格）
切换到 <对话名称>：       切换到已有会话（可省略空格）
重新登录：                更换身份（买家/商家），切换后清空上下文
清空当前记忆：            清空当前会话记忆
清空所有对话记忆：        清空所有会话记忆
帮助：                    显示命令帮助
菜单：                    显示可执行指令
退出：                    退出程序"""

API_KEY_WARN = """⚠️ 未检测到 API 密钥！请在项目根目录 .env 文件中配置：

AGENT_API_KEY=sk-...

或在启动前设置环境变量（PowerShell）：
$env:AGENT_API_KEY="sk-..."

当前 Agent 只能返回��底回复，无法调用大模型。"""



set_console_logging_enabled(False)
logger = get_logger(__name__)
init_auth_files()


# ====================== 运维演示 ======================

def refresh_ops_panel(state):
    """刷新运维演示面板：ChromaDB 状态、API 配置、RAG 参数。仅商家可查看。"""
    import config
    if not state or not state.get("username") or state.get("role") != ROLE_MERCHANT:
        return "仅商家角色可查看运维面板"

    lines = []
    primary_ok = not rag_pipeline.chroma_connection_failed and rag_pipeline.collection is not None
    fallback_ok = not rag_pipeline.fallback_chroma_connection_failed and rag_pipeline.fallback_collection is not None

    lines.append("### 📊 向量库状态")
    lines.append(f"- **主库 (768维)**: {'✅ 正常' if primary_ok else '❌ 不可用'}")
    lines.append(f"- **备用库 (384维)**: {'✅ 正常' if fallback_ok else '❌ 不可用'}")

    if primary_ok:
        try:
            count = rag_pipeline.collection.count()
            lines.append(f"  - 主库向量数: {count}")
        except Exception:
            pass
    if fallback_ok:
        try:
            count = rag_pipeline.fallback_collection.count()
            lines.append(f"  - 备用库向量数: {count}")
        except Exception:
            pass

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
    lines.append("- 主模型: bge-base-zh-v1.5 (768 维)")
    lines.append("- 备用模型: paraphrase-multilingual-MiniLM-L12-v2 (384 维)")

    return "\n".join(lines)


# ====================== 辅助函数 ======================

def _role_label(role: str) -> str:
    return "消费者" if role == ROLE_CONSUMER else "商家"


def _load_history(session, username, role, limit=30):
    """加载会话历史，转为 Gradio messages 格式 [{"role":..,"content":..}, ...]。"""
    records = get_recent_chat_records(session, username=username, role=role, limit=limit)
    return [{"role": r["role"], "content": r["content"]} for r in records]


def _status_text(state):
    if not state or not state.get("username"):
        return "未登录"
    return (f"角色：{_role_label(state['role'])}　|　"
            f"用户：{state['username']}　|　会话：{state['session']}")


# ====================== 回调函数 ======================

def do_login(role_radio, username, password):
    """登录：成功切换到主界面，失败提示。检测 API Key 是否已配置。"""
    import config
    role = ROLE_CONSUMER if "消费者" in str(role_radio) else ROLE_MERCHANT
    username = (username or "").strip()
    ok, msg, r = login_user(role, username, password or "")
    if not ok:
        return (
            gr.update(visible=True), gr.update(visible=False),
            msg, {}, [], gr.update(choices=[], value=None), "未登录", "", "",
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
        _status_text(state), warn, "",
        gr.update(visible=(r == ROLE_MERCHANT)),
    )


def do_register(role_radio, username, password):
    """注册新账号。"""
    role = ROLE_CONSUMER if "消费者" in str(role_radio) else ROLE_MERCHANT
    username = (username or "").strip()
    ok, msg = register_user(role, username, password or "")
    return msg


def chat_respond(message, history, state):
    """发送消息，调用 Agent 获取回答。支持帮助/菜单快捷命令。"""
    if not state or not state.get("username"):
        return history, "", "请先登录"
    message = (message or "").strip()
    if not message:
        return history, "", _status_text(state)

    # 快捷命令：帮助 / 菜单
    if message == "帮助":
        answer = f"📖 **命令帮助**\n\n{HELP_TEXT}"
    elif message == "菜单":
        answer = f"📋 **可执行指令**\n\n{HELP_TEXT}"
    else:
        try:
            answer = run_agent(
                message,
                session_name=state["session"],
                current_role=state["role"],
                current_username=state["username"],
            )
        except Exception as e:
            answer = f"⚠️ 处理出错：{e}"
            logger.error(f"Web UI chat 异常: {e}")

    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer},
    ]
    return history, "", _status_text(state)


def new_session(new_name, state):
    """新建会话（名称留空则自动编号）。"""
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
    """切换到选定的会话。"""
    if not state or not state.get("username"):
        return [], "请先登录", state, gr.update()
    session = selected or DEFAULT_SESSION
    state = {**state, "session": session}
    history = _load_history(session, state["username"], state["role"])
    return history, _status_text(state), state, gr.update(value=session)


def clear_current(state):
    """清空当前会话记忆。"""
    if not state or not state.get("username"):
        return [], "请先登录", state
    clear_memory(state["session"], username=state["username"], role=state["role"])
    return [], f"已清空会话【{state['session']}】的记忆", state


def clear_all_sessions(state):
    """清空全部会话记忆。"""
    if not state or not state.get("username"):
        return [], "请先登录", state, gr.update()
    clear_all_memory(username=state["username"], role=state["role"])
    state = {**state, "session": DEFAULT_SESSION}
    sessions = list_sessions(state["username"], state["role"])
    return [], "已清空全部会话记忆", state, gr.update(choices=sessions, value=DEFAULT_SESSION)


def relogin():
    """重新登录：回到登录页，清空状态。"""
    return (
        gr.update(visible=True), gr.update(visible=False),
        "", {}, [], gr.update(choices=[], value=None), "未登录", "", "",
        gr.update(visible=False),
    )


def refresh_sessions(state):
    """刷新会话列表下拉框。"""
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
            login_status = gr.Markdown("")
            gr.Markdown("<small>请输入用户名和密码</small>")

    # ===== 主界面视图 =====
    with gr.Row(visible=False) as main_view:
        # 左侧聊天区
        with gr.Column(scale=3):
            status_bar = gr.Markdown("未登录", elem_classes=["status-bar"])
            api_key_warn = gr.Markdown("")
            gr.Markdown(
                "💡 **提示**：输入「帮助」查看全部会话管理命令，输入「菜单」查看可执行指令"
            )
            chatbot = gr.Chatbot(label="对话", height=480)
            with gr.Row():
                msg_box = gr.Textbox(
                    placeholder="输入客服问题，回车发送",
                    scale=5, show_label=False, autofocus=True,
                )
                send_btn = gr.Button("发送", variant="primary", scale=1)
        # 右侧会话管理
        with gr.Column(scale=1, min_width=260):
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
        chatbot, session_dropdown, status_bar, api_key_warn, password_box,
        ops_accordion,
    ]
    login_btn.click(do_login, [role_radio, username_box, password_box], login_outputs)
    register_btn.click(do_register, [role_radio, username_box, password_box], [login_status])

    send_btn.click(chat_respond, [msg_box, chatbot, state], [chatbot, msg_box, status_bar])
    msg_box.submit(chat_respond, [msg_box, chatbot, state], [chatbot, msg_box, status_bar])

    new_btn.click(new_session, [new_name_box, state], [session_dropdown, chatbot, status_bar, state])
    switch_btn.click(switch_session, [session_dropdown, state], [chatbot, status_bar, state, session_dropdown])
    refresh_btn.click(refresh_sessions, [state], [session_dropdown])
    clear_cur_btn.click(clear_current, [state], [chatbot, status_bar, state])
    clear_all_btn.click(clear_all_sessions, [state], [chatbot, status_bar, state, session_dropdown])
    relogin_btn.click(relogin, outputs=login_outputs)

    ops_refresh_btn.click(refresh_ops_panel, [state], [ops_content])


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, inbrowser=True,
                theme=gr.themes.Soft(), css=custom_css)
