"""Phase C 多跳检索节点 Mixin"""
import json
import os
from datetime import datetime

import config
from observability import get_logger, get_error_snapshot_logger
from agent_components.dual_chroma import get_chroma_db
from agent_components.state import State
from prompts.response_model import TestPointList, IntentConfirmation, ApiDefinition

logger = get_logger(__name__)


def _mod_exists_in_tree(module_name: str) -> bool:
    """检查模块名是否在模块树中真实存在。"""
    import agent_components.module_tree as mt
    try:
        all_modules = mt.get_all()
        return any(m.get("name") == module_name for m in all_modules)
    except Exception:
        logger.debug("查询模块树失败，假定模块 [%s] 不存在", module_name, exc_info=True)
        return False


class RetrievalMixin:
    """Phase C 多跳检索 + 测试点分析节点"""
    # ==================== 图外方法（确认后执行） ====================

    # ==================== Phase C 多跳检索 + 测试点分析 ====================

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

    # ---- 辅助：从 ChromaDB 检索 ----

    def _search_product_docs(self, query: str, doc_ids: list[str] | None = None) -> list[dict]:
        """检索产品文档，无结果返回空列表。"""
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
        return []

    def _search_api_defs(self, query: str, doc_ids: list[str] | None = None) -> list[dict]:
        """检索接口定义，无结果返回空列表。"""
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
        return []

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
        import agent_components.module_tree as mt
        all_modules = mt.get_all()
        module_names = [m["name"] for m in all_modules if m.get("name")]

        if not module_names:
            logger.warning("   ⚠️ 模块树为空，跳过意图识别")
            return {
                "candidate_modules": [],
                "confirmation_question": "系统中暂无可用模块，请先上传文档并创建模块。",
                "workflow_status": "WAITING",
            }

        # LLM 语义匹配
        prompt = self.prompt_factory.confirm_user_intent()
        result = self._invoke_structured(
            prompt, IntentConfirmation,
            method="json_mode", thinking=False,
            user_input=state["user_input"],
            module_list="\n".join(f"- {n}" for n in module_names),
        )

        candidates = result.matched_modules if result else []
        confidence = result.confidence if result else "low"

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

    # ---- 节点 3：提取关联模块（基于 SQLite 绑定关系） ----

    def _extract_related_modules(self, state: State):
        """从 product_docs 中提取关联模块（一次 SQL 批量查询，避免 N+1）。"""
        logger.info("\n--- 提取关联模块 ---")
        confirmed_module = state.get("confirmed_module", "")
        related: set[str] = set()

        doc_sources = [d.get("source", d.get("doc_id", ""))
                       for d in state.get("product_docs", [])]
        doc_sources = [s for s in doc_sources if s]  # 过滤空

        if doc_sources:
            from database import get_session_ctx
            from database.operations import BindingOps
            with get_session_ctx() as session:
                results = BindingOps.get_partners_batch(
                    session, "product", doc_sources, partner_type="module",
                )
                for doc_id, partners in results.items():
                    for _ptype, pname in partners:
                        if pname and pname != confirmed_module:
                            related.add(pname)

        mods = sorted(related)
        logger.info(f"   => 关联模块: {mods if mods else '无'}")
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
            elif _mod_exists_in_tree(_base_mod):
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

        # 接口去重（同一接口绑定到多个模块时只保留一份）
        seen_api = {}
        for a in api_defs:
            key = f"{a.get('method', '')} {a.get('url', '')}"
            seen_api.setdefault(key, a)
        if len(api_defs) != len(seen_api):
            logger.info(f"   => 接口去重: {len(seen_api)} 个唯一（去重前 {len(api_defs)} 个）")
            api_defs = list(seen_api.values())

        logger.info(f"   => 汇总: {len(all_docs)} 文档片段, {len(api_defs)} 个接口")
        return {"product_docs": all_docs, "api_definitions": api_defs}

    # ---- 节点 5：测试点分析 ----

    def _analyze_test_points_raw(self, state: State):
        """Phase C — 测试点原始分析（thinking 节点）：输出自由文本分析报告。"""
        logger.info("\n🧠 分析测试场景（深度思考）...")
        prompt = self.prompt_factory.analyze_test_points_raw()

        docs_text = "\n\n".join(
            f"[{d.get('module', d.get('source', '?'))}] {d.get('content', '')}"
            for d in state.get("product_docs", [])
        )
        related_text = ", ".join(state.get("related_modules", [])) or "无"
        apis_text = "\n".join(
            f"  - {a.get('name', '?')} ({a.get('method', 'GET')} {a.get('url', '')})"
            for a in state.get("api_definitions", [])
        )

        # 显式控制 thinking 开关（bind 方式，invoke 的 **kwargs 会被 LangChain 路由到 RunnableConfig）
        llm_kwargs = {}
        if config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        bound_llm = self.llm.bind(**llm_kwargs)
        result = bound_llm.invoke(
            prompt.format_messages(
                user_context=state["original_input"],
                product_docs=docs_text,
                related_docs=related_text,
                api_definitions=apis_text,
            ),
        )
        analysis = result.content if hasattr(result, "content") else str(result)
        logger.info(f"   => 测试场景分析完成（{len(analysis)} 字符）")
        return {"test_point_analysis": analysis}

    def _format_test_points(self, state: State):
        """Phase C — 格式化测试点为 JSON（thinking off + json_mode）。"""
        logger.info("\n--- 格式化测试点 ---")
        prompt = self.prompt_factory.format_test_points()

        docs_text = "\n\n".join(
            f"[{d.get('module', d.get('source', '?'))}] {d.get('content', '')}"
            for d in state.get("product_docs", [])
        )
        related_text = ", ".join(state.get("related_modules", [])) or "无"
        apis_text = "\n".join(
            f"  - {a.get('name', '?')} ({a.get('method', 'GET')} {a.get('url', '')})"
            for a in state.get("api_definitions", [])
        )

        result = self._invoke_structured(prompt, TestPointList,
            method="json_mode",
            user_context=state["original_input"],
            product_docs=docs_text,
            related_docs=related_text,
            api_definitions=apis_text,
            test_point_analysis=state.get("test_point_analysis") or "（无）",
        )

        if isinstance(result, list):
            result = TestPointList(test_points=result, project_name="Unknown", summary="")

        count = len(result.test_points)
        logger.info(f"   => 完成: {count} 个测试点")
        if result.risk_areas:
            areas_str = "; ".join(
                f"{r.area}({r.reason})" if hasattr(r, 'reason') else str(r)
                for r in result.risk_areas
            )
            logger.info(f"   => 风险区域: {areas_str}")

        return {"test_points": result.model_dump()}

    # ---- 节点 6：桥接（不变） ----

    def _prepare_excel_plan_data(self, state: State):
        """桥接：将 api_definitions (dicts) 转换为 api_definition_list (ApiDefinition 列表)。"""
        raw = state.get("api_definitions", [])
        api_definition_list = []
        for d in raw:
            api_definition_list.append(ApiDefinition(
                name=d.get("name", "未命名"),
                url=d.get("url", ""),
                method=d.get("method", "GET"),
                description=d.get("description", d.get("name", "")),
                parameters=d.get("parameters", d.get("params", {})),
                returns=d.get("returns", {}),
            ))
        logger.info(f"   => 桥接: {len(api_definition_list)} 个接口 -> api_definition_list")
        return {"api_definition_list": api_definition_list}

