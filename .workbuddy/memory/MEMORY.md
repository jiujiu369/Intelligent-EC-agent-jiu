# 项目长期记忆 — 电商客服 Agent (project1)

## 技术栈
- Python + 云端LLM(agnes-2.0-flash) function-call 循环
- chromadb + BAAI/bge-base-zh-v1.5 做RAG
- 业务数据为 datas/*.json（中文字段名）

## 关键约定/坑
- ~~字段名中英不匹配是核心bug~~ → **已修复(2026-07-27)**：data_loader.py 所有字段名已改为中文，与 JSON 数据一致。
- ~~订单时间格式不匹配~~ → **已修复**：export_sales_report 用 `%Y-%m-%d %H:%M:%S` 解析，end_time 含当天23:59:59。
- ~~功能菜单死代码~~ → **已修复**：__main__ 增加 "菜单" 命令入口，菜单补齐7个选项。
- ~~新会话不可见~~ → **已修复**：新建对话后补 save_memory 写空文件。
- ~~create_aftersale_ticket 字段不一致~~ → **已修复**：alias_map 归一 + 默认字段补全。
- ~~README 英文命令~~ → **已修复**：改为实际中文命令。
- ~~schema.py 过时注释~~ → **已修复**。
- **教训**：测试含破坏性命令(reset_all)前必须先备份 agent_memory/，会话文件未被git跟踪，误删不可恢复。

## 入口
`agent/main_agent.py`（交互式CLI），`tools/data_loader.py` 启动自动 init_data()。
测试套件：17 个 test_*.py 文件，覆盖全部模块（auth/rbac/business_tools/error_handler/hallucination/rate_limiter/logger/api_monitor/prompt_manager/config/rag/fallback/vector_maintenance/集成/边界场景）。

## 登录认证模块（2026-07-27 新增）
- 模块：`tools/auth_login.py`
- 账号文件：`datas/consumer_users.json`、`datas/merchant_users.json`（角色拆分）
- 密码：PBKDF2-SHA256 加盐哈希，100k 迭代，32字节随机盐，恒定时间比较
- 预置测试账号：consumer→user1/123456；merchant→admin/admin123
- CLI 启动自动调 auth_interactive()，登录后获得 current_role + current_username
- 「重新登录」命令可切换账号，自动清空会话上下文
- 不改动 RAG、业务工具、rbac 源码

## Python 环境（2026-07-27）
- 已删除 Managed Python 3.13.12（缺所有依赖）
- System Python 3.14.6 四件套齐全：chromadb + sentence_transformers + PyPDF2 + python-docx
- `python` 命令现在直接可用，无需区分路径

## RBAC 角色权限控制（2026-07-27 新增）
- 模块：`tools/rbac.py`
- 角色：consumer消费者（5工具）/ merchant商家（7工具全量）
- 消费者禁用：update_goods、export_sales_report
- 双层防护：① get_filtered_schemas 过滤传给LLM的Schema ② check_permission 代码执行前二次校验
- 数据脱敏：消费者 query_goods 隐藏「上架状态」字段
- 角色切换：仅通过「重新登录」命令更换身份，切换时自动 clear_memory 清空上下文（直接切角色已禁止）
- 系统提示词追加 get_role_prompt_suffix 让LLM感知角色

## CLI 设计约定（2026-07-27）
- 默认会话名：「对话一」（中文编号，非 default/对话1）
- 无名新建对话自动编号：对话二、对话三...（中文数字，支持到九十九）
- 指令支持无空格：「新建对话售后」「新建对话：售后」「新建对话  售后」均等效
- 「菜单」显示可执行指令列表，「帮助」显示命令帮助（不再支持 /help /? 英文触发词）
- 功能菜单（show_feature_menu/interactive_feature_choice）已删除，纯对话式 Agent
- 「当前角色」命令已删除，角色信息通过登录 welcome 消息和提示符展示
- 提示符格式：[对话N|消费者] 或 [对话N|商家]

## 新增模块（2026-07-27 晚）
- `config.py` — 统一配置（API/PATHS/RAG/SESSION/AGENT），支持环境变量覆盖 `AGENT_<KEY>`
- `tools/prompt_manager.py` — 系统提示词模板化管理，build_system_prompt 按角色+上下文动态拼接
- `tools/error_handler.py` — 输入校验(空/乱码/闲聊/超长) + 工具参数校验 + 空结果包装 + 原子JSON读写(线程锁) + 会话文件损坏自动恢复 + 记忆摘要压缩(>20轮压缩旧消息)
- `tools/hallucination_checker.py` — 幻觉检测：黑名单词(绝对/肯定/保证/100%) + 订单号/金额/商品名交叉校验 + 风险评分(0-1,阈值0.3) + 会话统计
- `utils/rate_limiter.py` — 令牌桶(30/60s) + TTL缓存(query_goods 5min/rag_search 10min) + 重复提问去重(5s TTL)
- `utils/logger.py` — 按日切割(logs/YYYY-MM-DD.log) + 30天保留 + 会话名注入 + stdout errors=replace

## 已有模块的重要更新（2026-07-27 晚）
- `utils/api_monitor.py` — @rate_limit 装饰器 + HTTP状态码分类降级(401/402/429/5xx) + 指数退避重试(max 2)
- `embedding/rag_pipeline.py` — 回退模型(paraphrase-multilingual-MiniLM-L12-v2) + 关键词降级检索(jieba+TF-IDF,无jieba降级正则) + 向量维护(单商品更新/全量重建含回滚/去重入库/清空商品向量)
- `tools/schema.py` — query_goods 和 rag_search 经 cache_query_goods/cache_rag_search 包装TTL缓存
- `tools/data_loader.py` — 全部工具经 _guard_tool_result 包装(空结果→失败提示 + 异常捕获)
