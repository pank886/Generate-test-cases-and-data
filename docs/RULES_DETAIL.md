# 架构规则详情

> 最后编译: 2026-07-22 | 覆盖问题: 86 项 (P0×32, P1×35, P2×16, 架构改进×2, 死代码清理×1) | [Ref: 1~64]

<!-- RULE: M1 -->
<!-- PRIORITY: P0 -->
<!-- KEYWORDS: session.commit, get_session_ctx, add_product_doc_chunks, add_api_defs, _save_to_sqlite, _delete_sqlite_doc, _add_imported_file, BindingOps.delete_bindings_for_doc, DocOps.delete_document, os.remove, _remove_imported_file -->

## M1：事务边界与数据一致性

**核心定义**：跨存储介质写入必须保证数据一致性——SQLite 先写、ChromaDB 后写，失败时补偿回滚；删除操作必须同时清理 SQLite + ChromaDB + 内存三处。批量数据生成必须对唯一标识字段做去重校验。

**涵盖原规则**：DC-1~26, CS-7, CS-8, CS-9, [Ref: 21~23], [Ref: 25], [Ref: 27], [Ref: 60]

✅ **正确示例**：
```python
# SQLite 先写 → ChromaDB 后写 → 失败补偿
_save_to_sqlite(doc_id=doc_id, ...)
try:
    db.add_product_doc_chunks(doc_id, chunks)
except Exception:
    _delete_sqlite_doc(doc_id)
    raise

# 删除三处清理
DocOps.delete_document(session, doc_id)  # 1. SQLite
_chroma_db.delete_by_doc_id(doc_id)       # 2. ChromaDB
await _remove_imported_file(filename)      # 3. 内存

# 数据库会话统一管理
with get_session_ctx() as session:
    ...
# 自动 commit / rollback / close

# 批量事务（多条记录一次提交）
with get_session_ctx() as session:
    for d in docs:
        session.merge(d)
```

❌ **错误示例**：
```python
# 反序：ChromaDB 先写 → SQLite 失败则孤立
db.add_product_doc_chunks(doc_id, chunks)
_save_to_sqlite(...)

# 半删除：漏 _remove_imported_file
DocOps.delete_document(session, doc_id)
# 文件仍在内存列表 → 刷新后仍显示

# 循环内交替写入不同存储（半提交）
for api in apis:
    db.add_api_defs(doc_id, [api])
    _save_to_sqlite(...)
# 第 5 条失败 → 前 4 条已提交

# lifespan 赋值无 global
async def lifespan(app):
    _phase_c_graph = build_new_workflow()  # 局部变量！
```

**边界情况**：
- `_add_imported_file` 是内存缓存，失败时不应回滚 SQLite/ChromaDB（缓存可丢，数据不可丢）
- 删除端点中磁盘文件不存在时，仍须清理 SQLite + 内存（不阻断流程）
- **新增**：删除端点中 SQLite 记录缺失时，须从 `.meta.json` 读取 doc_id 尝试清理 ChromaDB，防止检索污染
- **新增**：补偿函数必须返回 bool 或抛出异常，调用方必须检查返回值并记录失败
- `get_session_ctx()` 内部已处理 commit/rollback，不需要在业务代码中手动调用
- **新增**：批量数据生成（Excel、YAML、数据库记录）必须对唯一标识字段做去重校验（`seen_ids` set），LLM 可能输出重复 ID——首轮校验和修复轮校验均须检查唯一性，重复 ID 标记错误进修复轮或直接丢弃。禁止仅校验字段格式而忽略唯一性 [Ref: 60]

---

<!-- RULE: M2 -->
<!-- PRIORITY: P0 -->
<!-- KEYWORDS: _invoke_structured, with_structured_output, METHOD_FEATURES, thinking, extra_body, model_validator, IntentConfirmation, ApiDefinition, TestCase, ChatPromptTemplate, prompt.input_variables, FallbackOllamaEmbeddings, _embed_via_old_api, /api/embed, /api/embeddings, _should_use_old_api -->

## M2：LLM 交互规范

**核心定义**：LLM 结构化输出必须用 Pydantic 模型 SSOT 约束，类型须覆盖框架实际能力边界；字段漂移用 `model_validator` 兼容；解析失败必须降级而非抛 500。thinking 与 json_mode/function_calling 互斥。校验失败不静默修正，抛教学级错误。LLM 输出裁剪由代码侧执行（禁 prompt 控制）。ChatPromptTemplate 中 JSON 示例必须用双大括号转义 `{key}`。

**涵盖原规则**：LLM-1~12, [Ref: 26], [Ref: 31], [Ref: 58], [Ref: 59], [Ref: 62]

✅ **正确示例**：
```python
# thinking 兼容性声明式配置
METHOD_FEATURES = {
    "function_calling": {"supports_thinking": False},
    "json_mode": {"supports_thinking": False},
    "json_schema": {"supports_thinking": False},
    "free_text": {"supports_thinking": True},
}

# 用 bind 而非 invoke 传 extra_body
bound_llm = self.llm.bind(**llm_kwargs)
result = bound_llm.invoke(prompt)

# 结构化输出 + 异常降级
try:
    result = self._invoke_structured(prompt, IntentConfirmation, ...)
    candidates = result.matched_modules
except Exception:
    candidates = []  # 降级而非抛 500

# 字段漂移兼容
@model_validator(mode="before")
@classmethod
def migrate(cls, data):
    if "matches" in data and "matched_modules" not in data:
        data["matched_modules"] = data.pop("matches")
    return data

# Pydantic SSOT（不在 prompt 中写 Schema）
class TestCase(BaseModel):
    request_body: Optional[Dict] = Field(
        default=None, serialization_alias="json", validation_alias="json"
    )

# ChatPromptTemplate 变量转义
prompt = ChatPromptTemplate.from_messages([
    ("system",
     "规则\n"
     '1. **输出格式**：{{"matched_modules": ["模块名1"], "confidence": "high"}}'
     # 双大括号 {{ → LangChain 渲染为字面量 {
    ),
    ("human", "用户输入: {user_input}"),
])
assert "matched_modules" not in prompt.input_variables  # 验证无泄漏
```

❌ **错误示例**：
```python
# if-elif 硬编码 thinking 兼容性
if method == "function_calling":
    thinking = False
elif method == "json_mode":
    thinking = False
# 新增 method 需改多处

# 用 invoke 而非 bind 传 extra_body
self.llm.invoke(prompt, **llm_kwargs)  # kwargs 被路由到 RunnableConfig

# 解析失败不降级 → 抛 500
result = self._invoke_structured(...)
# 直接崩溃，用户看到 500

# Dict[str, Any] 接 LLM 输出
testCase: List[Dict[str, Any]]  # 无结构约束

# prompt 中写 JSON Schema（58 行字符串）

# ChatPromptTemplate 中 {key} 未转义 → 变量泄漏
prompt = ChatPromptTemplate.from_messages([
    ("system",
     '输出格式：{"matched_modules": [...], "confidence": "high"}'
     # 单大括号 → LangChain 解析为模板变量！
    ),
    ("human", "用户输入: {user_input}"),
])
# prompt.input_variables = ["matched_modules", "confidence", "user_input"]
# ↑ user_input 和 module_list 传参时缺少 matched_modules → 格式错误
```

**边界情况**：
- private thinking（`free_text` 格式）可与 thinking 共存；仅 `function_calling`/`json_mode`/`json_schema` 需禁用
- 字段漂移频率 > 5% 时应优化 prompt 而非增加 validator 逻辑
- 节点函数中的 `_invoke_structured` 也必须包裹 try/except（如 `_format_test_points`）
- ChatPromptTemplate 中 JSON 格式字符串的 `{key}` 须用 `{{key}}` 双大括号转义；新增或修改 prompt 后须通过 `prompt.input_variables` 确认无意外变量泄漏
- `${{get_extract_data(...)}}` 等合法双括号引用不受影响（LangChain 将 `{{` 渲染为字面量 `{`）
- Embedding API 端点版本不匹配时必须降级：Ollama 服务端 v0.1.x 仅支持 `/api/embeddings`，Python 客户端 v0.6+ 调 `/api/embed` 返回 404 时，调用方必须自动降级到 `/api/embeddings`；降级状态按 URL 缓存至模块级存储（非 Pydantic 字段），避免同服务端重复探测
- **新增**：LLM 输出字段的 Pydantic 类型必须覆盖框架实际能力边界（如 `json` 参数接受 dict/list/str 三种形态），禁止 Schema 比运行时更狭窄——会导致 LLM 被迫捏造假结构绕过约束 [Ref: 58]
- **新增**：Schema 校验器（`model_validator`）失败时必须抛带有教学意义的 `ValueError`（包含：错在哪、为什么错、正确做法是什么），严禁静默修正（如 neq→ne 自动替换、extract 自动补 `$.` 前缀）。错误信息是修复轮中 LLM 自查的唯一线索，不含教学意义的错误信息等同于盲修 [Ref: 58]
- **新增**：Prompt 铁律必须与框架实际行为一致。禁止出现与框架行为矛盾的指令（如"仅 params 时不写 header"但框架直接读 `case_info['baseInfo']['header']` 缺键即 KeyError）[Ref: 58]
- **新增**：LLM 输出范围的裁剪必须由代码侧根据确定性的 ID 集合（如 `failed_ids`、`_already_confirmed`）执行，禁止依赖 prompt 指令（如"只能输出以下 ID"）让 LLM 自我约束。Prompt 只管"怎么修"，代码管"取哪些" [Ref: 62]
- **新增**：修复 prompt 只传入需要修复的条目（失败用例 + 错误原因 + 通过条件），禁止注入全量上下文（如 `original_test_analysis` 包含全部 53 条用例）。全量上下文浪费 token 且诱发 LLM 重复输出已通过用例 [Ref: 62]
- **新增**：前置门禁校验（如断言格式检查）失败不能硬阻断整个生成流程（`return result`），应降级为 `logger.warning` + 跳过问题行。阻断性校验必须放在生成循环内，借助修复轮兜底 [Ref: 59]

---

<!-- RULE: M3 -->
<!-- PRIORITY: P0 -->
<!-- KEYWORDS: except:, except Exception:, except: pass, catch (e) {}, extract_text, or default_value -->

## M3：异常处理与日志

**核心定义**：禁止裸 except、禁止空 catch、禁止静默吞异常、禁止将可能为 None 的值直接传给下游。所有 except 块必须至少记录日志。

**涵盖原规则**：EL-1~14, FP-9, FP-10, [Ref: 34], [Ref: 35], [Ref: 36], [Ref: 39], [Ref: 40], [Ref: 61], [Ref: 63]

✅ **正确示例**：
```python
# 精确异常捕获
except (ValueError, json5.Json5Exception) as e:
    logger.warning("解析失败: %s", e, exc_info=True)

# None 防御
text = page.extract_text() or ""
# 而非 "\n\n".join(page.extract_text())

# catch 记录日志
except Exception:
    logger.warning("操作失败", exc_info=True)

# 前端 catch 显示错误
catch (e) {
    console.error("加载失败:", e);
    el.innerHTML = '<div style="color:red">加载失败</div>';
}

# 外部服务异常包装
try:
    context = self.dual_chroma.search_context(...)
except Exception as e:
    context = f"【向量库异常】{e}，请检查 Ollama 服务"
```

❌ **错误示例**：
```python
# 裸 except → 吞 MemoryError/KeyboardInterrupt
except Exception:
    return {"error": "failed"}

# 空 catch → 无法排查
except Exception:
    pass

# 前端空 catch
catch (e) {}  # 用户永远看不到错误

# None 直接传递
"\n\n".join(page.extract_text())  # None → TypeError
```

**边界情况**：
- API 端点顶层 `except Exception` 返回 JSONResponse 是可接受的（最后一道防线），但必须记录日志
- `finally` 块中不应 return/raise（会覆盖 try 块异常）
- **新增**：非关键路径（如读取可选日志文件、解析可选元数据文件）失败时，必须 `logger.warning(..., exc_info=True)` 后继续流程，严禁 `except Exception: pass` 静默跳过 [Ref: 48, 49]
- **新增**：所有 Schema 校验器的失败必须进入可观测体系——独立于 `_generation_errors.json`，单独统计各规则的拦截次数、占比与错误样本，写入 `logs/VALIDATION_INTERCEPT.md`。用于持续优化提示词：命中次数最多的规则 → 优先强化对应铁律 [Ref: 63]
- **新增**：日志/摘要存储的数据必须是最终产出物全量（如 `valid_cases` 累计 53 条），禁止存储中间补丁（如最后一轮修复 LLM 输出的 8 条修复版）。摘要读取必须兼容所有活跃的模型版本（如 ExcelPlanV2 的 `test_cases` 和 ExcelPlan 的 `rows` 双 key 读取）[Ref: 61]

---

<!-- RULE: M4 -->
<!-- PRIORITY: P1 -->
<!-- KEYWORDS: threading.Lock, _lock, BoundedSemaphore, ThreadPoolExecutor, max_workers, max_queue, asyncio.Lock, run_coroutine_threadsafe, reload_llm, _llm_instance, @property llm, httpx.Client, asyncio.to_thread -->

## M4：并发安全

**核心定义**：双检锁的副作用必须在锁内执行；所有模块级单例必须用 `threading.Lock` 保护；线程池必须有界阻塞；跨工作流共享的 HTTP 客户端（LLM/Embedding）必须在每轮工作流启动时重建连接池。

**涵盖原规则**：CS-1~6

✅ **正确示例**：
```python
# 双检锁：检查 → 加锁 → 再检查 → 执行副作用 → 释放
if _instance is None:
    with _lock:
        if _instance is None:
            _instance = create()    # 副作用在锁内

# 有界线程池 + 背压
class _BoundedThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers=10, max_queue=30):
        self._sem = BoundedSemaphore(max_queue)
    def submit(self, fn, *args):
        self._sem.acquire()
        future = super().submit(fn, *args)
        future.add_done_callback(lambda _: self._sem.release())
        return future
```

❌ **错误示例**：
```python
# 双检锁：副作用在锁外
if _instance is None:
    with _lock:
        if _instance is None:
            pass
    _instance = create()  # 锁外初始化 → 竞态

# 标准线程池无界队列 → 突发流量 OOM
executor = ThreadPoolExecutor(max_workers=10)

# LLM 单例 httpx.Client 跨工作流复用 → 僵死连接导致 Connection error
_llm_instance = DeepSeekChatOpenAI(...)  # 全局单例，永不重建
# Phase B 大量调用后连接池残留僵死连接，Phase C 复用即崩溃
```

✅ **更多正确示例**：
```python
# LLM 客户端惰性属性 + 热重载机制
@property
def llm(self):
    return _get_llm()  # 惰性获取，reload_llm() 后自动拿新实例

# 工作流入口显式重建 HTTP 客户端
def _run_chat_bg(task_id, user_input):
    reload_llm()  # 销毁旧客户端 → 下一行 self.llm 重建 → 新连接池
    response = await asyncio.to_thread(_chat_func, user_input)
```

**边界情况**：
- `asyncio.Lock` 跨线程访问需经 `run_coroutine_threadsafe` 调度回事件循环
- 数据库引擎单例与 LLM 客户端单例保护模式相同
- `httpx.Client` 非线程安全，`asyncio.to_thread` 中使用的 HTTP 客户端必须独立于事件循环线程的生命周期；跨工作流复用时必须在入口处重建，关键路径：`reload_llm()` → `_llm_instance = None` → 下次 `@property llm` 惰性创建
- LLM 客户端的 `@property` 惰性获取模式可推广至所有需要热重载的模块级单例

---

<!-- RULE: M5 -->
<!-- PRIORITY: P0 -->
<!-- KEYWORDS: os.path.join, BASE_DIR, os.path.basename, os.remove, os.rename, _win_remove, mkdtemp, tempfile, _images -->

## M5：文件与路径安全

**核心定义**：所有文件路径以 `config.BASE_DIR` 为根；用户输入必须经 `os.path.basename()` 清洗；文件写操作必须包裹 `try/except OSError`；临时资源必须有 finally 清理。

**涵盖原规则**：FP-1~19, [Ref: 14~16], [Ref: 18], [Ref: 30]

✅ **正确示例**：
```python
# BASE_DIR 统一路径
file_path = os.path.join(config.BASE_DIR, "uploads", "md", safe_filename)

# 路径遍历防护
filename = os.path.basename(raw_filename)

# 文件删除防御
try:
    os.remove(file_path)
except OSError:
    logger.warning("删除失败: %s", file_path, exc_info=True)

# 临时目录 + try/finally
tmp_dir = tempfile.mkdtemp()
try:
    ...
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

# 临时目录命名含文件名标识
img_dir = os.path.join(base, f"_images_{file_stem}")

# 外部资源不可用 → 延迟补偿
if _chroma_db is None:
    asyncio.create_task(_retry_delete(doc_id))
```

❌ **错误示例**：
```python
# 相对路径
os.path.join("uploads", "md", filename)  # 依赖 CWD

# 用户输入直接拼路径
os.path.join("uploads", file.filename)   # 路径遍历！

# os.remove 无保护
os.remove(file_path)  # PermissionError → 500

# 删除后访问文件元数据
os.remove(fpath)
age = os.path.getmtime(fpath)  # 文件已删除 → FileNotFoundError

# 临时目录无 finally
tmp_dir = mkdtemp()
do_something()  # 抛异常 → 目录泄漏

# 固定临时目录名 → 并发冲突
img_dir = os.path.join(base, "_images")

# 外部资源不可用时静默跳过
if _chroma_db is None:
    pass  # 脏数据残留
```

**边界情况**：
- `_win_remove` 带重试（最多 3 次）用于 Windows Defender 锁定场景
- 删除端点中磁盘文件不存在不应阻断流程，须继续清理 SQLite + 内存
- `os.makedirs` 无需 try/except（已设 `exist_ok=True`）

---

<!-- RULE: M6 -->
<!-- PRIORITY: P1 -->
<!-- KEYWORDS: settings., config., Field(default=, global, lifespan, _phase_c_graph, _chroma_db, _chat_func, ThreadPoolExecutor, _BoundedThreadPoolExecutor, to_thread, heartbeat, _update_task, pollTask, load_dotenv, env_file, validation_alias, AliasChoices, .env, def get_xxx_logger, 死代码 -->

## M6：代码结构与配置

**核心定义**：`.env` 仅放模型地址/API Key（8 个字段），其余所有可调参数通过 `settings.py` 的 `Field(default=...)` 管理；`lifespan` 中赋值模块级变量必须 `global` 声明；全局单例在使用点必须判 None；长时间 `to_thread` 同步操作必须配套心跳协程更新前端进度；删除死代码前必须 grep 确认零引用。

**涵盖原规则**：CSL-1~17, [Ref: 25], [Ref: 28], [Ref: 29], [Ref: 32], [Ref: 56], [Ref: 57]

✅ **正确示例**：
```python
# 配置外化
class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")  # 无 env_file=
    chroma_retry_delay: int = Field(default=300, ge=10, le=3600)
    task_max_workers: int = Field(default=10)

# .env 仅放模型地址/Key，通过 load_dotenv() 加载
from dotenv import load_dotenv
load_dotenv()
class Settings(BaseSettings):
    deep_url: str | None = Field(default=None, description="[.env] DeepSeek API 地址")
    chunk_size: int = Field(default=1000, ...)  # settings.py 管理，.env 不生效

# lifespan global 声明
async def lifespan(app):
    global _chroma_db, _chat_func, _components, _vector_ready
    global _phase_c_graph, _phase_c_components

# 全局单例判 None
if _chroma_db is not None:
    _chroma_db.delete_by_doc_id(doc_id)

# 跨函数共享常量（模块级）
const labels = { product: '产品文档', api: '接口定义' };

# 有界线程池（配置化）
executor = _BoundedThreadPoolExecutor(
    max_workers=config.TASK_MAX_WORKERS,
    max_queue=config.TASK_MAX_QUEUE,
)

# 长时间 to_thread 操作 + 心跳进度上报
_heartbeat_stop = False
async def _heartbeat():
    nonlocal _heartbeat_stop
    _t0 = time.time()
    _messages = ["正在检索...", "正在分析...", "正在生成..."]
    while not _heartbeat_stop:
        await asyncio.sleep(10)
        if _heartbeat_stop: break
        elapsed = int(time.time() - _t0)
        await _update_task(task_id, progress=15,
                           message=f"{_messages[step]}（{elapsed}s）")

hb_task = asyncio.create_task(_heartbeat())
try:
    result = await asyncio.to_thread(sync_blocking_call, state)
finally:
    _heartbeat_stop = True
    hb_task.cancel()
    try: await hb_task
    except asyncio.CancelledError: pass
```

❌ **错误示例**：
```python
# 硬编码魔法数字
executor = ThreadPoolExecutor(max_workers=10)

# lifespan 缺 global
async def lifespan(app):
    _phase_c_graph = build_new_workflow()  # 局部变量 → 模块级仍是 None

# 全局单例直接调用（不判 None）
_chroma_db.delete_by_doc_id(doc_id)  # None 时 AttributeError → 500

# 函数内定义常量期望跨函数可见
function loadBoundDocs() {
    const labels = {...};  // 仅本函数可见
}
function loadUnassociatedDocs() {
    labels[dt]  // ReferenceError!
}

# to_thread 同步操作无心跳 → 前端进度卡死
await _update_task(task_id, progress=10, message="处理中...")
result = await asyncio.to_thread(long_blocking_call, data)
# ↑ 如果 long_blocking_call 执行 2 分钟，前端 2 分钟看不到任何进度变化
await _update_task(task_id, progress=80, message="处理完成")

# .env 承载所有配置 → 边界模糊
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", ...)
    chunk_size: int = ...   # ← 可从 .env 覆盖，来源不清
    retrieval_k: int = ...  # ← 同上

# 非模型字段设 validation_alias 引用 .env
testcase_base: str = Field(
    validation_alias=AliasChoices("PYTEST_DATA_DIR", "testcase_base")
)  # ← 鼓励将非模型配置放入 .env，破坏职责边界

# 死代码保留（grep 确认零引用后仍未删除）
def get_error_snapshot_logger():  # ← 无任何调用方，属于死代码
    ...
```

**边界情况**：
- `lifespan` 中创建的纯粹局部变量（如 `_cleanup_task`, `_meta`）无需 global 声明
- `settings.py` 中 `Field(ge=..., le=...)` 约束用于启动时校验
- 测试时可通过 `@patch("config.CHROMA_RETRY_DELAY", 0.1)` 缩短配置值
- **新增**：所有后台任务（`confirmPlan`、`_confirm_plan_bg` 等）必须配套实时进度更新，禁止仅用 toast 做首尾通知；前端须用 `pollTask` + 内嵌进度消息模式
- **新增**：TypedDict / dataclass 中同一字段名禁止重复定义（后定义静默覆盖前定义，编译期无报错）。代码审查时发现重复字段必须合并 [Ref: 53]
- **新增**：`.env` 仅可配置模型地址和 API Key（`EMBEDDING_MODEL`/`EMBEDDING_URL`、`DEEP_URL`/`DEEP_API_KEY`/`DEEP_MODEL`、`LLM_MODEL`/`LLM_API_KEY`/`LLM_BASE_URL`），共 8 项。其余所有可调参数必须在 `settings.py` 的 `Field(default=...)` 中管理。禁止为非模型字段设置 `validation_alias` 或 `AliasChoices` 指向 .env 变量名 [Ref: 56]
- **新增**：删除任何全局可访问的函数/类/变量前，必须用 grep 全量搜索确认零调用方。确认后立即删除，禁止保留已确认无引用的死代码（避免代码阅读干扰和后续维护者误用）[Ref: 57]

---

<!-- RULE: M7 -->
<!-- PRIORITY: P0 -->
<!-- KEYWORDS: {{, tojson, script src=, onclick=, onchange=, catch (e) {} -->

## M7：前端安全与交互

**核心定义**：静态 JS 文件禁止包含 Jinja2 模板语法；服务端数据必须通过 HTML `<script>` 块注入；异步操作 catch 禁止为空；页面初始化必须渲染首屏数据。

**涵盖原规则**：EL-14, CSL-14, CSL-15, CSL-17, [Ref: 24], [Ref: 64]

✅ **正确示例**：

```html
<!-- 模板注入变量 -->
<script>
    var VECTOR_READY = {
    {
        vector_ready | tojson | safe
    }
    }
    ;
    var INITIAL_FILES = {
    {
        imported_files | tojson | safe
    }
    }
    ;
</script>
<script src="/static/app.js?v=20260711"></script>
```
```javascript
// catch 非空
catch (e) {
    console.error("加载失败:", e);
    el.innerHTML = '加载失败，请重试';
}

// init 加载首屏
function init() {
    if (typeof INITIAL_FILES !== 'undefined') {
        renderFileList(INITIAL_FILES);
    }
    refreshFileList();
}
```

❌ **错误示例**：
```javascript
// 静态 JS 写 Jinja2 → SyntaxError
const INITIAL_FILES = {{ imported_files | tojson | safe }};
// 浏览器看到字面量 { → 整个脚本崩溃

// 空 catch → 静默吞所有错误
catch (e) {}

// init 不加载首屏 → 用户看到空白
function init() {
    // 不调 refreshFileList()
}

// 模板用 const 而非 var
<script>
  const VECTOR_READY = ...;  // 跨 <script> 块不可访问
</script>
```

**边界情况**：
- 模板中注入 JS 变量必须用 `var`（函数作用域可跨 `<script>` 块访问），不能用 `const`
- cache-busting 参数（`?v=YYYYMMDD`）在每次更新前端文件时递增
- `onclick=` / `onchange=` 内联事件绑定的函数必须在全局作用域可访问
- **新增**：`try/catch/finally` 之间共享的标志变量必须声明在函数作用域顶层，禁止在 `try{}` 块内用 `let/const` 声明（块作用域导致 `catch`/`finally` 无法访问）
- **新增**：动态文件路径传递给按钮时，必须用 `data-*` 属性 + 全局事件委托模式，禁止在 HTML 字符串中 `'...' + esc(path) + '...'` 拼入 JS 字面量（`esc()` 的 `&#39;` 会被 HTML 解码为 `'` 破坏 JS 语法）。正确模式：`data-action="openFile" data-path="' + encPath + '"` + `document.addEventListener('click', ...)` 委托分派 [Ref: 51]
- **新增**：上传等异步操作的完成状态用 JS 闭包变量追踪，不依赖 UI 文本内容匹配
- **新增**：并发文件上传必须为每个文件创建独立进度卡片（`up-{timestamp}-{random}` ID），禁止多文件共享全局单例进度指示器
- **新增**：HTML 文本内容展示用轻量 `escText()`（仅转义 `&` `<` `>`），HTML 属性值用完整 `esc()`（含 `\` `'` `"`），禁止混用导致反斜杠双重转义
- **新增**：所有 `catch` 块必须输出 `console.error(e)` 保留错误堆栈，禁止仅弹 toast 不记录原始错误（如 `catch (e) { toast('❌ 失败'); }` 缺少排查手段） [Ref: 54]
- **新增**：前端轮询（`pollTask`）的循环上限（`for (let i = 0; i < N; i++)`）乘以轮询间隔必须大于后端任务最长可能执行时间。YAML 生成等多轮 LLM 调用可能耗时 10-20 分钟，轮询上限 4 分钟（120×2s）会导致前端提前报"任务超时"而实际后端仍在跑。必须配合后端心跳 `_update_task` 每 10s 更新进度，前端展示实时进度文本 [Ref: 64]

<!-- RULE: M8 -->
<!-- PRIORITY: P0 -->
<!-- KEYWORDS: MOCK_, FAKE_, SAMPLE_, mock_data, fallback数据, 兜底数据, api_defs_json, 检索为空, 假数据, 静默降级 -->

## M8：数据真实性与缺失阻断

**核心定义**：生产链路任何环节（检索、生成、交接）遇到数据缺失时，必须显式失败并报告（`requires_review` / 任务 `failed` / 错误清单文件），严禁以 mock/示例/占位/硬编码假数据继续流程，严禁静默降级。

**涵盖原规则**：[Ref: 55]

✅ **正确示例**：

```python
# 关键输入缺失 → 显式阻断并给出可操作的提示
api_defs = _load_api_defs(excel_path)
if not api_defs:
    await _update_task(task_id, status="failed",
                       error="未找到接口定义（api_defs.json 缺失），请先完成 Phase B 或重新绑定接口文档")
    return

# 生成失败 → 登记错误清单，不写占位文件
if registry:
    with open(errors_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
```

❌ **错误示例**：

```python
# 检索为空时用假数据兜底（mock_data.py 原型，已删除）
docs = chroma_db.search_product_docs(query)
if not docs:
    docs = MOCK_PRODUCT_DOCS.get(module, [])   # ← 假数据混入生产流程

# 关键输入为空仍继续生成（Phase C 盲写 63 个 YAML 的事故原型）
def _confirm_plan_bg(task_id, excel_path, api_defs_json="", user_ctx=""):
    ...
    yaml_result = _generate_all_yamls(excel_path, api_defs_json, user_ctx)  # ← 空定义直传，字段全靠编
```

**边界情况**：

- **测试代码豁免**：`tests/` 下的 `MagicMock`、`SAMPLE_APIS`、伪造响应等属于标准单元测试手段，不在禁止范围。
- **协议级降级不属于假数据**：如 Embedding 新旧端点切换（M2 的 `FallbackOllamaEmbeddings`）是"换通道取同一份真实数据"，允许；被禁止的是"取不到数据就编一份"。
- **确定性格式规整不属于托底**：对 LLM 输出做 method 小写、url 去域名等语义等价修正（2026-07-18 质量治理计划清单 A）允许；语义性缺失/错误（清单 B）必须回炉或失败，不得代编。
- **骨架/初始化文件例外**：Skill A 冷启动创建的 `fixes_summary.md` 骨架属于文档初始化，非运行时数据托底。
