#!/usr/bin/env python3
"""Web 入口：智能测试助手 Web 版（FastAPI）"""
import json
import os
import sys
from pathlib import Path

# 强制 UTF-8 编码，防止 Windows 终端打印 emoji 时报 GBK 错误
sys.stdout.reconfigure(encoding="utf-8")

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from ingest_file import build_vector_store
from agent_components.chromadb_file import ensure_directory, ReadersChromadb
from agent_components.graph_builder import build_and_run_agent
from datetime import datetime

# ----------------------------------------------------------------
# 应用初始化
# ----------------------------------------------------------------
app = FastAPI(title="智能测试助手", version="0.1")

# Jinja2 模板
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_DIR.mkdir(exist_ok=True)
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# 全局状态（单用户模式）
_chat_func = None
_components = None  # ChatTestAgentGraph 实例，用于后续生成
_vector_ready = False
_imported_files = []  # 已导入文件列表：[{"name", "size", "chunks", "time"}]
_last_api_defs = None  # 最后一次聊天中的 API 定义
_last_user_input = None  # 最后一次用户输入


# ----------------------------------------------------------------
# 生命周期
# ----------------------------------------------------------------
@app.on_event("startup")
async def startup():
    global _chat_func, _components, _vector_ready, _imported_files
    print(">>> 启动智能测试助手 Web 服务 ...")
    _chat_func = build_and_run_agent()
    _components = _chat_func.components  # 保存实例用于后续生成

    # 扫描 uploads/ 下各类型子目录，恢复已导入文件列表（不分组，混合展示）
    scan_dirs = [
        ("uploads/pdf", ".pdf"),
        ("uploads/md", ".md"),
        ("uploads", ".pdf"),  # 兼容旧版根目录的 PDF
    ]
    for scan_dir, ext in scan_dirs:
        dir_path = Path(scan_dir)
        if dir_path.exists():
            files = sorted(dir_path.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in files:
                size_kb = f.stat().st_size / 1024
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                _imported_files.append({
                    "name": f.name,
                    "size": f"{size_kb:.1f} KB",
                    "chunks": "—",
                    "time": mtime,
                })

    # 判断向量库是否已就绪（目录存在且有数据文件）
    chroma_path = Path(config.CHROMA_DB_DIR)
    if chroma_path.exists() and any(chroma_path.iterdir()):
        _vector_ready = True
        print(f"   ✅ 向量库已就绪 ({len(_imported_files)} 个文件)")
    else:
        print("   ℹ️ 向量库为空，请上传 API 文档")


# ----------------------------------------------------------------
# 页面路由
# ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    template = _env.get_template("index.html")
    return HTMLResponse(template.render(
        vector_ready=_vector_ready,
        imported_files=_imported_files,
    ))


# ----------------------------------------------------------------
# API 接口
# ----------------------------------------------------------------
@app.post("/upload-file")
async def upload_file(file: UploadFile = File(...)):
    """上传文件（PDF/MD）-> 按类型分目录存储 -> 存入向量库"""
    global _vector_ready, _imported_files

    # 1. 识别文件类型
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    type_map = {
        ".pdf": "pdf",
        ".md": "md",
    }
    file_type = type_map.get(ext)
    if file_type is None:
        supported = ", ".join(type_map.keys())
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"不支持的文件类型: {ext}。当前支持: {supported}"},
        )

    # 2. 保存到类型专属子目录（便于扩展新类型）
    type_dir = ensure_directory(f"./uploads/{file_type}")
    file_path = os.path.join(type_dir, filename)
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 3. 构建向量库（统一入口，内部按扩展名分发）
    try:
        count = build_vector_store(file_path)
        if count == 0:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "文件解析后无内容，请检查文件是否有效。",
                },
            )
        _vector_ready = True

        # 记录已导入文件（不分组混合展示）
        file_info = {
            "name": filename,
            "size": f"{len(content) / 1024:.1f} KB",
            "chunks": count,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _imported_files.insert(0, file_info)
        print(f"✅ 向量库构建完成：{count} 个文本块")
        return {"success": True, "message": f"已处理 {count} 个文本块", "file": file_info}
    except FileNotFoundError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "上传文件不存在"},
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)},
        )


@app.post("/delete-file")
async def delete_file(filename: str = Form(...)):
    """删除已上传的文件及其向量库数据"""
    global _vector_ready, _imported_files

    # 1. 在所有上传目录中查找文件
    file_path = None
    for scan_dir in ["uploads/pdf", "uploads/md", "uploads"]:
        candidate = os.path.join(scan_dir, filename)
        if os.path.exists(candidate):
            file_path = os.path.abspath(candidate)
            break

    if not file_path:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": f"文件 '{filename}' 不存在"},
        )

    try:
        # 2. 从 ChromaDB 中删除该文件的所有文档块（按 source 元数据过滤）
        db_client = ReadersChromadb(
            persist_directory=config.CHROMA_DB_DIR,
            collection_name=config.CHROMA_COLLECTION,
        )
        # Chroma 的 delete 支持 where 过滤
        # 使用 os.path.normpath 确保与存储时的路径格式一致
        source_path = os.path.normpath(file_path)
        deleted_count = db_client.vector_store.delete(where={"source": source_path})
        print(f"🗑️ 从向量库删除了 {deleted_count or 0} 个文本块 (source={source_path})")

        # 3. 删除物理文件
        os.remove(file_path)
        print(f"🗑️ 已删除文件: {file_path}")

        # 4. 更新内存中的文件列表
        _imported_files = [f for f in _imported_files if f["name"] != filename]

        # 5. 更新向量库状态
        if not _imported_files:
            _vector_ready = False

        return {"success": True, "message": f"已删除 '{filename}' 及对应的向量数据"}
    except Exception as e:
        print(f"❌ 删除失败: {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"删除失败: {str(e)}"},
        )


@app.get("/uploaded-files")
async def uploaded_files():
    """获取已导入文件列表"""
    return {"files": _imported_files, "vector_ready": _vector_ready}


@app.post("/chat")
async def chat(user_input: str = Form(...)):
    """接收用户需求 -> 跑完整测试流程 -> 返回结果"""
    if not _vector_ready:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "请先上传 PDF 文档",
            },
        )

    try:
        response = _chat_func(user_input)
        if response:
            result = {
                "success": True,
                "thinking": response.proper_thinking,
                "reply": response.final_response,
            }
            # 如果生成了 Excel 计划，把路径返回给前端
            if hasattr(response, "excel_path") and response.excel_path:
                result["excel_path"] = response.excel_path
                result["excel_name"] = os.path.basename(response.excel_path)
                result["output_dir"] = getattr(response, "output_dir", os.path.dirname(response.excel_path))
            # 保存 API 定义和上下文供后续步骤使用
            global _last_api_defs, _last_user_input
            if hasattr(response, "api_definition_list"):
                _last_api_defs = response.api_definition_list
            _last_user_input = user_input
            return result
        return {"success": False, "message": "模型无响应"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)},
        )


@app.post("/open-file")
async def open_file(file_path: str = Form(...)):
    """打开本地文件（调用系统默认应用）"""
    import os as _os
    try:
        _os.startfile(file_path)
        return {"success": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/confirm-plan")
async def confirm_plan(excel_path: str = Form(None)):
    """确认测试计划 -> 生成 .py 和 .yaml 文件"""
    print(">>> 测试计划已确认，开始生成测试文件...")

    # 优先使用前端传入的路径，否则从全局查找最新的 Excel
    if not excel_path:
        import glob
        excel_files = glob.glob(os.path.join(config.TESTCASE_BASE, "**", "test_plan.xlsx"), recursive=True)
        if excel_files:
            excel_path = max(excel_files, key=os.path.getmtime)

    if not excel_path:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "未找到测试计划 Excel 文件"},
        )

    if not _components:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "组件未初始化"},
        )

    try:
        # Step A: 生成 .py 文件
        py_result = _components._generate_py_file(excel_path)

        # Step B: 生成 YAML 数据文件
        api_defs = _last_api_defs or []
        api_defs_json = json.dumps(
            [a.model_dump() if hasattr(a, "model_dump") else a for a in api_defs],
            indent=2, ensure_ascii=False,
        ) if api_defs else "[]"
        user_ctx = _last_user_input or ""
        yaml_result = _components._generate_all_yamls(excel_path, api_defs_json, user_ctx)

        msg = f".py: {py_result['py_file_name']}（{py_result['modules']}模块）"
        if yaml_result["total"] > 0:
            msg += f" | YAML: {yaml_result['success']}/{yaml_result['total']} 个"

        return {
            "success": True,
            "message": msg,
            "py_file": py_result["py_file_name"],
            "yaml_success": yaml_result["success"],
            "yaml_total": yaml_result["total"],
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)},
        )


# ----------------------------------------------------------------
# 启动
# ----------------------------------------------------------------
if __name__ == "__main__":
    import threading

    local_url = f"http://{config.WEB_HOST}:{config.WEB_PORT}"

    # 在子线程中启动 uvicorn
    server_config = uvicorn.Config(app, host=config.WEB_HOST, port=config.WEB_PORT)
    server = uvicorn.Server(server_config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # 输出访问提示
    print(f"\n🌐 本地访问地址: {local_url}")
    print(f"   如果 0.0.0.0 无法访问，尝试: http://localhost:{config.WEB_PORT}")
    print(f"\n💡 输入 q 并回车可停止服务\n")

    # 监听键盘输入 "q" 停止服务
    try:
        while True:
            cmd = input().strip().lower()
            if cmd == "q":
                print(">>> 正在停止服务 ...")
                server.should_exit = True
                break
    except (KeyboardInterrupt, EOFError):
        print("\n>>> 正在停止服务 ...")
        server.should_exit = True
