# RAG/Auth/UI 最终统一修复报告

## 结论

- 已一次修复 `final-review-findings.md` 中 6 个 Important，并包含同线 2 个 Minor。
- 生产代码与行为测试提交：`21bd06c0fb1649b505ce6c8478d758b0a938492f`。
- 所有 findings 指定的直接命令均使用 `F:\code\project1\.venv\Scripts\python.exe` 运行并通过。
- `datas/consumer_users.json` 与 `datas/merchant_users.json` 未改变；测试临时残留为 0。

## 修复映射

### Important 1：主模型环境变量契约

- `config.Config._get_env_override()` 增加显式映射：
  - `RAG.embedding_model` → `AGENT_RAG_PRIMARY_MODEL`
  - `RAG.fallback_model` → `AGENT_RAG_FALLBACK_MODEL`
- 新增 `test/test_config_env.py`，通过真实 `importlib.reload(config)` 验证两个环境变量，不使用配置桩。

### Important 2：UI 角色映射 fail closed

- `_role_from_radio()` 只接受精确值 `消费者`、`商家`，其他值返回 `None`。
- `_role_label()` 与 `_usage_guide()` 对未知角色返回明确错误，不再默认商家。
- 登录、注册、查询安全问题、重设密码、修改密码、设置安全问题六个账号回调均在认证调用前拒绝未知角色。
- UI 测试用 mock 仅用于证明认证函数“没有被调用”；行为结果由真实回调返回值验证。

### Important 3：找回密码限流

- 在 `tools/auth_login.py` 增加按 `role + username` 隔离的进程内失败计数。
- 连续 3 次错误后冷却 300 秒；未知账号与错误答案使用相同通用提示。
- 成功重设密码后清除该账号失败状态；冷却到期后惰性清除。
- 测试通过 patch `time.monotonic()` 确定性推进时钟，不使用 `sleep`。

### Important 4：维护路径同步主、备用向量库

- `_collection_id_exists()`、`_add_chunks_if_missing()` 改为 `is None` 选择默认集合，移除集合真值依赖。
- 删除、快照、恢复 helper 接受显式 `target_collection`。
- 单商品更新、全量重建、清空商品向量分别对主库与备用库独立执行，并返回 `primary`/`fallback` 结果；单库失败时整体返回 `partial`。
- 全量重建按库独立快照与回滚；主库失败不会阻断备用库完成重建。
- `FakeCollection.__bool__()` 刻意返回 `False`，测试仍验证两个真实假集合的 `items` 状态变化，而非只断言 mock 调用。
- 检索路径未改，仍保持主库 → 备用库 → 关键词。

### Important 5：账号操作后清空敏感输入

- 注册回调清空密码、安全答案。
- 重设密码回调清空安全答案、新密码、确认密码。
- 修改密码回调清空旧密码、新密码、确认密码。
- 设置安全问题回调清空当前密码、安全答案。
- 成功、失败、未登录和非法角色分支均保持相同返回基数并清空敏感字段。
- fake Gradio 记录真实事件绑定；测试逐项验证 callback inputs/outputs 数量及输出组件身份。

### Important 6：恢复覆盖与 Windows 直接运行

- `test/test_auth.py` 不再调用 `tempfile.mkdtemp()`；改为在既有可写 `datas` 目录创建两个 UUID 独占 JSON，并只删除本次创建的文件。
- 恢复/覆盖：空文件初始化、注册输入校验、登录成功与通用失败消息、密码随机盐独立性、RBAC 与脱敏旧行为、旧账号兼容、原子写失败保护及完整账号安全流程。
- `python test\test_auth.py` 在目标 Windows 环境直接通过，无 `chmod`、ACL harness 或系统临时目录依赖。
- 认证、UI、RAG 测试不加载真实模型、不启动服务器、不访问外部 API，且不触碰真实用户 JSON。

### 同线 Minor

- 运维面板主、备用库及模型文字统一为 BGE/768，移除 UI 中 MiniLM/384 描述。
- 消费者与商家说明增加可复制自然语言示例；商家说明包含运维面板使用示例。

## TDD RED 证据

生产代码修改前先修改测试并运行：

1. 配置环境变量

   ```powershell
   F:\code\project1\.venv\Scripts\python.exe test\test_config_env.py
   ```

   输出摘要：`FAILED (failures=1)`，`RAG.embedding_model` 实际返回本地 BGE 路径而非 `test-primary-model`，退出码 1。

2. 找回密码限流与 Windows 直接认证

   ```powershell
   F:\code\project1\.venv\Scripts\python.exe test\test_auth.py
   ```

   输出摘要：11 个测试中 1 个预期失败；连续 3 次错误后第 4 次正确答案仍成功，退出码 1。该次直接命令已无 `PermissionError`，证明新文件策略绕开原 ACL 问题。

3. 双库维护

   ```powershell
   F:\code\project1\.venv\Scripts\python.exe test\test_rag_vector_maintenance.py
   ```

   输出摘要：`通过: 14 失败: 6 总计: 20`，失败点为备用库未收到单商品更新、无分库结果、备用库未独立重建、清空结果未同步，退出码 1。

4. UI fail closed、敏感字段与事件绑定

   ```powershell
   F:\code\project1\.venv\Scripts\python.exe test\test_web_ui_account_helpers.py
   ```

   输出摘要：7 个测试、10 个失败；未知角色映射为 merchant，四类账号事件均只绑定状态输出，回调未返回敏感字段清空更新，退出码 1。

## GREEN 与最终验证

最终验证命令及结果：

```powershell
F:\code\project1\.venv\Scripts\python.exe test\test_auth.py
F:\code\project1\.venv\Scripts\python.exe test\test_rag_pipeline_fallback.py
F:\code\project1\.venv\Scripts\python.exe test\test_rag_vector_maintenance.py
F:\code\project1\.venv\Scripts\python.exe test\test_web_ui_account_helpers.py
F:\code\project1\.venv\Scripts\python.exe test\test_config_env.py
F:\code\project1\.venv\Scripts\python.exe test\test_config.py
F:\code\project1\.venv\Scripts\python.exe -m py_compile config.py tools\auth_login.py embedding\rag_pipeline.py web_ui.py test\test_auth.py test\test_rag_pipeline_fallback.py test\test_rag_vector_maintenance.py test\test_web_ui_account_helpers.py test\test_config_env.py
git diff --check
```

输出摘要：

- 认证：`Ran 11 tests ... OK`，退出码 0。
- RAG 备用：`通过: 5 失败: 0 总计: 5`，退出码 0。
- RAG 维护：`通过: 20 失败: 0 总计: 20`，退出码 0。
- UI helper：`Ran 7 tests ... OK`，退出码 0。
- 配置环境：`Ran 1 test ... OK`，退出码 0。
- 旧配置回归：9 项 PASS，退出码 0。
- `py_compile`：退出码 0。
- `git diff --check`：退出码 0；仅显示 Windows LF→CRLF 工作区提示，无空白错误。

## 数据与临时文件检查

- 修复前后 SHA-256 一致：
  - `datas/consumer_users.json`：`A1E8C1BF172015EC2B0F4C36287839E48ECE3DD982DE336BED58A05E8169CF71`
  - `datas/merchant_users.json`：`AA056821BFEA59DC26170B08FA80D81C136CB4535E3DFA0440808B3F754EFBC8`
- 清理了旧版 `tempfile.mkdtemp()` 遗留的 13 个 ACL 异常测试目录；未删除业务数据。
- 最终扫描 `auth_test_*`、`web_ui_auth_*`、`rag_vector_maintenance_*`、`.users_*.tmp` 等模式：`TEMP_RESIDUE_COUNT=0`。

## 自查与残余顾虑

- 六个 Important 和两个同线 Minor 均有代码或测试映射，无发现未修 Important。
- 找回密码限流按评审要求是最小进程内实现；应用进程重启会清空计数，多进程间不共享状态。这是本地单进程 UI 的已知边界，若未来改为多实例服务需迁移到共享存储。
- 按 findings 的测试安全约束未启动 Gradio 服务器、未做浏览器视觉验收；事件绑定和返回基数由导入期 fake Gradio 契约测试覆盖。
- 当前工作树不存在 `test/test_docstrings.py`，因此额外的文档字符串检查命令不可用；指定文件的 `py_compile` 已通过。
