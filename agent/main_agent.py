# agent/main_agent.py
import sys, os

# 自动把当前脚本所在目录的【上一级目录】加入模块检索路径
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)
    
import json
from typing import List, Dict, Optional
import config
from utils.api_monitor import llm_client
from utils.rate_limiter import get_repeated_query_answer, remember_query_answer
from tools.schema import tool_schemas, func_mapping
from tools.prompt_manager import build_system_prompt
from tools.hallucination_checker import check as check_hallucination
from tools.error_handler import (
    LOST_MESSAGE,
    atomic_save_json,
    recover_memory_file,
    summarize_memory,
    validate_tool_args,
    validate_user_input,
)
from tools.rbac import (
    get_filtered_schemas,
    check_permission,
    get_permission_denied_msg,
    mask_tool_result,
    ROLE_CONSUMER,
    ROLE_MERCHANT,
)
from utils.logger import get_logger, set_console_logging_enabled

logger = get_logger(__name__)

MEMORY_DIR = os.path.join(ROOT_PATH, config.get("PATHS", "agent_memory_dir"))
DEFAULT_SESSION = config.get("SESSION", "default_session")

# 中文数字映射（用于自动编号对话名）
_CN_DIGITS = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
_CN_TENS = ["", "十", "二十", "三十", "四十", "五十", "六十", "七十", "八十", "九十"]


def _to_cn_num(n: int) -> str:
    """阿拉伯数字 → 中文数字（1-99）"""
    if n <= 0:
        return "零"
    if n <= 10:
        return _CN_DIGITS[n] if n <= 10 else _CN_DIGITS[n]
    if n < 20:
        return "十" + _CN_DIGITS[n - 10]
    tens = n // 10
    ones = n % 10
    return _CN_TENS[tens] + _CN_DIGITS[ones]
MAX_MESSAGE_ROUNDS = config.get("SESSION", "max_message_rounds")
SUMMARY_KEEP_ROUNDS = config.get("SESSION", "summary_keep_rounds")
MAX_MEMORY_ITEMS = MAX_MESSAGE_ROUNDS * 2

if not os.path.exists(MEMORY_DIR):
    os.makedirs(MEMORY_DIR, exist_ok=True)


def normalize_session_name(session_name: str) -> str:
    return "".join([c if c.isalnum() else "_" for c in session_name.strip()]) or DEFAULT_SESSION


def _parse_command_arg(raw_input: str, prefix: str):
    """
    解析命令行参数，支持无空格、冒号分隔、多空格分隔等写法:
      "新建对话咨询" / "新建对话：咨询" / "新建对话   咨询" 等效
    返回: (matched: bool, argument: str|None)
    """
    if not raw_input.startswith(prefix):
        return False, None
    arg = raw_input[len(prefix):]
    # 去掉前导的空格、全角/半角冒号
    arg = arg.lstrip("：: \t")
    return True, arg if arg else None


def _get_user_scope(username: Optional[str] = None, role: Optional[str] = None) -> str:
    if not username:
        return ""
    safe_role = normalize_session_name(role or "user")
    safe_username = normalize_session_name(username)
    return f"{safe_role}_{safe_username}"


def _get_memory_base_dir(username: Optional[str] = None, role: Optional[str] = None) -> str:
    user_scope = _get_user_scope(username, role)
    if not user_scope:
        return MEMORY_DIR
    return os.path.join(MEMORY_DIR, user_scope)


def get_session_label(
    session_name: str,
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> str:
    user_scope = _get_user_scope(username, role)
    safe_name = normalize_session_name(session_name)
    return f"{user_scope}/{safe_name}" if user_scope else safe_name


def _next_auto_session(username: Optional[str] = None, role: Optional[str] = None) -> str:
    """返回下一个自动编号的会话名：对话一、对话二、..."""
    existing = set(list_sessions(username=username, role=role))
    i = 1
    while True:
        name = f"对话{_to_cn_num(i)}"
        if name not in existing:
            return name
        i += 1


def get_session_path(
    session_name: str,
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> str:
    safe_name = normalize_session_name(session_name)
    return os.path.join(_get_memory_base_dir(username, role), f"{safe_name}.json")


def list_sessions(username: Optional[str] = None, role: Optional[str] = None) -> List[str]:
    sessions = []
    base_dir = _get_memory_base_dir(username, role)
    if not os.path.exists(base_dir):
        return sessions
    for filename in os.listdir(base_dir):
        if filename.endswith(".json"):
            sessions.append(os.path.splitext(filename)[0])
    return sorted(sessions)


def load_memory(
    session_name: str = DEFAULT_SESSION,
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> List[Dict]:
    session_path = get_session_path(session_name, username=username, role=role)
    return recover_memory_file(session_path)


def save_memory(
    messages: List[Dict],
    session_name: str = DEFAULT_SESSION,
    username: Optional[str] = None,
    role: Optional[str] = None,
):
    session_path = get_session_path(session_name, username=username, role=role)
    memory_messages = [m for m in messages if m.get("role") != "system"]
    summary_message, memory_messages = summarize_memory(
        memory_messages,
        max_rounds=MAX_MESSAGE_ROUNDS,
        summary_keep_rounds=SUMMARY_KEEP_ROUNDS,
    )
    if summary_message:
        memory_messages = [summary_message] + memory_messages
    atomic_save_json(session_path, memory_messages)


def clear_memory(
    session_name: str = DEFAULT_SESSION,
    username: Optional[str] = None,
    role: Optional[str] = None,
) -> None:
    session_path = get_session_path(session_name, username=username, role=role)
    if os.path.exists(session_path):
        try:
            os.remove(session_path)
            logger.info("清空当前会话记忆", extra={"session_name": get_session_label(session_name, username, role)})
        except Exception:
            pass


def clear_all_memory(username: Optional[str] = None, role: Optional[str] = None) -> None:
    base_dir = _get_memory_base_dir(username, role)
    if not os.path.exists(base_dir):
        return
    for filename in os.listdir(base_dir):
        if filename.endswith(".json"):
            try:
                os.remove(os.path.join(base_dir, filename))
                logger.info("清空会话文件 %s", filename, extra={"session_name": _get_user_scope(username, role) or "-"})
            except Exception:
                pass


def get_recent_chat_records(
    session_name: str = DEFAULT_SESSION,
    username: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 5,
) -> List[Dict]:
    memory = load_memory(session_name, username=username, role=role)
    chat_records = []
    for item in memory:
        if item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content")
        if content is None or not str(content).strip():
            continue
        chat_records.append(item)
    return chat_records[-limit:]


def format_recent_chat_records(records: List[Dict]) -> str:
    if not records:
        return "最近没有聊天记录。"
    lines = ["最近五条聊天记录："]
    role_labels = {"user": "用户", "assistant": "客服"}
    for item in records:
        role_label = role_labels.get(item.get("role"), item.get("role", "未知"))
        content = str(item.get("content", "")).replace("\n", " ").strip()
        lines.append(f"{role_label}：{content}")
    return "\n".join(lines)


def print_recent_chat_records(
    session_name: str = DEFAULT_SESSION,
    username: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 5,
) -> None:
    records = get_recent_chat_records(session_name, username=username, role=role, limit=limit)
    print(format_recent_chat_records(records))


def run_agent(user_query: str, session_name: str = DEFAULT_SESSION,
              current_role: str = ROLE_CONSUMER, use_memory: bool = True,
              current_username: Optional[str] = None) -> str:
    try:
        return _run_agent(user_query, session_name, current_role, use_memory, current_username)
    except Exception as exc:
        logger.error(f"Agent执行异常 error={exc}", extra={"session_name": get_session_label(session_name, current_username, current_role)})
        return LOST_MESSAGE


def _run_agent(user_query: str, session_name: str = DEFAULT_SESSION,
               current_role: str = ROLE_CONSUMER, use_memory: bool = True,
               current_username: Optional[str] = None) -> str:
    scoped_session = get_session_label(session_name, current_username, current_role)
    input_result = validate_user_input(user_query)
    if not input_result.ok:
        logger.warning(
            f"输入被过滤 reason={input_result.message} raw={user_query}",
            extra={"session_name": scoped_session},
        )
        return input_result.message or LOST_MESSAGE
    user_query = input_result.text or ""
    input_notice = input_result.message
    logger.info(f"用户请求 content={user_query}", extra={"session_name": scoped_session})
    if input_notice:
        logger.warning(f"输入被截断 notice={input_notice}", extra={"session_name": scoped_session})

    cached_answer = get_repeated_query_answer(scoped_session, user_query)
    if cached_answer is not None:
        logger.info(f"重复提问命中缓存 query={user_query}", extra={"session_name": scoped_session})
        return cached_answer

    previous_memory = []
    session_context = None
    if use_memory:
        previous_memory = load_memory(session_name, username=current_username, role=current_role)
        if previous_memory:
            summary_parts = [m.get("content", "") for m in previous_memory if m.get("role") == "system"]
            if summary_parts:
                session_context = "\n".join(summary_parts)

    system_content = build_system_prompt(current_role, session_context=session_context)
    messages = [{"role": "system", "content": system_content}]
    if previous_memory:
        messages.extend([m for m in previous_memory if m.get("role") != "system"])
    messages.append({"role": "user", "content": user_query})

    # 【第一层防护】按角色过滤 Function Schema，仅传入当前角色允许的工具
    role_schemas = get_filtered_schemas(current_role, tool_schemas)

    max_loop = config.get("AGENT", "max_loop")  # 限制最大工具调用轮次，防止无限循环
    loop_times = 0
    tool_results = []

    while loop_times < max_loop:
        loop_times += 1
        logger.info(f"LLM调用开始 loop={loop_times}", extra={"session_name": scoped_session})
        # 请求云端大模型，传入【角色过滤后】的工具清单
        llm_result = llm_client.chat_completion(
            messages=messages,
            tools=role_schemas,
            temperature=config.get("AGENT", "llm_temperature"),
            session_name=scoped_session,
        )
        logger.info(f"LLM调用结束 loop={loop_times}", extra={"session_name": scoped_session})
        choice = llm_result["choices"][0]
        message = choice["message"]

        # 情况1：不需要调用工具，直接输出回答
        if "tool_calls" not in message or message["tool_calls"] is None:
            messages.append(message)
            save_memory(messages, session_name, username=current_username, role=current_role)
            return _finalize_answer(message["content"], input_notice, tool_results, scoped_session, user_query)

        # 情况2：需要调用工具
        messages.append(message)
        for tool_call in message["tool_calls"]:
            func_name = tool_call["function"]["name"]
            try:
                func_args = json.loads(tool_call["function"]["arguments"])
            except Exception:
                func_args = {}
            logger.info(
                f"工具调用开始 name={func_name} args={func_args}",
                extra={"session_name": scoped_session},
            )

            validation = validate_tool_args(func_name, func_args, tool_schemas)
            if not validation["ok"]:
                logger.warning(
                    f"工具参数缺失 name={func_name} msg={validation['msg']}",
                    extra={"session_name": scoped_session},
                )
                tool_result = {"status": "fail", "msg": validation["msg"]}
                tool_results.append(tool_result)
                tool_content = json.dumps(tool_result, ensure_ascii=False)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": func_name,
                    "content": tool_content
                })
                continue

            # 【第二层防护】代码执行前二次校验权限，越权直接拦截
            if not check_permission(func_name, current_role):
                tool_result = {"status": "denied", "msg": get_permission_denied_msg(func_name, current_role)}
                tool_results.append(tool_result)
                tool_content = json.dumps(tool_result, ensure_ascii=False)
                logger.warning(
                    f"越权拦截 role={current_role} tool={func_name}",
                    extra={"session_name": scoped_session},
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": func_name,
                    "content": tool_content
                })
                continue

            # 通过映射表拿到真实函数
            target_func = func_mapping.get(func_name)
            if target_func is None:
                tool_content = f"错误：不存在工具 {func_name}"
            else:
                try:
                    # 执行工具
                    tool_return = target_func(**func_args)
                    # 【数据脱敏】按角色对工具返回结果做脱敏处理
                    tool_return = mask_tool_result(func_name, tool_return, current_role)
                    tool_results.append(tool_return)
                    logger.info(
                        f"工具调用结束 name={func_name} result_summary={_summarize_tool_result(tool_return)}",
                        extra={"session_name": scoped_session},
                    )
                    tool_content = json.dumps(tool_return, ensure_ascii=False)
                except Exception as exc:
                    logger.error(
                        f"工具调用异常 name={func_name} error={exc}",
                        extra={"session_name": scoped_session},
                    )
                    tool_result = {"status": "fail", "msg": f"工具执行失败：{exc}"}
                    tool_results.append(tool_result)
                    tool_content = json.dumps(
                        tool_result,
                        ensure_ascii=False
                    )

            # 将工具执行结果加入消息队列，传给LLM
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": func_name,
                "content": tool_content
            })

    save_memory(messages, session_name, username=current_username, role=current_role)
    return _finalize_answer("已达到最大工具调用轮次，无法完成查询", input_notice, tool_results, scoped_session, user_query)


def _with_input_notice(answer: str, input_notice: str = None) -> str:
    if input_notice:
        return f"{input_notice}\n{answer}"
    return answer


def _finalize_answer(answer: str, input_notice: str, tool_results: List, session_name: str, user_query: str) -> str:
    check_result = check_hallucination(answer, tool_results, session_name=session_name)
    logger.info(
        f"幻觉检测 score={check_result['score']} risk={check_result['risk']} ratio={check_result['session_stats']['risk_ratio']}",
        extra={"session_name": session_name},
    )
    final_answer = _with_input_notice(answer, input_notice)
    remember_query_answer(session_name, user_query, final_answer)
    return final_answer


def _summarize_tool_result(result) -> str:
    if isinstance(result, list):
        return f"list_count={len(result)}"
    if isinstance(result, dict):
        keys = ",".join(list(result.keys())[:5])
        return f"dict_keys={keys}"
    return str(result)[:120]


# 本地调试入口
if __name__ == "__main__":
    set_console_logging_enabled(False)

    # === 登录认证（独立模块，启动即执行） ===
    from tools.auth_login import init_auth_files, auth_interactive

    init_auth_files()  # 自动初始化账号文件（含预设测试账号）
    result = auth_interactive()
    if result == (None, None):
        print("登录已取消，程序退出。")
        sys.exit(0)

    current_role, current_username = result   # type: ignore[misc]
    current_session = DEFAULT_SESSION
    role_label = "买家" if current_role == ROLE_CONSUMER else "商家"
    print(f"当前角色：{role_label}  |  用户：{current_username}")
    print(f"当前对话会话：{current_session}")
    print_recent_chat_records(current_session, username=current_username, role=current_role)
    print("输入帮助查看全部会话管理命令，输入菜单查看可执行指令\n")

    help_text = """
========== 会话命令清单 ==========
已有对话 或 历史对话：    查看已有会话列表
新建对话 <名字>：         创建并切换到新会话（可省略空格）
切换到 <对话名称>：       切换到已有会话（可省略空格）
重新登录：                更换身份（买家/商家），切换后清空上下文
清空当前记忆：            清空当前会话记忆
清空所有对话记忆：        清空所有会话记忆
帮助：                    显示命令帮助
菜单：                    显示可执行指令
退出：                    退出程序
==================================
"""

    while True:
        role_label = "买家" if current_role == ROLE_CONSUMER else "商家"
        question = input(f"\n[{current_session}|{role_label}] 请输入客服问题(输入【退出】结束程序)：")
        raw_input = question.strip()

        # 退出程序
        if raw_input == "退出":
            print("程序结束")
            break
        # 帮助指令
        if raw_input == "帮助":
            print(help_text)
            continue
        # 查看会话列表
        if raw_input in {"已有对话", "历史对话"}:
            sessions = list_sessions(username=current_username, role=current_role)
            print("可用会话：", ", ".join(sessions) if sessions else "(暂无会话)")
            continue
        # 新建对话（支持无空格：新建对话咨询 / 新建对话：咨询 / 新建对话   咨询）
        matched, name = _parse_command_arg(raw_input, "新建对话")
        if matched:
            if name:
                safe_name = normalize_session_name(name)
            else:
                # 未指定名称 → 自动编号 对话N
                safe_name = _next_auto_session(username=current_username, role=current_role)
            current_session = safe_name
            clear_memory(current_session, username=current_username, role=current_role)
            save_memory([], current_session, username=current_username, role=current_role)
            logger.info("会话切换 type=create", extra={"session_name": get_session_label(current_session, current_username, current_role)})
            print(f"已创建并切换到会话：{current_session}")
            continue
        # 切换会话（支持无空格写法）
        matched, name = _parse_command_arg(raw_input, "切换到")
        if matched:
            if name:
                safe_name = normalize_session_name(name)
                current_session = safe_name
                session_list = list_sessions(username=current_username, role=current_role)
                if current_session not in session_list:
                    logger.info("会话切换 type=new_empty", extra={"session_name": get_session_label(current_session, current_username, current_role)})
                    print(f"会话 {current_session} 不存在，将启用全新空会话")
                else:
                    logger.info("会话切换 type=switch", extra={"session_name": get_session_label(current_session, current_username, current_role)})
                    print(f"已切换到会话：{current_session}")
                    print_recent_chat_records(current_session, username=current_username, role=current_role)
            else:
                print("请输入会话名称！示例：切换到售后咨询")
            continue
        # 重新登录（唯一更换身份的方式，切换后清空上下文）
        if raw_input == "重新登录":
            result = auth_interactive()
            if result == (None, None):
                print("登录已取消，保持当前账号")
                continue
            current_role, current_username = result   # type: ignore[misc]
            clear_memory(current_session, username=current_username, role=current_role)
            save_memory([], current_session, username=current_username, role=current_role)
            logger.info("重新登录后清空会话上下文", extra={"session_name": get_session_label(current_session, current_username, current_role)})
            role_label = "买家" if current_role == ROLE_CONSUMER else "商家"
            print(f"已切换身份，当前角色：{role_label}  |  用户：{current_username}，会话上下文已清空")
        # 清空当前会话记忆
        if raw_input == "清空当前记忆":
            clear_memory(current_session, username=current_username, role=current_role)
            print(f"已清空会话【{current_session}】的对话记忆")
            continue
        # 清空全部会话记忆
        if raw_input == "清空所有对话记忆":
            clear_all_memory(username=current_username, role=current_role)
            print("已清空全部会话记忆")
            continue
        # 显示可执行指令
        if raw_input == "菜单":
            print(help_text)
            continue

        # 普通客服问题，交给Agent处理
        answer = run_agent(
            raw_input,
            session_name=current_session,
            current_role=current_role,
            current_username=current_username,
        )
        print(f"\n客服回答：{answer}")
