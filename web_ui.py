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

CONSUMER_GUIDE = """### 👤 消费者使用说明

- **商品查询**：查询商品信息，例如：`查询 SP001 的商品信息`
- **订单查询**：仅查询当前账号自己的订单，例如：`查询 DD001`
- **售后工单**：创建和查询自己的售后申请，例如：`为订单 DD001 创建退货售后`
- **知识库查询**：解答售后政策、活动规则等问题，例如：`七天无理由退货有什么要求`
- **会话管理**：输入 `帮助` 查看记忆和会话管理命令
"""

MERCHANT_GUIDE = """### 🏪 商家使用说明

- **商品管理**：查询商品信息、修改价格/规格/上架状态等，例如：`把 SP001 的售价改为 99 元`
- **库存查询**：查看商品库存数量，例如：`查询 SP001 的库存`
- **订单查询**：根据订单号、商品或时间范围查询订单，例如：`查询 DD001`、`查询本月订单`
- **售后工单**：创建和管理售后工单，例如：`查询待处理的售后工单`
- **销售报表**：生成指定时间段的销售统计，例如：`导出本月销售报表`
- **知识库查询**：解答售后政策、活动规则等问题，例如：`查询双十一活动规则`
- **会话管理**：输入 `帮助` 查看记忆和会话管理命令
- 运维面板：登录后展开「运维演示」，点击`刷新运维面板`查看主库状态
"""



set_console_logging_enabled(False)
logger = get_logger(__name__)
init_auth_files()

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


def chat_respond(message, history, state):
    """发送消息并在当前进程调用 Agent，避免重复加载模型。
    :param message: 用户输入或待处理的消息文本。
    :param history: 当前会话的历史消息。
    :param state: 界面保存的当前登录及会话状态。
    :return: 返回函数处理得到的结果。
    """
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
            if answer is None or not str(answer).strip():
                answer = "⚠️ 模型返回空内容，请稍后重试；本次请求未生成有效回答。"
        except Exception as e:
            answer = f"⚠️ 处理出错：{e}"
            logger.exception(f"Web UI chat 异常: {e}")

    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": str(answer)},
    ]
    return history, "", _status_text(state)
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

    send_btn.click(chat_respond, [msg_box, chatbot, state], [chatbot, msg_box, status_bar])
    msg_box.submit(chat_respond, [msg_box, chatbot, state], [chatbot, msg_box, status_bar])

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
