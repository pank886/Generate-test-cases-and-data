# 修复总结报告

> 生成日期: 2026-07-10 | 最后更新: 2026-07-20
> 范围: 全代码库架构审查与修复

---

## 目录

1. [统计数据](#1-统计数据)
2. [P0 — 致命缺陷](#2-p0--致命缺陷)
3. [P1 — 严重隐患](#3-p1--严重隐患)
4. [P2 — 中等风险](#4-p2--中等风险)
5. [架构改进](#5-架构改进)
6. [涉及模块清单](#6-涉及模块清单)

---

## 1. 统计数据

| 等级 | 数量 |
|------|------|
| P0 | 14 |
| P1 | 16 |
| P2 | 8 |
| 架构改进 | 2 |

---

## 2. P0 — 致命缺陷

### [1] [P0] 路径遍历漏洞 — 文件上传未清洗文件名

- **涉及模块**：`web/routes/api_extract.py`, `web/routes/files.py`
- **问题根因**：`os.path.join("uploads", "md", file.filename)` 直接拼接用户输入的文件名，攻击者可通过 `../../app/db.py` 覆盖项目源码
- **架构决策**：所有文件上传入口加 `os.path.basename()` 清洗目录遍历字符，API 提取入口额外加 UUID 前缀防同名覆盖（输入清洗 + 最小权限路径拼接）
- **衍生规则**：所有用户输入作为文件路径组成部分时，必须先通过 `os.path.basename()` 或等价函数清洗，禁止直接拼接用户输入到路径中

### [2] [P0] 向量库数据孤岛 — Phase A 检索与入库指向不同数据库

- **涉及模块**：`agent_components/nodes.py`, `agent_components/chromadb_file.py`, `agent_components/dual_chroma.py`
- **问题根因**：Phase A `_retrieve_node` 使用 `ReadersChromadb`（指向 `CHROMA_DB_DIR/my_rag_collection`），但所有数据通过 `DualChromaDB` 写入 `CHROMA_DB_DIR/product_docs/` + `CHROMA_DB_DIR/api_defs/`，读写路径不一致导致 Phase A 永远查不到数据
- **架构决策**：废弃 `ReadersChromadb`，`_retrieve_node` 改为调用 `self.dual_chroma.search_context()`，读写统一入口（废弃旧实现，统一数据访问路径）
- **衍生规则**：数据写入和读取必须使用同一套接口/同一数据库路径，禁止读写分离到不同实例

### [3] [P0] DeepSeek thinking 与结构化输出不兼容 — 多处漏配 + 控制不一致

- **涉及模块**：`agent_components/nodes.py`, `agent_components/retrievers.py`, `agent_components/generators.py`（10+ 处调用方）
- **问题根因**：DeepSeek V4 要求 `json_mode` / `function_calling` 必须禁用 `thinking`，但 `_invoke_structured` 缺少 json_mode 分支；多处调用方未传 `extra_body`；且 thinking 控制逻辑散落在 if-elif 中，新增 method 需改多处代码
- **架构决策**：
  - `_invoke_structured` 中 `function_calling` / `json_mode` / `json_schema` 统一禁用 thinking
  - 新增 `METHOD_FEATURES` 声明式配置表管理 method ↔ thinking 兼容性，未知 method 自动降级 + 日志告警
  - `_parse_api_node` 改用 `_invoke_structured` 调用，消除裸调用
  - Phase A/C 拆分为两阶段节点：thinking 分析阶段（自由文本）→ json_mode 格式化阶段（thinking off），彻底隔离不兼容配置
- **衍生规则**：新增 LLM 调用方式时，必须在 `METHOD_FEATURES` 配置表中声明其与 thinking 的兼容性；禁止在业务代码中用 if-elif 硬编码兼容性判断

### [4] [P0] `extract_text()` 返回 None 导致 join 崩溃

- **涉及模块**：`agent_components/chromadb_file.py`
- **问题根因**：`page.extract_text()` 在遇到图片页时返回 `None`，下游 `"\n\n".join()` 直接抛出 `TypeError`
- **架构决策**：`page.extract_text() or ""` 防御式编程，一行修复
- **衍生规则**：调用任何可能返回 None 的外部/第三方函数时，必须使用 `or default_value` 或显式 None 检查，禁止直接将返回值传给期望 `str` / `list` 的下游函数

### [5] [P0] `mkdtemp()` 无 try/finally — 异常时临时目录泄漏

- **涉及模块**：`agent_components/axure_parser.py`
- **问题根因**：`parse()` 中 `mkdtemp()` + `extractall()` 后如果解析抛异常，`self.cleanup()` 不会被调用，临时目录永久残留
- **架构决策**：整个解析逻辑用 `try/finally` 包裹，`finally` 中调用 `self.cleanup()`（RAII 资源管理模式）
- **衍生规则**：所有临时资源（目录、文件、连接）的创建必须紧跟 `try/finally` 清理块；禁止在无 finally 保护的情况下创建临时资源

### [6] [P0] 裸 `except Exception` 吞没致命异常

- **涉及模块**：`agent_components/axure_parser.py`
- **问题根因**：`except Exception: return {...}` 捕获所有异常（包括 `MemoryError`、`KeyboardInterrupt`），掩盖真正的系统错误
- **架构决策**：收窄为 `except (ValueError, json5.Json5Exception)`，仅捕获业务可预期的异常类型（精确异常捕获）
- **衍生规则**：禁止使用裸 `except Exception` 或 `except:`；必须捕获具体异常类型，或至少限定为明确的业务异常元组

### [7] [P0] Phase C 工作流恢复机制断裂

- **涉及模块**：`agent_components/retrievers.py`, `agent_components/graph_builder.py`
- **问题根因**：`_confirm_user_intent` 无条件返回 `workflow_status: "WAITING"`，覆盖恢复路径传入的 `"CONFIRMED"` 状态，条件边路由到 END 导致下游节点不可达
- **架构决策**：入口检查 `state.get("confirmed_module") and state.get("workflow_status") == "CONFIRMED"`，匹配时直接短路放行，不覆盖上游状态（恢复路径检测 + 短路返回）
- **衍生规则**：状态机节点入口必须先检查恢复路径/已确认状态，再执行业务逻辑；禁止无条件覆盖上游传入的状态标记

### [8] [P0] `all_apis_dict` 未定义导致 NameError

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`all_apis_dict` 只在条件分支内定义，当上游 `_analyze_scenarios_node` 返回 `all_apis_json` 时该分支被跳过，下游 `len(all_apis_dict)` 抛出 `NameError`
- **架构决策**：将 `all_apis_dict` 变量定义提升到条件分支外，确保所有代码路径中变量都已定义（变量定义前置）
- **衍生规则**：变量必须在使用前于所有代码路径中完成定义；禁止在条件分支内首次定义变量后在外层作用域直接使用

### [9] [P0] `get_chroma_db()` 单例工厂函数被误删

- **涉及模块**：`agent_components/dual_chroma.py`
- **问题根因**：删除废弃方法时误删了 `get_chroma_db()` 单例工厂函数，导致全模块 `ImportError`
- **架构决策**：恢复 `_chroma_instance`、`_chroma_lock`、`get_chroma_db()`，并追加 `threading.Lock` + 双重检查锁保证线程安全
- **衍生规则**：删除任何全局可访问的函数/类/变量前，必须用 grep/IDE 引用搜索确认零调用方；禁止仅凭记忆或局部搜索删除

### [10] [P0] `_load_factory_methods()` 双检锁临界区外置

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`with _factory_methods_lock:` 块只保护第二次检空，文件 I/O 和缓存赋值在锁外执行，多线程并发时重复读盘且可能读到不完整数据
- **架构决策**：文件 I/O + 缓存赋值全部移入锁内（双检锁正确实现：检查→加锁→再检查→执行副作用→释放）
- **衍生规则**：双检锁（DCL）模式中，文件 I/O、网络请求、缓存赋值等所有副作用操作必须全部在锁内执行；禁止将副作用操作放在锁外仅保护第二次空检查

### [11] [P0] `_generate_excel_plan_node` 重试耗尽后 `requires_review` 标记被丢弃

- **涉及模块**：`agent_components/graph_builder.py`, `agent_components/nodes.py`
- **问题根因**：重试耗尽返回 `{"requires_review": True}` 但不含 `response_obj` 字段，`chat()` 的 fallback 路径创建 `SimpleNamespace` 时丢失该标记，下游跳过人工审核环节
- **架构决策**：
  - `nodes.py` 重试耗尽路径补传 `response_obj: ProperResponse`
  - `graph_builder.py` fallback 路径补传 `requires_review` + `error_info`（状态逐级透传）
- **衍生规则**：多层调用链中，每层的 fallback/异常处理路径必须显式透传上层需要的关键标记字段；禁止创建新对象时丢弃上游传入的状态信息

### [12] [P0] 正则与 UUID 前缀长度不匹配 — 临时文件永久泄漏

- **涉及模块**：`web/app.py`, `web/routes/api_extract.py`
- **问题根因**：`api_extract.py` 用 `uuid4().hex[:8]`（8 位前缀），`_cleanup_temp_files_loop` 正则为 `[0-9a-f]{32}_`（要求 32 位），模式永不匹配
- **架构决策**：正则改为 `[0-9a-f]{8,32}_` 兼容变长前缀；`md_dir` 改为 `config.BASE_DIR` 绝对路径，保证清理循环在任意 CWD 下都能定位目录（模式匹配兼容 + 路径统一）
- **衍生规则**：字符串生成格式与匹配正则必须在同一处定义或从同一常量派生；禁止在代码不同位置独立硬编码互相依赖的格式字符串

### [13] [P0] 路径不统一 — 相对路径与绝对路径分裂

- **涉及模块**：`web/app.py`, `web/routes/files.py`, `web/routes/api_extract.py`
- **问题根因**：部分路径用 `"uploads/md"`（相对 CWD），部分用 `config.BASE_DIR` 绝对路径，CWD 改变时路径分裂，文件清理循环找不到上传的文件
- **架构决策**：全部改为 `os.path.join(config.BASE_DIR, "uploads", ...)` 绝对路径（统一基准路径）
- **衍生规则**：项目中所有文件系统路径必须以统一的 `BASE_DIR` 为根进行拼接；禁止使用相对路径或假设 CWD 的路径

### [14] [P0] 上传超限 `os.remove` 无异常处理 — Windows 文件残留

- **涉及模块**：`web/routes/files.py`
- **问题根因**：大文件超限时 `os.remove(file_path)` 可能被 Windows Defender 锁定抛 `PermissionError`，异常穿透导致 500 响应 + 文件残留
- **架构决策**：`try/except OSError` + `logger.warning`，删除失败不阻断响应流程（防御式删除 + 日志记录）
- **衍生规则**：所有文件系统写操作（删除、重命名、移动）必须包裹 `try/except OSError` 并记录日志；禁止假设文件操作一定成功

---

## 3. P1 — 严重隐患

### [1] [P1] 查询 N+1 — 关联文档逐条查 SQLite

- **涉及模块**：`web/routes/docs.py`
- **问题根因**：`for pt, pi in partners: session.get(DocModel, pi)` — 循环内逐条查询，50 个关联 = 50 次 SQL 往返
- **架构决策**：`session.query(DocModel).filter(DocModel.id.in_(ids)).all()` 一次批量查询 + 内存字典索引（批量查询替代逐条查询）
- **衍生规则**：涉及多条记录的数据库查询必须使用批量查询（`WHERE id IN (...)`）；禁止在 for 循环中逐条查询

### [2] [P1] `except Exception: pass` 静默吞异常（5 处）

- **涉及模块**：`web/tasks.py`, `web/app.py`, `web/routes/api_extract.py`
- **问题根因**：多处 `except Exception: pass` 静默丢弃异常，线上问题无从排查
- **架构决策**：全部改为 `logger.warning(..., exc_info=True)`，异常至少写入日志（异常日志化）
- **衍生规则**：所有 `except` 块必须至少记录日志（`logger.warning` + `exc_info=True`）；禁止使用 `except: pass` 或 `except Exception: pass` 静默吞没异常

### [3] [P1] `doc.doc_type if doc else "product"` — doc 为 None 时误判类型

- **涉及模块**：`web/services/doc_binding.py`
- **问题根因**：`doc` 不存在时默认 `"product"` 类型，若实际是 `api` 类型则错误绑定到 product 分类
- **架构决策**：`doc is None` 时直接抛出 `ValueError`，在数据入口处暴露问题（快速失败 / Fail-Fast 模式）
- **衍生规则**：可选值缺失时，应使用快速失败策略（抛异常）；禁止使用无依据的默认值掩盖数据缺失

### [4] [P1] SQLAlchemy 会话关闭后访问 ORM 对象

- **涉及模块**：`web/routes/files.py`
- **问题根因**：`session.close()` 后访问 `doc.id`，detached 状态下访问懒加载属性可能抛出 `DetachedInstanceError`
- **架构决策**：`doc_id = doc.id if doc else None` 移到 `finally` 之前，在会话内完成所有 ORM 属性读取并存入局部变量
- **衍生规则**：所有 ORM 对象属性必须在 `session.close()` 之前完成读取并存入局部变量；禁止在会话关闭后访问 ORM 对象的懒加载属性

### [5] [P1] LLM 单例非线程安全

- **涉及模块**：`agent_components/nodes.py`, `database/__init__.py`
- **问题根因**：`_get_llm()` 和 `get_engine()` / `get_session()` 无锁单例初始化，高并发时可能创建多个实例，导致连接泄漏或资源竞争
- **架构决策**：加 `threading.Lock` + 双重检查锁，保证多线程环境下仅创建一个实例（双检锁单例）
- **衍生规则**：所有模块级单例（LLM 客户端、数据库引擎、连接池等）必须使用 `threading.Lock` + 双重检查锁实现线程安全的懒加载

### [6] [P1] API Key 可能通过日志泄露

- **涉及模块**：`observability.py`, `config.py`
- **问题根因**：LLM 调用错误信息中包含 API Key，未经脱敏直接写入日志文件
- **架构决策**：
  - `JSONFormatter.format()` 中增加 `_sanitize()` 脱敏方法：匹配 `api_key=xxx`、`Authorization xxx` 等模式后替换为 `***`
  - `config.LLM_API_KEY` / `config.DEEP_API_KEY` 从模块级字符串改为运行时函数 `LLM_API_KEY()` / `DEEP_API_KEY()`，避免 API Key 在模块导入时驻留内存
- **衍生规则**：所有日志输出（尤其是异常堆栈、请求响应日志）必须经过脱敏处理；禁止将 API Key、Token、密码等敏感信息写入日志

### [7] [P1] 分析节点 thinking 模式未生效 — `bind` vs `invoke`

- **涉及模块**：`agent_components/nodes.py`, `agent_components/retrievers.py`
- **问题根因**：`_analyze_scenarios_node` 和 `_analyze_test_points_raw` 用 `self.llm.invoke(..., **llm_kwargs)` 传 `extra_body`，但 LangChain 的 `invoke` kwargs 被路由到 `RunnableConfig`，`thinking` 参数实际未传递到模型 API
- **架构决策**：改为 `self.llm.bind(**llm_kwargs).invoke(...)`，确保额外参数正确传递到模型 API（LangChain bind 方式）
- **衍生规则**：LangChain 中需要将额外参数传入模型 API 时，必须使用 `llm.bind(**kwargs).invoke()` 方式；禁止使用 `llm.invoke(..., **kwargs)` 传递 `extra_body` 等模型参数

### [8] [P1] 关联模块检索忽略 `doc_id` 过滤

- **涉及模块**：`agent_components/retrievers.py`
- **问题根因**：Hop 2a 和 2b 遍历关联模块时传 `doc_ids=None`，导致全库检索而非按模块过滤，检索结果包含不相关文档
- **架构决策**：每个模块先查 `BindingOps.get_bound_docs(session, mod)` 获取 `doc_ids`，再传 `_search_product_docs(query, doc_ids=bound_ids)` 进行模块级过滤（模块级权限过滤）
- **衍生规则**：多模块检索必须按模块粒度过滤关联文档；禁止以 `doc_ids=None` 进行全库检索

### [9] [P1] ChromaDB 检索无错误提示

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`_retrieve_node` 未捕获 ChromaDB 异常，异常穿透到顶层通用 `except`，用户仅看到"聊天处理失败"但不知根因
- **架构决策**：`try/except` 捕获 ChromaDB 异常，返回 `【向量库异常】{e}，请检查 Ollama 服务` 用户可理解的错误信息
- **衍生规则**：所有外部服务调用（数据库、API、向量库）必须捕获异常并返回用户可理解的错误信息；禁止让底层异常原始穿透到用户界面

### [10] [P1] 文件覆盖竞态 — 清理失败仍继续写入

- **涉及模块**：`web/routes/files.py`
- **问题根因**：同名文件覆盖时先清理旧数据，清理失败只打 warning 继续写入，导致 SQLite / ChromaDB 新旧数据混合不一致
- **架构决策**：清理失败立即 `return 500` 阻断上传流程，保证事务级原子性（全部成功或全部失败）
- **衍生规则**：多步写操作中，任一步骤失败必须阻断后续步骤并返回错误；禁止仅记录警告后继续执行

### [11] [P1] `/workflow/start` 缺少向量库就绪校验

- **涉及模块**：`web/routes/chat.py`
- **问题根因**：只检查文件列表非空，未检查向量库是否就绪，Ollama 异常时前端收到 0 条测试点但无错误提示
- **架构决策**：加 `if not _vector_ready: return 400` + 明确错误信息（前置条件检查）
- **衍生规则**：所有对外暴露的 API / 工作流入口必须显式校验依赖服务（数据库、向量库等）的就绪状态；禁止假设下游服务可用

### [12] [P1] `wb.save()` 后未 `wb.close()` — Windows 文件锁定

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`wb.save()` 后立即 `validate_excel_file()` 打开同一文件，Windows 上 openpyxl 可能未释放内部文件句柄，导致后续操作被拒绝访问
- **架构决策**：`wb.save()` 后显式调用 `wb.close()`（显式资源释放）
- **衍生规则**：所有文件写入操作后必须显式关闭文件句柄/工作簿对象；禁止依赖垃圾回收器自动释放文件资源

### [13] [P1] `DocOps.delete_document` 不自动清理 Binding 级联

- **涉及模块**：`database/operations.py`
- **问题根因**：`session.delete(doc)` 只级联删除 `GlossaryTerm`（ORM relationship 配置了 cascade），`Binding` 表无级联约束，删除文档后残留孤儿 Binding 记录
- **架构决策**：`delete_document` 内显式调用 `BindingOps.delete_bindings_for_doc(session, doc_id)`，在同一事务中清理关联表
- **衍生规则**：删除主记录时，必须在同一事务中显式清理所有关联表记录（Binding、Glossary 等）；禁止仅依赖数据库外键级联

### [14] [P1] `asyncio.Lock` 跨线程访问（已评估，无需修复）

- **涉及模块**：`web/app.py`
- **问题根因**：`_task_store_lock` 为 `asyncio.Lock`，被后台线程通过 `asyncio.run_coroutine_threadsafe` 访问
- **架构决策**：经分析，所有访问均经主事件循环调度，`asyncio.Lock` 在此模式下线程安全，无需修改
- **衍生规则**：`asyncio.Lock` 仅在协程内直接 await 时保证线程安全；跨线程访问必须通过 `asyncio.run_coroutine_threadsafe` 将操作调度回事件循环

---

### [15] [P1] Ollama Embedding 端点版本兼容层

- **涉及模块**：`agent_components/fallback_embeddings.py`, `agent_components/dual_chroma.py`
- **问题根因**：Python `ollama` 客户端 v0.6+ 使用 `/api/embed` 端点，但 Ollama 服务端 v0.1.x 仅有 `/api/embeddings`，LangChain 调用时返回 404，导致 RAG 检索链路中断
- **架构决策**：新增 `FallbackOllamaEmbeddings` 继承 `OllamaEmbeddings`，`embed_documents` / `aembed_documents` 先尝试新端点，404 时自动降级到 `/api/embeddings`；降级状态按 `base_url` 缓存至模块级 dict（非 Pydantic 字段），避免同服务端重复探测；仅首次降级时写 warning 日志
- **衍生规则**：
  - Ollama Python 客户端版本必须与服务端 API 版本兼容；若无法升级服务端，调用链路必须具备端点降级能力
  - 外部 HTTP 服务调用（Embedding / LLM / 第三方 API）必须有明确的降级或兜底路径
  - Pydantic BaseModel 子类的类变量不加类型注解（否则被 Pydantic 识别为 model field），降级状态等非业务字段使用模块级 dict 存储

### [16] [P1] LLM 连接池跨工作流污染（热重载机制）

- **涉及模块**：`agent_components/nodes.py`, `web/tasks.py`, `web/routes/chat.py`
- **问题根因**：`_llm_instance` 为模块级单例，底层 `httpx.Client` 连接池在 Phase B 大量调用后残留僵死连接，下一轮工作流（Phase B 或 Phase C）在新线程中复用时出现 `Connection error`；原有 `reload_llm()` 函数虽已定义但从未被调用
- **架构决策**：
  - `ChatTestAgentGraph.llm` 从 `__init__` 中 `self.llm = _get_llm()` 改为 `@property` 惰性获取，`reload_llm()` 将 `_llm_instance` 置 None 后，下次属性访问自动重建新实例
  - 三个工作流入口 `_run_chat_bg`、`_confirm_plan_bg`、`/workflow/start` 在处理前调用 `reload_llm()`，确保每轮工作流拿到独立的 `httpx.Client` 连接池
  - `_get_llm()` 双检锁不变，保证同一工作流内只创建一个客户端
- **衍生规则**：
  - 跨工作流/跨请求共享的 `httpx.Client` 必须在每轮启动时重建；`Client` 非线程安全，不得跨线程复用连接池
  - LLM / Embedding 等外部服务客户端若采用模块级单例模式，必须配套热重载机制（reset + lazy init），并在工作流入口显式触发
  - `asyncio.to_thread` 中使用的 HTTP 客户端必须独立于事件循环线程的生命周期管理

---

## 4. P2 — 中等风险

### [1] [P2] 双集合合并 `(pd + ad)[:k]` 丢弃 api_defs 结果

- **涉及模块**：`agent_components/dual_chroma.py`
- **问题根因**：product_docs 结果排在 api_defs 之前，简单拼接后 `[:k]` 截断导致 k 较小时 api_defs 几乎全部丢失
- **架构决策**：改为交错合并算法，两类结果轮流取前 k 条（公平合并算法，保证数据源多样性和结果完整性）
- **衍生规则**：多数据源合并时必须确保各类数据公平参与排序（如交错取前 k 条）；禁止简单拼接后截断

### [2] [P2] `load_dotenv()` 模块导入时副作用

- **涉及模块**：`agent_components/chromadb_file.py`
- **问题根因**：`import` 模块时自动执行 `load_dotenv(find_dotenv())`，污染模块导入过程，导致单元测试困难和环境变量加载时机不可控
- **架构决策**：改为延迟加载函数 `ensure_env_loaded()`，在 `__init__` 中显式调用（副作用延迟化，遵循关注点分离原则）
- **衍生规则**：禁止在模块顶层导入时执行具有副作用的操作（如 `load_dotenv`、网络请求、文件 I/O）；副作用操作必须封装为函数，由调用方显式触发

### [3] [P2] Axure 交互流/页面数截断无日志

- **涉及模块**：`agent_components/axure_parser.py`, `ingest_v2.py`
- **问题根因**：3 处数据截断（交互流截断 20 条、chunks 截断 50 页、page_details 截断 50 页）均为静默操作，数据丢失无法发现
- **架构决策**：3 处均加 `logger.warning`，输出原始数量和截断后数量（可观测性建设）
- **衍生规则**：任何数据截断/限制操作必须通过 `logger.warning` 记录原始数量与截断后数量；禁止静默丢弃数据

### [4] [P2] `_group_chunks_into_batches` 分隔符计数偏差 + 命名误导

- **涉及模块**：`ingest_v2.py`
- **问题根因**：首元素后多算分隔符长度导致本可同批的 chunk 被错误分到下一批；函数名 `_chunk_batches` 语义模糊
- **架构决策**：
  - 重命名为 `_group_chunks_into_batches`，明确语义
  - 简化逻辑：直接用 `"\n\n".join(batch + [c])` 计算实际拼接长度替代分隔符估算
- **衍生规则**：分批/分组算法必须使用实际拼接后的内容长度作为判断依据；禁止用分隔符长度估算代替实际度量

### [5] [P2] 大文件全量加载到内存

- **涉及模块**：`web/routes/files.py`
- **问题根因**：100MB 文件上传用 `chunks = []` 收集所有分片 + `b"".join()` 一次性合并，峰值内存 ~200MB，高并发下 OOM 风险
- **架构决策**：改为边读边写流式处理，每 8KB 直接刷盘（流式写入，内存占用恒定）
- **衍生规则**：处理文件上传/读取时，必须使用流式（分块读写）方式；禁止将完整文件内容一次性加载到内存中

### [6] [P2] `testCase` 从裸字典迁移到 Pydantic 模型 + 字段名漂移防御

- **涉及模块**：`prompts/response_model.py`（及所有消费 `testCase` 的模块）
- **问题根因**：
  - `testCase: List[Dict[str, Any]]` 无结构约束，LLM 输出字段不可预测
  - LLM 有时输出 `data` 字段名而非 `json`，下游代码字段名不匹配导致数据丢失
- **架构决策**：
  - 新增 `TestCase` Pydantic 模型（`case_name`, `json`, `params`, `validation` 等字段）
  - `StepData.testCase` 改为 `List[TestCase]`
  - `model_validator(mode="before")` 自动迁移 `data` → `json`（向后兼容 + 漂移统计日志）
  - JSON Schema 从 58 行 prompt 字符串删除，改为 Pydantic 模型单一事实来源（SSOT）
  - `method="json_mode"` 统一迁移为 `method="function_calling"`
- **衍生规则**：LLM 输出的数据字段必须使用 Pydantic 模型定义结构约束；禁止使用 `Dict[str, Any]` 裸字典接收 LLM 输出

### [7] [P2] `get_chroma_db()` 单例线程安全加固

- **涉及模块**：`agent_components/dual_chroma.py`
- **问题根因**：单例工厂函数无锁保护，高并发初始化时可能创建多个 ChromaDB 实例，导致资源浪费和实例不一致
- **架构决策**：加 `threading.Lock` + 双重检查锁（与 P1-[5] 同模式）
- **衍生规则**：所有模块级单例必须使用 `threading.Lock` + 双重检查锁实现线程安全的懒加载

### [8] [P2] 配置外化 — 6 项硬编码迁移到 settings

- **涉及模块**：`web/tasks.py`, `web/routes/files.py`, `web/app.py`, `agent_components/retrievers.py`, `config.py` → `settings.py`
- **问题根因**：线程池大小、队列上限、上传大小限制、会话超时、公共基础服务模块名等在业务代码中硬编码为魔法数字
- **架构决策**：全部迁移到 `settings.py` 集中管理（`task_max_workers`, `task_max_queue`, `upload_max_size_mb`, `workflow_session_ttl`, `common_service_module`）
- **衍生规则**：所有可变量（阈值、超时、大小限制、特性开关）必须通过 `settings` / `config` 模块集中管理；禁止在业务代码中硬编码魔法数字

---

## 5. 架构改进

### [A1] SQLite Session 统一管理

- **涉及模块**：全代码库 22+ 处 Session 使用点，`database/__init__.py`, `module_tree.py`
- **问题根因**：各模块手动 `get_session()` → `try/finally` 管理会话生命周期，模式重复且容易遗漏 `close()` 导致连接泄漏
- **架构决策**：
  - 新增 `get_session_ctx()` 上下文管理器（自动 commit / rollback / close）
  - 22+ 处手动的 `get_session()` → `try/finally` 迁移到 `with get_session_ctx() as session:`
  - `module_tree.py` 关键函数加可选 `session` 参数支持事务注入
- **衍生规则**：所有数据库会话必须通过统一的上下文管理器获取（`with get_session_ctx() as session:`）；禁止手动 `get_session()` + `try/finally` 管理会话生命周期

### [A2] ThreadPoolExecutor 有界队列

- **涉及模块**：`web/tasks.py`
- **问题根因**：标准库 `ThreadPoolExecutor` 使用无界队列，突发流量下任务无限堆积导致内存耗尽
- **架构决策**：`ThreadPoolExecutor` → `_BoundedThreadPoolExecutor`，`max_queue=30`，队列满时 `submit` 阻塞调用方，实现背压（Backpressure）机制
- **衍生规则**：所有线程池必须设置最大队列长度并有界阻塞；禁止使用无界队列的线程池处理外部输入驱动的任务

---

## 6. 涉及模块清单

| 模块 | 文件数 | 主要变更 |
|------|--------|---------|
| `prompts/` | 3 | Pydantic 模型重构、prompt 优化、JSON Schema 删除（SSOT） |
| `agent_components/` | 10 | 节点拆分、thinking 控制、异常处理、双检锁、向量库统一 |
| `web/routes/` | 6 | 路径遍历修复、异常处理、路径统一、流式上传 |
| `web/`（app + tasks） | 2 | 路径清理、有界线程池、会话管理、配置外化 |
| `database/` | 3 | Session 上下文管理器、WAL 优化、Binding 级联清理 |
| `ingest_v2.py` | 1 | 切分算法、doc_id 安全、截断日志 |
| `config.py` + `settings.py` | 2 | 6 项配置外化、API Key 运行时读取 |
| `observability.py` | 1 | JSON 脱敏、getMessage 安全兜底 |

**总计: 28+ 文件，200+ 处修改**

---

## 7. 后续修复补充（2026-07-10 架构审查后修复 + 2026-07-12 增量修复）

| 等级 | 数量 |
|------|------|
| P0 | 8 |
| P1 | 6 |
| P2 | 4 |

### [15] [P0] `api_extract.py` 文件路径未以 BASE_DIR 为根

- **涉及模块**：`web/routes/api_extract.py`
- **问题根因**：`os.path.join("uploads", "md", ...)` 使用相对路径依赖 CWD，`commit_api_endpoint` 的路径校验也使用了 `os.path.abspath("uploads")`
- **架构决策**：全部改用 `os.path.join(config.BASE_DIR, "uploads", ...)` 绝对路径，路径校验也改用 `config.BASE_DIR` 基准
- **衍生规则**：所有文件路径操作必须以 `config.BASE_DIR` 为根；`os.path.abspath("...")` 相对路径校验均需替换为 `config.BASE_DIR` 版本

### [16] [P1] `_cleanup_temp_files_loop` 中 os.remove 后访问已删文件

- **涉及模块**：`web/app.py`
- **问题根因**：`os.remove(fpath)` 成功后在同语句中调用 `os.path.getmtime(fpath)` 计算日志年龄，文件已删除导致 `FileNotFoundError`
- **架构决策**：将 `age` 计算移到 `os.remove` 之前，`os.remove` 包裹 `try/except OSError`，失败时 `continue` 而非继续处理
- **衍生规则**：文件状态变更操作（删除/重命名/移动）后，禁止在同一函数调用中访问该文件的元数据

### [17] [P1] Phase C 工作流 session 在后台任务入列后立即删除

- **涉及模块**：`web/routes/chat.py`, `web/tasks.py`
- **问题根因**：`/workflow/confirm` 中 `background_tasks.add_task()` 后立即 `pop(session_id)`，若任务被拒绝或失败则 session 不可恢复
- **架构决策**：`chat.py` 中不再 pop session，改为在 `_resume_workflow_bg` 的 `finally` 块中清理；异常退出时依赖 TTL（1800s）保障最终清理
- **衍生规则**：跨请求的工作流 session 必须有 TTL 兜底清理；后台任务应在 finally 中清理自己关联的 session，而非在入列时立即删除

### [18] [P2] docx 图片临时目录并发冲突

- **涉及模块**：`ingest_v2.py`
- **问题根因**：`_extract_docx` 和 `process_product_doc` 共用固定 `_images` 目录名，并发处理同一目录下的两个 docx 文件时 `finally` 的 `shutil.rmtree` 会误删另一线程的图片
- **架构决策**：抽取 `_docx_img_dir(file_path)` 函数，目录名包含文件名标识（`_images_{file_stem}`），实现文件级隔离
- **衍生规则**：临时目录/文件命名必须包含创建者标识（如源文件名、线程 ID），禁止使用固定名称的临时目录

### [19] [P2] `_serialize_for_log` 未处理 datetime 类型

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`isinstance` 链缺少 `datetime` 分支，datetime 对象走 `str(obj)` 兜底，输出格式非 ISO 标准
- **架构决策**：`datetime` 分支用 `.isoformat()` 序列化，符合 JSON 序列化标准
- **衍生规则**：自定义序列化函数必须覆盖所有标准库类型（str/int/float/bool/None/datetime/Decimal/UUID），避免不可预测的 str 兜底

### [20] [P2] 接口去重策略保留首次而非最新版本

- **涉及模块**：`agent_components/retrievers.py`
- **问题根因**：`seen_api.setdefault(key, a)` 保留首个出现的版本，多版本文档场景下用户期望保留最新版本
- **架构决策**：改为 `seen_api[key] = a`，后出现的覆盖先出现的；增加 `dup_count` 统计
- **衍生规则**：数据去重时，若保留最新版本语义明确（后写覆盖先写），应使用 `dict[key] = value` 而非 `dict.setdefault(key, value)`

### [21] [P0] 跨存储介质写入顺序错误 — ChromaDB 先写后写 SQLite

- **涉及模块**：`ingest_v2.py`（`process_product_doc`、`process_axure_zip`、`commit_api_docs`）
- **问题根因**：ChromaDB（向量库）和 SQLite（关系库）是两个独立的持久化层。旧顺序是"ChromaDB 先写 → SQLite 后写"，当 SQLite 写入失败时，ChromaDB 已落盘的数据成为孤立数据，且无任何补偿机制回收。
- **架构决策**：统一改为"SQLite 先写 → ChromaDB 后写"严格顺序。新增 `_delete_sqlite_doc()` 补偿函数，在 ChromaDB 写入失败时自动回滚 SQLite 记录。`commit_api_docs` 同时将循环内逐条写入改为批量 SQLite 事务 + 批量 ChromaDB。
- **衍生规则**：跨存储介质写入必须遵循"关系库先写，向量库后写"顺序。后写操作失败时必须有补偿回滚机制。涉及批量操作时，SQLite 写入必须合并为同一事务。

### [22] [P0] `commit_api_docs` 循环内逐条写入导致半提交状态

- **涉及模块**：`ingest_v2.py`（`commit_api_docs`）
- **问题根因**：在 `for api in apis` 循环中逐个处理并写入 ChromaDB + SQLite，一旦中途抛出异常，前面已处理的 API 处于"已提交"状态，后面的处于"未处理"状态，数据处于不可知的"半截子"状态。
- **架构决策**：分两阶段执行 — Phase 1 批量写入 SQLite（同一 `get_session_ctx()` 事务），Phase 2 逐条写入 ChromaDB。ChromaDB 任一条失败时回滚所有已写入的 SQLite 记录。
- **衍生规则**：涉及多条记录的跨存储写入必须保证：要么全部成功，要么全部回滚。循环内不允许交替写入不同存储。

### [23] [P1] `_add_imported_file` 在存储完成后调用导致内存状态滞后

- **涉及模块**：`web/tasks.py`（`_process_file_bg`）
- **问题根因**：存储（SQLite + ChromaDB）与内存状态更新之间存在时间差且无异常保护。`_add_imported_file` 在存储完成后调用，若该步抛出异常，数据已持久化但前端不显示。
- **架构决策**：`_add_imported_file` 调用包裹 `try/except`，失败时仅记录 WARNING 不阻断流程（数据已持久化，下次启动自动恢复）。同时在 file_info 中加入 `status: "ready"` 字段标识数据完整性。
- **衍生规则**：内存状态更新仅作为缓存加速，不应影响数据持久化流程。缓存更新失败时不能回滚已成功的持久化操作。

### [24] [P0] 前端文件列表展示缺陷 — 静态 JS 含 Jinja2 语法 + 模板缺变量定义

- **涉及模块**：`static/app.js`, `templates/index.html`, `web/tasks.py`
- **问题根因**（链式崩溃）：
  1. **【致命】静态文件含模板语法**：`static/app.js:2` 存在 `const INITIAL_FILES = {{ imported_files \| tojson \| safe }};`——`app.js` 通过 `/static/app.js` 直接提供，**不经过 Jinja2 渲染**，浏览器收到字面量 `{{` 后抛出 `SyntaxError: Unexpected token '{'`，导致整个 `<script>` **完全停止执行**。后续所有函数（`uploadFile`、`refreshFileList` 等）均未定义，页面所有交互失效。
  2. **模板未注入变量**：即使第 1 点不存在，`templates/index.html` 中未定义 `VECTOR_READY` / `INITIAL_FILES`，`init()` 中 `setNavStatus(VECTOR_READY)` 会因读取未声明变量抛 ReferenceError。
  3. **空 catch 吞异常**：`refreshFileList()` 的 `catch (e) {}` 空块吞掉所有错误。
  4. **`.md` 文件跳过后端列表更新**：`_process_file_bg` 在 `.md` 文件 LLM 提取后提前返回，未调用 `_add_imported_file`。
- **架构决策**：
  - **删除 `app.js` 中的 Jinja2 语法**（`static/app.js:2-3`），改为注释说明变量由模板注入。
  - 模板在 `<script>` 块中用 `var VECTOR_READY = {{ vector_ready \| tojson \| safe }};` 和 `var INITIAL_FILES = {{ imported_files \| tojson \| safe }};` 注入服务端数据。
  - `app.js:init()` 改为先 `typeof VECTOR_READY !== 'undefined'` 检查，避免崩溃。使用 `INITIAL_FILES` 首屏直出代替异步请求。
  - `refreshFileList()` 的 `catch` 改为 `console.error` + 页面错误文案。
  - `.md` 文件提前返回前调用 `_add_imported_file`。
  - 脚本加载加 `?v=20260711` 缓存破坏参数。
- **衍生规则**：
  1. **静态 JS 文件（`/static/*.js`）绝对禁止使用 Jinja2 模板语法 `{{ }}` 或 `{% %}`**——它们不经过 Jinja2 渲染。服务端数据必须通过 HTML 模板中的 `<script>` 块注入。
  2. 模板中注入 JS 变量必须用 `var` 而非 `const`（`var` 是函数作用域可跨 `<script>` 块访问）。
  3. 所有前端异步操作 catch 块禁止为空。
  4. 页面初始化必须渲染首屏数据（服务端直出）。
  4. 前端脚本添加 cache-busting 参数防止浏览器缓存旧版本。


### [25] [P0] lifespan 中 _phase_c_graph 缺少 global 声明

- **涉及模块**：web/app.py, agent_components/retrievers.py, web/routes/chat.py
- **问题根因**：lifespan() 函数中 _phase_c_graph, _phase_c_components = build_new_workflow() 未在函数头部的 global 声明中列出，Python 将其解释为局部变量，赋值后函数退出即丢弃。模块级变量始终为 None，/workflow/start 每次读取都返回报错。
- **架构决策**：在 lifespan 函数头增加 global _phase_c_graph, _phase_c_components。新增测试用例 test_all_module_globals_in_global_stmt 用 AST 静态扫描自动验证。
- **衍生规则**：lifespan 中赋值任何模块级变量时，必须同步添加 global 声明。

### [26] [P0] IntentConfirmation 字段漂移 + _confirm_user_intent 缺少异常降级

- **涉及模块**：prompts/response_model.py, prompts/definitions.py, agent_components/retrievers.py
- **问题根因**：LLM 输出字段名 matches 而非 matched_modules，且值格式为 [{module: xxx}] 而非 [xxx]。Pydantic 校验失败抛异常，穿透到 /workflow/start 返回 500，用户看不到候选模块确认界面。
- **架构决策**：IntentConfirmation 增加 model_validator(mode=before)，自动处理字段名和值格式双重漂移。_confirm_user_intent 中 _invoke_structured 调用包裹 try/except，解析失败时降级为 WAITING 状态让用户重新输入。
- **衍生规则**：所有 LLM 结构化输出解析点必须包裹 try/except，解析失败时降级而非抛异常。

### [27] [P0] 删除文件端点 if not doc: 分支漏调 _remove_imported_file

- **涉及模块**：web/routes/files.py
- **问题根因**：delete_file() 中当 SQLite 无记录时提前 return 前未调用 _remove_imported_file，文件仍留在内存中，刷新后仍然显示。
- **架构决策**：增加 _remove_imported_file 调用。同时增加 _chroma_db is Not None 防御。删除端点重构为不依赖磁盘文件存在。
- **衍生规则**：删除操作必须同时清理 SQLite + ChromaDB + 内存三处。

### [28] [P1] _chroma_db 空指针导致 get_module_docs 和 delete_file 崩溃

- **涉及模块**：web/routes/modules.py, web/routes/files.py
- **问题根因**：_chroma_db 为 None 时直接调用 get_doc_apis()/delete_by_doc_id() 抛 AttributeError，端点返回 500。
- **架构决策**：两处均增加 if _chroma_db is not None: 防御 + try/except 兜底。
- **衍生规则**：全局单例在使用点必须判 None，禁止直接调用方法。

### [29] [P1] loadUnassociatedDocs 中 labels 未定义 + 渲染目标 div 错误

- **涉及模块**：static/app.js
- **问题根因**：loadUnassociatedDocs() 引用 labels 但该变量仅在 loadBoundDocs() 的 const 作用域内。且写入 unassociated-docs（中栏）而非 unassociated-by-type（右栏可关联文件），右栏始终为空。
- **架构决策**：补 const labels = {...}。目标元素改为 unassociated-by-type。catch 从空块改为 console.error + 页面提示。
- **衍生规则**：跨函数共享常量应定义为模块级，禁止在某个函数内定义后期望另一函数可见。

### [30] [P2] ChromaDB 不可用时异步延迟重试删除

- **涉及模块**：web/routes/files.py, settings.py, config.py
- **问题根因**：ChromaDB 不可用时删除端点跳过清理，向量库残留脏数据且无补偿机制。
- **架构决策**：创建 asyncio 延迟任务，CHROMA_RETRY_DELAY 秒后重试。结果同步输出到控制台和日志。配置项统一管理到 settings.py。
- **衍生规则**：外部资源不可用时不应静默跳过，应创建延迟补偿机制。

### [31] [P0] LangChain Prompt 模板变量未转义导致意图识别永久降级

- **涉及模块**：`prompts/definitions.py`
- **问题根因**：`confirm_user_intent()` 的 system prompt 中 JSON 输出格式示例 `{matched_modules}` 和 `{confidence}` 未使用双大括号转义，LangChain 的 `ChatPromptTemplate.from_messages()` 将其解析为模板变量。调用链只传入 `user_input` 和 `module_list`，缺少 `matched_modules` 导致格式化失败，`_confirm_user_intent` 每次进入 `except` 降级路径，用户永远看不到模块推荐。
- **架构决策**：将 `{matched_modules}` 和 `{confidence}` 改为 `{{matched_modules}}` 和 `{{confidence}}` 双大括号转义。新增 API 集成测试 `test_workflow_api.py` 验证 `prompt.input_variables` 不含泄漏的变量名。
- **衍生规则**：所有 `ChatPromptTemplate` 的 system/human message 中包含 JSON 格式示例时，其中的 `{key}` 必须使用 `{{key}}` 双大括号转义。新增或修改 prompt 时须通过 `assert "unexpected_var" not in prompt.input_variables` 确认无意外模板变量泄漏。

### [32] [P1] 后台任务进度无心跳 — 长时间 LLM/graph 执行时前端进度"卡死"

- **涉及模块**：`web/tasks.py`
- **问题根因**：`_resume_workflow_bg` 和 `_run_chat_bg` 只有头尾两处进度更新（10% 和 80%），中间整个 LangGraph 执行（ChromaDB 查询 + 多轮 LLM 调用）是单次 `to_thread` 调用，期间无任何进度上报。前端 `pollTask` 每次轮询看到相同的进度值，用户感知为"界面卡死"。
- **架构决策**：在独立线程同步执行 LangGraph（`asyncio.to_thread`），主协程启动独立心跳协程，每 10s 更新一次进度消息并附带已运行秒数。心跳协程在 `to_thread` 返回后通过 `CancelledError` 安全终止。心跳消息按执行阶段轮转（检索产品文档→提取关联模块→检索接口定义→分析测试场景→生成测试计划）。
- **衍生规则**：所有在 `to_thread` 中执行的长时间同步操作，必须配套心跳协程定期更新任务进度；禁止让前端轮询在长时间同步操作期间看到不变的进度值。


---

## 8. 2026-07-14 批量修复（Skill C 执行）

### [33] [P0] JS `let _uploadDone` 块作用域导致上传进度卡永远不隐藏

- **涉及模块**：`static/app.js`
- **问题根因**：`let _uploadDone = false` 声明在 `try {}` 块内，JavaScript 块作用域导致 `catch` 和 `finally` 块无法访问。`catch` 中的 `_uploadDone = true` 实际创建了全局 `window._uploadDone`，`finally` 读到的是 `undefined`，卡片永远不隐藏。
- **架构决策**：将 `let _uploadDone` 提升至函数作用域（`try` 之前），确保所有块可见。同时将 `catch` 路径也设置 `_uploadDone = true`。
- **衍生规则**：JS 中需要在 `try/catch/finally` 间共享的标志变量必须声明在函数作用域顶层，禁止在 `try` 块内用 `let/const` 声明。

### [34] [P1] `_serialize_for_log` 缺少 Decimal/UUID 类型分支

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：自定义序列化函数的 `isinstance` 链缺少 `Decimal` 和 `UUID` 标准库类型，走 `str()` 兜底导致 Decimal 被序列化为字符串而非数字。
- **架构决策**：新增 `Decimal` → `float()` 和 `UUID` → `str()` 显式分支。
- **衍生规则**：自定义序列化函数必须覆盖所有标准库类型（datetime/Decimal/UUID），禁止依赖 `str()` 兜底。

### [35] [P1] delete-file 端点 SQLite 无记录时漏清 ChromaDB 和 .meta.json

- **涉及模块**：`web/routes/files.py`
- **问题根因**：`if not doc:` 分支只清理物理文件+内存状态，跳过 ChromaDB 向量清理和 `.meta.json` 删除。当 SQLite 记录因历史崩溃丢失时，ChromeraDB 残留数据污染检索。
- **架构决策**：在 `if not doc:` 分支中从 `.meta.json` 读取 `doc_id`，尝试调用 `_chroma_db.delete_by_doc_id()`。同时删除 `.meta.json` 文件。
- **衍生规则**：删除操作的三处清理（SQLite + ChromaDB + 内存）中，任一处不可用时不应跳过其他处，应从可用数据源（meta.json 等）恢复缺失的标识符。

### [36] [P1] 路径包含检查 `str.startswith` 可被 sibling 目录绕过

- **涉及模块**：`web/routes/files.py`
- **问题根因**：`abs_path.startswith(allowed_dir)` 可将 `/app/testcases_backup/file` 误判为在 `/app/testcases` 内。同时 `os.path.abspath("uploads")` 依赖 CWD 而非项目根目录。
- **架构决策**：改用 `os.path.commonpath([abs_path, d]) == d` 严格路径包含检查，并包裹 `try/except ValueError` 防跨盘符崩溃。`uploads` 改为 `os.path.join(config.BASE_DIR, "uploads")` 绝对路径。
- **衍生规则**：文件访问控制中的路径包含检查必须用 `commonpath` 或 `pathlib.Path.is_relative_to()`，禁止用 `str.startswith`。

### [37] [P2] `_read_excel_rows` 打开工作簿后未关闭

- **涉及模块**：`agent_components/generators.py`
- **问题根因**：`load_workbook()` 后无 `wb.close()`，Windows 上可能因文件句柄泄漏导致后续 `os.remove` 失败。
- **架构决策**：加 `try/finally` 确保 `wb.close()` 始终执行。

### [38] [P2] `_extract_text` 中 PDF `extract_text()` 重复调用

- **涉及模块**：`ingest_v2.py`
- **问题根因**：列表推导式 `[p.extract_text() for p in pages if p.extract_text()]` 对每个页面调用两次。
- **架构决策**：改为显式循环，每个页面只调一次。

### [39] [P2] `_invoke_structured` 中 free_text 方法未显式控制 thinking

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`features["supports_thinking"]=True` 且 `thinking=False` 时，`llm_kwargs` 为空，DeepSeek API 行为不可预测。
- **架构决策**：为所有路径显式设置 `extra_body`，无默认隐式行为。

### [40] [P2] `_resolve_path("")` 静默返回项目根目录

- **涉及模块**：`config.py`
- **问题根因**：空字符串传给 `_resolve_path` 时，`os.path.isabs("")` 返回 `False`，`os.path.join(BASE_DIR, "")` 返回 `BASE_DIR`，导致路径配置错误时静默指向项目根。
- **架构决策**：空字符串时抛出 `ValueError`，明确拒绝无效配置。

### [41] [P1] 输出路径硬编码为 `./testcase_out`，依赖 CWD

- **涉及模块**：`config.py`, `settings.py`
- **问题根因**：`testcase_base` 默认值 `"./testcase_out"` 依赖工作目录。同时 `.env` 变量名 `TESTCASE_BASE` 与用户已有 `PYTEST_DATA_DIR` 不一致。
- **架构决策**：新增 `PYCHARM_MISC` 配置项，`TESTCASE_BASE = PYCHARM_MISC + PYTEST_DATA_DIR` 组合路径。`settings.py` 使用 `AliasChoices` 兼容新旧变量名。启动时强制校验，未配置则报错。


---

## 9. 2026-07-14 前端优化 + 可观测性建设

### [42] [P1] 多文件上传共享单进度卡，并发上传互相覆盖

- **涉及模块**：`static/app.js`, `templates/index.html`, `static/style.css`
- **问题根因**：全局唯一 `upload-progress-card`，多个文件同时上传时后一个覆盖前一个的进度。
- **架构决策**：改为动态创建独立进度卡（`up-{timestamp}-{random}`），上传完成 3 秒后渐隐移除。CSS flex column 布局 + fadeIn 动画。
- **衍生规则**：并发操作的 UI 反馈必须是独立隔离的，禁止全局单例进度指示器。

### [43] [P2] 文件路径展示时反斜杠被双重转义

- **涉及模块**：`static/app.js`
- **问题根因**：`esc()` 函数将 `\` → `\\`，用于 HTML 属性安全。展示文本（聊天消息、弹窗）无需转义反斜杠，导致 Windows 路径展示为 `C:\\Users\\...`。
- **架构决策**：新增 `escText()` 轻量版（只转义 `&` `<` `>`），文本展示用 `escText()`，属性值保持 `esc()`。
- **衍生规则**：HTML 属性值必须用完整 `esc()`，HTML 文本内容用轻量 `escText()`。

### [44] [P2] PY+YAML 生成时前端只有 toast 无实时进度

- **涉及模块**：`static/app.js`
- **问题根因**：`confirmPlan` 只用 `toast` 提示开始和结束，中间无进度更新，用户不知道生成进展。
- **架构决策**：改为聊天框内嵌进度消息，`pollTask` 实时更新 `⏳ 正在生成 .py...（20%）` → `✅ 完成`，与 Phase C 工作流体验一致。
- **衍生规则**：所有后台任务的前端反馈必须包含实时进度条/文本，禁止仅用 toast 做首尾通知。

### [45] [P2] Thinking 节点原始输出无记录，提示词调优缺数据

- **涉及模块**：`observability.py`, `agent_components/nodes.py`, `retrievers.py`, `generators.py`
- **问题根因**：`_analyze_scenarios_node`、`_analyze_test_points_raw`、`_analyze_data_deps` 三个 thinking 节点的 LLM 原始输出只打 `logger.info` 摘要，完整内容丢失。
- **架构决策**：新增 `log_thinking(node, user_input, output)` 写入 `logs/thinking_trace.log`（RotatingFileHandler，5MB/10归档），记录节点名+用户输入+完整输出。
- **衍生规则**：所有 LLM 非结构化输出节点必须将原始结果写入专属日志，禁止仅记录长度或截断内容。

### [46] [P2] 工作流日志路径硬编码，未跟随 LOG_DIR 配置

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：`Path("logs") / "workflow"` 硬编码，不受 `LOG_DIR` 配置影响。
- **架构决策**：改为 `Path(config.LOG_DIR) / "workflow"`。

### [47] [P1] Axure 文档绑定 type 存储为 product，解绑失败

- **涉及模块**：数据库记录
- **问题根因**：历史上 Axure 文档与模块的绑定记录使用了错误的 `type="product"` 而非 `"axure"`，导致前端传正确 type 时 normalize 查询不到，解绑报"绑定不存在"。
- **架构决策**：修复数据库中 `axure_` 前缀绑定的 type 为 `axure`。

---

## 10. 2026-07-18 Phase B 实现 + Skill B/C 修复

### [48] [P0] `_resume_workflow_bg` 读取思考日志时 `except Exception: pass` 静默吞异常

- **涉及模块**：`web/tasks.py`
- **问题根因**：Phase B 恢复执行时读取 `thinking_trace.log` 提取失败用例编号，`except Exception: pass` 吞掉所有异常（编码错误、权限拒绝），`failed_tc_ids` 为空列表，前端不展示校验失败警告。
- **架构决策**：改为 `logger.warning("无法读取思考日志，跳过失败用例提取", exc_info=True)`，降级但不静默。
- **衍生规则**：同 [2] — 所有 `except` 块必须至少记录日志

### [49] [P0] delete-file 端点读取 meta.json 时 `except Exception: pass` 静默吞异常

- **涉及模块**：`web/routes/files.py`
- **问题根因**：删除文件时从 `.meta.json` 读取 `doc_id`，`except Exception: pass` 吞掉 JSON 解析异常，`_doc_id` 保持 None，ChromaDB 孤儿数据永不清理。
- **架构决策**：改为 `logger.warning("读取 meta.json 失败，跳过 ChromaDB 孤儿清理: %s", meta_path, exc_info=True)`。
- **衍生规则**：删除操作中的可选步骤失败时，应记录日志后继续（不阻断主流程），但必须保留异常信息供排查

### [50] [P0] 路径包含检查 `startswith` → `commonpath`

- **涉及模块**：`web/routes/files.py`
- **问题根因**：文件访问白名单用 `str.startswith` 做目录归属判断，`/tmp/attack_app` 会通过 `/tmp/app` 前缀检查，存在路径穿越风险。
- **架构决策**：改为 `os.path.commonpath([abs_path, d]) == d` 语义等价的严格检查，消除前缀绕过。
- **衍生规则**：同 [36] — 路径包含检查必须用 `commonpath` 或 `is_relative_to()`，禁止 `startswith`

### [51] [P0] `onclick` 属性中 `esc(path)` 拼接 JS 字符串 — 改用 `data-*` + 事件委托

- **涉及模块**：`static/app.js`
- **问题根因**：3 处 `onclick="openLocalFile('...' + esc(path) + '...')"` 中 `esc()` 将 `'` 转 `&#39;`，浏览器 HTML 解码后 `&#39;` 还原为 `'`，破坏 JS 字符串边界。
- **架构决策**：替换为 `data-action="..." data-path="..."` 属性，新增全局 `click` 事件委托处理器，按 `data-action` 值分派函数调用。
- **衍生规则**：动态数据传递给 JS 事件处理函数时，必须使用 `data-*` 属性 + 全局事件委托模式；禁止在 HTML 字符串中拼接 JS 字面量

### [52] [P1] `os.path.abspath("uploads")` 相对路径依赖 CWD

- **涉及模块**：`web/routes/files.py`
- **问题根因**：文件访问白名单中 `os.path.abspath("uploads")` 依赖 CWD，不同启动方式解析到不同目录。
- **架构决策**：改为 `os.path.abspath(os.path.join(config.BASE_DIR, "uploads"))`，与项目其他路径统一基准。
- **衍生规则**：同 [15] — 所有路径以 `config.BASE_DIR` 为根

### [53] [P1] `test_point_analysis` 字段在 TypedDict 中重复定义

- **涉及模块**：`agent_components/state.py`
- **问题根因**：同一 TypedDict 键定义两次，类型相同但注释不同。Python 中后定义覆盖前定义，编译期无错误但造成混淆。
- **架构决策**：删除第一条重复定义，合并注释。
- **衍生规则**：TypedDict / dataclass 字段名必须唯一；禁止重复定义同一键

### [54] [P1] 前端 12 处 `catch` 块缺少 `console.error`

- **涉及模块**：`static/app.js`
- **问题根因**：多处 API 调用 `catch (e) { toast('❌ ...'); }` 不记录 `console.error`，用户看到通用错误提示但无法排查具体原因。
- **架构决策**：统一追加 `console.error(e)` 保留错误堆栈，便于开发者从控制台排查。
- **衍生规则**：所有前端 `catch` 块必须输出 `console.error`，禁止仅弹 toast 不记录原始错误

### [55] [P0] 生产链路虚假数据托底清除 — mock_data.py 删除与数据缺失阻断

- **涉及模块**：`data_factory/mock_data.py`（已删除）、`web/tasks.py`、`web/routes/chat.py`、`agent_components/generators.py`
- **问题根因**：`mock_data.py` 自述"ChromaDB 无结果时兜底"，内置 `MOCK_PRODUCT_DOCS`/`MOCK_API_DEFS` 假数据（合同/房产等与本项目无关的样例）；同类问题：`/confirm-plan` 链路 `api_defs_json` 恒为空时，Phase C 未阻断而是静默盲写 63 个 YAML，字段名/类型/断言大面积幻觉（`status: 正常开放` vs 接口定义 `gymStatus: integer`、`code` vs `retCode`、提取 returns 中不存在的 `$.data.id`）。
- **架构决策**：删除 `mock_data.py`（经全盘检索确认已无生产代码引用）；确立"数据缺失必须显式失败"原则——检索为空/定义缺失/交接丢失时，返回 `requires_review`、任务 `failed` 或写入错误清单（如 `_generation_errors.json`），绝不以任何假数据继续流程。
- **衍生规则**：系统任何环节（检索、生成、交接）遇到数据缺失，必须显式阻断并报告，严禁以 mock/示例/占位/硬编码假数据托底续跑，严禁静默降级；单元测试中的 MagicMock/测试样例数据不在禁止范围。

---

## 11. 2026-07-20 配置架构重构 + 死代码清理

### [56] [P1] 配置管理架构重构：.env 职责收缩为仅模型地址/Key

- **涉及模块**：`settings.py`, `config.py`, `web/app.py`
- **问题根因**：`.env` 文件承载了所有配置项（从模型地址、API Key 到 chunk_size、retrieval_k 等可调参数），导致 .env 膨胀、配置来源不清晰、敏感信息与非敏感参数混在一起。开发者不确定某项配置该改 .env 还是改 settings.py。
- **架构决策**：
  - `.env` 仅保留模型地址和 API Key（8 个字段：`EMBEDDING_MODEL`/`EMBEDDING_URL`、`DEEP_URL`/`DEEP_API_KEY`/`DEEP_MODEL`、`LLM_MODEL`/`LLM_API_KEY`/`LLM_BASE_URL`）
  - 其余所有可调参数（`chunk_size`、`retrieval_k`、`embedding_timeout`、线程池大小、超时等 25+ 项）统一在 `settings.py` 中通过 `Field(default=...)` 管理
  - `SettingsConfigDict` 移除 `env_file=".env"`，改用 `load_dotenv()` 手动加载，明确配置边界
  - 移除已废弃的 env 变量别名（`PYTEST_DATA_DIR` → `testcase_base`、`LANGCHAIN_URL` → `llm_base_url`）
  - `config.py` 和 `web/app.py` 的报错提示从"检查 .env"改为"检查 settings.py"
- **衍生规则**：
  - 新增配置项时：属于模型地址/API Key → 入 .env；属于可调参数 → 只在 settings.py 加 Field(default=...)
  - 禁止为非模型字段添加 `validation_alias` 或 `AliasChoices` 指向 .env 变量名
  - 禁止在 .env 中放置 chunk_size、retrieval_k、timeout、线程池大小等可调参数
  - .env 文件仅包含 8 个模型相关变量，其余一律不认

### [57] [P2] 移除废弃的 repair_failures 快照 Logger

- **涉及模块**：`observability.py`, `agent_components/nodes.py`, `agent_components/retrievers.py`
- **问题根因**：`get_error_snapshot_logger()` 函数及配套的 `_repair_logger` 全局变量已无任何调用方，属于死代码。`repair_failures.log` 文件不再生成，残留的 RotatingFileHandler 配置造成代码阅读干扰。
- **架构决策**：删除 `get_error_snapshot_logger()` 函数定义（~30 行）及 `nodes.py`/`retrievers.py` 中对应的无用 import。`repair_failures.log` 引用从 `settings.py` 日志目录描述中移除。
- **衍生规则**：删除任何全局可访问的函数/类/变量前，必须用 grep 确认零调用方；禁止保留已确认无引用的死代码

---

## 12. 2026-07-22 YAML 合规审查 + Phase B/C 生成链路修复（Skill C 执行）

### [58] [P0] `request_body` 类型过窄 + Schema 校验体系缺失 → YAML 9 类合规问题无人拦截

- **涉及模块**：`prompts/response_model.py`, `prompts/extraction_prompts.py`, `agent_components/generators.py`
- **问题根因**：
  1. `request_body: Optional[Dict[str, Any]]` 只能接受 dict，LLM 遇到数组型 body（如 `/electricMeter/delete` 的 `["id1","id2"]`）时被夹在中间：API 要数组，Schema 要 dict → LLM 被迫捏造 `{body: [...]}` 包裹层
  2. StepData/TestCase 无校验器，URL `${}` 占位符、`neq` 运算符、header 缺键、params 错放 baseInfo、GET/POST 方法-参数不匹配、extract JSONPath 缺 `$` 前缀、validation 为空 等 7 类问题进入 YAML 后运行时才暴露
  3. 原有 prompt 铁律 #4 "仅 params 时不写 header" 是误导指令，教 LLM 省略 header 导致框架 KeyError
- **架构决策**：
  - `request_body` 放宽为 `Optional[Union[Dict[str, Any], List[Any], str]]`，与 requests 库 `json` 参数能力对齐
  - StepData 新增 4 个 validator：url 禁 `${}`、header 必存在、params 禁放 baseInfo、方法-参数类型匹配
  - TestCase 新增 3 个 validator：neq 非法（应为 ne）、extract JSONPath 必须 `$.` 开头、validation 不能为空
  - 全部 validator 不静默修正，抛 ValueError 附完整错误原因+正确做法，倒逼 LLM 修复轮自查
  - `format_yaml_data_prompt` 铁律全面重写（12→13 条），修正"不写 header"误导指令，新增 url 禁 `${}`、params 归属 testCase、断言运算符白名单、JSONPath `$.` 前缀等硬约束
- **衍生规则**：
  - LLM 输出字段的 Pydantic 类型必须覆盖框架实际能力边界（如 `json` 参数接受 dict/list/str），禁止 Schema 比运行时更狭窄
  - 框架合规校验应在生成阶段（Schema validators）兜底，禁止依赖运行时暴露
  - Schema 校验失败不静默修正，必须抛带教学意义（错在哪+为什么+正确做法）的错误信息
  - Prompt 铁律必须与框架实际行为一致，禁止包含"XX 情况不写 YY"等与框架行为矛盾的指令

### [59] [P0] 断言格式前置校验硬阻断 Phase C 全部 YAML 生成

- **涉及模块**：`agent_components/generators.py`
- **问题根因**：`_generate_all_yamls()` 在 YAML 生成循环前执行断言格式校验（`_parse_assertion`），校验失败直接 `return result`，`total=0, success=0`。后续 `_run_yaml_rounds()` 修复逻辑永远不可达。同时 `_parse_assertion` 禁止同一步骤出现多个断言关键词（如 `[contains]...，[ne]...`），但这是合理的复合断言语义。
- **架构决策**：
  - 删除多断言关键词拦截规则
  - 校验失败从硬阻断 `return result` 改为仅 `logger.warning`，不阻断后续 YAML 生成
  - 真正的断言结构校验交由 `response_model.py` 的 TestCase validators 在生成阶段拦截
- **衍生规则**：前置门禁校验失败不能阻断整个流程，应降级为 warn + 跳过问题行；阻断性校验必须放在生成循环内（借助修复轮兜底）

### [60] [P0] Excel 生成缺少 case_id 去重 → 6 组重复用例写入 Excel

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**：LLM 在初始生成中输出了重复的 case_id（同一 ID 出现两次），校验逻辑检查了字段非空、前置引用、步骤/预期数量匹配，唯独没有 case_id 去重检查。同一 `TC-012` 通过所有校验 → `all_confirmed.append()` 两次 → 写入 Excel 两行。
- **架构决策**：
  - 首轮校验新增 `seen_ids` set，遇到重复 ID 标记错误进入修复轮
  - 修复轮校验新增 `_already_confirmed` 检查，拒绝已通过用例的重复输出
- **衍生规则**：批量数据生成（Excel、YAML、数据库记录）必须对唯一标识字段做去重校验；禁止仅校验字段格式而忽略唯一性

### [61] [P1] Excel 工作流日志摘要与实际数据脱节（三重偏差）

- **涉及模块**：`agent_components/nodes.py`
- **问题根因**（链式偏差）：
  1. **模型不兼容**：摘要代码读 `plan.get("rows", [])` 但实际模型是 `ExcelPlanV2`（字段名 `test_cases`），永远读到空列表 → 前端显示"0 条用例，0 模块"
  2. **全量 vs 补丁**：`_log_node_output` 存的 `plan` 是最后一轮修复 LLM 的输出（仅失败行的修复版，如 8 条），而非累计写入 Excel 的 `valid_cases` 全量（53 条）→ 前端显示"共 8 条用例"
  3. 实际 Excel 写入的是 `valid_cases`（全量累计，53 条），与日志完全不一致
- **架构决策**：
  - 摘要兼容 `test_cases`（ExcelPlanV2）和 `rows`（ExcelPlan）两种模型
  - `_log_node_output` 改为存储 `valid_cases` 全量（`[tc.model_dump() for tc in valid_cases]`）而非最后一轮 `plan`
  - story 字段名兼容两种模型（`r.get("story", r.get("module_name", ""))`）
- **衍生规则**：日志/摘要存储的数据必须是最终产出物（全量），禁止存储中间补丁或部分快照；摘要读取必须兼容所有活跃的模型版本

### [62] [P1] Excel 修复 prompt 模型侧控制 + 全量上下文泄漏 → LLM 输出已通过用例

- **涉及模块**：`prompts/extraction_prompts.py`, `agent_components/nodes.py`
- **问题根因**：
  1. 修复 prompt 通过 `{failed_ids}` 和"只能输出以下 ID"指令要求 LLM 自我约束输出范围——这是模型侧控制，不可靠。LLM 忽略指令仍输出已通过用例，代码虽拦截但 token 已浪费
  2. `{original_test_analysis}` 传入完整 53 条用例的全量分析报告，修复 8 条失败用例时 LLM 看到全部上下文 → 诱发"热心"重生成已通过用例
- **架构决策**：
  - 剥离 prompt 中所有模型侧输出范围控制（删除 `{failed_ids}` 占位符、删除"只能输出以下 ID"/"不要包含已通过的用例"指令）
  - 输出裁剪完全由代码负责：`failed_ids` 过滤 + `_already_confirmed` 去重，LLM 输出什么 ID 都行，代码只取合法部分
  - 移除 `{original_test_analysis}` 全量上下文，修复 prompt 仅传入 `{failed_test_cases}`（失败用例 + 错误原因 + 通过条件）
- **衍生规则**：LLM 输出范围的裁剪必须由代码侧根据确定性的 ID 集合执行，禁止依赖 prompt 指令让 LLM 自我约束；修复 prompt 只传入需要修复的条目，禁止传入全量数据作为"上下文"

### [63] [P1] ValidationInterceptor — Schema 校验拦截可观测性

- **涉及模块**：`prompts/response_model.py`, `agent_components/generators.py`
- **问题根因**：Schema validators 校验失败后错误信息仅流向修复轮和 `_generation_errors.json`，无汇总统计。无法回答"哪个规则命中最多""提示词哪条最需要优化"。
- **架构决策**：
  - 新增 `ValidationInterceptor` 类（类级别计数器 + 样本收集）
  - 每个 validator 失败时调用 `ValidationInterceptor.record(rule_name, error_message)`
  - `_run_yaml_rounds` 开始时 `reset()`，结束时 `write_report("logs")` 写入 `logs/VALIDATION_INTERCEPT.md`
  - 报告含：总拦截次数、各规则次数与占比、各规则错误信息样本（最多 3 条）
- **衍生规则**：所有 Schema 校验器的失败必须进入可观测体系（计数 + 样本），用于持续优化提示词；禁止校验失败后仅抛异常不统计

### [64] [P2] 前端轮询超时 4 分钟 → YAML 生成超时误报

- **涉及模块**：`static/app.js`
- **问题根因**：`pollTask()` 循环上限 120 次 × 2 秒 = 4 分钟，YAML 生成阶段多轮 LLM 调用远超 4 分钟，前端提前报"任务超时"而实际后端还在跑。
- **架构决策**：循环上限 120→900（30 分钟），覆盖 YAML 生成最长时间。后端心跳继续每 10s 更新进度。
- **衍生规则**：前端轮询超时必须大于后端任务最长可能执行时间；长时间任务必须有后端心跳 + 前端长轮询双重保障

---

## 13. 统计数据更新

| 等级 | 本次新增 | 累计 |
|------|---------|------|
| P0 | 3 | 17 |
| P1 | 3 | 19 |
| P2 | 1 | 9 |
| 架构改进 | 0 | 2 |
