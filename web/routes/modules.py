"""模块管理路由：树查询 + CRUD + 合并 + 术语表。"""

from urllib.parse import unquote

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database import get_session_ctx
from database.operations import ModuleOps, BindingOps, GlossaryOps

router = APIRouter(prefix="/api/modules", tags=["modules"])


# ── 模块树查询 ──

@router.get("")
async def get_modules():
    """获取模块树。"""
    with get_session_ctx() as session:
        return {"success": True, "tree": ModuleOps.get_tree(session)}


@router.get("/{module_name}/docs")
async def get_module_docs(module_name: str):
    """获取模块关联的所有文档和接口。"""
    from web.state import _chroma_db  # 避免循环导入，从 state 取避免 None 引用

    try:
        with get_session_ctx() as session:
            docs = BindingOps.get_bound_docs(session, module_name)
            chroma = _chroma_db
            result = []
            for d in docs:
                item = {
                    "doc_id": d.id, "module": module_name,
                    "doc_type": d.doc_type, "type": d.doc_type,
                    "chunks": d.chunk_count, "file_name": d.file_name,
                }
                if d.doc_type == "api" and chroma is not None:
                    try:
                        apis = chroma.get_doc_apis(d.id)
                        item["api_count"] = len(apis)
                        item["api_names"] = [a["api_name"] for a in apis
                                             if a.get("api_name")]
                    except Exception:
                        item["api_count"] = 0
                        item["api_names"] = []
                result.append(item)
            return {"success": True, "docs": result}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"success": False, "message": str(e)})


@router.get("/{module_name}/related")
async def get_module_related(module_name: str):
    """获取模块的关联模块（module↔module）。"""
    with get_session_ctx() as session:
        partners = BindingOps.get_partners(
            session, "module", module_name, "module",
        )
        return {"success": True,
                "related": [{"name": p[1]} for p in partners]}


# ── 模块 CRUD ──

@router.post("")
async def create_module(data: dict):
    """创建模块。"""
    name = data.get("name", "").strip()
    parent_id = data.get("parent_id")
    if not name:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "模块名不能为空"})
    with get_session_ctx() as session:
        mod = ModuleOps.create_module(session, name=name, parent_id=parent_id)
        mod_id, mod_name, mod_parent = mod.id, mod.name, mod.parent_id
    return {"success": True, "module": {
        "id": mod_id, "name": mod_name, "parent_id": mod_parent,
    }}


@router.put("/{module_id}")
async def update_module(module_id: str, data: dict):
    """更新模块（重命名）。"""
    if "name" in data:
        with get_session_ctx() as session:
            ok, msg = ModuleOps.rename_module(session, module_id, data["name"])
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse(status_code=400,
                            content={"success": False, "message": msg})
    return JSONResponse(status_code=400,
                        content={"success": False, "message": "缺少 name 参数"})


@router.delete("/{module_id}")
async def delete_module(module_id: str):
    """删除模块。"""
    try:
        with get_session_ctx() as session:
            ok, msg = ModuleOps.delete_module(session, module_id)
        if ok:
            return {"success": True, "message": "已删除"}
        return JSONResponse(status_code=400,
                            content={"success": False, "message": msg})
    except Exception as e:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": str(e)})


@router.post("/merge")
async def merge_modules(data: dict):
    """合并模块。"""
    source = data.get("source_id")
    target = data.get("target_id")
    try:
        with get_session_ctx() as session:
            ok, msg = ModuleOps.merge_modules(session, source, target)
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse(status_code=400,
                            content={"success": False, "message": msg})
    except Exception as e:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": str(e)})


# ── 术语表 ──

def _term_to_dict(t) -> dict:
    return {"term": t.term, "definition": t.definition, "notes": t.notes or ""}


@router.get("/{module_name}/glossary")
async def get_glossary(module_name: str):
    """获取模块术语表。"""
    with get_session_ctx() as session:
        terms = GlossaryOps.get_terms_for_module(session, module_name)
        return {"success": True, "terms": [_term_to_dict(t) for t in terms]}


@router.post("/{module_name}/glossary")
async def add_glossary_term(module_name: str, data: dict):
    """添加/更新模块术语。"""
    term = data.get("term", "").strip()
    definition = data.get("definition", "").strip()
    notes = data.get("notes", "").strip()
    if not term:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "术语名不能为空"})
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, module_name)
        if not mod:
            return JSONResponse(status_code=404,
                                content={"success": False, "message": "模块不存在"})
        # 术语绑定到模块所在的产品文档（通过 bindings）
        doc_ids = []
        for pt, pi in BindingOps.get_partners(session, "module", module_name, "product"):
            doc_ids.append(pi)
        if doc_ids:
            GlossaryOps.add_term(session, doc_ids[0], term, definition, notes=notes)
        else:
            return JSONResponse(status_code=400,
                                content={"success": False, "message": "模块未绑定产品文档"})
        return {"success": True, "message": f"已保存: {term}"}


@router.delete("/{module_name}/glossary/{term}")
async def delete_glossary_term(module_name: str, term: str):
    """删除模块术语。"""
    with get_session_ctx() as session:
        terms = GlossaryOps.get_terms_for_module(session, module_name)
        decoded = unquote(term)
        for t in terms:
            if t.term == decoded:
                GlossaryOps.delete_term(session, t.id)
                return {"success": True, "message": f"已删除: {term}"}
        return JSONResponse(status_code=404,
                            content={"success": False, "message": "模块或术语不存在"})


# ── 模块场景分析 CRUD（Phase A 入库预处理） ──

@router.get("/{module_name}/analysis")
async def get_module_analysis(module_name: str):
    """读取模块的场景分析 JSON。"""
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, module_name)
        if not mod:
            return JSONResponse(status_code=404,
                                content={"success": False, "message": "模块不存在"})
        from database.operations.analysis import AnalysisOps
        record = AnalysisOps.get_by_module_id(session, mod.id)
        if not record:
            return {"success": True, "analysis": None}
        return {
            "success": True,
            "analysis": {
                "module_id": record.module_id,
                "module_name": record.module_name,
                "analysis_json": record.analysis_json,
                "scenario_analysis": record.scenario_analysis,
                "ui_flow_analysis": record.ui_flow_analysis,
                "api_analysis": record.api_analysis,
                "status": record.status,
                "version": record.version,
                "extracted_at": record.extracted_at.isoformat() if record.extracted_at else None,
                "modified_at": record.modified_at.isoformat() if record.modified_at else None,
            },
        }


@router.put("/{module_name}/analysis")
async def update_module_analysis(module_name: str, data: dict):
    """手动保存编辑后的场景分析（支持新三步格式和旧 JSON 格式）。"""
    analysis_json = data.get("analysis_json", "")
    scenario_analysis = data.get("scenario_analysis", "")
    ui_flow_analysis = data.get("ui_flow_analysis", "")
    api_analysis = data.get("api_analysis", "")
    is_new_format = scenario_analysis or ui_flow_analysis or api_analysis
    if not is_new_format and not analysis_json:
        return JSONResponse(status_code=400,
                            content={"success": False, "message": "缺少分析内容"})
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, module_name)
        if not mod:
            return JSONResponse(status_code=404,
                                content={"success": False, "message": "模块不存在"})
        from database.operations.analysis import AnalysisOps
        if is_new_format:
            record = AnalysisOps.upsert_3step(
                session, mod.id, module_name,
                scenario_analysis=scenario_analysis,
                ui_flow_analysis=ui_flow_analysis,
                api_analysis=api_analysis,
            )
        else:
            record = AnalysisOps.upsert(session, mod.id, module_name, analysis_json)
        # 手动编辑标记为 reviewed
        record.status = "reviewed"
        record.modified_by = data.get("modified_by", "")
        session.commit()
        return {"success": True, "message": "已保存", "version": record.version}


@router.post("/{module_name}/analysis/approve")
async def approve_module_analysis(module_name: str):
    """标记场景分析为 approved（仅前端追踪，Phase B 不检查）。"""
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, module_name)
        if not mod:
            return JSONResponse(status_code=404,
                                content={"success": False, "message": "模块不存在"})
        from database.operations.analysis import AnalysisOps
        record = AnalysisOps.get_by_module_id(session, mod.id)
        if not record:
            return JSONResponse(status_code=404,
                                content={"success": False, "message": "未找到分析记录"})
        record.status = "approved"
        session.commit()
        return {"success": True, "message": "已标记为 approved"}


@router.delete("/{module_name}/analysis")
async def delete_module_analysis(module_name: str):
    """删除模块的场景分析（绑定变更时由前端调用）。"""
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, module_name)
        if not mod:
            return JSONResponse(status_code=404,
                                content={"success": False, "message": "模块不存在"})
        from database.operations.analysis import AnalysisOps
        AnalysisOps.delete_by_module_id(session, mod.id)
        session.commit()
        return {"success": True, "message": "已删除"}


@router.get("/{module_name}/api-defs")
async def get_module_api_defs(module_name: str, doc_id: str = ""):
    """读取模块的接口定义（含 annotations 字段）。

    可传 doc_id 过滤只返回该文档的接口；不传则返回全部。
    优先从 SQLite documents.api_* 列读取，降级 ChromaDB。
    """
    import json as _json
    from database.models import Document

    with get_session_ctx() as session:
        docs = BindingOps.get_bound_docs(session, module_name)
        result = []
        for d in docs:
            if d.doc_type != "api":
                continue
            if doc_id and d.id != doc_id:
                continue

            # ── 优先：SQLite api_* 列 ──
            if d.api_url:
                api = {
                    "name": d.api_name,
                    "url": d.api_url,
                    "method": d.api_method,
                    "description": d.api_description,
                    "headers": _json.loads(d.api_headers or "[]"),
                    "parameters": _json.loads(d.api_parameters or "[]"),
                    "returns": _json.loads(d.api_returns or "[]"),
                    "annotations": _json.loads(d.api_annotations or "{}"),
                }
                result.append(api)
                continue

            # ── 降级：ChromaDB（存量数据未迁移）──
            from web.state import _chroma_db
            chroma = _chroma_db
            if not chroma:
                continue
            apis = chroma.get_doc_apis(d.id)
            for raw in apis:
                api = {}
                content_str = raw.get("content", "")
                if content_str:
                    try:
                        api = _json.loads(content_str)
                    except (_json.JSONDecodeError, TypeError):
                        api = {"_parse_error": True, "_raw": str(raw)}
                if not api.get("name") and raw.get("api_name"):
                    api["name"] = raw["api_name"]
                if "annotations" not in api:
                    api["annotations"] = {}
                result.append(api)
        return {"success": True, "api_defs": result}


@router.put("/{module_name}/api-defs/{index}/annotations")
async def update_api_annotations(module_name: str, index: int, data: dict):
    """更新单个接口的 annotations 字段（SQLite 为准，ChromaDB 异步重建）。

    body = {annotations: {...}, doc_id: "..."}
    可选 full_update: {...}  完整替换 API 定义的字段（name/url/method/description/headers/parameters/returns）
    """
    import json as _json
    from database.models import Document
    from agent_components.dual_chroma import get_chroma_db
    from ingest_v2 import _build_api_search_text

    annotations = data.get("annotations")
    doc_id_filter = data.get("doc_id", "")
    full_update = data.get("full_update")

    with get_session_ctx() as session:
        docs = BindingOps.get_bound_docs(session, module_name)
        # 过滤出 API 文档，按 index 定位
        api_docs = []
        for d in docs:
            if d.doc_type != "api":
                continue
            if doc_id_filter and d.id != doc_id_filter:
                continue
            api_docs.append(d)

        if index < 0 or index >= len(api_docs):
            return JSONResponse(status_code=400,
                                content={"success": False, "message": "索引超出范围"})

        target = api_docs[index]

        # ── 1. 更新 SQLite api_* 列（以 SQLite 为准）──
        update_vals = {
            "api_annotations": _json.dumps(annotations, ensure_ascii=False),
        }
        if full_update:
            for key, col in [("name", "api_name"), ("url", "api_url"),
                             ("method", "api_method"), ("description", "api_description")]:
                if key in full_update:
                    update_vals[col] = full_update[key]
            for key, col in [("headers", "api_headers"), ("parameters", "api_parameters"),
                             ("returns", "api_returns")]:
                if key in full_update:
                    update_vals[col] = _json.dumps(full_update[key], ensure_ascii=False)

        session.query(Document).filter_by(id=target.id).update(update_vals)
        session.commit()

        # ── 2. 异步重建 ChromaDB 检索文本 ──
        try:
            doc = session.query(Document).filter_by(id=target.id).first()
            api = {
                "name": doc.api_name, "url": doc.api_url,
                "method": doc.api_method, "description": doc.api_description,
                "headers": _json.loads(doc.api_headers or "[]"),
                "parameters": _json.loads(doc.api_parameters or "[]"),
                "returns": _json.loads(doc.api_returns or "[]"),
                "annotations": _json.loads(doc.api_annotations or "{}"),
            }
            api["_search_text"] = _build_api_search_text(api)
            api["_doc_id"] = doc.id

            db = get_chroma_db()
            db.delete_by_doc_id(doc.id)
            db.add_api_defs(doc.id, [api])
        except Exception as e:
            logger.warning("ChromaDB 检索文本重建失败，创建补偿任务: %s", e)
            try:
                from database.operations.compensation import CompensationOps
                import config
                CompensationOps.create(
                    session, "api_search_text",
                    {"doc_id": target.id},
                    max_retries=config.COMPENSATION_MAX_RETRIES,
                )
                session.commit()
            except Exception as e2:
                logger.error("补偿任务创建也失败: %s", e2)

        return {"success": True, "message": "已更新"}


@router.get("/{module_name}/annotation-types")
async def get_annotation_types(module_name: str):
    """返回可选的 API 异常标识类型列表（前端下拉菜单）。"""
    from agent_components.api_annotations import ApiAnnotationRegistry
    types = ApiAnnotationRegistry.get_types()
    return {
        "success": True,
        "types": [
            {"key": t.key, "label": t.label, "description": t.description,
             "category": t.category}
            for t in types
        ],
    }
