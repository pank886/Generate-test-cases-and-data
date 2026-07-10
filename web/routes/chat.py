"""测试生成路由：聊天、确认计划、任务状态轮询。"""

import os

from fastapi import APIRouter, Form, BackgroundTasks
from fastapi.responses import JSONResponse

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(user_input: str = Form(...),
                background_tasks: BackgroundTasks = None):
    """接收用户需求 → 立即返回 task_id，后台异步生成测试计划。"""
    from web.app import _get_imported_files, _create_task

    files = await _get_imported_files()
    if not files:
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": "请先上传 PDF 文档"})

    task_id = await _create_task()
    from web.tasks import _run_chat_bg
    background_tasks.add_task(_run_chat_bg, task_id, user_input)
    return {"success": True, "task_id": task_id, "message": "任务已提交，后台处理中"}


@router.post("/confirm-plan")
async def confirm_plan(excel_path: str = Form(None),
                       api_defs_json: str = Form(""),
                       user_ctx: str = Form(""),
                       background_tasks: BackgroundTasks = None):
    """确认测试计划 → 立即返回 task_id，后台异步生成 .py + .yaml。"""
    import config
    from web.app import _components, _create_task
    from observability import get_logger

    logger = get_logger(__name__)
    logger.info(">>> 测试计划已确认，开始生成测试文件...")

    if not excel_path:
        import glob
        excel_files = glob.glob(
            os.path.join(config.TESTCASE_BASE, "**", "test_plan.xlsx"),
            recursive=True,
        )
        if excel_files:
            excel_path = max(excel_files, key=os.path.getmtime)

    if not excel_path:
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": "未找到测试计划 Excel 文件"})

    if not _components:
        return JSONResponse(status_code=500,
                            content={"success": False,
                                     "message": "组件未初始化"})

    task_id = await _create_task()
    from web.tasks import _confirm_plan_bg
    background_tasks.add_task(
        _confirm_plan_bg, task_id, excel_path, api_defs_json, user_ctx,
    )
    return {"success": True, "task_id": task_id,
            "message": "确认计划已提交，后台生成中"}


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """轮询查询后台任务进度。"""
    from web.app import _task_store, _task_store_lock

    async with _task_store_lock:
        task = _task_store.get(task_id)
    if not task:
        return JSONResponse(status_code=404,
                            content={"success": False,
                                     "message": "任务不存在或已过期"})
    return {"success": True, "task": task}


# ----------------------------------------------------------------
# Phase C 多轮工作流端点
# ----------------------------------------------------------------

@router.post("/workflow/start")
async def workflow_start(user_input: str = Form(...),
                         background_tasks: BackgroundTasks = None):
    """Phase C 工作流入口：执行节点1（意图识别）→ 挂起 → 返回候选模块。

    前端应渲染 candidate_modules 为可点击按钮，用户选择后调用 /workflow/confirm。
    """
    import time
    import uuid
    from agent_components.graph_builder import _make_initial_state
    from web.app import (
        _get_imported_files, _phase_c_graph, _phase_c_components,
        _vector_ready,
        _workflow_sessions, _workflow_sessions_lock, _cleanup_expired_sessions,
    )

    if not user_input.strip():
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": "请输入需求描述"})

    if not _vector_ready:
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": "向量库未就绪，请检查 Ollama 服务状态"})

    files = await _get_imported_files()
    if not files:
        return JSONResponse(status_code=400,
                            content={"success": False,
                                     "message": "请先上传文档并创建模块"})

    if not _phase_c_graph:
        return JSONResponse(status_code=500,
                            content={"success": False,
                                     "message": "Phase C 工作流未初始化"})

    # 清理过期会话
    await _cleanup_expired_sessions()

    session_id = uuid.uuid4().hex
    initial_state = _make_initial_state(user_input)

    import asyncio
    try:
        result = await asyncio.to_thread(_phase_c_graph.invoke, initial_state)
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": f"意图识别失败: {e}"})

    candidates = result.get("candidate_modules") or []
    question = result.get("confirmation_question", "请描述您的需求")

    # 仅当状态为 WAITING 时保存会话（等待用户确认）
    if result.get("workflow_status") == "WAITING":
        async with _workflow_sessions_lock:
            _workflow_sessions[session_id] = {
                "state": dict(result),  # 保存当前 state 快照
                "created_at": time.time(),
            }
        return {
            "success": True,
            "session_id": session_id,
            "status": "waiting",
            "question": question,
            "candidates": candidates,
        }
    else:
        # 异常：意图识别未触发等待（如无可用模块）
        return {
            "success": True,
            "session_id": session_id,
            "status": "no_match",
            "question": question,
            "candidates": candidates,
        }


@router.post("/workflow/confirm")
async def workflow_confirm(session_id: str = Form(...),
                           choice: str = Form(...),
                           background_tasks: BackgroundTasks = None):
    """Phase C 工作流恢复：用户确认模块 → 执行节点2-6 → 返回 task_id。

    用户 choice 解析策略（前端应渲染按钮，点击直接传模块名）:
      1. 纯数字 → 按 candidate_modules 序号匹配
      2. 精确匹配候选模块名
      3. 都失败 → 当作新描述，重新走 /workflow/start
    """
    import time
    from web.app import (
        _phase_c_graph, _workflow_sessions, _workflow_sessions_lock,
        _cleanup_expired_sessions, _create_task,
    )

    await _cleanup_expired_sessions()

    async with _workflow_sessions_lock:
        session = _workflow_sessions.get(session_id)
        if not session:
            return JSONResponse(status_code=404,
                                content={"success": False,
                                         "message": "会话不存在或已过期，请重新开始对话"})

        state = session["state"]
        # 暂不删除 session，待后台任务入列后再清理（防止任务被丢弃后无法重试）

    candidates: list[str] = state.get("candidate_modules") or []

    # 解析用户选择
    confirmed_module: str | None = None

    # 策略1: 纯数字 → 序号匹配
    stripped = choice.strip()
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(candidates):
            confirmed_module = candidates[idx]

    # 策略2: 精确匹配模块名
    if not confirmed_module:
        if stripped in candidates:
            confirmed_module = stripped

    # 策略3: 都不匹配 → 当作新描述重新识别
    if not confirmed_module:
        from agent_components.graph_builder import _make_initial_state
        new_state = _make_initial_state(stripped)
        import asyncio
        try:
            result = await asyncio.to_thread(_phase_c_graph.invoke, new_state)
        except Exception as e:
            return JSONResponse(status_code=500,
                                content={"success": False, "message": f"意图识别失败: {e}"})
        new_candidates = result.get("candidate_modules") or []
        new_question = result.get("confirmation_question", "")
        if result.get("workflow_status") == "WAITING":
            async with _workflow_sessions_lock:
                _workflow_sessions[session_id] = {
                    "state": dict(result),
                    "created_at": time.time(),
                }
        return {
            "success": True,
            "session_id": session_id,
            "status": "reconfirm",
            "question": new_question,
            "candidates": new_candidates,
            "message": f"未识别到模块 '{stripped}'，已根据您的输入重新匹配",
        }

    # 更新 state 并恢复执行
    state["confirmed_module"] = confirmed_module
    state["workflow_status"] = "CONFIRMED"

    task_id = await _create_task()
    from web.tasks import _resume_workflow_bg
    background_tasks.add_task(_resume_workflow_bg, task_id, session_id, state)
    # 任务已入列，可安全清理 session
    async with _workflow_sessions_lock:
        _workflow_sessions.pop(session_id, None)
    return {"success": True, "task_id": task_id, "status": "running",
            "message": f"已确认模块 [{confirmed_module}]，正在生成测试计划..."}
