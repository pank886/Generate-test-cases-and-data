"""后台异步任务：文件处理、聊天生成、测试计划确认。

所有同步阻塞调用（LLM、文件 I/O、LangGraph）均通过 asyncio.to_thread()
卸载到独立线程池，保持 FastAPI 事件循环始终可响应轮询请求。
"""

import asyncio
import os
import json as _json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from infrastructure.observability import set_trace_id, get_logger

logger = get_logger(__name__)


class _BoundedThreadPoolExecutor(ThreadPoolExecutor):
    """有界线程池：队列满时 submit 阻塞（默认 LinkedBlockingQueue 会无限制累积）。"""

    def __init__(self, max_workers: int = 10, max_queue: int = 30, **kwargs):
        super().__init__(max_workers=max_workers, **kwargs)
        self._sem = threading.BoundedSemaphore(max_queue)

    def submit(self, fn, *args, **kwargs):
        self._sem.acquire()
        future = super().submit(fn, *args, **kwargs)
        future.add_done_callback(lambda _: self._sem.release())
        return future


import infrastructure.config as _config
_MAX_WORKERS = _config.TASK_MAX_WORKERS
_executor = _BoundedThreadPoolExecutor(max_workers=_MAX_WORKERS, max_queue=_config.TASK_MAX_QUEUE)


# ========================================================================
# 文件处理（上传 → 向量库入库）
# ========================================================================

async def _process_file_bg(task_id: str, file_path: str, ext: str,
                            file_size: int, filename: str, file_type: str):
    """后台处理上传文件 -> 向量库入库。"""
    from web.state import _add_imported_file, _update_task

    set_trace_id(task_id)
    loop = asyncio.get_running_loop()

    def _progress(pct: int, msg: str):
        """跨线程安全回调：轻量，只做 run_coroutine_threadsafe 一件事。"""
        asyncio.run_coroutine_threadsafe(
            _update_task(task_id, progress=pct, message=msg),
            loop,
        )

    try:
        await _update_task(task_id, status="running", progress=5,
                           message="接收文件，开始处理...")

        if ext == ".zip":
            from ingest_v2 import process_axure_zip
            _progress(10, "解压 Axure 包，解析页面结构...")
            result = await asyncio.to_thread(
                process_axure_zip,
                file_path,
                progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m),
            )
            count = result.get("chunks", 0)
            source = "Axure 原型"
        elif ext in (".md", ".json"):
            _progress(20, "接口文档已接收，等待选择提取方式...")
            # 不在这里跑 LLM——前端弹窗让用户选 代码提取 / LLM 提取 / JSON 代码提取
            resp = {
                "success": True,
                "message": "请选择接口提取方式",
                "file_path": file_path,
                "file_name": filename,
                "needs_extract_choice": True,  # 前端检测此标记 → 弹选择窗
            }
            await _update_task(task_id, status="completed", progress=100,
                               message="等待选择提取方式", result=resp)
            return

        else:
            from ingest_v2 import process_product_doc
            _progress(10, "读取文档，提取模块信息...")
            result = await asyncio.to_thread(
                process_product_doc,
                file_path,
                progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m),
            )
            count = result.get("chunks", 0)
            source = {".docx": "Word 文档", ".pdf": "PDF 文档", ".xlsx": "Excel 文档"}.get(ext, "文档")

        await _update_task(task_id, progress=90,
                           message=f"{source} 处理完成：{count} 个文本块")

        if count == 0:
            await _update_task(task_id, status="failed",
                               error="文件解析后无内容，请检查文件是否有效。")
            return

        module_name = result.get("module_name")
        doc_id = result.get("doc_id")

        file_info = {
            "name": filename,
            "size": f"{file_size / 1024:.1f} KB",
            "chunks": count,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "type": file_type,
            "status": "ready",  # 数据已写入 SQLite + ChromaDB
        }
        try:
            await _add_imported_file(file_info)
        except Exception:
            logger.warning("内存状态更新失败（数据已持久化，下次启动自动恢复）: %s", filename, exc_info=True)

        logger.info("✅ %s 处理完成：%d 个文本块", source, count)

        try:
            _meta = {"chunks": count, "type": file_type,
                     "time": datetime.now().isoformat(),
                     "module": module_name or "", "doc_id": doc_id or ""}
            with open(file_path + ".meta.json", "w", encoding="utf-8") as _mf:
                _json.dump(_meta, _mf, ensure_ascii=False)
        except Exception:
            logger.warning("写入 meta.json 失败: %s", file_path, exc_info=True)

        resp = {
            "success": True,
            "message": f"已处理 {count} 个文本块",
            "file": file_info,
        }
        if module_name:
            resp["module_name"] = module_name
            resp["doc_id"] = doc_id
            resp["related_modules"] = result.get("related_modules", [])

        await _update_task(task_id, status="completed", progress=100,
                           message="处理完成", result=resp)

    except FileNotFoundError:
        await _update_task(task_id, status="failed", error="上传文件不存在")
    except Exception as e:
        logger.error("❌ 文件处理失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))
        if ext == ".md":
            try:
                os.remove(file_path)
            except Exception:
                logger.warning("清理失败文件失败: %s", file_path, exc_info=True)


# ========================================================================
# ========================================================================
# Phase C: 确认计划 → 生成 .py + .yaml
# ========================================================================

def _resolve_api_defs(excel_path: str, api_defs_json: str = "",
                      module_name: str = "") -> str | None:
    """解析 Phase C 所需的接口定义（规则 M8：缺失必须显式失败，禁止空定义续跑）。

    2026-08-18 改：Phase C 直接用 SQL 从 documents 表读接口详情（body/return/header/annotations），
    不再依赖 Phase B 快照——快照来自 ChromaDB 检索，返回结构缺 body/return，
    曾导致 YAML 生成无字段名可抄而瞎编（meterName/meterNo 等）。

    2026-08-20 改：接口范围收敛——只读「测试模块绑定 + 模块关联模块绑定」的接口
    （用户决策；关联模块发现后续增加语义检索），而非 SQL 全部记录。

    优先级:
      1. 显式入参 api_defs_json（非空且非 "[]"）——信任原样（兼容历史调用方）
      2. 模块作用域详情：module_name 优先；空则从计划目录 module_scope.json 读模块名
         → 该模块绑定 + 关联模块绑定的接口详情
      3. 作用域为空（模块无绑定接口）→ 回退全部接口详情（避免空定义，警告）
      4. SQL 无任何接口定义 → 返回 None（M8：调用方必须阻断任务）
    返回 None 表示无接口定义，调用方必须阻断任务，严禁以空值/假数据继续生成。
    """
    if api_defs_json and api_defs_json.strip() and api_defs_json.strip() != "[]":
        return api_defs_json

    if not module_name:
        module_name = _read_module_scope(excel_path)
    if module_name:
        scoped = _load_api_defs_scoped(module_name)
        if scoped:
            logger.info("SQL 读取接口定义（模块作用域 [%s]）: %d 个",
                        module_name, len(scoped))
            return _json.dumps(scoped, ensure_ascii=False, indent=2)
        logger.warning("模块 [%s] 无绑定接口，回退全部接口定义", module_name)

    apis = _load_all_api_defs()
    if not apis:
        logger.warning("SQL 无接口定义（documents 表 doc_type=api），Phase C 阻断（M8）")
        return None
    return _json.dumps(apis, ensure_ascii=False, indent=2)


def _read_module_scope(excel_path: str) -> str:
    """读取计划目录 module_scope.json 的模块名（B→C 作用域契约，M8 产物传递）。

    缺失/解析失败返回空串（调用方回退全部接口定义，不阻断旧计划）。
    """
    if not excel_path:
        return ""
    scope_path = os.path.join(os.path.dirname(excel_path), "module_scope.json")
    try:
        with open(scope_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        module = data.get("module") or ""
        return module if isinstance(module, str) else ""
    except (OSError, ValueError):
        return ""


def _load_api_defs_scoped(module_name: str) -> list[dict]:
    """按模块作用域读取接口详情：模块绑定 + 关联模块绑定（+ 公共基础服务存在时）。

    与 Phase B 检索口径一致（search_modules = {模块} ∪ 关联模块 ∪ 公共基础服务）。
    返回 SQL 详情形式（body/return/header/annotations + apply_all），非 ChromaDB 概要。
    """
    from database import get_session_ctx
    from database.operations import BindingOps, ModuleOps
    from database.operations.bindings import discover_related_modules

    scope_modules: list[str] = [module_name]
    try:
        with get_session_ctx() as session:
            scope_modules.extend(
                m for m in discover_related_modules(session, module_name)
                if m != module_name
            )
            # 镜像 Phase B：公共基础服务模块存在时纳入（2026-08-20 决策口径）
            try:
                if (_config.COMMON_SERVICE_MODULE
                        and _config.COMMON_SERVICE_MODULE not in scope_modules
                        and ModuleOps.get_by_name(session, _config.COMMON_SERVICE_MODULE)):
                    scope_modules.append(_config.COMMON_SERVICE_MODULE)
            except Exception:
                logger.debug("公共基础服务模块检查失败，跳过", exc_info=True)

            api_doc_ids: set[str] = set()
            for m in dict.fromkeys(scope_modules):
                for d in BindingOps.get_bound_docs(session, m):
                    if d.doc_type == "api":
                        api_doc_ids.add(d.id)
    except Exception as e:
        logger.error("模块作用域计算失败: %s", e, exc_info=True)
        return []

    if not api_doc_ids:
        return []
    return _load_all_api_defs(doc_ids=api_doc_ids)


def _load_all_api_defs(doc_ids: list[str] | set[str] | None = None) -> list[dict]:
    """从 SQL（documents 表）读取接口详情，供 Phase C 使用。

    绕过 ChromaDB 检索（检索结果结构上缺 body/return，见 2026-08-18 根因）。
    doc_ids 非 None 时仅返回指定文档的接口（2026-08-20 模块作用域收敛）；None = 全部。
    annotations 列已含 is_export/has_path_params 持久化结果，此处再跑一次
    ApiAnnotationRegistry.apply_all（幂等、不覆盖人工编辑），保证新注册检测器也生效。
    """
    from database import get_session_ctx
    from database.models import Document as DocModel
    from infrastructure.annotations.api_annotations import ApiAnnotationRegistry

    apis = []
    try:
        with get_session_ctx() as session:
            q = session.query(DocModel).filter_by(doc_type="api")
            if doc_ids:
                q = q.filter(DocModel.id.in_(doc_ids))
            records = q.all()
            for d in records:
                if not d.api_url:
                    continue
                api = {
                    "name": d.api_name, "url": d.api_url,
                    "method": d.api_method, "description": d.api_description,
                    "header": _json.loads(d.api_headers or "{}"),
                    "body": _json.loads(d.api_parameters or "[]"),
                    "return": _json.loads(d.api_returns or "[]"),
                    "annotations": _json.loads(d.api_annotations or "{}"),
                }
                ApiAnnotationRegistry.apply_all(api)
                apis.append(api)
        logger.info("SQL 读取接口定义: %d 个", len(apis))
        return apis
    except Exception as e:
        logger.error("SQL 读取接口定义失败: %s", e, exc_info=True)
        return []


async def _confirm_plan_bg(task_id: str, excel_path: str | None,
                          api_defs_json: str = "", user_ctx: str = "",
                          module_name: str = ""):
    """后台执行确认计划 -> 生成 .py + .yaml。

    api_defs_json / user_ctx 由 /confirm-plan 端点显式传入，
    不再依赖全局 _last_api_defs / _last_user_input。
    module_name：模块作用域（模块绑定 + 关联模块绑定接口，2026-08-20 收敛）；
    空时由 _resolve_api_defs 回退读取计划目录 module_scope.json。
    """
    import glob
    import infrastructure.config as config

    from web.state import _phase_b_components, _update_task, _chroma_db

    set_trace_id(task_id)

    # 重建 LLM 客户端，避免复用上一个工作流残留的僵死连接池
    from agent_components.graph.nodes import reload_llm
    reload_llm()

    try:
        if not excel_path:
            excel_files = glob.glob(
                os.path.join(config.TESTCASE_BASE, "**", "test_plan.xlsx"),
                recursive=True,
            )
            if excel_files:
                excel_path = max(excel_files, key=os.path.getmtime)

        if not excel_path:
            await _update_task(task_id, status="failed",
                               error="未找到测试计划 Excel 文件")
            return

        if not _phase_b_components:
            await _update_task(task_id, status="failed",
                               error="组件未初始化")
            return

        # 规则 M8：接口定义缺失必须显式阻断，禁止空定义盲写 YAML
        resolved_defs = _resolve_api_defs(excel_path, api_defs_json, module_name)
        if resolved_defs is None:
            await _update_task(
                task_id, status="failed",
                error="未找到接口定义（计划目录缺少 api_defs.json 或内容为空），"
                      "请重新执行 Phase B 生成测试计划后再确认",
            )
            return
        api_defs_json = resolved_defs

        # ---- Phase C Step 0: 生成 dependency_map.json ----
        await _update_task(task_id, status="running", progress=15,
                           message="正在分析用例依赖关系...")
        output_dir = os.path.dirname(excel_path)
        try:
            # 从 ChromaDB 检索 product_docs（用 confirmed_module 作为查询 key）
            product_docs_json = "[]"
            try:
                if _chroma_db is not None:
                    from infrastructure.vector_store.dual_chroma import get_chroma_db
                    db = get_chroma_db()
                    docs = db.search_product_docs(
                        query=user_ctx,
                        k=config.RETRIEVAL_K,
                    )
                    if docs:
                        product_docs_json = _json.dumps(
                            [{"page_content": d.page_content, "metadata": d.metadata}
                             for d in docs],
                            ensure_ascii=False,
                        )
            except Exception:
                logger.warning("ChromaDB product_docs 检索失败，使用空文档继续", exc_info=True)

            # 模块树
            from database import get_session_ctx
            from database.operations import ModuleOps
            with get_session_ctx() as session:
                tree = ModuleOps.get_tree(session)
            module_tree_json = _json.dumps(tree, indent=2, ensure_ascii=False)

            # 异步生成 dep_map（LLM thinking 调用走线程池）
            dep_map_path = await asyncio.to_thread(
                _phase_b_components._generate_dependency_map,
                excel_path, output_dir, api_defs_json,
                module_tree_json, product_docs_json,
                "（Phase C Step 0 生成）", user_ctx,
            )
        except Exception as e:
            logger.warning("   ⚠️ dependency_map.json 生成失败（非致命，继续后续步骤）: %s", e)
            dep_map_path = None
        if dep_map_path:
            logger.info("   📄 dependency_map.json 已生成: %s", dep_map_path)

        # ---- Phase C Step 1: 加载 + 预校验 dep_map（失败不阻断） ----
        try:
            if dep_map_path:
                with open(dep_map_path, "r", encoding="utf-8") as f:
                    dep_map = _json.load(f)
                if dep_map.get("stories"):
                    logger.info("   📄 dependency_map.json 已加载: %d 个 story",
                                len(dep_map.get("stories", [])))
                else:
                    logger.warning("   ⚠️ dependency_map.json 无有效 story，跳过")
        except Exception as e:
            logger.warning("   ⚠️ dependency_map.json 解析失败（非致命）: %s", e)

        await _update_task(task_id, status="running", progress=20,
                           message="正在生成 .py 测试文件...")

        # LLM 调用 → 线程池
        py_result = await asyncio.to_thread(
            _phase_b_components._generate_py_file, excel_path,
        )

        await _update_task(task_id, progress=50,
                           message="正在生成 YAML 数据文件...")

        # LLM 调用 → 线程池（带心跳，YAML 生成耗时长，避免前端轮询超时）
        import asyncio as _asyncio
        import time as _time

        _heartbeat_stop = False

        async def _heartbeat():
            nonlocal _heartbeat_stop
            _t0 = _time.time()
            while not _heartbeat_stop:
                await _asyncio.sleep(10)
                if _heartbeat_stop:
                    break
                elapsed = int(_time.time() - _t0)
                await _update_task(
                    task_id, progress=55,
                    message=f"正在生成 YAML 数据文件...（{elapsed}s）",
                )

        hb_task = _asyncio.create_task(_heartbeat())
        try:
            yaml_result = await asyncio.to_thread(
                _phase_b_components._generate_all_yamls,
                excel_path, api_defs_json, user_ctx,
            )
        finally:
            _heartbeat_stop = True
            hb_task.cancel()
            try:
                await hb_task
            except _asyncio.CancelledError:
                pass

        msg = f".py: {py_result['py_file_name']}（{py_result['modules']}模块）"
        if yaml_result["total"] > 0:
            msg += f" | YAML: {yaml_result['success']}/{yaml_result['total']} 个"
            if yaml_result.get("repaired"):
                msg += f"（含自查修复 {yaml_result['repaired']} 个）"
            if yaml_result.get("failed"):
                msg += (f"，仍失败 {yaml_result['failed']} 个"
                        f"（详见 _generation_errors.json 与 logs/thinking_trace.log）")

        result = {
            "success": True,
            "message": msg,
            "py_file": py_result["py_file_name"],
            "py_path": py_result.get("py_path", ""),
            "yaml_success": yaml_result["success"],
            "yaml_total": yaml_result["total"],
            "yaml_repaired": yaml_result.get("repaired", 0),
            "yaml_failed": yaml_result.get("failed", 0),
            "yaml_rounds": yaml_result.get("rounds", 0),
            "errors_file": yaml_result.get("errors_file"),
            "excel_path": excel_path,
            "output_dir": os.path.dirname(excel_path),
        }

        await _update_task(task_id, status="completed", progress=100,
                           message="文件生成完成", result=result)

    except Exception as e:
        logger.error("❌ 确认计划失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


# ========================================================================
# Phase C 独立重跑（仅 YAML 数据生成，2026-09-09）
#   - 入口：对话工作流「Phase C 项」/phase-c/start → /workflow/confirm（flow=phase_c）
#   - 范围：只跑 _generate_all_yamls（读 test_plan.xlsx + api_defs.json + 约定 context 文件），
#     不生成 .py、不重算 dependency_map.json
#   - user_context 注入：模块目录约定文件 phase_c_user_context.md（放 test_plan.xlsx 旁）
# ========================================================================

def _list_phase_c_module_dirs(testcase_base: str) -> list[str]:
    """列出可单独重跑 Phase C 的模块目录（含 test_plan.xlsx + api_defs.json + context 文件）。

    TESTCASE_BASE 下模块目录可能按域分组多级嵌套（如 `园区基线/智慧停车/`），故递归扫描：
    - 目录名含 .bak / _backup / _regenerated（历史备份/重生成克隆）→ 跳过整个子树；
    - 目录含 test_plan.xlsx（模块根）→ 满足 api_defs.json + phase_c_user_context.md 门槛则
      收录，不再下钻（其子目录是 YAML/setup 输出，非独立模块）；
    - 否则作为分组目录递归下钻。
    以模块目录存在 `phase_c_user_context.md` 为门槛（该文件即本次要注入的 user_context，
    没有它单独重跑无意义）。返回目录绝对路径列表（basename 即模块名，用于前端候选展示）。
    """
    if not testcase_base or not os.path.isdir(testcase_base):
        return []

    def _excluded(name: str) -> bool:
        return (name.startswith((".", "~"))
                or any(tok in name for tok in (".bak", "_backup", "_regenerated")))

    def _walk(directory: str, out: list[str]) -> None:
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            return
        for name in entries:
            if _excluded(name):
                continue
            full = os.path.join(directory, name)
            if not os.path.isdir(full):
                continue
            if os.path.exists(os.path.join(full, "test_plan.xlsx")):
                # 模块根：门槛齐备才收录；不递归（子目录为输出产物）
                if (os.path.exists(os.path.join(full, "api_defs.json"))
                        and os.path.exists(os.path.join(full, "phase_c_user_context.md"))):
                    out.append(full)
            else:
                _walk(full, out)

    dirs: list[str] = []
    _walk(testcase_base, dirs)
    return dirs


def _read_phase_c_user_ctx(module_dir: str) -> str:
    """读取模块目录约定文件 phase_c_user_context.md，缺失返回空串。"""
    if not module_dir:
        return ""
    path = os.path.join(module_dir, "phase_c_user_context.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


async def _phase_c_rerun_bg(task_id: str, module_dir: str, session_id: str = ""):
    """Phase C 独立重跑：仅 YAML 数据生成（_generate_all_yamls）。

    module_dir：模块目录（含 test_plan.xlsx / api_defs.json / phase_c_user_context.md）。
    不调用 _generate_py_file / _generate_dependency_map —— 该分支的语义就是只重跑数据。
    session_id：Phase C 工作流会话，完成后清理（非空时）。
    """
    import infrastructure.config as config

    from web.state import _phase_b_components, _update_task

    set_trace_id(task_id)

    # 重建 LLM 客户端，避免复用上一个工作流残留的僵死连接池
    from agent_components.graph.nodes import reload_llm
    reload_llm()

    try:
        excel_path = os.path.join(module_dir, "test_plan.xlsx")
        if not os.path.exists(excel_path):
            await _update_task(task_id, status="failed",
                               error=f"模块目录缺少 test_plan.xlsx: {module_dir}")
            return

        if not _phase_b_components:
            await _update_task(task_id, status="failed",
                               error="组件未初始化")
            return

        # 规则 M8：接口定义缺失必须显式阻断，禁止空定义盲写 YAML
        api_defs_path = os.path.join(module_dir, "api_defs.json")
        if not os.path.exists(api_defs_path):
            await _update_task(
                task_id, status="failed",
                error="模块目录缺少 api_defs.json，请先执行 Phase B 生成测试计划后再确认",
            )
            return
        with open(api_defs_path, "r", encoding="utf-8") as f:
            api_defs_json = f.read()

        # 约定文件 → user_context（缺失则空串）
        user_ctx = _read_phase_c_user_ctx(module_dir)

        await _update_task(task_id, status="running", progress=15,
                           message="正在重新生成 YAML 数据文件（Phase C 独立重跑）...")

        # 心跳：YAML 生成耗时长，避免前端轮询超时
        import time as _time
        _heartbeat_stop = False

        async def _heartbeat():
            nonlocal _heartbeat_stop
            _t0 = _time.time()
            while not _heartbeat_stop:
                await asyncio.sleep(10)
                if _heartbeat_stop:
                    break
                elapsed = int(_time.time() - _t0)
                await _update_task(
                    task_id, progress=55,
                    message=f"正在重新生成 YAML 数据文件...（{elapsed}s）",
                )

        hb_task = asyncio.create_task(_heartbeat())
        try:
            yaml_result = await asyncio.to_thread(
                _phase_b_components._generate_all_yamls,
                excel_path, api_defs_json, user_ctx,
            )
        finally:
            _heartbeat_stop = True
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

        msg = f"Phase C 独立重跑完成（仅 YAML）"
        if yaml_result["total"] > 0:
            msg += f" | YAML: {yaml_result['success']}/{yaml_result['total']} 个"
            if yaml_result.get("repaired"):
                msg += f"（含自查修复 {yaml_result['repaired']} 个）"
            if yaml_result.get("failed"):
                msg += (f"，仍失败 {yaml_result['failed']} 个"
                        f"（详见 _generation_errors.json 与 logs/thinking_trace.log）")

        result = {
            "success": True,
            "message": msg,
            "reply": msg,
            "yaml_success": yaml_result["success"],
            "yaml_total": yaml_result["total"],
            "yaml_repaired": yaml_result.get("repaired", 0),
            "yaml_failed": yaml_result.get("failed", 0),
            "yaml_rounds": yaml_result.get("rounds", 0),
            "errors_file": yaml_result.get("errors_file"),
            "excel_path": excel_path,
            "output_dir": module_dir,
        }

        await _update_task(task_id, status="completed", progress=100,
                           message="YAML 数据文件重新生成完成", result=result)

    except Exception as e:
        logger.error("❌ Phase C 独立重跑失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))
    finally:
        if session_id:
            from web.state import _workflow_sessions, _workflow_sessions_lock
            async with _workflow_sessions_lock:
                _workflow_sessions.pop(session_id, None)


async def _selective_phase_c_rerun_bg(task_id: str, module_dir: str,
                                      junit_path: str | None = None,
                                      log_path: str | None = None,
                                      run_ts: str | None = None,
                                      session_id: str = ""):
    """Phase C 定向重跑（2026-09-09）：只重生成「实际失败 + 导致失败的上游」YAML。

    依据模块目录逐 YAML `.last_run.json`（read_last_run_records，只留最新 cohort）选点：
      - test 仅 verdict==failed 重生成其 test_data（body 真跑过且失败）；
      - setup 仅 verdict∈(broken,failed) 重生成（上游根因）；
      - broken-by-setup 的 test_data 不重生成（body 未执行），excluded_broken 信息性回填。
    失败事实注入：`{user_context}` = 静态 phase_c_user_context.md + 该文件自己的失败摘要
    （build_failure_digest → ctx_overrides 逐文件注入，非全量日志）。
    无落点兜底：仅当模块无任何 .last_run.json 且给了 junit_path → ingest_run_results 引导一次；
    仍无 → failed。不改执行框架；不碰 .py / dependency_map；teardown 本轮不涉及（0）。
    """
    import infrastructure.config as config  # noqa: F401  （与全量分支一致，保留 config 引用语义）

    from web.state import _phase_b_components, _update_task

    set_trace_id(task_id)

    # 重建 LLM 客户端，避免复用上一个工作流残留的僵死连接池
    from agent_components.graph.nodes import reload_llm
    reload_llm()

    try:
        excel_path = os.path.join(module_dir, "test_plan.xlsx")
        if not os.path.exists(excel_path):
            await _update_task(task_id, status="failed",
                               error=f"模块目录缺少 test_plan.xlsx: {module_dir}")
            return

        if not _phase_b_components:
            await _update_task(task_id, status="failed",
                               error="组件未初始化")
            return

        # 规则 M8：接口定义缺失必须显式阻断，禁止空定义盲写 YAML
        api_defs_path = os.path.join(module_dir, "api_defs.json")
        if not os.path.exists(api_defs_path):
            await _update_task(
                task_id, status="failed",
                error="模块目录缺少 api_defs.json，请先执行 Phase B 生成测试计划后再确认",
            )
            return
        with open(api_defs_path, "r", encoding="utf-8") as f:
            api_defs_json = f.read()

        # 静态规则上下文（约定文件，缺失空串）
        static_ctx = _read_phase_c_user_ctx(module_dir)

        from agent_components.run_results import (
            build_failure_digest,
            ingest_run_results,
            read_last_run_records,
            select_rerun_targets,
        )

        records = read_last_run_records(module_dir)
        if not records and junit_path:
            # 兜底引导：模块从未摄取过落点且本次给了 junit → 先摄取一次（不伪造数据）
            records = ingest_run_results(module_dir, junit_path, log_path, run_ts)
        if not records:
            await _update_task(
                task_id, status="failed",
                error="模块目录无 .last_run.json 落点，请先运行摄取驱动"
                      "（有作用/PhaseC/_ingest_phase_c_run_sidecars.py）或传入 junit_path",
            )
            return

        targets = select_rerun_targets(module_dir, records)
        if not targets["test_yamls"] and not targets["setup_yamls"]:
            msg = "定向重跑：无失败/破损目标（本轮全部通过或未执行）"
            if targets["excluded_broken"]:
                msg += f"，其中 {len(targets['excluded_broken'])} 个 body 未执行不重生成"
            await _update_task(task_id, status="completed", progress=100, message=msg,
                               result={"success": True, "message": msg, "reply": msg,
                                       "excel_path": excel_path, "output_dir": module_dir,
                                       "target_counts": {"test": 0, "setup": 0},
                                       "excluded_broken": targets["excluded_broken"]})
            return

        # 逐选中文件失败摘要 → ctx_overrides（静态 + 文件级事实；无示例）
        digests = build_failure_digest(module_dir, records, targets)
        ctx_overrides = {p: static_ctx + "\n\n" + digests[p] for p in digests}
        allow_paths = set(targets["test_yamls"]) | set(targets["setup_yamls"])

        await _update_task(task_id, status="running", progress=15,
                           message=f"定向重跑 YAML（test {len(targets['test_yamls'])} + "
                                   f"setup {len(targets['setup_yamls'])}）...")

        # 心跳：YAML 生成耗时长，避免前端轮询超时
        import time as _time
        _heartbeat_stop = False

        async def _heartbeat():
            nonlocal _heartbeat_stop
            _t0 = _time.time()
            while not _heartbeat_stop:
                await asyncio.sleep(10)
                if _heartbeat_stop:
                    break
                elapsed = int(_time.time() - _t0)
                await _update_task(
                    task_id, progress=55,
                    message=f"定向重跑 YAML 数据文件中...（{elapsed}s）",
                )

        hb_task = asyncio.create_task(_heartbeat())
        try:
            yaml_result = await asyncio.to_thread(
                _phase_b_components._generate_all_yamls,
                excel_path, api_defs_json, static_ctx,
                allow_paths=allow_paths, ctx_overrides=ctx_overrides,
            )
        finally:
            _heartbeat_stop = True
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass

        msg = f"Phase C 定向重跑完成（仅 YAML）"
        if yaml_result["total"] > 0:
            msg += (f" | YAML: {yaml_result['success']}/{yaml_result['total']} 个"
                    f"（目标 test {len(targets['test_yamls'])} + "
                    f"setup {len(targets['setup_yamls'])}）")
            if yaml_result.get("repaired"):
                msg += f"（含自查修复 {yaml_result['repaired']} 个）"
            if yaml_result.get("failed"):
                msg += (f"，仍失败 {yaml_result['failed']} 个"
                        f"（详见 _generation_errors.json 与 logs/thinking_trace.log）")

        result = {
            "success": True,
            "message": msg,
            "reply": msg,
            "yaml_success": yaml_result["success"],
            "yaml_total": yaml_result["total"],
            "yaml_repaired": yaml_result.get("repaired", 0),
            "yaml_failed": yaml_result.get("failed", 0),
            "yaml_rounds": yaml_result.get("rounds", 0),
            "errors_file": yaml_result.get("errors_file"),
            "excel_path": excel_path,
            "output_dir": module_dir,
            "target_counts": {"test": len(targets["test_yamls"]),
                              "setup": len(targets["setup_yamls"])},
            "excluded_broken": targets["excluded_broken"],
        }

        await _update_task(task_id, status="completed", progress=100,
                           message="定向重跑 YAML 数据文件生成完成", result=result)

    except Exception as e:
        logger.error("❌ Phase C 定向重跑失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))
    finally:
        if session_id:
            from web.state import _workflow_sessions, _workflow_sessions_lock
            async with _workflow_sessions_lock:
                _workflow_sessions.pop(session_id, None)


# ========================================================================
# Phase B: 多轮工作流恢复执行
# ========================================================================

def _resolve_failed_case_info(result: dict, trace_tail: str = "") -> tuple:
    """判定 Phase B 的失败提示与失败编号（纯逻辑，便于单测）。

    节点正常路径总会透传 failed_case_details，三者语义不同：
      - 非空列表 → 真实失败：编号取 failed_case_ids，详情由 reply 逐行渲染；
      - 空列表 [] → 0 失败：不提示（2026-09-02 误报修复：空列表 ≠ 缺失，
        此前 `if not ...` 把 [] 当缺失 → 误读整份 thinking_trace.log 命中历史 FAILED）；
      - None（节点未透传，异常路径）→ 兜底读本次运行日志段 trace_tail，
        仅当含 generate_excel_plan_FAILED 才提示，并提取 TC-/PRE- 编号。
    """
    details = result.get("failed_case_details")
    if details:
        return "", list(result.get("failed_case_ids") or [])
    if details is not None:
        # 空列表 = 0 失败，无提示
        return "", []
    if "generate_excel_plan_FAILED" in (trace_tail or ""):
        # 提取失败用例编号（含 TC- 与 PRE-，此前只匹配 TC- 漏 PRE-）
        import re
        return ("（部分用例校验失败，详见 logs/thinking_trace.log）",
                list(set(re.findall(r"\| ((?:TC|PRE)-\d+) \|", trace_tail))))
    return "", []


def _build_phase_b_reply(result: dict, case_count: int, fail_warn: str = "") -> str:
    """Phase B 完成后回复文案。

    requires_review（无 plan / 人工审查）→ 明确提示需人工审查，
    禁止误导性"已生成：共 0 条用例"。
    """
    if result.get("requires_review"):
        errors = result.get("error_info") or []
        detail = "；".join(errors) if errors else "生成环节未产出完整测试计划"
        return f"⚠️ 测试计划需人工审查：{detail}"
    base = f"Excel 测试计划已生成：共 {case_count} 条用例"
    details = result.get("failed_case_details")
    if details:
        rows = "\n".join(
            f"  ⚠️ {d.get('id', '?')} {d.get('title', '')}: {'; '.join(d.get('errors') or [])}"
            for d in details)
        return f"{base}\n{len(details)} 行未通过校验，需人工审查：\n{rows}"
    return base + fail_warn


async def _resume_workflow_bg(task_id: str, session_id: str, state: dict):
    """Phase B 后台恢复执行：从节点2开始，完成产品文档检索→关联模块→接口→测试点→Excel。

    使用 _phase_b_graph.astream() 逐节点上报进度，前端实时可见。
    """
    import os as _os
    import infrastructure.config as config
    from web.state import _phase_b_graph, _update_task

    set_trace_id(task_id)

    # 节点 → 进度映射
    try:
        await _update_task(task_id, status="running", progress=10,
                           message="正在检索产品文档...")

        # LangGraph 在独立线程中执行（同步节点：ChromaDB、LLM 等），
        # 主协程保持可响应，定期发心跳避免前端超时
        import asyncio as _asyncio
        import time as _time

        _heartbeat_stop = False

        async def _heartbeat():
            nonlocal _heartbeat_stop
            _t0 = _time.time()
            _step = 1
            _messages = [
                "正在检索产品文档...",
                "正在分析关联模块...",
                "正在检索接口定义...",
                "正在分析测试场景...",
                "正在生成测试计划...",
            ]
            while not _heartbeat_stop:
                await _asyncio.sleep(10)
                if _heartbeat_stop:
                    break
                elapsed = int(_time.time() - _t0)
                msg = _messages[min(_step, len(_messages) - 1)]
                _step += 1
                await _update_task(
                    task_id, progress=15,
                    message=f"{msg}（{elapsed}s）",
                )

        # 兜底日志截点：invoke 前记录 thinking_trace.log 偏移，兜底只读本次运行追加的
        # 尾部，避免历史 FAILED 标记污染本次回复（2026-09-02 误报回归点）
        _trace_path = _os.path.join(config.LOG_DIR, "thinking_trace.log")
        _trace_start = 0
        try:
            _trace_start = _os.path.getsize(_trace_path)
        except OSError:
            pass

        hb_task = _asyncio.create_task(_heartbeat())
        try:
            result = await asyncio.to_thread(_phase_b_graph.invoke, state)
        finally:
            _heartbeat_stop = True
            hb_task.cancel()
            try:
                await hb_task
            except _asyncio.CancelledError:
                pass

        # 检查 NO_DATA 中断
        if result.get("workflow_status") == "NO_DATA":
            await _update_task(task_id, status="failed", progress=100,
                               error=result.get("confirmation_question",
                                                "未找到产品文档，请先导入数据"))
            return

        await _update_task(task_id, progress=85,
                           message="生成完成，正在保存结果...")

        # 构建响应
        plan = result.get("excel_plan")
        case_count = len(plan.test_cases) if plan and hasattr(plan, "test_cases") else 0

        # 校验失败行：优先用 nodes 透传的失败详情（含编号+标题+原因），
        # 仅当节点未透传（异常路径）才兜底读本次运行追加的日志段（防历史 FAILED 污染）
        tail = ""
        if result.get("failed_case_details") is None:
            try:
                with open(_trace_path, "r", encoding="utf-8") as _lf:
                    _lf.seek(_trace_start)
                    tail = _lf.read()
            except Exception:
                logger.warning("无法读取思考日志，跳过失败用例提取", exc_info=True)
        fail_warn, failed_tc_ids = _resolve_failed_case_info(result, tail)

        thinking_parts = [f"Excel 计划 {case_count} 条用例"]
        if failed_tc_ids:
            thinking_parts.append(
                f"⚠️ {len(failed_tc_ids)} 行校验失败需人工审查: {', '.join(sorted(failed_tc_ids))}"
            )
        resp = {
            "success": True,
            "thinking": thinking_parts,
            "reply": _build_phase_b_reply(result, case_count, fail_warn),
        }
        if result.get("excel_path"):
            resp["excel_path"] = result["excel_path"]
            resp["excel_name"] = _os.path.basename(result["excel_path"])
            resp["output_dir"] = result.get(
                "output_dir", _os.path.dirname(result["excel_path"]),
            )
        if result.get("requires_review"):
            resp["requires_review"] = True
            resp["error_info"] = result.get("error_info", [])

        await _update_task(task_id, status="completed", progress=100,
                           message="测试计划生成完成", result=resp)

    except Exception as e:
        logger.error("❌ Phase B 工作流执行失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))
    finally:
        from web.state import _workflow_sessions, _workflow_sessions_lock
        async with _workflow_sessions_lock:
            _workflow_sessions.pop(session_id, None)


# ========================================================================
# Phase A: 三步分析管线（2026-07-31 — 替代旧 _analyze_module_scenarios_bg）
# ========================================================================

async def _analyze_module_scenarios_3step_bg(task_id: str, module_name: str):
    """三步分析管线：产品→场景 / Axure→逻辑关系 / API→接口映射。

    Step 1: product 文档 → scenario_analysis
    Step 2: axure 文档 → ui_flow_analysis（无 Axure 跳过）
    Step 3: api 定义 → api_analysis（无 API 跳过）
    每步 thinking 模式，1 次 LLM 调用，输出自由文本。
    """
    from web.state import _update_task
    from agent_components.graph.nodes import reload_llm, _get_llm
    from prompts.extraction_prompts import (
        analyze_product_scenarios_prompt,
        analyze_axure_ui_flow_prompt,
        analyze_api_mapping_prompt,
    )
    import infrastructure.config as _cfg

    set_trace_id(task_id)
    reload_llm()

    try:
        await _update_task(task_id, status="running", progress=5,
                           message=f"三步分析「{module_name}」...")

        # ── 1. 查 module_id + 加载绑定文档（单 session 取完所有数据）──
        from database import get_session_ctx
        from database.operations import ModuleOps, BindingOps
        from database.models import DocumentChunk, Document

        with get_session_ctx() as session:
            mod = ModuleOps.get_by_name(session, module_name)
            if not mod:
                await _update_task(task_id, status="failed",
                                   error=f"模块「{module_name}」不存在")
                return
            module_id = mod.id

            bound_docs = BindingOps.get_bound_docs(session, module_name)
            partners = BindingOps.get_partners(session, "module", module_name, "module")

            # ── 分离三种文档类型（session 内提取纯数据）──
            product_chunks: list[str] = []  # Step 1 输入
            axure_chunks: list[tuple[str, str]] = []  # Step 2 输入: (page_name, content)
            api_defs_raw: list[dict] = []  # Step 3 输入（session 内取出纯 dict）

            for doc in bound_docs:
                if doc.doc_type == "api":
                    if doc.api_url:
                        api_defs_raw.append({
                            "name": doc.api_name, "url": doc.api_url,
                            "method": doc.api_method,
                            "description": doc.api_description,
                            "header": _json.loads(doc.api_headers or "{}"),
                            "body": _json.loads(doc.api_parameters or "[]"),
                            "return": _json.loads(doc.api_returns or "[]"),
                        })
                else:
                    chunks = session.query(DocumentChunk).filter_by(
                        doc_id=doc.id).order_by(DocumentChunk.chunk_index).all()
                    for c in chunks:
                        content = c.content
                        pn = c.page_name or doc.file_name
                        if doc.doc_type == "axure":
                            axure_chunks.append((pn, content))
                        else:
                            product_chunks.append(content)

            # 跨模块文本
            cross_text = ", ".join(p[1] for p in partners) if partners else "无"

            # 模块树
            tree = ModuleOps.get_tree(session)
            module_tree_json = _json.dumps(tree, indent=2, ensure_ascii=False)

        # ── session 已关闭，后续只使用提取后的纯数据 ──
        api_defs_json = _json.dumps(api_defs_raw, indent=2, ensure_ascii=False)

        llm = _get_llm()
        think_kwargs = {}
        if _cfg.ENABLE_THINKING:
            think_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        scenario_text = ""
        ui_flow_text = ""
        api_text = ""

        # ── 心跳工具 ──
        import asyncio as _asyncio
        import time as _time

        async def _heartbeat(msg: str, base_progress: int):
            _t0 = _time.time()
            while not _heartbeat_stop:
                await _asyncio.sleep(10)
                if _heartbeat_stop:
                    break
                elapsed = int(_time.time() - _t0)
                await _update_task(
                    task_id, progress=base_progress,
                    message=f"{msg}（{elapsed}s）",
                )

        # ════════════════════════════════════════════
        # Step 1: 产品文档 → 测试场景总结
        # ════════════════════════════════════════════
        if product_chunks:
            await _update_task(task_id, progress=15,
                               message=f"Step 1/3: 分析产品文档（{len(product_chunks)} 块）...")
            product_docs_text = "\n\n---\n\n".join(product_chunks)

            _heartbeat_stop = False
            hb_task = _asyncio.create_task(_heartbeat("Step 1/3: LLM 分析产品文档...", 20))
            try:
                prompt = analyze_product_scenarios_prompt()
                bound_llm = llm.bind(temperature=0.6, **think_kwargs)
                result = await asyncio.to_thread(
                    bound_llm.invoke,
                    prompt.format_messages(
                        module_name=module_name,
                        product_docs=product_docs_text,
                        cross_module_relations=cross_text,
                    ),
                )
                scenario_text = result.content if hasattr(result, "content") else str(result)
            finally:
                _heartbeat_stop = True
                hb_task.cancel()
                try: await hb_task
                except _asyncio.CancelledError: pass

            # 写入 Step 1 结果
            with get_session_ctx() as session:
                from database.operations.analysis import AnalysisOps
                AnalysisOps.upsert_3step(
                    session, module_id, module_name,
                    scenario_analysis=scenario_text,
                )
                session.commit()
            await _update_task(task_id, progress=40,
                               message="Step 1/3 完成：场景总结已保存")
        else:
            logger.info("三步分析: 无 product 文档，跳过 Step 1")

        # ════════════════════════════════════════════
        # Step 2: 场景 + Axure → 逻辑关系总结
        # ════════════════════════════════════════════
        if axure_chunks and scenario_text:
            await _update_task(task_id, progress=45,
                               message=f"Step 2/3: 分析 Axure 页面（{len(axure_chunks)} 块）...")
            axure_text = "\n\n---\n\n".join(
                f"[页面: {pn}] {content}" for pn, content in axure_chunks
            )

            _heartbeat_stop = False
            hb_task = _asyncio.create_task(_heartbeat("Step 2/3: LLM 分析 Axure 交互...", 50))
            try:
                prompt = analyze_axure_ui_flow_prompt()
                bound_llm = llm.bind(temperature=0.6, **think_kwargs)
                result = await asyncio.to_thread(
                    bound_llm.invoke,
                    prompt.format_messages(
                        module_name=module_name,
                        scenario_analysis=scenario_text,
                        axure_pages=axure_text,
                    ),
                )
                ui_flow_text = result.content if hasattr(result, "content") else str(result)
            finally:
                _heartbeat_stop = True
                hb_task.cancel()
                try: await hb_task
                except _asyncio.CancelledError: pass

            # 写入 Step 2 结果
            with get_session_ctx() as session:
                from database.operations.analysis import AnalysisOps
                AnalysisOps.upsert_3step(
                    session, module_id, module_name,
                    ui_flow_analysis=ui_flow_text,
                )
                session.commit()
            await _update_task(task_id, progress=65,
                               message="Step 2/3 完成：交互逻辑已保存")
        else:
            logger.info("三步分析: 无 Axure 文档或场景文本，跳过 Step 2")

        # ════════════════════════════════════════════
        # Step 3: 场景 + 逻辑关系 + API → 接口总结
        # ════════════════════════════════════════════
        if api_defs_raw:
            await _update_task(task_id, progress=70,
                               message=f"Step 3/3: 分析 API 映射（{len(api_defs_raw)} 个接口文档）...")

            _heartbeat_stop = False
            hb_task = _asyncio.create_task(_heartbeat("Step 3/3: LLM 分析接口映射...", 75))
            try:
                prompt = analyze_api_mapping_prompt()
                bound_llm = llm.bind(temperature=0.6, **think_kwargs)
                result = await asyncio.to_thread(
                    bound_llm.invoke,
                    prompt.format_messages(
                        module_name=module_name,
                        scenario_analysis=scenario_text or "（未分析）",
                        ui_flow_analysis=ui_flow_text or "（未分析）",
                        api_definitions=api_defs_json,
                        module_tree=module_tree_json,
                        cross_module_relations=cross_text,
                    ),
                )
                api_text = result.content if hasattr(result, "content") else str(result)
            finally:
                _heartbeat_stop = True
                hb_task.cancel()
                try: await hb_task
                except _asyncio.CancelledError: pass

            # 写入 Step 3 结果
            with get_session_ctx() as session:
                from database.operations.analysis import AnalysisOps
                AnalysisOps.upsert_3step(
                    session, module_id, module_name,
                    api_analysis=api_text,
                )
                session.commit()
            await _update_task(task_id, progress=90,
                               message="Step 3/3 完成：接口映射已保存")
        else:
            logger.info("三步分析: 无 API 文档，跳过 Step 3")

        # ── 完成 ──
        resp = {
            "success": True,
            "module_name": module_name,
            "analysis": {
                "has_scenario": bool(scenario_text),
                "has_ui_flow": bool(ui_flow_text),
                "has_api": bool(api_text),
                "summary": (
                    f"{'场景' if scenario_text else ''}"
                    f"{' · ' if scenario_text and ui_flow_text else ''}"
                    f"{'交互逻辑' if ui_flow_text else ''}"
                    f"{' · ' if (scenario_text or ui_flow_text) and api_text else ''}"
                    f"{'接口映射' if api_text else ''}"
                ),
            },
        }
        await _update_task(task_id, status="completed", progress=100,
                           message="三步分析完成", result=resp)

    except Exception as e:
        logger.error("❌ 三步分析失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


# ========================================================================
# Phase A: 模块场景分析（旧版 — 已废弃，保留兼容未升级的前端调用）
# ========================================================================

async def _analyze_module_scenarios_bg(task_id: str, module_name: str):
    """[已废弃] 后台任务：分析模块场景 → 生成 module_analysis JSON → 写入 SQLite。

    请使用 _analyze_module_scenarios_3step_bg() 替代。
    """
    # 委托新版三步分析
    await _analyze_module_scenarios_3step_bg(task_id, module_name)


async def _commit_apis_bg(task_id: str, file_path: str, module_name: str,
                           apis: list, delete_original: bool):
    """后台任务：逐条向量化入库，前端轮询进度。"""
    from web.state import _add_imported_file, _update_task
    from ingest_v2 import commit_api_docs

    set_trace_id(task_id)
    loop = asyncio.get_running_loop()
    total = len(apis)

    def _progress(pct: int, msg: str):
        asyncio.run_coroutine_threadsafe(
            _update_task(task_id, progress=pct, message=msg),
            loop,
        )

    try:
        await _update_task(task_id, status="running", progress=0,
                           message=f"开始入库 {total} 个接口...")
        result = await asyncio.to_thread(
            commit_api_docs, file_path, module_name, apis,
            delete_original=delete_original,
            progress_cb=lambda p, m: _progress(p, m),
        )
        # 更新内存文件列表
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for api in apis:
            try:
                await _add_imported_file({
                    "name": f"{api.get('method', '?')} {api.get('url', '')}",
                    "size": "—", "chunks": 1, "time": now_str, "type": "api",
                })
            except Exception:
                pass
        await _update_task(task_id, status="completed", progress=100,
                           message=f"✅ {total} 个接口入库完成", result=result)
    except Exception as e:
        logger.error("❌ 接口入库失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


# ========================================================================
# 补偿 Worker（独立轮询线程）—— 已迁移至 web/compensation.py，此处 re-export 保持兼容
# ========================================================================
from web.compensation import (
    _start_compensation_worker,
    _stop_compensation_worker,
    _compensation_loop,
    _process_pending_compensation,
    _compensate_simple_summary,
    _compensate_chroma_rebuild,
    _compensate_api_search_text,
)
