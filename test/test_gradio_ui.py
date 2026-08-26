# test_gradio_ui.py
# Gradio UI 辅助逻辑测试，可独立运行。
import os
import sys

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_PATH not in sys.path:
    sys.path.append(ROOT_PATH)

from tools.rbac import ROLE_CONSUMER, ROLE_MERCHANT
from ui import gradio_app


_pass, _fail = 0, 0


def _assert(condition, label):
    """记录测试断言结果，并输出对应的通过或失败信息。
    :param condition: 断言是否成立的布尔条件。
    :param label: 用于日志或测试输出的说明标签。
    """
    global _pass, _fail
    if condition:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}")


def _run_case(label, fn):
    """执行单个测试用例，并将异常转换为失败记录。
    :param label: 用于日志或测试输出的说明标签。
    :param fn: 需要调用、包装或测试的函数。
    """
    try:
        fn()
    except Exception as exc:
        _assert(False, f"{label} -> {type(exc).__name__}: {exc}")


def test_ops_panel_only_visible_to_merchant():
    """验证 ops panel only visible to merchant 场景符合预期行为。"""
    _assert(gradio_app.get_ops_panel_visible(ROLE_CONSUMER) is False, "买家看不到运维演示区")
    _assert(gradio_app.get_ops_panel_visible(ROLE_MERCHANT) is True, "商家可看到运维演示区")


def test_chat_hint_mentions_help_and_menu():
    """验证 chat hint mentions help and menu 场景符合预期行为。"""
    hint = gradio_app.build_chat_hint()
    _assert("帮助" in hint and "菜单" in hint, "聊天区置顶提示包含帮助和菜单")


def test_help_and_menu_messages_do_not_require_login_or_llm():
    """验证 help and menu messages do not require login or llm 场景符合预期行为。"""
    state = gradio_app.default_ui_state()
    help_result = gradio_app.handle_chat_message("帮助", state)
    menu_result = gradio_app.handle_chat_message("菜单", state)

    _assert("会话命令" in help_result["answer"], "帮助返回命令说明")
    _assert("可执行指令" in menu_result["answer"], "菜单返回指令说明")
    _assert(help_result["state"]["chat_history"][-1]["content"] == help_result["answer"], "帮助写入聊天记录")
    _assert(menu_result["state"]["chat_history"][-1]["content"] == menu_result["answer"], "菜单写入聊天记录")


def test_chatbot_history_uses_gradio_tuple_format():
    """验证 chatbot history uses gradio tuple format 场景符合预期行为。"""
    history = [
        {"role": "user", "content": "查订单"},
        {"role": "assistant", "content": "请提供订单号"},
    ]
    view = gradio_app.to_chatbot_view(history)
    _assert(view == [("查订单", "请提供订单号")], "Chatbot 输出兼容 Gradio tuple 格式")


def test_page_visibility_switches_after_login():
    """验证 page visibility switches after login 场景符合预期行为。"""
    logged_out = gradio_app.get_page_visibility(False)
    logged_in = gradio_app.get_page_visibility(True)

    _assert(logged_out["login_page"] is True and logged_out["chat_page"] is False, "未登录时只显示登录页")
    _assert(logged_in["login_page"] is False and logged_in["chat_page"] is True, "登录成功后显示聊天页")


def test_default_accounts_login_success():
    """验证 default accounts login success 场景符合预期行为。"""
    buyer = gradio_app.handle_login("买家", "user1", "123456", gradio_app.default_ui_state())
    merchant = gradio_app.handle_login("商家", "admin", "admin123", gradio_app.default_ui_state())

    _assert(buyer["ok"] is True, "默认买家账号密码可登录")
    _assert(merchant["ok"] is True, "默认商家账号密码可登录")


def test_register_validates_password_confirm():
    """验证 register validates password confirm 场景符合预期行为。"""
    result = gradio_app.handle_register("买家", "new_ui_user", "pass1234", "pass9999")
    _assert(result["ok"] is False and "两次密码不一致" in result["message"], "注册校验确认密码")


def test_custom_css_matches_reference_style():
    """验证 custom css matches reference style 场景符合预期行为。"""
    css = gradio_app.build_app_css()
    _assert("#f7f8fb" in css, "页面使用浅灰背景")
    _assert(".login-card" in css and "max-width: 660px" in css, "登录卡片居中且宽度接近参考图")
    _assert(".chat-layout" in css and "grid-template-columns" in css, "聊天页使用左右布局")
    _assert("#635df6" in css, "主按钮使用紫蓝色")


print("=" * 60)
print("  test_gradio_ui.py")
print("=" * 60)
for name, case in [
    ("运维演示区权限", test_ops_panel_only_visible_to_merchant),
    ("聊天区顶部提示", test_chat_hint_mentions_help_and_menu),
    ("帮助菜单快捷回复", test_help_and_menu_messages_do_not_require_login_or_llm),
    ("Chatbot 格式兼容", test_chatbot_history_uses_gradio_tuple_format),
    ("登录页聊天页切换", test_page_visibility_switches_after_login),
    ("默认账号登录", test_default_accounts_login_success),
    ("注册确认密码校验", test_register_validates_password_confirm),
    ("参考图样式 CSS", test_custom_css_matches_reference_style),
]:
    _run_case(name, case)

print("=" * 60)
print(f"  通过: {_pass}  失败: {_fail}  总计: {_pass + _fail}")
raise SystemExit(0 if _fail == 0 else 1)
