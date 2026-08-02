"""LangGraph 各个节点方法"""
import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime
from typing import Callable, Optional, Type

import openai
from pydantic import BaseModel, ValidationError

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from langchain_core.exceptions import OutputParserException
from agent_components.llm.deepseek import DeepSeekChatOpenAI

import config
from observability import get_logger
from agent_components.dual_chroma import get_chroma_db
from agent_components.state import State
from prompts.response_model import (
    ProperResponse,
    ApiDefinition,
    TestData,
    ExcelPlan,
    ExcelRow,
    ExcelPlanV2,
    SharedPrecondition,
    TestCaseRow,
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
                    max_tokens=config.LLM_MAX_TOKENS,
                )
    return _llm_instance


class ChatTestAgentGraph(RetrievalMixin, GenerationMixin):
    """智能测试助手——LangGraph 节点方法的容器类

    Excel 计划生成 + 核心工具方法（本文件）
    Phase B 检索节点 → RetrievalMixin (retrievers.py)
    Phase C PY/YAML 生成节点 → GenerationMixin (generators.py)
    """

    def __init__(self):
        self.llm = _get_llm()

        self.prompt_factory = PromptFactory()

        self.dual_chroma = get_chroma_db()

        # 工作流日志累积器（同一次运行的所有节点共用一份文件）
        # 原初始化误放在 _finalize_excel_plan 的 return 之后（死代码），导致 _log_node_output 访问时报
        # AttributeError: _run_timestamp；2026-08-03 移入 __init__ 修复。
        self._run_data: dict = {}
        self._run_timestamp: Optional[str] = None

    # ── 共享：写 Excel + api_defs.json 快照 ──

    def _finalize_excel_plan(self, state: State, plan,
                             api_full_for_snapshot: list, module_tree: str) -> dict:
        """将 ExcelPlanV2 写入 xlsx + api_defs.json，返回 state 更新 dict。"""
        confirmed_module = state.get("confirmed_module", "")
        output_dir = os.path.join(config.TESTCASE_BASE, confirmed_module)
        os.makedirs(output_dir, exist_ok=True)
        excel_path = os.path.join(output_dir, "test_plan.xlsx")

        # 写入 Excel（双 Sheet）
        hf = Font(bold=True, color="FFFFFF", size=11)
        hfill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
        tb = Border(left=Side(style="thin"), right=Side(style="thin"),
                    top=Side(style="thin"), bottom=Side(style="thin"))
        wa = Alignment(wrap_text=True, vertical="center")
        wb = Workbook()
        # Sheet 1: 测试计划
        ws1 = wb.active
        ws1.title = "测试计划"
        for col, h in enumerate(["@allure.story", "@allure.title", "用例编号", "前置步骤", "执行步骤", "预期结果"], 1):
            c = ws1.cell(row=1, column=col, value=h)
            c.font, c.fill, c.border, c.alignment = hf, hfill, tb, Alignment(horizontal="center", vertical="center")
        for i, tc in enumerate(plan.test_cases, 2):
            for col, val in enumerate([tc.story, tc.title, tc.id,
                                        ", ".join(tc.preconditions) if tc.preconditions else "无",
                                        tc.steps, tc.expected], 1):
                c = ws1.cell(row=i, column=col, value=val)
                c.border, c.alignment = tb, wa
        # Sheet 2: 共享前置
        ws2 = wb.create_sheet("共享前置")
        for col, h in enumerate(["前置编号", "前置名称", "详细步骤", "预期结果"], 1):
            c = ws2.cell(row=1, column=col, value=h)
            c.font, c.fill, c.border, c.alignment = hf, hfill, tb, Alignment(horizontal="center", vertical="center")
        for i, pre in enumerate(plan.shared_preconditions, 2):
            for col, val in enumerate([pre.id, pre.name, pre.steps, pre.expected], 1):
                c = ws2.cell(row=i, column=col, value=val)
                c.border, c.alignment = tb, wa
        wb.save(excel_path)

        # api_defs.json 快照
        api_snapshot_path = os.path.join(output_dir, "api_defs.json")
        with open(api_snapshot_path, "w", encoding="utf-8") as f:
            json.dump(api_full_for_snapshot, f, ensure_ascii=False, indent=2)
        logger.info(f"   📄 api_defs.json → {api_snapshot_path} ({len(api_full_for_snapshot)} 个接口)")

        n_cases = len(plan.test_cases)
        n_pres = len(plan.shared_preconditions)
        n_modules = len(set(tc.story for tc in plan.test_cases))
        logger.info(f"   📄 Excel: {excel_path} ({n_cases}条/{n_modules}模块, {n_pres}共享前置)")

        return {
            "excel_plan": plan,
            "excel_path": excel_path,
            "output_dir": output_dir,
            "thinking": [f"生成 {n_cases} 条用例, {n_pres} 共享前置, {n_modules} 模块"],
        }

    # ==================== 图内节点方法 ====================

    def _generate_excel_plan_thinking(self, state: State):
        """【新】thinking+json_mode 一步生成 Excel 计划。

        合并原 analyze_test_points_raw + generate_excel_plan 两步：
        thinking 分析 → json_object 直接输出 ExcelPlanV2。
        失败时由 graph builder 降级到旧两步流程。
        """
        from observability import log_phase_header
        from prompts.response_model import ExcelPlanV2, ApiDefinition
        from agent_components.retrievers import _build_full_api_defs_text
        from database import get_session_ctx
        from database.operations import ModuleOps, BindingOps
        from database.operations.analysis import AnalysisOps
        from agent_components.api_annotations import ApiAnnotationRegistry

        log_phase_header("Phase B — thinking+json 一步生成 Excel 计划")
        logger.info("\n🧠 一步生成 Excel 测试计划（thinking + json_object）...")

        # ── 1. 准备数据（同 _analyze_test_points_raw）──
        confirmed_module = state.get("confirmed_module", "")
        analysis_text = ""
        with get_session_ctx() as session:
            mod = ModuleOps.get_by_name(session, confirmed_module)
            if mod:
                record = AnalysisOps.get_by_module_id(session, mod.id)
                if record:
                    parts = []
                    for key, label in [("scenario_analysis", "### 测试场景分析\n"),
                                       ("ui_flow_analysis", "### 页面交互逻辑\n"),
                                       ("api_analysis", "### 接口映射分析\n")]:
                        val = getattr(record, key, None)
                        if val:
                            parts.append(label + val)
                    if parts:
                        analysis_text = "\n\n".join(parts)
                    elif record.analysis_json:
                        analysis_text = record.analysis_json

            # 模块树
            tree = ModuleOps.get_tree(session)
            module_tree = json.dumps(tree, indent=2, ensure_ascii=False) if tree else "[]"

        # API 定义（供 LLM 分析：只给概要 name/method/url/description，
        # 避免全量参数/返回值撑爆 context 导致 thinking+json_object 返回空 content；
        # 详细参数分析由 module_analysis 预分析提供，与旧两步流程 _generate_excel_plan_node 一致）
        apis_text = json.dumps(
            [{"name": d.get("name", "?"), "method": d.get("method", "GET"),
              "url": d.get("url", ""), "description": d.get("description", "")}
             for d in (state.get("api_definitions") or [])],
            indent=2, ensure_ascii=False)

        # Phase C 快照用完整 API
        api_full_for_snapshot = [
            ApiDefinition(
                name=d.get("name", "?"), url=d.get("url", ""),
                method=d.get("method", "GET"), description=d.get("description", ""),
                parameters=d.get("parameters", {}), returns=d.get("returns", {}),
            ).model_dump()
            for d in (state.get("api_definitions") or [])
        ]
        for api in api_full_for_snapshot:
            ApiAnnotationRegistry.apply_all(api)

        # 关联模块
        related_text = ", ".join(state.get("related_modules", [])) or "无"
        user_ctx = state.get("original_input", "")

        # ── 2. 构造 prompt ──
        prompt = self.prompt_factory.generate_excel_plan_thinking()

        # ── 3. thinking + json_object 调用 ──
        _think_llm = self.llm.bind(
            temperature=0.4,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "enabled"}},
        )
        _raw = _think_llm.invoke(prompt.format_messages(
            json_schema=json.dumps(
                ExcelPlanV2.model_json_schema(), ensure_ascii=False, indent=2),
            module_analysis=analysis_text or "（无预分析，请根据接口定义自行分析）",
            api_definitions=apis_text,
            related_docs=related_text,
            user_context=user_ctx,
        ))
        _text = _raw.content if hasattr(_raw, "content") else str(_raw)

        # ── 4. 解析 ──
        plan = ExcelPlanV2.model_validate(json.loads(_text))
        logger.info(f"   => 一步生成完成: {len(plan.shared_preconditions)} 前置, {len(plan.test_cases)} 用例")

        # ── 5. 只生成不落盘：标注数据源，交由 generate_excel_plan 处理节点 校验/修复/落盘 ──
        return {
            "excel_plan": plan,
            "plan_source": "thinking",
            "api_full_for_snapshot": api_full_for_snapshot,
            "module_tree_json": module_tree,
        }

    def _generate_excel_plan_node(self, state: State):
        """生成 Excel 测试计划 V2（双 Sheet：测试计划 + 共享前置）。"""
        logger.info("\n📊 正在生成 Excel 测试计划...")

        from prompts.extraction_prompts import repair_excel_plan_prompt
        from agent_components.validator import validate_excel_file
        from prompts.response_model import ApiDefinition
        from agent_components.plan_validator import ExcelPlanValidator

        prompt = self.prompt_factory.generate_excel_plan_node()
        # Phase B prompt：只传接口概要（name/method/url/description），避免全量 JSON 撑爆 context
        api_summaries = [
            {"name": d.get("name", "?"), "method": d.get("method", "GET"),
             "url": d.get("url", ""), "description": d.get("description", "")}
            for d in (state.get("api_definitions") or [])
        ]
        all_apis_json = json.dumps(api_summaries, indent=2, ensure_ascii=False)
        # Phase C 快照：保留完整定义（parameters/returns 等），存为 api_defs.json
        api_full_for_snapshot = [
            ApiDefinition(
                name=d.get("name", "?"), url=d.get("url", ""),
                method=d.get("method", "GET"), description=d.get("description", ""),
                parameters=d.get("parameters", {}), returns=d.get("returns", {}),
            ).model_dump()
            for d in (state.get("api_definitions") or [])
        ]
        # 接口异常标识自动检测
        from agent_components.api_annotations import ApiAnnotationRegistry
        for api in api_full_for_snapshot:
            ApiAnnotationRegistry.apply_all(api)
        from database import get_session_ctx
        from database.operations import ModuleOps
        with get_session_ctx() as session:
            tree = ModuleOps.get_tree(session)
        module_tree_json = json.dumps(tree, indent=2, ensure_ascii=False)
        test_analysis = state.get("test_point_analysis") or "（无）"
        _sections = self._split_thinking_sections(test_analysis)
        prompt_vars = {
            "module_tree": module_tree_json,
            "analysis_section": _sections["analysis"],
            "shared_pre_section": _sections["preconditions"],
            "cases_section": _sections["cases"],
            "all_apis_info": all_apis_json,
            "user_context": state["original_input"],
        }

        # ── 数据源检测（2026-08 生成/处理解耦，方案3）：
        #    generate_excel_plan 为纯处理节点，只消费上游生成的 plan（thinking / 未来旧链路）。
        #    无外部 plan（thinking 失败）→ requires_review，不降级自生成。
        #    旧节点自生成兜底方案见 changelog/2026-08-02_old_generation_fallback.md，暂未启用。
        incoming_plan = state.get("excel_plan")
        if incoming_plan is None:
            return {
                "excel_plan": None, "excel_path": "", "output_dir": None,
                "requires_review": True,
                "error_info": ["生成环节未产出 plan（thinking 失败），且旧节点自生成兜底未启用"],
                "response_obj": ProperResponse(
                    proper_thinking=[], worth_to_remember=False,
                    final_response="测试计划生成失败：生成环节未产出计划，请重试",
                ),
            }
        # thinking 已构造的快照/模块树优先，避免重复查询与不一致
        api_full_for_snapshot = state.get("api_full_for_snapshot") or api_full_for_snapshot
        module_tree_json = state.get("module_tree_json") or module_tree_json

        output_dir = None
        plan = None
        failed_details: list[tuple[int, dict, list[str]]] = []
        all_confirmed: list = []
        all_shared_pres: list = []  # 首轮共享前置，重试时复用
        failed_ids: set = set()  # 失败行 TC ID 集合，重试时只接受这些 ID 的修复
        _gen_attempt = 1          # 全量生成次数（含质量不达标重试）
        _gen_warning = ""         # 质量不达标时的警告信息

        for attempt in range(config.EXCEL_REPAIR_ATTEMPTS):
            if attempt == 0 or _gen_warning:
                # === 首轮 / 质量不达标重试 ===
                if attempt == 0:
                    # 消费上游 thinking 生成的 plan（纯处理，不生成）
                    plan = incoming_plan
                    incoming_plan = None  # 仅首轮消费
                else:
                    # 质量不达标需重新生成 plan —— 方案3 无自生成能力，交人工审查
                    return {
                        "excel_plan": None, "excel_path": "", "output_dir": output_dir,
                        "requires_review": True,
                        "error_info": [f"生成质量未达标（第{_gen_attempt}次），且无自生成兜底，交人工审查"],
                        "response_obj": ProperResponse(
                            proper_thinking=[], worth_to_remember=False,
                            final_response="测试计划生成质量未达标，需人工审查",
                        ),
                    }
                all_shared_pres = plan.shared_preconditions

                # 首轮校验全部用例（校验收敛：ExcelPlanValidator，含 7 类错误聚合）
                _vr = ExcelPlanValidator.validate(plan, test_analysis)
                _new_failed = _vr.failed_details
                all_confirmed = _vr.all_confirmed

                # 质量门禁：首轮通过率 < 50% 时重新全量生成（非修复）
                n_total = len(plan.test_cases)
                n_pass = len(all_confirmed)
                if n_total > 0 and n_pass < n_total / 2 and _gen_attempt < 3:
                    _gen_attempt += 1
                    logger.warning(
                        f"   ⚠️ 质量门禁：首轮通过率 {n_pass}/{n_total} < 50%，"
                        f"触发第 {_gen_attempt} 次全量重新生成"
                    )
                    _gen_warning = (
                        f"⚠️ 【系统警告：生成质量未达标，触发强制重试】\n"
                        f"这是你的第 {_gen_attempt} 次生成尝试。上一轮 {n_total} 条用例中仅有 {n_pass} 条通过校验，"
                        f"通过率 {n_pass}/{n_total}，未达到 100% 的格式要求。\n"
                        "所有用例的步骤与预期必须 100% 精确对齐，不允许任何不对齐的情况。\n\n"
                        "在本次生成中，你必须：\n"
                        "1. 严格回顾并遵守上述所有格式规则，绝对不要偏离。\n"
                        "2. 仔细检查步骤（Steps）和预期（Expected）的数量，必须精确一一对应。\n"
                        "3. 参考上方提供的 ✅ 正确示例 和 ❌ 错误示例 进行自我校验。\n"
                    )
                    failed_details = []
                    continue
                if n_total > 0 and n_pass < n_total / 2 and _gen_attempt >= 3:
                    raise RuntimeError(
                        f"Excel 生成质量不达标：连续 {_gen_attempt} 次全量生成通过率 < 50%"
                        f"（本次 {n_pass}/{n_total}），已终止"
                    )

                # 记录失败行 ID（重试时只有匹配这些 ID 的修复才被接受）
                failed_ids = {f[1].get("id", "") for f in _new_failed}
                failed_details = _new_failed
                logger.warning(
                    f"   ⚠️ 校验: {n_pass} 用例通过, "
                    f"{len(failed_details)} 失败 (第{_gen_attempt}次)"
                )
                if not failed_details:
                    break
            else:
                # 重试：LLM 获得完整上下文修复，代码侧根据 failed_ids 裁剪输出
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
                # 修复节点入参统一（2026-08 D1/D2）：
                #   ① 共享数据（与生成节点一致，_prepare_plan_prompt_vars）
                #   ② 错误用例 failed_test_cases
                #   ③ 拦截原因 block_reasons（validator 聚合，同类一条）
                _shared_vars = self._prepare_plan_prompt_vars(state)
                _block_reasons_text = "\n".join(
                    ExcelPlanValidator.aggregate_block_reasons(failed_details))
                repair_prompt = repair_excel_plan_prompt()
                plan = self._invoke_structured(repair_prompt, ExcelPlanV2,
                    method="json_mode",
                    failed_test_cases=failed_tc_text,
                    block_reasons=_block_reasons_text,
                    module_tree=_shared_vars["module_tree"],
                    analysis_section=_shared_vars["analysis_section"],
                    shared_pre_section=_shared_vars["shared_pre_section"],
                    cases_section=_shared_vars["cases_section"],
                    all_apis_info=_shared_vars["all_apis_info"],
                )
                if isinstance(plan, list):
                    plan = ExcelPlanV2(shared_preconditions=[], test_cases=plan)

                # 重试校验：只接受 ID 匹配失败行的修复
                pre_ids_all = {p.id for p in all_shared_pres}
                for p in plan.shared_preconditions:
                    pre_ids_all.add(p.id)

                _new_failed = []
                fixed_ids = set()
                _already_confirmed = {tc.id for tc in all_confirmed}
                _seen_in_retry = set()  # 防止同一批次内 LLM 输出重复 TC ID
                for tc in plan.test_cases:
                    # 拒绝不在失败 ID 集合中的行（LLM 幻觉出新的用例）
                    if tc.id not in failed_ids:
                        logger.warning(f"   ⚠️ 重试返回了不在失败列表中的 TC {tc.id}，已丢弃")
                        continue
                    # 拒绝重复输出已通过校验的用例
                    if tc.id in _already_confirmed:
                        logger.warning(f"   ⚠️ 重试返回了已通过的 TC {tc.id}，已丢弃")
                        continue
                    # 拒绝同一批次内的重复（LLM 在单次输出中生成多个相同 ID）
                    if tc.id in _seen_in_retry:
                        logger.warning(f"   ⚠️ 重试批次内重复 TC {tc.id}，已丢弃")
                        continue
                    _seen_in_retry.add(tc.id)
                    # 单用例校验（校验收敛：ExcelPlanValidator.check_case）
                    errs = ExcelPlanValidator.check_case(tc, pre_ids_all)
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
                # 去重：同一 ID 在旧版和新版同时存在时保留最新版
                _seen_ids = {}
                for _item in failed_details:
                    _seen_ids[_item[1].get("id", "")] = _item
                if len(_seen_ids) < len(failed_details):
                    logger.warning(f"   ⚠️ 修复轮去重: {len(failed_details)} → {len(_seen_ids)}")
                failed_details = list(_seen_ids.values())
                logger.warning(
                    f"   ⚠️ 校验: {len(all_confirmed)} 用例通过（本次修复 {len(plan.test_cases)} 行, "
                    f"接受 {len(fixed_ids)}, 仍失败 {len(failed_details)}）, "
                    f"({attempt_label})"
                )
                if not failed_details:
                    break

        # 最终校验 + 引用完整性
        if failed_details:
            pre_ids = {p.id for p in all_shared_pres}
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

        # === 最终安全阀：valid_cases 按 ID 去重（防御多路径聚合的重复） ===
        _seen_vc = set()
        _deduped = []
        _dup_count = 0
        for tc in valid_cases:
            if tc.id in _seen_vc:
                _dup_count += 1
                continue
            _seen_vc.add(tc.id)
            _deduped.append(tc)
        if _dup_count:
            logger.warning(
                f"   ⚠️ 最终去重安全阀触发: 移除 {_dup_count} 条重复用例 "
                f"（{len(valid_cases)} → {len(_deduped)}）")
        valid_cases = _deduped

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
        # 使用包含全部 confirmed 用例的临时 plan，确保所有轮次的 TC 都经过消解
        if all_shared_pres:
            _full_plan = ExcelPlanV2(
                shared_preconditions=list(all_shared_pres),
                test_cases=list(valid_cases),
            )
            self._resolve_resource_conflicts(_full_plan, all_shared_pres)

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
        # 去重: PRE 关联用例列表按首次出现顺序去重
        for pid in pre_to_cases:
            _seen_linked = set()
            _deduped_linked = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen_linked:
                    _seen_linked.add(cid)
                    _deduped_linked.append(cid)
            pre_to_cases[pid] = _deduped_linked
        # 去重: shared_preconditions 按 ID 去重
        _seen_pre = set()
        _deduped_pres = []
        for pre in all_shared_pres:
            if pre.id not in _seen_pre:
                _seen_pre.add(pre.id)
                _deduped_pres.append(pre)
        if len(_deduped_pres) < len(all_shared_pres):
            logger.warning(f"   ⚠️ 共享前置去重: {len(all_shared_pres)} → {len(_deduped_pres)}")
        all_shared_pres = _deduped_pres
        for i, pre in enumerate(all_shared_pres, 2):
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
        # 规则 M8：接口定义靠产物传递（快照随计划走），禁止依赖内存态跨阶段交接。
        api_defs_path = os.path.join(output_dir, "api_defs.json")
        try:
            with open(api_defs_path, "w", encoding="utf-8") as f:
                json.dump(api_full_for_snapshot, f, ensure_ascii=False, indent=2)
            logger.info(f"   📄 接口定义快照已保存: {api_defs_path} ({len(api_full_for_snapshot)} 个接口)")
        except OSError:
            logger.error("接口定义快照写入失败（Phase C 确认时将按 M8 阻断）: %s",
                         api_defs_path, exc_info=True)

        fail_warn = f"（{len(failed_details)} 行未通过校验，需人工审查）" if failed_details else ""
        n_modules = len(set(tc.story for tc in valid_cases))
        n_pres = len(all_shared_pres)
        logger.info(f"   📄 Excel 已保存: {excel_path} ({n_confirmed}条/{n_modules}模块, {n_pres}共享前置){fail_warn}")

        self._log_node_output("generate_excel_plan",
                              {"excel_plan": {
                                  "shared_preconditions": [p.model_dump() for p in all_shared_pres],
                                  "test_cases": [tc.model_dump() for tc in valid_cases],
                              }, "excel_path": excel_path, "output_dir": output_dir})
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
                proper_thinking=[f"已提取 {len(api_full_for_snapshot)} 个接口，分析 {n_confirmed} 条用例"],
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

    def _resolve_resource_conflicts(self, plan: ExcelPlanV2,
                                     shared_pres: list = None) -> None:
        """资源冲突消解：检测同一 PRE 被多个正向写操作用例引用时，克隆隔离。

        纯代码节点，不调用 LLM。嵌入在 _generate_excel_plan_node 内部，
        shared_pres 为初始轮保存的共享前置列表（修复轮 plan 可能为空）。
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
            _pre_list = shared_pres if shared_pres else plan.shared_preconditions
            original = next((p for p in _pre_list if p.id == pre_id), None)
            if original is None:
                logger.warning("消解器: PRE %s 被 %d 个用例引用但未在 shared_preconditions 中找到，跳过",
                               pre_id, len(ref_list))
                continue
            # 第一个用例保持引用原始 PRE，其余克隆隔离
            for tc in ref_list[1:]:
                clone_id = f"{pre_id}_isolated_{tc.id}"
                _pre_list.append(SharedPrecondition(
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
    def _split_thinking_sections(text: str) -> dict:
        """将 thinking 分析输出按三个段落拆分为独立输入。

        宽松匹配：包含关键词即视为段落起始，不要求精确标题。
        LLM 输出标题略有偏差（如「测试分析」vs「测试场景分析」）时仍能正确解析。
        """
        result = {"analysis": "（无）", "preconditions": "（无）", "cases": "（无）"}
        if not text:
            return result

        patterns = [
            ("analysis",      re.compile(r'测试场景分析|场景分析|测试分析', re.IGNORECASE)),
            ("preconditions", re.compile(r'共享前置|前置条件|前置准备', re.IGNORECASE)),
            ("cases",         re.compile(r'测试用例|用例设计|用例列表', re.IGNORECASE)),
        ]

        positions = []
        for key, pat in patterns:
            m = pat.search(text)
            if m:
                positions.append((m.start(), key))
        if not positions:
            return result
        positions.sort()

        for i, (pos, key) in enumerate(positions):
            next_pos = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            result[key] = text[pos:next_pos].strip()

        return result

    def _prepare_plan_prompt_vars(self, state: State) -> dict:
        """生成/修复共用的 prompt 变量构造（单一数据源）。2026-08 新增。

        供生成节点与修复节点统一调用，保证 API 信息（概要）、模块树、分析段落
        来自同一份来源；入参含 plan_source 数据源标注。
        接线到具体节点待「待删除清单」确认后执行（当前仅新增，不改现有行为）。
        """
        module_tree = state.get("module_tree_json") or "[]"
        test_analysis = state.get("test_point_analysis") or "（无）"
        _sections = self._split_thinking_sections(test_analysis)
        api_summaries = [
            {"name": d.get("name", "?"), "method": d.get("method", "GET"),
             "url": d.get("url", ""), "description": d.get("description", "")}
            for d in (state.get("api_definitions") or [])
        ]
        return {
            "module_tree": module_tree,
            "analysis_section": _sections["analysis"],
            "shared_pre_section": _sections["preconditions"],
            "cases_section": _sections["cases"],
            "all_apis_info": json.dumps(api_summaries, indent=2, ensure_ascii=False),
            "plan_source": state.get("plan_source"),
            "user_context": state.get("original_input", ""),
        }

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
        node_order = ["generate_excel_plan",
                       "analyze_test_points_raw", "format_test_points",
                       "generate_py_file", "generate_all_yamls"]
        for nname in node_order:
            if nname not in self._run_data:
                continue
            data = self._run_data[nname]
            md_lines.append(f"## {nname}")

            if nname == "generate_excel_plan":
                plan = data.get("excel_plan", {})
                # 兼容 ExcelPlanV2 (test_cases) 和 ExcelPlan (rows) 两种模型
                rows = (plan.get("test_cases", []) or plan.get("rows", [])
                        if isinstance(plan, dict) else [])
                modules = len(set(r.get("story", r.get("module_name", "")) for r in rows)) if rows else 0
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
                           temperature: float | None = None,
                           log_label: str = "",
                           pre_validate: Callable[[dict], dict] | None = None,
                           **kwargs) -> BaseModel:
        """调用 LLM 并校验结构化输出，失败时自动重试。

        Args:
            prompt: ChatPromptTemplate
            model_class: Pydantic 模型类
            max_retries: 最大重试次数（默认 2）
            method: 结构化输出方法，可选 "function_calling" / "json_mode" / "json_schema"
            thinking: 是否使用深度思考模式（由 METHOD_FEATURES 判定兼容性）
            temperature: 温度参数，None 使用全局默认值
            log_label: 不为空时将原始输出写入 thinking_trace.log
            pre_validate: json_mode 下在 Pydantic 构造前对 dict 执行的回调（用于注入 _annotations 等元数据）
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
        # max_tokens 在 model_kwargs 中，仅温度走 bind
        _bind_kwargs: dict = {}
        if temperature is not None:
            _bind_kwargs["temperature"] = temperature
        _llm = self.llm.bind(**_bind_kwargs) if _bind_kwargs else self.llm
        # chain 在重试间不变，只需构建一次
        chain = prompt | _llm.with_structured_output(
            model_class, method=method, **llm_kwargs
        )

        for attempt in range(1 + max_retries):
            try:
                result = chain.invoke(kwargs)
                if result is None:
                    raise ValueError("LLM 返回了空结果（None）")
                # ── pre_validate 钩子：json_mode 下在 Pydantic 构造前修改 dict ──
                if pre_validate and isinstance(result, dict):
                    result = pre_validate(result)
                if log_label:
                    from observability import log_thinking
                    _raw = result.model_dump() if hasattr(result, "model_dump") else str(result)
                    log_thinking(log_label, "", f"shared_preconditions={len(_raw.get('shared_preconditions',[]))}条, test_cases={len(_raw.get('test_cases',[]))}条\n{json.dumps(_raw, indent=2, ensure_ascii=False)[:8000]}",
                                 prompt_label=log_label)
                if isinstance(result, dict):
                    result = model_class(**result)
                return result
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning("输出校验失败，第 %d 次重试 (%s): %s",
                                   attempt + 1, type(e).__name__, e, exc_info=True)

        raise RuntimeError(
            f"LLM 结构化输出校验失败（本调用内重试 {max_retries} 次，外层修复轮独立计数）: {last_error}"
        )
