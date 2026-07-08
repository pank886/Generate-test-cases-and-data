"""LangGraph 各个节点方法"""
import json
import os
import re
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
from agent_components.chromadb_file import ReadersChromadb
from agent_components.dual_chroma import get_chroma_db
from agent_components.state import State, ApiDefinitionList
from prompts.response_model import (
    ProperResponse,
    ApiDefinition,
    TestData,
    ExcelPlan,
    ExcelRow,
    PyFile,
    ClassCode,
    TestPointList,
    IntentConfirmation,
)
from prompts.definitions import PromptFactory
from agent_components.retrievers import RetrievalMixin
from agent_components.generators import GenerationMixin

logger = get_logger(__name__)


class ChatTestAgentGraph(RetrievalMixin, GenerationMixin):
    """智能测试助手——LangGraph 节点方法的容器类

    Phase A 节点 + 核心工具方法（本文件）
    Phase C 检索节点 → RetrievalMixin (retrievers.py)
    PY/YAML 生成节点 → GenerationMixin (generators.py)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.llm = DeepSeekChatOpenAI(
            model=config.LLM_MODEL,
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            temperature=config.LLM_TEMPERATURE,
        )

        self.prompt_factory = PromptFactory()

        self.vector_store = None
        if db_path:
            self.vector_store = ReadersChromadb(persist_directory=db_path)

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
        if not self.vector_store:
            context = "未检索到知识库"
        else:
            # 使用较大的 k 值以覆盖所有接口（每个块 ≈ 一个接口）
            context = self.vector_store.search_context(
                user_question_str=state["user_input"],
                k=config.RETRIEVAL_K,
            )
        self._log_node_output("retrieve", {"context": context})
        return {"context": context}

    def _parse_api_node(self, state: State):
        """分析接口定义"""
        logger.info("\n正在分析文档，提取接口定义...")

        prompt = self.prompt_factory.parse_api_node()
        chain = prompt | self.llm.with_structured_output(
            ApiDefinitionList, method="json_mode"
        )

        result = chain.invoke({
            "content": state["context"],
            "user_context": state["original_input"],
        })

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

    def _generate_excel_plan_node(self, state: State):
        """生成 Excel 测试计划（含自动校验修复循环）"""
        logger.info("\n📊 正在生成 Excel 测试计划...")

        from prompts.extraction_prompts import repair_excel_plan_prompt
        from agent_components.validator import validate_excel_file

        prompt = self.prompt_factory.generate_excel_plan_node()
        all_apis_dict = [api.model_dump() for api in state["api_definition_list"]]
        all_apis_json = json.dumps(all_apis_dict, indent=2, ensure_ascii=False)
        prompt_vars = {"all_apis_info": all_apis_json, "user_context": state["original_input"]}

        bad_output_text = ""
        repair_errors = []
        output_dir = None
        for attempt in range(config.EXCEL_REPAIR_ATTEMPTS):
            if attempt == 0:
                plan = self._invoke_structured(prompt, ExcelPlan,
                    method="json_mode", thinking=True, **prompt_vars)
            else:
                plan = self._invoke_structured(repair_excel_plan_prompt(), ExcelPlan,
                    method="json_mode", thinking=True,
                    original_system=str(prompt), user_vars=str(prompt_vars),
                    bad_output=bad_output_text,
                    repair_errors="\n".join(repair_errors),
                )

            if isinstance(plan, list):
                plan = ExcelPlan(rows=plan)

            pydantic_errors = self._validate_excel_plan(plan)
            if pydantic_errors:
                repair_errors = pydantic_errors
                bad_output_text = str(plan.model_dump())
                logger.warning(f"   ⚠️ 校验失败 (第{attempt+1}次): {len(pydantic_errors)} 个错误")
                continue

            # 成功 → 写 Excel
            project_name = plan.rows[0].project_name if plan.rows else "Unknown"
            output_dir = state.get("output_dir")
            if not output_dir:
                output_dir = os.path.join(config.TESTCASE_BASE, project_name)
                if os.path.exists(output_dir):
                    from datetime import datetime
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    candidate = f"{project_name}_{ts}"
                    output_dir = os.path.join(config.TESTCASE_BASE, candidate)
                    project_name = candidate
            os.makedirs(output_dir, exist_ok=True)
            excel_path = os.path.join(output_dir, plan.file_name)

            wb = Workbook()
            ws = wb.active
            ws.title = "测试计划"
            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill(start_color="1A73E8", end_color="1A73E8", fill_type="solid")
            thin_border = Border(left=Side(style="thin"), right=Side(style="thin"),
                                 top=Side(style="thin"), bottom=Side(style="thin"))
            wrap_align = Alignment(wrap_text=True, vertical="center")
            headers = ["项目名称", "Allure Epic", "模块名称", "Allure Feature",
                       "Allure Story", "fixture等级", "用例名称", "执行步骤", "测试数据YAML", "是否启用"]
            for col, h in enumerate(headers, 1):
                c = ws.cell(row=1, column=col, value=h)
                c.font, c.fill, c.border, c.alignment = header_font, header_fill, thin_border, Alignment(horizontal="center", vertical="center")
            for i, row in enumerate(plan.rows, 2):
                for col, val in enumerate([row.project_name, row.allure_epic, row.module_name,
                    row.allure_feature, row.allure_story, row.fixture_level,
                    row.case_name, "; ".join(row.steps), row.test_data_yaml, row.enabled], 1):
                    c = ws.cell(row=i, column=col, value=val)
                    c.border, c.alignment = thin_border, wrap_align
            # 根据内容自动计算列宽（取表头和数据中较长者，封顶 55 避免单列过宽）
            col_values: list[list[str]] = [[] for _ in headers]
            for row in plan.rows:
                vals = [row.project_name, row.allure_epic, row.module_name,
                        row.allure_feature, row.allure_story, row.fixture_level,
                        row.case_name, "; ".join(row.steps), row.test_data_yaml, row.enabled]
                for ci, v in enumerate(vals):
                    col_values[ci].append(str(v) if v else "")
            for ci, h in enumerate(headers):
                max_data = max((len(v) for v in col_values[ci]), default=0)
                width = max(len(h) + 2, min(max_data + 2, 55))
                ws.column_dimensions[get_column_letter(ci + 1)].width = width
            wb.save(excel_path)
            logger.info(f"   📄 Excel 已保存: {excel_path} ({len(plan.rows)}条/{len(set(r.module_name for r in plan.rows))}模块)")

            # 文件层校验
            file_ok, file_errors = validate_excel_file(excel_path)
            if file_ok:
                self._log_node_output("generate_excel_plan", {"excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir})
                return {"excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir}
            else:
                repair_errors = file_errors
                bad_output_text = str(plan.model_dump())
                logger.warning(f"   ⚠️ 文件校验失败 (第{attempt+1}次): {len(file_errors)} 个错误")
                continue

        # 所有重试耗尽
        logger.error(f"   ❌ 校验失败（已重试 {config.EXCEL_REPAIR_ATTEMPTS} 次），标记需人工审查")

        # 构建 fallback 目录
        fallback_dir = os.path.join(config.TESTCASE_BASE, "manual_review")
        os.makedirs(fallback_dir, exist_ok=True)

        # 写入错误快照（RotatingFileHandler 自动轮转，5MB/10个归档）
        error_logger = get_error_snapshot_logger()
        error_logger.error(
            f"=== LLM 结构化输出修复失败 ===\n"
            f"原始输入: {state.get('original_input', 'unknown')}\n"
            f"重试次数: {config.EXCEL_REPAIR_ATTEMPTS}\n"
            f"Pydantic/文件校验报错:\n{chr(10).join(repair_errors) if repair_errors else '无'}\n"
            f"--- LLM 最后一次原始返回 ---\n"
            f"{bad_output_text or '无返回内容'}\n"
            f"=== 报告结束 ===\n"
        )
        logger.info(f"   📝 错误快照已保存至: {config.LOG_DIR}/repair_failures.log")

        return {
            "requires_review": True,
            "error_info": repair_errors,
            "output_dir": fallback_dir,
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
        node_order = ["retrieve", "parse_api", "generate_excel_plan", "generate_py_file", "generate_all_yamls"]
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
        """从 data_factory/methods.yaml 读取数据工厂方法列表，格式化为提示文本"""
        factory_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_factory", "methods.yaml")
        if not os.path.exists(factory_path):
            return "（无可用数据工厂方法）"

        import yaml as _yaml
        with open(factory_path, "r", encoding="utf-8") as f:
            raw = _yaml.safe_load(f)

        methods = raw.get("methods", []) if isinstance(raw, dict) else []
        if not methods:
            return "（无可用数据工厂方法）"

        lines = []
        for m in methods:
            name = m.get("name", "?")
            syntax = m.get("syntax", f"${{{name}(...)}}")
            desc = m.get("description", "")
            lines.append(f"   - `{syntax}`：{desc}")
            for tip in m.get("usage_tips", []):
                lines.append(f"     - {tip}")
        return "\n".join(lines)

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
            thinking: 是否使用深度思考模式（仅 method="json_mode" 时生效）
            **kwargs: prompt 模板变量
        """
        # 显式控制 thinking 开关
        # DeepSeek V4 默认开启 thinking，function_calling 必须显式禁用
        # 参考: https://api-docs.deepseek.com/zh-cn/guides/json_mode
        llm_kwargs = {}
        if method == "function_calling":
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif thinking and config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        last_error = None

        for attempt in range(1 + max_retries):
            chain = prompt | self.llm.with_structured_output(
                model_class, method=method, **llm_kwargs
            )

            try:
                result = chain.invoke(kwargs)
                if isinstance(result, dict):
                    result = model_class(**result)
                return result
            except (ValidationError, ValueError, TypeError, OutputParserException,
                    openai.BadRequestError) as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(f"   ⚠️ 输出校验失败，第 {attempt + 1} 次重试... ({e})")

        raise RuntimeError(
            f"LLM 结构化输出校验失败（已重试 {max_retries} 次）: {last_error}"
        )
