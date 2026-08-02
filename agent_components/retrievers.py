"""Phase B 多跳检索节点 Mixin"""
import json
import os
from datetime import datetime

import config
from observability import get_logger
from agent_components.dual_chroma import get_chroma_db
from agent_components.state import State
from prompts.response_model import IntentConfirmation

logger = get_logger(__name__)


def _mod_exists_in_tree(module_name: str, session) -> bool:
    """检查模块名是否在模块树中真实存在。"""
    from database.operations import ModuleOps
    try:
        all_modules = ModuleOps.get_all(session)
        return any(m.name == module_name for m in all_modules)
    except Exception:
        logger.debug("查询模块树失败，假定模块 [%s] 不存在", module_name, exc_info=True)
        return False


def _build_full_api_defs_text(api_defs: list[dict]) -> str:
    """从 api_definitions 中提取 doc_id，到 SQLite 查完整定义，构造 LLM 输入文本。

    每个 API 格式:
      [{method}] {url} — {name}
        描述: {description}
        参数: param1(type, 必填/可选): desc; param2(type, 可选): desc
        返回值: ret1(type): desc; ret2(type): desc
    """
    import json as _json
    from database import get_session_ctx
    from database.models import Document as DocModel

    # 收集所有 api 文档的 doc_id（去重）
    doc_ids: set[str] = set()
    for a in api_defs:
        sid = a.get("source", a.get("_doc_id", ""))
        if sid:
            doc_ids.add(sid)

    if not doc_ids:
        # 降级：直接用 api_defs 字典里的数据拼
        return _fallback_api_text(api_defs)

    # 从 SQLite 查全量 API 定义
    try:
        with get_session_ctx() as session:
            records = session.query(DocModel).filter(
                DocModel.id.in_(list(doc_ids)), DocModel.doc_type == "api"
            ).all()
            if not records:
                return _fallback_api_text(api_defs)

            parts = []
            for d in records:
                if not d.api_url:
                    continue
                lines = [f"[{d.api_method or '?'}] {d.api_url} — {d.api_name or ''}"]
                if d.api_description:
                    lines.append(f"  描述: {d.api_description}")
                # 参数
                params = _json.loads(d.api_parameters or "[]")
                if params:
                    param_strs = _format_params(params)
                    if param_strs:
                        lines.append(f"  参数: {param_strs}")
                # 返回值
                returns = _json.loads(d.api_returns or "[]")
                if returns:
                    ret_strs = _format_params(returns)
                    if ret_strs:
                        lines.append(f"  返回值: {ret_strs}")
                parts.append("\n".join(lines))
            return "\n\n".join(parts)
    except Exception:
        logger.warning("SQLite API 定义查询失败，降级", exc_info=True)
        return _fallback_api_text(api_defs)


def _format_params(params: list, indent: int = 3) -> str:
    """递归格式化参数列表为紧凑文本。

    每项: name(type, 必填/可选): desc

    Args:
        params: 参数列表
        indent: 缩进 level，用于嵌套子字段前缀
    """
    strs = []
    prefix = "  " * indent
    for p in params:
        if not isinstance(p, dict):
            continue
        name = p.get("name", "")
        ptype = p.get("type", "string")
        required = "必填" if p.get("required") else "可选"
        desc = p.get("description", "")
        if desc:
            item = f"{prefix}{name}({ptype}, {required}): {desc}"
        else:
            item = f"{prefix}{name}({ptype}, {required})"
        strs.append(item)
        # 递归处理子字段
        children = p.get("children", [])
        if children:
            child_str = _format_params(children, indent + 1)
            if child_str:
                strs.append(child_str)
    return "; ".join(strs)


def _fallback_api_text(api_defs: list[dict]) -> str:
    """降级：直接用 api_defs 字典拼凑（ChromaDB 检索结果可能只有摘要）。"""
    lines = []
    for a in api_defs:
        name = a.get("name", "?")
        method = a.get("method", "GET")
        url = a.get("url", "")
        desc = a.get("description", "")
        params = a.get("parameters", [])
        returns = a.get("returns", [])
        line = f"[{method}] {url} — {name}"
        if desc:
            line += f"\n  描述: {desc}"
        if params:
            line += f"\n  参数: {_format_params(params)}"
        if returns:
            line += f"\n  返回值: {_format_params(returns)}"
        lines.append(line)
    return "\n\n".join(lines) if lines else "无"


class RetrievalMixin:
    """Phase B 多跳检索 + 测试点分析节点"""
    # ==================== 图外方法（确认后执行） ====================

    # ==================== Phase B 多跳检索 + 测试点分析 ====================

    # ---- 辅助：从 ChromaDB 检索结果中提取文本 ----

    @staticmethod
    def _docs_to_text(docs: list) -> str:
        """将 ChromaDB Document 列表或 dict 列表拼接为上下文字符串。"""
        parts = []
        for d in docs:
            if hasattr(d, "page_content"):
                parts.append(d.page_content)
            elif isinstance(d, dict):
                parts.append(d.get("content", d.get("page_content", "")))
        return "\n\n---\n\n".join(parts)

    # ---- 辅助：从 ChromaDB 检索（含 SQLite 即时补偿） ----

    def _search_product_docs(self, query: str, doc_ids: list[str] | None = None) -> list[dict]:
        """检索产品文档，ChromaDB 空/异常时从 SQLite 即时补偿。

        补偿数据源优先级：analyzed_summary > simple_summary > content 前 500 字。
        """
        try:
            results = self.dual_chroma.search_product_docs(query, k=config.RETRIEVAL_K, doc_ids=doc_ids)
            if results:
                logger.info(f"   ChromaDB 命中 {len(results)} 条 product_docs")
                return [
                    {"content": r.page_content, "source": r.metadata.get("doc_id", ""),
                     "type": "product_doc"}
                    for r in results
                ]
        except Exception as e:
            logger.warning("ChromaDB product_docs 检索异常: %s", e, exc_info=True)

        # ── 即时补偿：ChromaDB 空/异常 → SQLite document_chunks ──
        logger.info("   ChromaDB 无结果，走 SQLite 即时补偿...")
        return self._compensate_product_docs_from_sqlite(doc_ids)

    @staticmethod
    def _compensate_product_docs_from_sqlite(doc_ids: list[str] | None = None) -> list[dict]:
        """ChromaDB 不可用时，从 SQLite document_chunks 即时补偿产品/Axure 文档。

        补偿数据源优先级：analyzed_summary > simple_summary > content 前 500 字
        """
        from database import get_session_ctx
        from database.models import DocumentChunk
        docs = []
        try:
            with get_session_ctx() as session:
                q = session.query(DocumentChunk)
                if doc_ids:
                    q = q.filter(DocumentChunk.doc_id.in_(doc_ids))
                chunks = q.order_by(DocumentChunk.chunk_index).limit(config.RETRIEVAL_K).all()
                for c in chunks:
                    text = c.analyzed_summary or c.simple_summary or (
                        c.content[:500] if c.content else "")
                    docs.append({
                        "content": text,
                        "source": c.doc_id,
                        "type": "product_doc",
                        "_compensated": True,
                    })
            logger.info(f"   SQLite 即时补偿: {len(docs)} 条 document_chunks")
        except Exception as e:
            logger.warning("SQLite 即时补偿也失败: %s", e)
        return docs

    def _search_api_defs(self, query: str, doc_ids: list[str] | None = None) -> list[dict]:
        """检索接口定义，ChromaDB 空/异常时从 SQLite 即时补偿。"""
        try:
            results = self.dual_chroma.search_api_defs(query, k=config.RETRIEVAL_K, doc_ids=doc_ids)
            if results:
                logger.info(f"   ChromaDB 命中 {len(results)} 条 api_defs")
                apis = []
                for r in results:
                    content = r.page_content
                    try:
                        api = json.loads(content) if content.strip().startswith("{") else {"raw": content}
                    except json.JSONDecodeError:
                        api = {"raw": content}
                    if isinstance(api, dict):
                        api.setdefault("source", r.metadata.get("doc_id", ""))
                        apis.append(api)
                return apis
        except Exception as e:
            logger.warning("ChromaDB api_defs 检索异常: %s", e, exc_info=True)

        # ── 即时补偿：ChromaDB 空/异常 → SQLite documents ──
        logger.info("   ChromaDB 无结果，走 SQLite 即时补偿...")
        return self._compensate_api_defs_from_sqlite(doc_ids)

    @staticmethod
    def _compensate_api_defs_from_sqlite(doc_ids: list[str] | None = None) -> list[dict]:
        """ChromaDB 不可用时，从 SQLite documents.api_* 列即时补偿 API 定义。"""
        import json as _json
        from database import get_session_ctx
        from database.models import Document as DocModel
        apis = []
        try:
            with get_session_ctx() as session:
                q = session.query(DocModel).filter_by(doc_type="api")
                if doc_ids:
                    q = q.filter(DocModel.id.in_(doc_ids))
                records = q.limit(config.RETRIEVAL_K).all()
                for d in records:
                    if d.api_url:
                        api = {
                            "name": d.api_name, "url": d.api_url,
                            "method": d.api_method, "description": d.api_description,
                            "headers": _json.loads(d.api_headers or "[]"),
                            "parameters": _json.loads(d.api_parameters or "[]"),
                            "returns": _json.loads(d.api_returns or "[]"),
                            "annotations": _json.loads(d.api_annotations or "{}"),
                            "source": d.id, "_compensated": True,
                        }
                        apis.append(api)
            logger.info(f"   SQLite 即时补偿: {len(apis)} 条 API 定义")
        except Exception as e:
            logger.warning("SQLite 即时补偿也失败: %s", e)
        return apis

    # ---- 节点 1：意图识别与推荐 ----

    def _confirm_user_intent(self, state: State):
        """纯语义计算：根据用户输入匹配候选模块，不触碰业务数据。

        工作流恢复路径：当 state 已携带 confirmed_module + CONFIRMED 状态时，
        跳过 LLM 调用直接放行，避免覆盖恢复进度。
        """
        # 恢复路径：用户已在前端确认模块 → 跳过意图识别直接放行
        if state.get("confirmed_module") and state.get("workflow_status") == "CONFIRMED":
            logger.info("   => 恢复路径: 已确认模块 [%s]，跳过意图识别", state["confirmed_module"])
            return {
                "candidate_modules": [state["confirmed_module"]],
                "workflow_status": "CONFIRMED",
            }

        logger.info("\n🎯 [节点1] 意图识别与模块推荐 ---")

        # 获取所有模块名
        from database import get_session_ctx
        from database.operations import ModuleOps
        with get_session_ctx() as session:
            all_modules = ModuleOps.get_all(session)
            module_names = [m.name for m in all_modules if m.name]

        if not module_names:
            logger.warning("   ⚠️ 模块树为空，跳过意图识别")
            return {
                "candidate_modules": [],
                "confirmation_question": "系统中暂无可用模块，请先上传文档并创建模块。",
                "workflow_status": "WAITING",
            }

        # LLM 语义匹配（解析失败时降级为"未匹配"，不抛 500 中断流程）
        prompt = self.prompt_factory.confirm_user_intent()
        try:
            result = self._invoke_structured(
                prompt, IntentConfirmation,
                method="json_mode", thinking=False,
                user_input=state["user_input"],
                module_list="\n".join(f"- {n}" for n in module_names),
            )
            candidates = result.matched_modules if result else []
            confidence = result.confidence if result else "low"
        except Exception as e:
            logger.warning("   ⚠️ LLM 意图识别解析失败，降级为未匹配: %s", e)
            candidates = []
            confidence = "low"

        # 过滤：只保留真实存在于模块树中的候选
        candidates = [c for c in candidates if c in module_names]

        if candidates and confidence != "low":
            question = (
                "根据您的描述，我为您找到了以下相关模块，请确认或选择：\n"
                + "\n".join(f"{i+1}. {name}" for i, name in enumerate(candidates))
                + "\n\n如果以上都不是，请重新描述您的需求。"
            )
        else:
            candidates = []
            question = "未能确定您所指的模块，请更具体地描述您的需求（例如模块名称或业务场景）。"

        logger.info(f"   => 候选模块: {candidates}, 置信度: {confidence}")
        return {
            "candidate_modules": candidates,
            "confirmation_question": question,
            "workflow_status": "WAITING",
        }

    # ---- 节点 2：精准产品文档检索 ----

    def _retrieve_product_docs(self, state: State):
        """Hop 1: 基于确认的模块名 + 用户输入，精准检索产品文档。

        检索策略：
          1. SQLite BindingOps 获取 confirmed_module 绑定的 doc_id 列表
          2. ChromaDB 语义检索 + doc_id 过滤
          3. 无结果 → workflow_status = "NO_DATA"，中断流程提示用户导入数据
        """
        logger.info("\n--- [Hop 1] 精准检索产品文档 ---")
        query = state["user_input"]
        confirmed_module = state.get("confirmed_module", "")

        docs = []
        doc_ids = None

        # Step 1: SQLite 精确过滤
        if confirmed_module:
            from database import get_session_ctx
            from database.operations import BindingOps
            with get_session_ctx() as session:
                bound_docs = BindingOps.get_bound_docs(session, confirmed_module)
                doc_ids = [d.id for d in bound_docs if d.doc_type in ("product", "axure")]
                logger.info(f"   模块 [{confirmed_module}] 绑定 {len(doc_ids)} 个产品文档")

        # Step 2: ChromaDB 语义检索
        if doc_ids:
            docs = self._search_product_docs(query, doc_ids=doc_ids)

        # 无 doc_id 过滤或过滤后无结果 → 全库检索
        if not docs:
            docs = self._search_product_docs(query)

        # Step 3: 无数据 → 中断流程
        if not docs:
            logger.warning(f"   ❌ 未检索到任何产品文档，请先导入数据")
            return {
                "product_docs": [],
                "context": "",
                "workflow_status": "NO_DATA",
                "confirmation_question": (
                    f"模块「{confirmed_module}」下未找到任何产品文档。\n"
                    "请先上传产品文档（PDF/Word/Axure）并关联到对应模块后再试。"
                ),
            }

        logger.info(f"   => 检索到 {len(docs)} 条产品文档片段")
        return {
            "product_docs": docs,
            "context": self._docs_to_text(docs),
        }

    # ---- 节点 3：提取关联模块（三路召回，基于 SQLite 绑定关系） ----

    def _extract_related_modules(self, state: State):
        """从已确认模块的所有绑定关系中提取关联模块。

        三路召回策略：
          1. module↔module 直接绑定 —— 模块之间的显式关联
          2. product/axure 文档 → 其他模块 —— 文档被多个模块共享
          3. API 文档 → 其他模块 —— API 被多个模块引用

        直接查询 SQLite，不依赖 state 中 ChromaDB 检索结果（不完整）。
        """
        logger.info("\n--- 提取关联模块（三路召回） ---")
        confirmed_module = state.get("confirmed_module", "")
        related: set[str] = set()

        if not confirmed_module:
            logger.info("   => 无确认模块，跳过关联模块提取")
            return {"related_modules": []}

        from database import get_session_ctx
        from database.operations import BindingOps

        with get_session_ctx() as session:
            # ── 路径 1：module↔module 直接绑定 ──
            mod_partners = BindingOps.get_partners(
                session, "module", confirmed_module, partner_type="module",
            )
            for _ptype, pname in mod_partners:
                if pname and pname != confirmed_module:
                    related.add(pname)
            logger.info(
                "   路径1 (module↔module): %d 个关联模块",
                len([p for p in mod_partners if p[1] != confirmed_module]),
            )

            # ── 路径 2+3：通过模块下所有类型文档查找关联模块 ──
            bound_docs = BindingOps.get_bound_docs(session, confirmed_module)

            # 按 doc_type 分组（product/axure 和 api 的绑定类型不同，需分别查询）
            product_ids = [d.id for d in bound_docs if d.doc_type == "product"]
            axure_ids = [d.id for d in bound_docs if d.doc_type == "axure"]
            api_ids = [d.id for d in bound_docs if d.doc_type == "api"]

            # 路径 2a：product 文档 → 共享模块
            if product_ids:
                results = BindingOps.get_partners_batch(
                    session, "product", product_ids, partner_type="module",
                )
                for _doc_id, partners in results.items():
                    for _ptype, pname in partners:
                        if pname and pname != confirmed_module:
                            related.add(pname)
                logger.info("   路径2a (product→module): %d 个文档参与查询", len(product_ids))

            # 路径 2b：axure 文档 → 共享模块
            if axure_ids:
                results = BindingOps.get_partners_batch(
                    session, "axure", axure_ids, partner_type="module",
                )
                for _doc_id, partners in results.items():
                    for _ptype, pname in partners:
                        if pname and pname != confirmed_module:
                            related.add(pname)
                logger.info("   路径2b (axure→module): %d 个文档参与查询", len(axure_ids))

            # 路径 3：API 文档 → 共享模块
            if api_ids:
                results = BindingOps.get_partners_batch(
                    session, "api", api_ids, partner_type="module",
                )
                for _doc_id, partners in results.items():
                    for _ptype, pname in partners:
                        if pname and pname != confirmed_module:
                            related.add(pname)
                logger.info("   路径3 (api→module): %d 个文档参与查询", len(api_ids))

        mods = sorted(related)
        logger.info(f"   => 关联模块汇总: {mods if mods else '无'}")
        return {"related_modules": mods}

    # ---- 节点 4：关联数据检索（Hop 2a + 2b） ----

    def _retrieve_related_data(self, state: State):
        """Hop 2a+2b: 检索关联模块的产品文档和接口定义。"""
        logger.info("\n--- [Hop 2] 检索关联数据 ---")
        modules: list[str] = state.get("related_modules", [])
        all_docs: list[dict] = list(state.get("product_docs", []))
        query = state["user_input"]

        from database import get_session_ctx
        from database.operations import BindingOps
        with get_session_ctx() as session:
            # Hop 2a: 检索关联模块的产品文档（按模块过滤 doc_id）
            for mod in modules:
                bound_docs = BindingOps.get_bound_docs(session, mod)
                doc_ids = [d.id for d in bound_docs if d.doc_type in ("product", "axure")]
                if not doc_ids:
                    continue
                extra = self._search_product_docs(query, doc_ids=doc_ids)
                for d in extra:
                    if d not in all_docs:
                        all_docs.append(d)
                        logger.info(f"   + 追加文档: {mod}")

            # Hop 2b: 检索主模块 + 关联模块 + 公共基础服务的接口定义
            api_defs: list[dict] = []
            confirmed_module = state.get("confirmed_module", "")
            search_modules: list[str] = list(dict.fromkeys(
                [m for m in [confirmed_module] + modules if m]
            ))
            # 公共基础服务模块（存在时才加入检索，避免空查和无关干扰）
            _base_mod = config.COMMON_SERVICE_MODULE
            if _base_mod in search_modules:
                pass  # 已在列表中
            elif _mod_exists_in_tree(_base_mod, session):
                search_modules.append(_base_mod)
            else:
                logger.debug("模块 [%s] 不存在，跳过", _base_mod)

            for mod in search_modules:
                bound_docs = BindingOps.get_bound_docs(session, mod)
                doc_ids = [d.id for d in bound_docs if d.doc_type == "api"]
                if not doc_ids:
                    continue
                apis = self._search_api_defs(query, doc_ids=doc_ids)
                if apis:
                    api_defs.extend(apis)
                    logger.info(f"   + 接口: {mod} ({len(apis)} 个)")

        # 接口去重（同一接口绑定到多个模块时只保留一份，后出现的覆盖先出现的）
        seen_api = {}
        dup_count = 0
        for a in api_defs:
            key = f"{a.get('method', '')} {a.get('url', '')}"
            if key in seen_api:
                dup_count += 1
            seen_api[key] = a  # 后出现的覆盖先出现的，保留最新版本
        if dup_count:
            logger.info(f"   => 接口去重: 合并 {dup_count} 个重复，剩余 {len(seen_api)} 个唯一（去重前 {len(api_defs)} 个）")
            api_defs = list(seen_api.values())

        logger.info(f"   => 汇总: {len(all_docs)} 文档片段, {len(api_defs)} 个接口")
        return {"product_docs": all_docs, "api_definitions": api_defs}

    # ---- 节点 5：测试点分析 ----

    def _analyze_test_points_raw(self, state: State):
        """Phase B — 测试点原始分析（thinking 节点）：输出自由文本分析报告。

        优先路径：module_analysis 存在 → 注入预分析结果，跳过 product_docs。
        降级路径：module_analysis 不存在 → 全量 product_docs（旧逻辑）。
        """
        from observability import log_phase_header
        log_phase_header("Phase B — 测试点分析")
        logger.info("\n🧠 分析测试场景（深度思考）...")
        prompt = self.prompt_factory.analyze_test_points_raw()

        # ── 优先/降级：查询 module_analysis ──
        confirmed_module = state.get("confirmed_module", "")
        analysis_text = ""
        try:
            from database import get_session_ctx
            from database.operations import ModuleOps
            from database.operations.analysis import AnalysisOps
            with get_session_ctx() as session:
                mod = ModuleOps.get_by_name(session, confirmed_module)
                if mod:
                    record = AnalysisOps.get_by_module_id(session, mod.id)
                    if record:
                        # 三步分析文本优先，旧 analysis_json 降级兼容
                        parts = []
                        if getattr(record, 'scenario_analysis', None):
                            parts.append("### 测试场景分析\n" + record.scenario_analysis)
                        if getattr(record, 'ui_flow_analysis', None):
                            parts.append("### 页面交互逻辑\n" + record.ui_flow_analysis)
                        if getattr(record, 'api_analysis', None):
                            parts.append("### 接口映射分析\n" + record.api_analysis)
                        if parts:
                            analysis_text = "\n\n".join(parts)
                        elif record.analysis_json:
                            analysis_text = record.analysis_json
                        logger.info(f"   📋 命中 module_analysis（{len(analysis_text)} 字符），走优先路径")
        except Exception:
            logger.warning("   ⚠️ module_analysis 查询失败，走降级路径", exc_info=True)

        # ── 优先路径：跳过 product_docs ──
        if analysis_text:
            docs_text = ""  # 不注入全量产品文档
        else:
            logger.info("   📄 无 module_analysis，走降级路径（全量原文）")
            docs_text = "\n\n".join(
                f"[{d.get('module', d.get('source', '?'))}] {d.get('content', '')}"
                for d in state.get("product_docs", [])
            )

        related_text = ", ".join(state.get("related_modules", [])) or "无"

        # ── 构造完整 API 定义文本（从 SQLite 查全量，非 ChromaDB 摘要）──
        apis_text = _build_full_api_defs_text(state.get("api_definitions", []))
        logger.info(f"   => API 定义文本: {len(apis_text)} 字符")

        # 显式控制 thinking 开关
        llm_kwargs = {}
        if config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        bound_llm = self.llm.bind(temperature=0.6, **llm_kwargs)
        result = bound_llm.invoke(
            prompt.format_messages(
                user_context=state["original_input"],
                module_analysis=analysis_text or "无",
                product_docs=docs_text,
                related_docs=related_text,
                api_definitions=apis_text,
            ),
        )
        analysis = result.content if hasattr(result, "content") else str(result)
        logger.info(f"   => 测试场景分析完成（{len(analysis)} 字符）")
        from observability import log_thinking
        log_thinking("analyze_test_points_raw", state["original_input"], analysis, prompt_label="analyze_test_points_raw_prompt")
        return {"test_point_analysis": analysis}

