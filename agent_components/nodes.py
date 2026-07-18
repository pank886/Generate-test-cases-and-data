"""LangGraph 各个节点方法"""
import json
import os
import threading
from collections import defaultdict
from datetime import datetime
from typing import Optional, Type

import openai
from pydantic import BaseModel, ValidationError

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from langchain_core.exceptions import OutputParserException
from agent_components.llm.deepseek import DeepSeekChatOpenAI

import config
from observability import get_logger, get_error_snapshot_logger
from agent_components.dual_chroma import get_chroma_db
from agent_components.state import State, ApiDefinitionList
from prompts.response_model import (
    ProperResponse,
    ApiDefinition,
    TestData,
    ExcelPlan,
    ExcelRow,
    ExcelPlanV2,
    SharedPrecondition,
    TestCaseRow,
    PyFile,
    ClassCode,
    IntentConfirmation,
)
from prompts.definitions import PromptFactory
from agent_components.retrievers import RetrievalMixin
from agent_components.generators import GenerationMixin

logger = get_logger(__name__)

# 方法特性配置表（声明式，集中管理 method 与 thinking 的兼容性）
METHOD_FEATURES = {
    "function_calling": {"supports_thinking": False},
    "json_mode": {"supports_thinking": False},
    "json_schema": {"supports_thinking": False},
    "free_text": {"supports_thinking": True},
}

# 数据工厂方法缓存已归位 data_factory/registry.py（此处不再维护）

# 全局共享的 LLM 客户端单例（避免多个 ChatTestAgentGraph 实例重复创建）
_llm_instance: Optional[DeepSeekChatOpenAI] = None
_llm_lock = threading.Lock()


def reload_llm():
    """重置 LLM 单例，下次 _get_llm 调用时使用最新配置重建（支持热重载）。"""
    global _llm_instance
    with _llm_lock:
        _llm_instance = None


def _get_llm() -> DeepSeekChatOpenAI:
    global _llm_instance
    if _llm_instance is None:
        with _llm_lock:
            if _llm_instance is None:  # 双重检查锁，防并发竞态
                _llm_instance = DeepSeekChatOpenAI(
                    model=config.LLM_MODEL,
                    base_url=config.LLM_BASE_URL,
                    api_key=config.LLM_API_KEY(),
                    temperature=config.LLM_TEMPERATURE,
                )
    return _llm_instance


class ChatTestAgentGraph(RetrievalMixin, GenerationMixin):
    """智能测试助手——LangGraph 节点方法的容器类

    Phase A 节点 + 核心工具方法（本文件）
    Phase B 检索节点 → RetrievalMixin (retrievers.py)
    Phase C PY/YAML 生成节点 → GenerationMixin (generators.py)
    """

    def __init__(self):
        self.llm = _get_llm()

        self.prompt_factory = PromptFactory()

        self.dual_chroma = get_chroma_db()

        # 工作流日志累积器（同一次运行的所有节点共用一份文件）
        self._run_data: dict = {}
        self._run_timestamp: Optional[str] = None


    # ==================== 图内节点方法 ====================

    def _retrieve_node(self, state: State):
        """检索知识库"""
        # 新运行开始，重置日志累积器
        self._run_data = {}
        self._run_timestamp = None

        logger.info("🔍 [节点] 正在调用外部工具检索...")
        try:
            context = self.dual_chroma.search_context(
                query=state["user_input"],
                k=config.RETRIEVAL_K,
            )
        except Exception as e:
            logger.error("ChromaDB 检索失败: %s", e, exc_info=True)
            context = f"【向量库异常】{e}，请检查 Ollama 服务状态后重试"
        self._log_node_output("retrieve", {"context": context})
        return {"context": context}

    def _parse_api_node(self, state: State):
        """分析接口定义"""
        logger.info("\n正在分析文档，提取接口定义...")

        prompt = self.prompt_factory.parse_api_node()
        result = self._invoke_structured(
            prompt, ApiDefinitionList, method="json_mode",
            content=state["context"],
            user_context=state["original_input"],
        )

        # json_mode 有时会返回 [{...}] 而非 {"apis": [{...}]}
        if isinstance(result, list):
            result = ApiDefinitionList(apis=result)
        api_list = result.apis
        if isinstance(api_list, list):
            logger.info(f"   🛠️ 成功提取到 {len(api_list)} 个接口:")
            for api in api_list:
                logger.info(f"      - {api.name}: {api.url}")
        else:
            logger.info(f"   ⚠️ 提取结果异常: {result}")
            api_list = []

        self._log_node_output("parse_api", {"api_definition_list": api_list})
        return {"api_definition_list": api_list}

    def _analyze_scenarios_node(self, state: State):
        """场景分析（thinking 节点）：输出自由文本分析报告供 format 节点使用。"""
        logger.info("\n🧠 正在分析测试场景（深度思考）...")
        prompt = self.prompt_factory.analyze_scenarios()
        all_apis_dict = [api.model_dump() for api in state["api_definition_list"]]
        if not all_apis_dict:
            logger.warning("接口列表为空，场景分析将无内容")
        all_apis_json = json.dumps(all_apis_dict, indent=2, ensure_ascii=False)

        # 显式控制 thinking 开关（bind 方式，invoke 的 **kwargs 会被 LangChain 路由到 RunnableConfig）
        llm_kwargs = {}
        if config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        bound_llm = self.llm.bind(**llm_kwargs)
        result = bound_llm.invoke(
            prompt.format_messages(
                all_apis_info=all_apis_json,
                user_context=state["original_input"],
            ),
        )
        analysis = result.content if hasattr(result, "content") else str(result)
        logger.info(f"   => 场景分析完成（{len(analysis)} 字符）")
        self._log_node_output("analyze_scenarios", {"scenario_analysis": analysis[:200]})
        return {"scenario_analysis": analysis, "all_apis_json": all_apis_json}

    def _generate_excel_plan_node(self, state: State):
        """生成 Excel 测试计划 V2（双 Sheet：测试计划 + 共享前置）。"""
        logger.info("\n📊 正在生成 Excel 测试计划...")

        from prompts.extraction_prompts import repair_excel_plan_prompt
        from agent_components.validator import validate_excel_file
        from prompts.response_model import ApiDefinition

        prompt = self.prompt_factory.generate_excel_plan_node()
        api_list = state.get("api_definition_list")
        if api_list is None:
            api_list = [
                ApiDefinition(
                    name=d.get("name", "?"), url=d.get("url", ""),
                    method=d.get("method", "GET"), description=d.get("description", ""),
                    parameters=d.get("parameters", {}), returns=d.get("returns", {}),
                )
                for d in (state.get("api_definitions") or [])
            ]
        all_apis_dict = [api.model_dump() for api in api_list]
        all_apis_json = state.get("all_apis_json")
        if not all_apis_json:
            all_apis_json = json.dumps(all_apis_dict, indent=2, ensure_ascii=False)
        import agent_components.module_tree as mt
        from database import get_session_ctx
        with get_session_ctx() as session:
            tree = mt.get_tree(session)
        module_tree_json = json.dumps(tree, indent=2, ensure_ascii=False)
        test_analysis = state.get("test_point_analysis") or state.get("scenario_analysis") or "（无）"
        prompt_vars = {
            "module_tree": module_tree_json,
            "test_analysis": test_analysis,
            "all_apis_info": all_apis_json,
            "user_context": state["original_input"],
        }

        output_dir = None
        plan = None
        failed_details: list[tuple[int, dict, list[str]]] = []
        all_confirmed: list = []
        all_shared_pres: list = []  # 首轮共享前置，重试时复用
        failed_ids: set = set()  # 失败行 TC ID 集合，重试时只接受这些 ID 的修复

        for attempt in range(config.EXCEL_REPAIR_ATTEMPTS):
            if attempt == 0:
                plan = self._invoke_structured(prompt, ExcelPlanV2,
                    method="json_mode", **prompt_vars)
                if isinstance(plan, list):
                    plan = ExcelPlanV2(shared_preconditions=[], test_cases=plan)
                all_shared_pres = plan.shared_preconditions

                # 首轮校验全部用例
                pre_ids = {p.id for p in plan.shared_preconditions}
                _new_failed: list = []
                for i, tc in enumerate(plan.test_cases, 1):
                    errs = []
                    for fld, lbl in [("id", "编号"), ("story", "子模块"), ("title", "标题"),
                                     ("steps", "步骤"), ("expected", "预期")]:
                        if not getattr(tc, fld, ""):
                            errs.append(f"{lbl}为空")
                    for pid in tc.preconditions:
                        if pid not in pre_ids:
                            errs.append(f"引用前置 {pid} 不存在")
                    if tc.steps and tc.expected:
                        ns = tc.steps.count("\n") + 1
                        ne = tc.expected.count("\n") + 1
                        if ns != ne:
                            errs.append(f"步骤({ns}条)与预期({ne}条)数量不一致")
                    if errs:
                        _new_failed.append((i, tc.model_dump(), errs))
                    else:
                        all_confirmed.append(tc)

                # 记录失败行 ID（重试时只有匹配这些 ID 的修复才被接受）
                failed_ids = {f[1].get("id", "") for f in _new_failed}
                failed_details = _new_failed
                logger.warning(
                    f"   ⚠️ 校验: {len(all_confirmed)} 用例通过, "
                    f"{len(failed_details)} 失败 (第1次)"
                )
                if not failed_details:
                    break
            else:
                # 重试：LLM 只返回失败行的修复版
                attempt_label = f"第{attempt+1}次重试"
                failed_tc_list = []
                for f_idx, f_dict, f_errs in failed_details:
                    failed_tc_list.append(
                        f"TC ID: {f_dict.get('id','?')}\n"
                        f"  子模块: {f_dict.get('story','?')}\n"
                        f"  标题: {f_dict.get('title','?')}\n"
                        f"  步骤: {f_dict.get('steps','?')}\n"
                        f"  预期: {f_dict.get('expected','?')}\n"
                        f"  错误: {'; '.join(f_errs)}"
                    )
                failed_tc_text = "\n---\n".join(failed_tc_list)
                # 把必须保持不变的 TC ID 列表写入 prompt
                repair_prompt = repair_excel_plan_prompt()
                failed_ids_str = ", ".join(sorted(failed_ids))

                plan = self._invoke_structured(repair_prompt, ExcelPlanV2,
                    method="json_mode",
                    original_test_analysis=test_analysis,
                    failed_test_cases=failed_tc_text,
                    failed_ids=failed_ids_str,
                )
                if isinstance(plan, list):
                    plan = ExcelPlanV2(shared_preconditions=[], test_cases=plan)

                # 重试校验：只接受 ID 匹配失败行的修复
                pre_ids_all = {p.id for p in all_shared_pres}
                for p in plan.shared_preconditions:
                    pre_ids_all.add(p.id)

                _new_failed = []
                fixed_ids = set()
                for tc in plan.test_cases:
                    # 拒绝不在失败 ID 集合中的行（LLM 幻觉出新的用例）
                    if tc.id not in failed_ids:
                        logger.warning(f"   ⚠️ 重试返回了不在失败列表中的 TC {tc.id}，已丢弃")
                        continue
                    errs = []
                    for fld, lbl in [("id", "编号"), ("story", "子模块"), ("title", "标题"),
                                     ("steps", "步骤"), ("expected", "预期")]:
                        if not getattr(tc, fld, ""):
                            errs.append(f"{lbl}为空")
                    for pid in tc.preconditions:
                        if pid not in pre_ids_all:
                            errs.append(f"引用前置 {pid} 不存在")
                    if tc.steps and tc.expected:
                        ns = tc.steps.count("\n") + 1
                        ne = tc.expected.count("\n") + 1
                        if ns != ne:
                            errs.append(f"步骤({ns}条)与预期({ne}条)数量不一致")
                    if errs:
                        _new_failed.append((0, tc.model_dump(), errs))
                    else:
                        all_confirmed.append(tc)
                        fixed_ids.add(tc.id)

                # 仍未修复的失败行：保留在 failed_details 中
                _still_failed = [
                    (f_idx, f_dict, f_errs)
                    for f_idx, f_dict, f_errs in failed_details
                    if f_dict.get("id", "") not in fixed_ids
                ]
                failed_details = _still_failed + _new_failed
                logger.warning(
                    f"   ⚠️ 校验: {len(all_confirmed)} 用例通过（本次修复 {len(plan.test_cases)} 行, "
                    f"接受 {len(fixed_ids)}, 仍失败 {len(failed_details)}）, "
                    f"({attempt_label})"
                )
                if not failed_details:
                    break

        # 最终校验 + 引用完整性
        if failed_details:
            pre_ids = {p.id for p in plan.shared_preconditions} if plan else set()
            valid_cases = [tc for tc in all_confirmed]
            for f_idx, f_dict, f_errs in failed_details:
                orphan = [p for p in (f_dict.get("preconditions") or []) if p not in pre_ids]
                if orphan:
                    continue
                valid_cases.append(TestCaseRow(
                    id=f_dict.get("id","?"), story=f_dict.get("story",""),
                    title=f_dict.get("title","?"),
                    preconditions=f_dict.get("preconditions") or [],
                    steps=f_dict.get("steps",""), expected=f_dict.get("expected",""),
                    mutates_data=f_dict.get("mutates_data", False),
                    is_negative_test=f_dict.get("is_negative_test", False),
                ))
        else:
            valid_cases = all_confirmed

        if failed_details:
            from observability import log_thinking
            error_parts = []
            for f_idx, f_dict, f_errs in failed_details:
                error_parts.append(
                    f"第{f_idx}行 | {f_dict.get('id','?')} | " + "; ".join(f_errs))
            fail_text = "\n".join(error_parts)
            log_thinking("generate_excel_plan_FAILED",
                         state.get("original_input", "?"),
                         f"校验失败 {len(failed_details)} 行\n"
                         f"--- 通过: {len(valid_cases)} 行 ---\n"
                         f"--- 失败详情 ---\n{fail_text}",
                         prompt_label="generate_excel_plan_node")

        if not valid_cases:
            return {
                "excel_plan": None, "excel_path": "",
                "output_dir": output_dir, "error_info": ["所有行均未通过校验"],
                "response_obj": ProperResponse(
                    proper_thinking=[], worth_to_remember=False,
                    final_response="Excel 测试计划生成失败：所有用例均未通过校验，请重试",
                ),
            }

        # 模块树路径
        tree_module = state.get("confirmed_module") or ""
        def _find_node_path(nodes, target, parts=None):
            if parts is None: parts = []
            for n in (nodes or []):
                if n.get("name") == target and n.get("name") != "全部模块":
                    return parts + [n.get("name")]
                found = _find_node_path(n.get("children") or [], target,
                                         parts + ([n.get("name")] if n.get("name") != "全部模块" else []))
                if found: return found
            return None
        path_parts = _find_node_path(tree, tree_module)
        if path_parts:
            dir_prefix = os.path.join(config.TESTCASE_BASE, *path_parts)
            _project = path_parts[0]
            _feature = path_parts[-1]
        else:
            _project = tree_module or valid_cases[0].story
            _feature = tree_module or valid_cases[0].story
            dir_prefix = os.path.join(config.TESTCASE_BASE, _project, _feature)

        output_dir = state.get("output_dir")
        if not output_dir:
            base_path = dir_prefix
            def _is_dir_empty(d):
                if not os.path.exists(d): return True
                try: return not any(os.path.isfile(os.path.join(d,f)) for f in os.listdir(d))
                except OSError: return True
            if os.path.exists(base_path) and not _is_dir_empty(base_path):
                for n in range(2, 1000):
                    alt = f"{dir_prefix}_{n}"
                    if not os.path.exists(alt) or _is_dir_empty(alt):
                        output_dir = alt; break
                else:
                    from datetime import datetime
                    output_dir = f"{dir_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            else:
                output_dir = base_path
        os.makedirs(output_dir, exist_ok=True)
        excel_path = os.path.join(output_dir, "test_plan.xlsx")

        # Phase B 资源冲突消解（纯代码，LLM 输出 → Excel 写入之间）
        if plan is not None and plan.shared_preconditions:
            self._resolve_resource_conflicts(plan)

        # 写双 Sheet
        n_confirmed = len(valid_cases)
        wb = Workbook()
        hf = Font(bold=True, color="FFFFFF", size=11)
        hfill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
        tb = Border(left=Side(style="thin"), right=Side(style="thin"),
                    top=Side(style="thin"), bottom=Side(style="thin"))
        wa = Alignment(wrap_text=True, vertical="center")

        # Sheet 1: 测试计划（9列）
        ws1 = wb.active
        ws1.title = "测试计划"
        h1 = ["@allure.epic", "@allure.feature", "@allure.story", "@allure.title",
              "fixture等级", "用例编号", "前置步骤", "执行步骤", "预期结果"]
        for col, h in enumerate(h1, 1):
            c = ws1.cell(row=1, column=col, value=h)
            c.font, c.fill, c.border, c.alignment = hf, hfill, tb, Alignment(horizontal="center", vertical="center")
        for i, tc in enumerate(valid_cases, 2):
            vals = [_project, _feature, tc.story, tc.title, "danyuan", tc.id,
                    ", ".join(tc.preconditions) if tc.preconditions else "无",
                    tc.steps, tc.expected]
            for col, val in enumerate(vals, 1):
                c = ws1.cell(row=i, column=col, value=val); c.border, c.alignment = tb, wa

        # Sheet 2: 共享前置
        ws2 = wb.create_sheet("共享前置")
        h2 = ["前置编号", "前置名称", "详细步骤", "预期结果", "关联用例"]
        for col, h in enumerate(h2, 1):
            c = ws2.cell(row=1, column=col, value=h)
            c.font, c.fill, c.border, c.alignment = hf, hfill, tb, Alignment(horizontal="center", vertical="center")
        pre_to_cases = {}
        for tc in valid_cases:
            for pid in tc.preconditions:
                pre_to_cases.setdefault(pid, []).append(tc.id)
        for i, pre in enumerate((plan.shared_preconditions if plan else []), 2):
            linked = ", ".join(pre_to_cases.get(pre.id, []))
            vals = [pre.id, pre.name, pre.steps, pre.expected, linked or "（无引用）"]
            for col, val in enumerate(vals, 1):
                c = ws2.cell(row=i, column=col, value=val); c.border, c.alignment = tb, wa

        for ws in (ws1, ws2):
            for ci, h in enumerate([c.value for c in ws[1]], 1):
                mx = max((len(str(ws.cell(r, ci).value or "")) for r in range(2, ws.max_row + 1)), default=0)
                ws.column_dimensions[get_column_letter(ci)].width = max(len(str(h)) + 2, min(mx + 2, 55))
        wb.save(excel_path); wb.close()

        # 接口定义快照与 Excel 同目录落盘 —— Phase C 生成 YAML 的数据来源。
        # 规则 M8：接口定义靠产物传递（快照随计划走），禁止依赖内存态跨阶段交接；
        # 此前 api_definition_list 在 _resume_workflow_bg 交接时丢失，导致 Phase C 空定义盲写。
        api_defs_path = os.path.join(output_dir, "api_defs.json")
        try:
            with open(api_defs_path, "w", encoding="utf-8") as f:
                json.dump(all_apis_dict, f, ensure_ascii=False, indent=2)
            logger.info(f"   📄 接口定义快照已保存: {api_defs_path} ({len(all_apis_dict)} 个接口)")
        except OSError:
            logger.error("接口定义快照写入失败（Phase C 确认时将按 M8 阻断）: %s",
                         api_defs_path, exc_info=True)

        fail_warn = f"（{len(failed_details)} 行未通过校验，需人工审查）" if failed_details else ""
        n_modules = len(set(tc.story for tc in valid_cases))
        n_pres = len(plan.shared_preconditions) if plan else 0
        logger.info(f"   📄 Excel 已保存: {excel_path} ({n_confirmed}条/{n_modules}模块, {n_pres}共享前置){fail_warn}")

        self._log_node_output("generate_excel_plan",
                              {"excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir})
        file_ok, file_errors = validate_excel_file(excel_path)
        if not file_ok:
            logger.warning(f"   ⚠️ 文件校验失败: {len(file_errors)} 个错误")
            from observability import log_thinking
            log_thinking("generate_excel_plan_FILE_FAIL",
                         state.get("original_input", "?"),
                         f"文件层校验失败（{n_confirmed} 行通过）\n文件: {excel_path}\n错误: {'; '.join(file_errors)}",
                         prompt_label="generate_excel_plan_node")
        return {
            "excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir,
            "response_obj": ProperResponse(
                proper_thinking=[f"已提取 {len(all_apis_dict)} 个接口，分析 {n_confirmed} 条用例"],
                final_response=f"Excel 测试计划已生成：共 {n_confirmed} 条用例{fail_warn}",
                worth_to_remember=False,
            ),
        }

    # ==================== 图外方法（确认后执行） ====================
    # ==================== 通用工具方法 ====================

    def _validate_excel_plan(self, plan: ExcelPlan) -> list:
        """校验 Excel 计划数据质量"""
        errors = []

        if not plan.rows:
            errors.append("rows 为空，未生成任何用例")
            return errors

        for idx, row in enumerate(plan.rows, 1):
            if not row.project_name:
                errors.append(f"第{idx}行: 项目名称为空")
            if not row.module_name:
                errors.append(f"第{idx}行: 模块名称为空")
            if not row.allure_story:
                errors.append(f"第{idx}行: Allure Story 为空")
            if not row.fixture_level:
                errors.append(f"第{idx}行: fixture等级为空")
            if not row.case_name:
                errors.append(f"第{idx}行: 用例名称为空")
            elif not row.case_name.startswith("test_"):
                errors.append(f"第{idx}行: 用例名称 '{row.case_name}' 必须以 test_ 开头")
            if not row.test_data_yaml:
                errors.append(f"第{idx}行: 测试数据YAML为空")
            if row.enabled not in ("Y", "N"):
                errors.append(f"第{idx}行: 是否启用必须为 Y 或 N，当前为 '{row.enabled}'")

        return errors

    # ==================== Phase B 资源冲突消解 ====================

    @staticmethod
    def _find_pre(plan: ExcelPlanV2, pre_id: str) -> SharedPrecondition | None:
        """在 plan.shared_preconditions 中查找指定 id 的前置条件。"""
        for pre in plan.shared_preconditions:
            if pre.id == pre_id:
                return pre
        return None

    def _resolve_resource_conflicts(self, plan: ExcelPlanV2) -> None:
        """资源冲突消解：检测同一 PRE 被多个正向写操作用例引用时，克隆隔离。

        纯代码节点，不调用 LLM。嵌入在 _generate_excel_plan_node 内部，
        LLM 生成 → 校验 → 消解 → 写 Excel 的流程中执行。

        算法:
          1. 关键词兜底 LLM 漏标（mutates_data 未标但 steps 含写操作关键词）
          2. 构建 PRE → 正向写操作用例列表
          3. 同一 PRE 被 ≥2 个正向写操作用例引用 → 克隆隔离
        """
        if not plan or not plan.test_cases:
            return

        # 1. 代码兜底 LLM 漏标
        for tc in plan.test_cases:
            if not tc.preconditions or tc.mutates_data:
                continue
            if any(kw in tc.steps for kw in config.RESOURCE_MUTATE_KEYWORDS):
                tc.mutates_data = True
                logger.debug(
                    "消解器兜底: %s 未标 mutates_data，但步骤含写操作关键词，已自动标记",
                    tc.id,
                )

        # 2. 构建 PRE → 正向写操作用例列表
        pre_refs: dict[str, list] = defaultdict(list)
        for tc in plan.test_cases:
            if not tc.mutates_data or tc.is_negative_test:
                continue
            for pid in tc.preconditions:
                pre_refs[pid].append(tc)

        # 3. 检测冲突 → 克隆隔离
        isolation_count = 0
        for pre_id, ref_list in pre_refs.items():
            if len(ref_list) <= 1:
                continue
            original = self._find_pre(plan, pre_id)
            if original is None:
                logger.warning("消解器: PRE %s 被 %d 个用例引用但未在 shared_preconditions 中找到，跳过",
                               pre_id, len(ref_list))
                continue
            # 第一个用例保持引用原始 PRE，其余克隆隔离
            for tc in ref_list[1:]:
                clone_id = f"{pre_id}_isolated_{tc.id}"
                plan.shared_preconditions.append(SharedPrecondition(
                    id=clone_id,
                    name=f"{original.name}（{tc.id}专用）",
                    steps=original.steps,
                    expected=original.expected,
                    cloned_from=pre_id,
                ))
                tc.preconditions = [
                    clone_id if p == pre_id else p for p in tc.preconditions
                ]
                isolation_count += 1
                logger.info("消解器: %s → %s（%s 隔离）", pre_id, clone_id, tc.id)

        if isolation_count:
            logger.info("消解器完成: %d 个 PRE 被隔离，共 %d 条用例受影响",
                        len([p for p, r in pre_refs.items() if len(r) > 1]),
                        isolation_count)

    # ==================== 日志辅助方法 ====================

    @staticmethod
    def _serialize_for_log(obj):
        """递归序列化对象为 JSON 可序列化的格式"""
        if isinstance(obj, BaseModel):
            return obj.model_dump()
        elif isinstance(obj, dict):
            return {k: ChatTestAgentGraph._serialize_for_log(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [ChatTestAgentGraph._serialize_for_log(v) for v in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        else:
            return str(obj)

    def _log_node_output(self, node_name: str, output: dict):
        """将节点产出物累积到当前运行日志文件（同一次运行共用一份 JSON + MD）"""
        from pathlib import Path
        log_dir = Path("logs") / "workflow"
        log_dir.mkdir(parents=True, exist_ok=True)

        # 首次调用时生成时间戳（同一次运行保持不变）
        if self._run_timestamp is None:
            self._run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 累积数据
        self._run_data[node_name] = self._serialize_for_log(output)

        base_name = f"workflow_{self._run_timestamp}"

        # ---- JSON（全量数据） ----
        json_path = log_dir / f"{base_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self._run_data, f, ensure_ascii=False, indent=2)

        # ---- MD（可读摘要） ----
        md_lines = [
            "# 工作流运行日志",
            f"**运行时间**: {self._run_timestamp[:4]}-{self._run_timestamp[4:6]}-{self._run_timestamp[6:8]} "
            f"{self._run_timestamp[9:11]}:{self._run_timestamp[11:13]}:{self._run_timestamp[13:15]}",
            "",
        ]
        node_order = ["retrieve", "parse_api", "analyze_scenarios", "generate_excel_plan",
                       "analyze_test_points_raw", "format_test_points",
                       "generate_py_file", "generate_all_yamls"]
        for nname in node_order:
            if nname not in self._run_data:
                continue
            data = self._run_data[nname]
            md_lines.append(f"## {nname}")

            if nname == "retrieve":
                ctx = data.get("context", "")
                summary = f"检索到 {len(ctx)} 字符" if ctx and ctx != "未检索到知识库" else "未检索到知识库"
                md_lines.append(f"**摘要**: {summary}")
                md_lines.append("```")
                md_lines.append(f"{ctx[:3000]}{'…(截断)' if len(ctx) > 3000 else ''}")
                md_lines.append("```")
            elif nname == "parse_api":
                apis = data.get("api_definition_list", [])
                md_lines.append(f"**摘要**: 提取了 {len(apis)} 个接口\n")
                for i, api in enumerate(apis, 1):
                    md_lines.append(f"### {i}. {api.get('name', '未命名')}")
                    md_lines.append(f"- **路径**: `{api.get('url', '')}`")
                    md_lines.append(f"- **方法**: {api.get('method', '')}")
                    ret = api.get("returns", {})
                    md_lines.append(f"- **返回字段**: {json.dumps(ret, ensure_ascii=False) if ret else '未提取'}")
            elif nname == "generate_excel_plan":
                plan = data.get("excel_plan", {})
                rows = plan.get("rows", []) if isinstance(plan, dict) else []
                modules = len(set(r.get("module_name", "") for r in rows)) if rows else 0
                md_lines.append(f"**摘要**: {len(rows)} 条用例，{modules} 个模块")
                md_lines.append(f"- **文件**: {data.get('excel_path', '')}")
                if rows:
                    md_lines.append("\n**模块列表**")
                    seen = set()
                    for r in rows:
                        mn = r.get("module_name", "")
                        if mn not in seen:
                            seen.add(mn)
                            md_lines.append(f"- `{mn}`")
            elif nname == "generate_py_file":
                md_lines.append(f"**摘要**: {data.get('py_file_name', '')}（{data.get('modules', 0)} 模块，{data.get('cases', 0)} 用例）")
                md_lines.append(f"- **文件**: {data.get('py_file_name', '')}")
                md_lines.append(f"- **路径**: {data.get('py_path', '')}")
                md_lines.append(f"- **模块数**: {data.get('modules', 0)}")
                md_lines.append(f"- **用例数**: {data.get('cases', 0)}")
            elif nname == "generate_all_yamls":
                total, ok, fail = data.get("total", 0), data.get("success", 0), data.get("failed", 0)
                md_lines.append(f"**摘要**: {ok}/{total} 成功{'，' + str(fail) + ' 失败' if fail else ''}")
                md_lines.append(f"- **总数**: {total}")
                md_lines.append(f"- **成功**: {ok}")
                md_lines.append(f"- **失败**: {fail}")
            md_lines.append("")

        md_path = log_dir / f"{base_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines) + "\n")

        # 清理：保留 ≤15 组（30 个文件）
        self._cleanup_logs(str(log_dir), max_pairs=15)

    @staticmethod
    def _cleanup_logs(log_dir: str, max_pairs: int = 15):
        """保留最多 max_pairs 组工作流日志，按组（.json + .md 成对）删除最旧的。

        文件名格式: workflow_20260708_120000.json / .md
        不完整的组（历史遗留孤儿文件）会被一并清理。
        """
        if not os.path.isdir(log_dir):
            return

        # 1. 按时间戳前缀分组
        groups: dict[str, list[str]] = {}
        for f in os.listdir(log_dir):
            if f.startswith("workflow_") and f.endswith((".json", ".md")):
                prefix = f[len("workflow_"):].rsplit(".", 1)[0]
                groups.setdefault(prefix, []).append(f)

        # 2. 删除不完整组（历史遗留孤儿文件）
        for prefix, files in list(groups.items()):
            if len(files) < 2:
                for f in files:
                    try:
                        os.remove(os.path.join(log_dir, f))
                    except OSError:
                        pass
                del groups[prefix]

        # 3. 完整组按时间戳排序，超限则删除最旧组
        sorted_prefixes = sorted(groups.keys())
        while len(sorted_prefixes) > max_pairs:
            oldest = sorted_prefixes.pop(0)
            for f in groups[oldest]:
                try:
                    os.remove(os.path.join(log_dir, f))
                except OSError:
                    pass

    @staticmethod
    def _load_factory_methods() -> str:
        """数据工厂方法清单（prompt 注入文本）。

        薄壳：实现已归位 data_factory/registry.py（单一事实源 methods.yaml v2，
        目录+分类详情渲染、缓存、旧结构兼容均在 registry 内）。
        """
        from data_factory.registry import render_for_prompt
        return render_for_prompt()

    def _invoke_structured(self, prompt, model_class: Type[BaseModel],
                           max_retries: int = config.MAX_RETRIES,
                           method: str = "function_calling",
                           thinking: bool = False,
                           **kwargs) -> BaseModel:
        """调用 LLM 并校验结构化输出，失败时自动重试。

        Args:
            prompt: ChatPromptTemplate
            model_class: Pydantic 模型类
            max_retries: 最大重试次数（默认 2）
            method: 结构化输出方法，可选 "function_calling" / "json_mode" / "json_schema"
            thinking: 是否使用深度思考模式（由 METHOD_FEATURES 判定兼容性）
            **kwargs: prompt 模板变量
        """
        # 根据 method 特性配置 thinking 开关
        features = METHOD_FEATURES.get(method)
        llm_kwargs = {}
        if features is None:
            logger.warning("未知 method '%s'，使用保守配置（禁用 thinking）", method)
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif not features["supports_thinking"]:
            if thinking:
                logger.warning("%s 不支持 thinking=True，已自动禁用 thinking", method)
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif thinking and config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        last_error = None
        # chain 在重试间不变，只需构建一次
        chain = prompt | self.llm.with_structured_output(
            model_class, method=method, **llm_kwargs
        )

        for attempt in range(1 + max_retries):
            try:
                result = chain.invoke(kwargs)
                if isinstance(result, dict):
                    result = model_class(**result)
                return result
            except (ValidationError, OutputParserException,
                    openai.BadRequestError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning("输出校验失败，第 %d 次重试: %s", attempt + 1, e, exc_info=True)

        raise RuntimeError(
            f"LLM 结构化输出校验失败（已重试 {max_retries} 次）: {last_error}"
        )
