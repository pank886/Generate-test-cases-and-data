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

from observability import set_trace_id, get_logger

logger = get_logger(__name__)


# ========================================================================
# 辅助函数：从 state dict 或 response 对象构建前端响应
# ========================================================================

def _build_response_from_state(state: dict, user_input: str = "") -> dict:
    """从 LangGraph 最终 state dict 构建前端 task result。"""
    plan = state.get("excel_plan")
    api_defs = state.get("api_definition_list") or []
    case_count = len(plan.rows) if plan and hasattr(plan, "rows") else 0

    resp = {
        "success": True,
        "thinking": [f"已提取 {len(api_defs)} 个接口"],
        "reply": f"Excel 测试计划已生成：共 {case_count} 条用例",
        "user_ctx": user_input,
    }
    if state.get("excel_path"):
        resp["excel_path"] = state["excel_path"]
        resp["excel_name"] = os.path.basename(state["excel_path"])
        resp["output_dir"] = state.get(
            "output_dir", os.path.dirname(state["excel_path"]),
        )
    if state.get("requires_review"):
        resp["requires_review"] = True
        resp["error_info"] = state.get("error_info", [])
    if api_defs:
        resp["api_defs_json"] = _json.dumps(
            [a.model_dump() if hasattr(a, "model_dump") else a
             for a in api_defs],
            indent=2, ensure_ascii=False,
        )
    else:
        resp["api_defs_json"] = "[]"
    return resp


def _build_response_from_result(response, user_input: str = "") -> dict | None:
    """从旧 _chat_func 的 response 对象构建前端 task result。"""
    if response is None:
        return None
    from types import SimpleNamespace
    if isinstance(response, SimpleNamespace):
        response = response.__dict__
    if isinstance(response, dict):
        return _build_response_from_state(response, user_input)
    # 对象模式
    result = {
        "success": True,
        "thinking": getattr(response, "proper_thinking", []),
        "reply": getattr(response, "final_response", ""),
    }
    if hasattr(response, "excel_path") and response.excel_path:
        result["excel_path"] = response.excel_path
        result["excel_name"] = os.path.basename(response.excel_path)
        result["output_dir"] = getattr(
            response, "output_dir", os.path.dirname(response.excel_path),
        )
    if hasattr(response, "requires_review") and response.requires_review:
        result["requires_review"] = True
        result["error_info"] = getattr(response, "error_info", [])
    if hasattr(response, "api_definition_list"):
        api_defs = response.api_definition_list
        result["api_defs_json"] = _json.dumps(
            [a.model_dump() if hasattr(a, "model_dump") else a
             for a in api_defs],
            indent=2, ensure_ascii=False,
        ) if api_defs else "[]"
    result["user_ctx"] = user_input
    return result


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


import config as _config
_MAX_WORKERS = _config.TASK_MAX_WORKERS
_executor = _BoundedThreadPoolExecutor(max_workers=_MAX_WORKERS, max_queue=_config.TASK_MAX_QUEUE)


# ========================================================================
# 文件处理（上传 → 向量库入库）
# ========================================================================

async def _process_file_bg(task_id: str, file_path: str, ext: str,
                            file_size: int, filename: str, file_type: str):
    """后台处理上传文件 -> 向量库入库。"""
    from web.app import _add_imported_file, _update_task

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
        elif ext == ".md":
            from ingest_v2 import process_api_doc_extract
            _progress(10, "读取 Markdown，提取接口定义...")
            result = await asyncio.to_thread(
                process_api_doc_extract,
                file_path,
                progress_cb=lambda p, m: _progress(10 + int(p * 0.8), m),
            )
            apis = result.get("apis", [])
            count = len(apis)
            source = "API 文档"
            module_name = result.get("module_name")

            if count == 0:
                try:
                    os.remove(file_path)
                except Exception:
                    logger.warning("删除空结果文件失败: %s", file_path, exc_info=True)
                await _update_task(task_id, status="failed",
                                   error="未提取到接口定义，请检查文档格式。")
                return

            # 将 MD 文件加入内存文件列表（即使未确认入库，也可在页面展示）
            try:
                await _add_imported_file({
                    "name": filename,
                    "size": f"{file_size / 1024:.1f} KB",
                    "chunks": count,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "type": "api",
                    "status": "pending",
                })
            except Exception:
                logger.warning("MD 文件列表更新失败（数据已提取，不影响后续确认）", exc_info=True)

            resp = {
                "success": True,
                "message": f"已提取 {count} 个接口定义，请确认后入库",
                "apis": apis,
                "file_path": file_path,
                "module_name": module_name or "Unknown",
            }
            await _update_task(task_id, status="completed", progress=100,
                               message="提取完成，等待确认", result=resp)
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
            source = {".docx": "Word 文档", ".pdf": "PDF 文档"}.get(ext, "文档")

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

async def _confirm_plan_bg(task_id: str, excel_path: str | None,
                          api_defs_json: str = "", user_ctx: str = ""):
    """后台执行确认计划 -> 生成 .py + .yaml。

    api_defs_json / user_ctx 由 /confirm-plan 端点显式传入，
    不再依赖全局 _last_api_defs / _last_user_input。
    """
    import glob
    import config

    from web.app import _phase_b_components, _update_task

    set_trace_id(task_id)

    # 重建 LLM 客户端，避免复用上一个工作流残留的僵死连接池
    from agent_components.nodes import reload_llm
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

        await _update_task(task_id, status="running", progress=20,
                           message="正在生成 .py 测试文件...")

        # LLM 调用 → 线程池
        py_result = await asyncio.to_thread(
            _phase_b_components._generate_py_file, excel_path,
        )

        await _update_task(task_id, progress=50,
                           message="正在生成 YAML 数据文件...")

        # LLM 调用 → 线程池
        yaml_result = await asyncio.to_thread(
            _phase_b_components._generate_all_yamls,
            excel_path, api_defs_json, user_ctx,
        )

        msg = f".py: {py_result['py_file_name']}（{py_result['modules']}模块）"
        if yaml_result["total"] > 0:
            msg += f" | YAML: {yaml_result['success']}/{yaml_result['total']} 个"

        result = {
            "success": True,
            "message": msg,
            "py_file": py_result["py_file_name"],
            "py_path": py_result.get("py_path", ""),
            "yaml_success": yaml_result["success"],
            "yaml_total": yaml_result["total"],
            "excel_path": excel_path,
            "output_dir": os.path.dirname(excel_path),
        }

        await _update_task(task_id, status="completed", progress=100,
                           message="文件生成完成", result=result)

    except Exception as e:
        logger.error("❌ 确认计划失败: %s", e)
        await _update_task(task_id, status="failed", error=str(e))


# ========================================================================
# Phase B: 多轮工作流恢复执行
# ========================================================================

async def _resume_workflow_bg(task_id: str, session_id: str, state: dict):
    """Phase B 后台恢复执行：从节点2开始，完成产品文档检索→关联模块→接口→测试点→Excel。

    使用 _phase_b_graph.astream() 逐节点上报进度，前端实时可见。
    """
    import os as _os
    import config
    from web.app import _phase_b_graph, _update_task

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

        # 从 thinking_trace.log 检查是否有校验失败的行
        fail_warn = ""
        failed_tc_ids = []
        try:
            with open(_os.path.join(config.LOG_DIR, "thinking_trace.log"), "r", encoding="utf-8") as _lf:
                content = _lf.read()
                if "generate_excel_plan_FAILED" in content:
                    fail_warn = "（部分用例校验失败，详见 logs/thinking_trace.log）"
                    # 提取失败用例编号
                    import re
                    failed_tc_ids = list(set(re.findall(
                        r"\| (TC-\d+) \|", content)))
        except Exception:
            pass

        thinking_parts = [f"Excel 计划 {case_count} 条用例"]
        if failed_tc_ids:
            thinking_parts.append(
                f"⚠️ {len(failed_tc_ids)} 行校验失败需人工审查: {', '.join(sorted(failed_tc_ids))}"
            )
        resp = {
            "success": True,
            "thinking": thinking_parts,
            "reply": f"Excel 测试计划已生成：共 {case_count} 条用例{fail_warn}",
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
        from web.app import _workflow_sessions, _workflow_sessions_lock
        async with _workflow_sessions_lock:
            _workflow_sessions.pop(session_id, None)
