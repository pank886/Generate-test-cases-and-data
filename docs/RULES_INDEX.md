# 架构规则索引

> 最后编译: 2026-07-20 | 元规则数: 8 | 覆盖问题: 79 项 (P0×29, P1×32, P2×15, 架构改进×2, 死代码清理×1)

## 第一部分：元规则速查

| # | 名称 | 口诀 |
|---|------|------|
| M1 | 事务边界与数据一致性 | 关系库先写向量库后写，删三处(SQLite+ChromaDB+内存)，删时Chrom缺失也清理，补偿必回滚且返回bool |
| M2 | LLM/Embedding 交互规范 | thinking 不碰结构化，Pydantic 控输出，解析失败必降级，ChatPromptTemplate 双大括号转义，Embedding 端点版本不匹配必降级 |
| M3 | 异常处理与日志 | 禁裸 except，禁空 catch，禁静默吞，自定义序列化全覆盖(含Decimal/UUID)，exc_info=True 不遗漏 |
| M4 | 并发安全 | 双检锁副作用归锁内，单例必上锁，线程池必有界，跨工作流 HTTP Client 必重建 |
| M5 | 文件与路径安全 | BASE_DIR 为根，basename 洗输入，os.remove 包 OSError，临时目录含标识，路径包含用 commonpath 非 startswith，resolve_path 拒空值 |
| M6 | 代码结构与配置 | .env 仅放模型地址/Key，其余参数 settings.py 管；lifespan 配 global；全局单例判 None；to_thread 配心跳；无引用代码必清 |
| M7 | 前端安全与交互 | 静态 JS 禁模板语法，catch 禁空，变量注入用 var，try/catch/finally 共享变量声明在顶层，动态路径用 data-* 禁 onclick 拼接 |
| M8 | 数据真实性与缺失阻断 | 数据缺失必显式失败(requires_review/failed/错误清单)，禁 mock/示例/占位假数据托底，禁静默降级续跑；测试 MagicMock 豁免 |

## 第二部分：关键词 → 规则映射表

| 关键词（代码中出现即触发） | 对应规则 | 风险等级 |
|---------------------------|----------|---------|
| `session.commit`, `get_session_ctx`, `add_product_doc_chunks`, `add_api_defs` | M1 | P0 |
| `delete_by_doc_id`, `_save_to_sqlite`, `_delete_sqlite_doc` | M1 | P0 |
| `_add_imported_file`, `_remove_imported_file`, `BindingOps.delete_bindings_for_doc` | M1 | P0 |
| `DocOps.delete_document`, `background_tasks.add_task` + `pop(session_id)` | M1 | P0 |
| `for ... in ...:` 内 `session.merge`/`session.add` 交替 ChromaDB 写入 | M1 | P0 |
| delete 端点 `if not doc:` 未调 `_chroma_db.delete_by_doc_id` | M1 | P1 |
| 补偿函数不返回 bool/不检查返回值 | M1 | P1 |
| `_invoke_structured`, `with_structured_output`, `METHOD_FEATURES` | M2 | P0 |
| `thinking`, `extra_body`, `model_validator(mode="before")` | M2 | P0 |
| `ChatPromptTemplate.from_messages`, `prompt.input_variables`, `{{` 转义 | M2 | P0 |
| `llm.invoke(...` 传 `extra_body`（应为 `llm.bind(**kw).invoke()`） | M2 | P0 |
| `/api/embed`, `/api/embeddings`, `FallbackOllamaEmbeddings` | M2 | P1 |
| `_embed_via_old_api`, `_mark_old_api`, `_should_use_old_api` | M2 | P1 |
| `except:`, `except Exception:`, `except Exception: pass` | M3 | P0 |
| `except Exception:\\s*pass` (无日志的静默吞) | M3 | P0 |
| `catch (e) {}`, `catch(e){}` | M3, M7 | P0 |
| `catch (e) { toast(` 后无 `console.error` | M7 | P1 |
| `logger.error(...)` 缺 `exc_info=True` | M3 | P0 |
| `print(f"...")` 用于运行时日志（应为 `logger.info(...)`） | M3 | P1 |
| `isinstance` 链缺 `datetime` / `Decimal` / `UUID` 分支 | M3 | P1 |
| `.extract_text()` 无 `or ""`，`or default_value` | M3 | P0 |
| `os.remove(fpath)` 后访问 `os.path.getmtime(fpath)` | M3, M5 | P1 |
| `let _uploadDone` / `let x` 在 `try{}` 内部（应用函数作用域顶层） | M7 | P0 |
| `threading.Lock`, `_lock`, `BoundedSemaphore` | M4 | P1 |
| `ThreadPoolExecutor`, `max_workers`, `max_queue` | M4 | P1 |
| `asyncio.Lock`, `run_coroutine_threadsafe` | M4 | P1 |
| `if _instance is None:` + `with _lock:` 副作用在锁外 | M4 | P0 |
| `reload_llm`, `_llm_instance = None`, 跨工作流 LLM 单例重建 | M4 | P1 |
| `@property llm`, 惰性获取 HTTP 客户端 | M4, M6 | P1 |
| `asyncio.to_thread` + LLM/Embedding 调用，`httpx.Client` 跨线程复用 | M4 | P1 |
| `os.path.join(`, `BASE_DIR`, `os.path.basename` | M5 | P0 |
| `os.remove`, `os.rename`, `os.replace`, `_win_remove` | M5 | P0 |
| `os.path.abspath("uploads")` 相对路径（应为 `config.BASE_DIR` 基准） | M5 | P0 |
| `.startswith(d)` 路径包含检查（应用 `os.path.commonpath`） | M5 | P0 |
| `_resolve_path("")` 空字符串调用（应 `raise ValueError`） | M5 | P1 |
| `mkdtemp`, `tempfile`，固定名称 `_images`（无创建者标识） | M5 | P1 |
| `shutil.rmtree` 无 `ignore_errors=True` | M5 | P1 |
| `settings.`, `config.`, `Field(default=` | M6 | P1 |
| `global `, `lifespan`, `_phase_c_graph`, `_chroma_db` | M6, M1 | P0 |
| `_chroma_db.` 无 `if _chroma_db is not None:` 前置检查 | M6 | P1 |
| `to_thread` + 长时间操作无心跳协程 | M6 | P1 |
| 心跳协程 `progress=固定值` 不递增 | M6 | P2 |
| `const labels = {...}` 在函数内（跨函数不可见） | M6, M7 | P1 |
| `dict.setdefault(key, value)` 去重保留旧版本 | M6 | P2 |
| TypedDict 同一键多次定义（重复字段） | M6 | P1 |
| `load_dotenv()` / `env_file=".env"` + 非模型字段 | M6 | P2 |
| `validation_alias` / `AliasChoices` 用于非模型配置项 | M6 | P1 |
| `.env` 文件含 `chunk_size`、`retrieval_k`、`timeout` 等参数 | M6 | P1 |
| `def get_xxx_logger` 无调用方引用（grep 零结果仍保留） | M6 | P2 |
| `{{`, `{%`, `tojson \| safe`, `script src=` | M7 | P0 |
| `onclick=`, `onchange=`, `addEventListener` | M7 | P1 |
| `data-action=` (正确模式：data-* + 事件委托) | M7 | ✅ 合规 |
| 模板中 `const VECTOR_READY`（应为 `var`） | M7 | P0 |
| `<script>` 块中 `var INITIAL_FILES` 首屏注入 | M7 | P0 |
| `onclick="...' + esc(path) + '..."` 拼接 JS（应用 `data-*`+委托） | M7 | P1 |
| `<script src="...">` 无 cache-busting `?v=` 参数 | M7 | P2 |
| `asyncio.create_task` + `_retry_delete_later` 延迟补偿 | M1, M5 | P2 |
| `_docx_img_dir`, `file_stem` 临时目录文件级隔离 | M5 | P2 |
| 单 `upload-progress-card` 被多文件并发覆盖（应动态创建独立卡片） | M7 | P1 |
| `escText` vs `esc` 混用，展示路径反斜杠双重转义 | M7 | P2 |
| 后台任务无实时进度（仅 toast 首尾通知） | M6 | P2 |
| `log_thinking` / `thinking_trace.log` LLM 原始输出未记录 | M3 | P2 |
| 日志路径硬编码 `Path("logs")` 未走 `config.LOG_DIR` | M5 | P2 |
| Binding type 与 Document doc_type 不一致 | M1 | P1 |
| `MOCK_`/`FAKE_`/`SAMPLE_` 常量定义于生产模块（非 tests/） | M8 | P0 |
| 检索/查询结果为空后未 return/raise 仍继续主流程 | M8 | P0 |
| `api_defs_json` 等关键输入为空串/空列表直传生成节点 | M8 | P0 |
| "兜底"/"fallback 数据"注释 + 硬编码业务样例 | M8 | P0 |

## 第三部分：风险等级总览

| 等级 | 含义 | 覆盖规则 |
|------|------|---------|
| P0 | 一票否决，违反即事故 | M1, M2, M3, M5, M7, M8 |
| P1 | 长期健康，违反即腐化 | M4, M6 |
| P2 | 最佳实践，提升可维护性 | M1~M8 边界情况 |
