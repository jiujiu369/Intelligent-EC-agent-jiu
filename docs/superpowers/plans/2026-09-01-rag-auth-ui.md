# RAG 768 维、账号安全与使用说明 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留独立双 ChromaDB，将主、备用嵌入统一为 768 维 BGE，并交付安全问题找回密码、登录后修改密码和分角色 Gradio 使用说明。

**Architecture:** RAG 配置层为备用库提供新的 768 维持久化目录，管道层继续复用通用模型加载和集合操作。认证逻辑集中在 `tools/auth_login.py`，UI 仅调用认证接口并维护登录态；安全答案和密码都使用现有 PBKDF2-SHA256 机制独立加盐。

**Tech Stack:** Python 3、ChromaDB、sentence-transformers、Gradio 6.20、JSON、PBKDF2-SHA256、项目现有脚本式测试。

**Spec:** `docs/superpowers/specs/2026-09-01-rag-auth-ui-design.md`

## Global Constraints

- 主、备用模型默认值均为 `bge-base-zh-v1.5`（768 维）。
- 模型路径仍分别接受 `AGENT_RAG_PRIMARY_MODEL`、`AGENT_RAG_FALLBACK_MODEL` 环境变量覆盖。
- 主库与备用库必须使用不同持久化目录和集合，旧 384 维库不得被新备用模型打开。
- 检索顺序保持主库、备用库、关键词兜底。
- 密码和安全答案不得明文落盘。
- 不修改 Agent 工具调用、会话隔离、RBAC 或其他业务流程。
- 保留工作区内已有无关修改。

---

### Task 1: 统一主、备用 RAG 为独立 768 维库

**Files:**
- Modify: `config.py`
- Modify: `embedding/rag_pipeline.py`
- Modify: `test/test_rag_pipeline_fallback.py`
- Modify: `test/test_rag_vector_maintenance.py`

**Interfaces:**
- Consumes: `config.get(section, key)`、`SentenceTransformerEmbeddingFunction`、两个独立 Chroma collection。
- Produces: `build_vector_db_docs_fallback() -> tuple`、`build_vector_db_goods_fallback() -> tuple`，以及标签 `primary-768`、`fallback-768`。

- [ ] **Step 1: 编写失败测试**

在隔离导入测试中为 `config` 提供主、备用 BGE 模型和两个不同的持久化目录，断言初始化调用分别收到 `primary-768`、`fallback-768`；调用两个备用入库入口并断言数据只写入 `fallback_collection`。已有测试中的 `_384` 入口改为期望 `_fallback` 入口。

```python
assert init_calls == [
    ("models/bge-base-zh-v1.5", primary_path, "customer_service_docs", "primary-768"),
    ("models/bge-base-zh-v1.5", fallback_path, "customer_service_docs_fallback_768", "fallback-768"),
]
assert module.build_vector_db_docs_fallback() == (1, 0)
assert module.build_vector_db_goods_fallback() == (1, 0)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe test\test_rag_pipeline_fallback.py`

Expected: FAIL，原因是当前标签、集合名和备用入库函数仍使用 384 命名。

- [ ] **Step 3: 最小化修改配置和管道**

```python
PATHS["chroma_persist_dir_fallback"] = "datas/chroma_db_fallback_768"
RAG["fallback_model"] = _model_path("bge-base-zh-v1.5")

FALLBACK_CHROMA_PERSIST_PATH = os.path.join(
    PROJECT_ROOT, config.get("PATHS", "chroma_persist_dir_fallback")
)
FALLBACK_COLLECTION_NAME = "customer_service_docs_fallback_768"
```

将备用日志统一为 `fallback-768`，将两个 `_384` 入库函数重命名为 `_fallback`。本地调试入口依次调用主、备用政策入库和主、备用商品入库。

- [ ] **Step 4: 运行 RAG 测试并确认 GREEN**

Run: `\.venv\Scripts\python.exe test\test_rag_pipeline_fallback.py`

Run: `\.venv\Scripts\python.exe test\test_rag_vector_maintenance.py`

Expected: 两个脚本均 PASS；若依赖环境阻止真实模型加载，测试仍通过隔离的本地假对象验证业务边界。

- [ ] **Step 5: 提交本任务**

```powershell
git add config.py embedding/rag_pipeline.py test/test_rag_pipeline_fallback.py test/test_rag_vector_maintenance.py
git commit -m "fix: unify fallback RAG on 768-dimensional BGE"
```

### Task 2: 实现安全问题、找回密码和修改密码

**Files:**
- Modify: `tools/auth_login.py`
- Modify: `test/test_auth.py`

**Interfaces:**
- Consumes: `_load_users(role) -> dict`、`_save_users(role, users) -> bool`、`_hash_password(value, salt=None) -> tuple[str, str]`。
- Produces:
  - `SECURITY_QUESTIONS: tuple[str, ...]`
  - `register_user(role, username, password, security_question, security_answer) -> tuple[bool, str]`
  - `get_security_question(role, username) -> tuple[bool, str]`
  - `reset_password(role, username, security_answer, new_password, confirm_password) -> tuple[bool, str]`
  - `change_password(role, username, old_password, new_password, confirm_password) -> tuple[bool, str]`
  - `set_security_question(role, username, current_password, question, answer) -> tuple[bool, str]`

- [ ] **Step 1: 编写认证失败测试**

使用临时用户 JSON 替换模块的 `CONSUMER_FILE` 和 `MERCHANT_FILE`，覆盖以下行为：

```python
ok, _ = register_user(ROLE_CONSUMER, "alice", "oldpass", SECURITY_QUESTIONS[0], "Taipei")
assert ok
assert "Taipei" not in CONSUMER_FILE.read_text(encoding="utf-8")
assert get_security_question(ROLE_CONSUMER, "alice") == (True, SECURITY_QUESTIONS[0])
assert reset_password(ROLE_CONSUMER, "alice", "wrong", "newpass", "newpass")[0] is False
assert reset_password(ROLE_CONSUMER, "alice", "taipei", "newpass", "newpass")[0] is True
assert login_user(ROLE_CONSUMER, "alice", "newpass")[0] is True
assert change_password(ROLE_CONSUMER, "alice", "newpass", "finalpass", "finalpass")[0] is True
```

另建缺少安全问题字段的旧账号，断言可以登录和修改密码，但 `get_security_question()` 返回未设置提示；调用 `set_security_question()` 后可找回密码。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe test\test_auth.py`

Expected: FAIL，原因是新认证接口和安全问题字段尚不存在。

- [ ] **Step 3: 实现认证接口**

复用 PBKDF2 实现答案哈希，并增加集中校验：

```python
SECURITY_QUESTIONS = (
    "你的第一所学校名称是？",
    "你的童年昵称是？",
    "你最喜欢的城市是？",
)

def _normalize_security_answer(answer: str) -> str:
    return (answer or "").strip().lower()
```

所有更新先修改内存字典，只有 `_save_users()` 成功才返回成功；错误答案、错误旧密码、两次新密码不一致和不受支持的问题均拒绝。

- [ ] **Step 4: 更新 CLI 注册流程**

在 `auth_interactive()` 注册分支输出三个固定问题，要求用户选择并输入答案，再调用扩展后的 `register_user()`；登录流程保持不变。

- [ ] **Step 5: 运行认证测试并确认 GREEN**

Run: `\.venv\Scripts\python.exe test\test_auth.py`

Expected: 所有认证测试 PASS，测试结束后恢复原用户文件，不改写真实账号数据。

- [ ] **Step 6: 提交本任务**

```powershell
git add tools/auth_login.py test/test_auth.py
git commit -m "feat: add secure password recovery and changes"
```

### Task 3: 接入 Gradio 密码流程和分角色使用说明

**Files:**
- Modify: `web_ui.py`
- Create: `test/test_web_ui_account_helpers.py`

**Interfaces:**
- Consumes: Task 2 的五个认证接口及 `SECURITY_QUESTIONS`。
- Produces: `do_get_security_question()`、`do_reset_password()`、`do_change_password()`、`do_set_security_question()` 和 `_usage_guide(role) -> str`。

- [ ] **Step 1: 编写 UI 回调失败测试**

直接调用回调函数而不启动服务器，使用临时账号验证：

```python
assert "订单" in _usage_guide(ROLE_CONSUMER)
assert "售后" in _usage_guide(ROLE_CONSUMER)
assert "库存" in _usage_guide(ROLE_MERCHANT)
assert "销售报表" in _usage_guide(ROLE_MERCHANT)
assert do_change_password("old", "newpass", "newpass", logged_in_state).startswith("✅")
assert do_change_password("old", "newpass", "mismatch", logged_in_state).startswith("❌")
```

找回密码回调需验证角色映射、问题返回和错误答案提示；未登录状态调用账号安全回调必须返回“请先登录”。

- [ ] **Step 2: 运行测试并确认 RED**

Run: `\.venv\Scripts\python.exe test\test_web_ui_account_helpers.py`

Expected: FAIL，原因是 UI 回调及角色使用说明尚不存在。

- [ ] **Step 3: 实现 UI 回调**

回调只负责角色映射、登录态读取、确认密码和图标化提示，认证与持久化全部委托给 `tools.auth_login`。

```python
def _usage_guide(role: str) -> str:
    return CONSUMER_GUIDE if role == ROLE_CONSUMER else MERCHANT_GUIDE
```

- [ ] **Step 4: 构建 Gradio 控件并绑定事件**

登录页增加找回密码 Accordion；主界面右侧增加账号安全与使用说明 Accordion。`do_login()` 增加使用说明输出，并按登录角色更新 Markdown；`relogin()` 同步清空该输出和密码字段。

- [ ] **Step 5: 运行 UI 回调测试并确认 GREEN**

Run: `\.venv\Scripts\python.exe test\test_web_ui_account_helpers.py`

Expected: PASS，导入 `web_ui` 不启动 Gradio 服务。

- [ ] **Step 6: 提交本任务**

```powershell
git add web_ui.py test/test_web_ui_account_helpers.py
git commit -m "feat: add account security and role usage guides"
```

### Task 4: 集成验证与范围检查

**Files:**
- Verify: `config.py`
- Verify: `embedding/rag_pipeline.py`
- Verify: `tools/auth_login.py`
- Verify: `web_ui.py`
- Verify: `test/`

**Interfaces:**
- Consumes: Tasks 1-3 的全部公开接口。
- Produces: 可复现的验证结果，不新增生产接口。

- [ ] **Step 1: 编译检查**

Run: `\.venv\Scripts\python.exe -m compileall config.py embedding\rag_pipeline.py tools\auth_login.py web_ui.py test\test_auth.py test\test_rag_pipeline_fallback.py test\test_rag_vector_maintenance.py test\test_web_ui_account_helpers.py`

Expected: 所有目标文件编译成功。

- [ ] **Step 2: 运行相关回归测试**

Run: `\.venv\Scripts\python.exe test\test_auth.py`

Run: `\.venv\Scripts\python.exe test\test_rag_pipeline_fallback.py`

Run: `\.venv\Scripts\python.exe test\test_rag_vector_maintenance.py`

Run: `\.venv\Scripts\python.exe test\test_web_ui_account_helpers.py`

Expected: 四个脚本均返回退出码 0。

- [ ] **Step 3: 检查遗留 384 维备用标识**

Run: `rg -n "MiniLM|fallback-384|384维备用|build_vector_db_(docs|goods)_384|chroma_persist_dir_384|customer_service_docs_384" config.py embedding/rag_pipeline.py`

Expected: 无匹配。`chunk_size = 384` 不属于向量维度，不应删除。

- [ ] **Step 4: 检查差异范围**

Run: `git diff --check`

Run: `git status --short`

Expected: 无空白错误；仅出现本计划文件与明确列出的实现、测试文件，以及用户原先已有的无关修改。

- [ ] **Step 5: 最终提交**

如 Task 1-3 已分别提交，仅提交计划勾选状态或验证所需的小修正；不得夹带用户原有修改。
