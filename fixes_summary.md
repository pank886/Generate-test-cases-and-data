# 修复总结报告

> 生成日期: 2026-07-10
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
| P1 | 14 |
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
