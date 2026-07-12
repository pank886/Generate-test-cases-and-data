# 架构规则索引

## 第一部分：元规则速查

| # | 名称 | 口诀 |
|---|------|------|
| M1 | 事务边界与数据一致性 | 关系库先写向量库后写，删三处，失败必补偿回滚 |
| M2 | LLM 交互规范 | thinking 不碰结构化，Pydantic 控输出，解析失败必降级 |
| M3 | 异常处理与日志 | 禁裸 except，禁空 catch，禁静默吞，禁 None 传递 |
| M4 | 并发安全 | 双检锁副作用归锁内，单例必上锁，线程池必有界 |
| M5 | 文件与路径安全 | BASE_DIR 为根，basename 洗输入，os.remove 包 OSError |
| M6 | 代码结构与配置 | settings 管参数，lifespan 配 global，全局单例判 None |
| M7 | 前端安全与交互 | 静态 JS 禁模板语法，catch 禁空，变量注入用 var |

## 第二部分：关键词 → 规则映射表

| 关键词（代码中出现即触发） | 对应规则 | 风险等级 |
|---------------------------|----------|---------|
| `session.commit`, `session.rollback`, `get_session_ctx` | M1 | P0 |
| `add_product_doc_chunks`, `add_api_defs`, `delete_by_doc_id` | M1 | P0 |
| `_save_to_sqlite`, `_delete_sqlite_doc`, `_add_imported_file` | M1 | P0 |
| `BindingOps.delete_bindings_for_doc`, `DocOps.delete_document` | M1 | P0 |
| `os.remove` 后无 `_remove_imported_file` | M1 | P0 |
| `_invoke_structured`, `with_structured_output` | M2 | P0 |
| `METHOD_FEATURES`, `thinking`, `extra_body` | M2 | P0 |
| `model_validator`, `model_dump`, `ApiDefinition`, `IntentConfirmation` | M2 | P0 |
| `ChatPromptTemplate.from_messages`, `prompt.input_variables`, `{{` 转义 | M2 | P0 |
| `except:`, `except Exception:` | M3 | P0 |
| `catch (e) {}`, `catch(e){}` | M3, M7 | P0 |
| `.extract_text()`, `or ""` | M3 | P0 |
| `threading.Lock`, `_lock`, `BoundedSemaphore` | M4 | P1 |
| `ThreadPoolExecutor`, `max_workers`, `max_queue` | M4 | P1 |
| `asyncio.Lock`, `run_coroutine_threadsafe` | M4 | P1 |
| `os.path.join(`, `BASE_DIR`, `os.path.basename` | M5 | P0 |
| `os.remove`, `os.rename`, `os.replace`, `_win_remove` | M5 | P0 |
| `mkdtemp`, `tempfile`, `_images` | M5 | P1 |
| `settings.`, `config.`, `Field(default=` | M6 | P1 |
| `global `, `lifespan`, `_phase_c_graph` | M6 | P0 |
| `ThreadPoolExecutor(` 无 `_Bounded` | M6 | P1 |
| `to_thread`, `_heartbeat`, `pollTask`, `_update_task` | M6 | P1 |
| `to_thread` 无配套心跳协程 | M6 | P1 |
| `{{`, `{%`, `tojson`, `script src=` | M7 | P0 |
| `onclick=`, `onchange=`, `addEventListener` | M7 | P1 |
| 模板中 `const VECTOR_READY` | M7 | P0 |

## 第三部分：风险等级总览

| 等级 | 含义 | 覆盖规则 |
|------|------|---------|
| P0 | 一票否决，违反即事故 | M1, M2, M3, M5, M7 |
| P1 | 长期健康，违反即腐化 | M4, M6 |
