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
from langchain_core.exceptions import OutputParserException
from agent_components.llm.deepseek import DeepSeekChatOpenAI

import config
from agent_components.chromadb_file import ReadersChromadb
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
)
from prompts.definitions import PromptFactory


class ChatTestAgentGraph:
    """智能测试助手——LangGraph 节点方法的容器类"""

    def __init__(self, db_path: Optional[str] = None):
        self.llm = DeepSeekChatOpenAI(
            model=config.LLM_MODEL,
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            temperature=config.LLM_TEMPERATURE,
            tiktoken_model_name="gpt-3.5-turbo",
        )

        self.prompt_factory = PromptFactory()

        self.vector_store = None
        if db_path:
            self.vector_store = ReadersChromadb(persist_directory=db_path)

        # 工作流日志累积器（同一次运行的所有节点共用一份文件）
        self._run_data: dict = {}
        self._run_timestamp: Optional[str] = None


    # ==================== 图内节点方法 ====================

    def _retrieve_node(self, state: State):
        """检索知识库"""
        # 新运行开始，重置日志累积器
        self._run_data = {}
        self._run_timestamp = None

        print("🔍 [节点] 正在调用外部工具检索...")
        if not self.vector_store:
            context = "未检索到知识库"
        else:
            # 使用较大的 k 值以覆盖所有接口（每个块 ≈ 一个接口）
            context = self.vector_store.search_context(
                user_question_str=state["user_input"],
                k=50,
            )
        self._log_node_output("retrieve", {"context": context})
        return {"context": context}

    def _parse_api_node(self, state: State):
        """分析接口定义"""
        print("\n正在分析文档，提取接口定义...")

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
            print(f"   🛠️ 成功提取到 {len(api_list)} 个接口:")
            for api in api_list:
                print(f"      - {api.name}: {api.url}")
        else:
            print(f"   ⚠️ 提取结果异常: {result}")
            api_list = []

        self._log_node_output("parse_api", {"api_definition_list": api_list})
        return {"api_definition_list": api_list}

    def _generate_excel_plan_node(self, state: State):
        """生成 Excel 测试计划（含自动校验修复循环）"""
        print("\n📊 正在生成 Excel 测试计划...")

        from prompts.extraction_prompts import repair_excel_plan_prompt
        from agent_components.validator import validate_excel_file

        prompt = self.prompt_factory.generate_excel_plan_node()
        all_apis_dict = [api.model_dump() for api in state["api_definition_list"]]
        all_apis_json = json.dumps(all_apis_dict, indent=2, ensure_ascii=False)
        prompt_vars = {"all_apis_info": all_apis_json, "user_context": state["original_input"]}

        bad_output_text = ""
        for attempt in range(3):
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
                print(f"   ⚠️ 校验失败 (第{attempt+1}次): {len(pydantic_errors)} 个错误")
                continue

            # 成功 → 写 Excel
            project_name = plan.rows[0].project_name if plan.rows else "Unknown"
            output_dir = state.get("output_dir")
            if not output_dir:
                output_dir = os.path.join(config.TESTCASE_BASE, project_name)
                if os.path.exists(output_dir):
                    base_dir = config.TESTCASE_BASE
                    existing_dirs = set()
                    if os.path.isdir(base_dir):
                        existing_dirs = {d.name for d in os.scandir(base_dir) if d.is_dir()}
                    for i in range(1, 100):
                        candidate = f"{project_name}_{i:03d}"
                        if candidate not in existing_dirs:
                            output_dir = os.path.join(config.TESTCASE_BASE, candidate)
                            project_name = candidate
                            break
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
            col_widths = [16, 14, 24, 14, 30, 14, 16, 30, 22, 10]
            for i, w in enumerate(col_widths, 1):
                ws.column_dimensions[chr(64 + i)].width = w
            wb.save(excel_path)
            print(f"   📄 Excel 已保存: {excel_path} ({len(plan.rows)}条/{len(set(r.module_name for r in plan.rows))}模块)")

            # 文件层校验
            file_ok, file_errors = validate_excel_file(excel_path)
            if file_ok:
                self._log_node_output("generate_excel_plan", {"excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir})
                return {"excel_plan": plan, "excel_path": excel_path, "output_dir": output_dir}
            else:
                repair_errors = file_errors
                bad_output_text = str(plan.model_dump())
                print(f"   ⚠️ 文件校验失败 (第{attempt+1}次): {len(file_errors)} 个错误")
                continue

        # 所有重试耗尽
        print(f"   ❌ 校验失败（已重试 3 次），标记需人工审查")
        return {"requires_review": True, "error_info": repair_errors, "output_dir": output_dir}

    # ==================== 图外方法（确认后执行） ====================

    # ==================== Phase C 多跳检索 + 测试点分析 ====================

    # ---- Mock 数据（Phase C 硬编码，A 阶段替换为真实检索） ----
    _MOCK_PRODUCT_DOCS = {
        "合同管理": [
            {"module": "合同管理", "content": "合同管理模块是整个系统的核心模块，负责合同的创建、审批、签署、归档全生命周期管理。主要功能包括合同起草、合同审批流程、电子签章、合同变更、合同终止。", "related_modules": ["房产模块", "商户模块"]},
            {"module": "合同管理", "content": "合同签约场景：用户选择已录入的房产和商户信息，填写合同条款（租金、周期、付款方式），上传附件，提交审批。审批通过后进入电子签章环节。", "related_modules": ["房产模块", "商户模块"]},
            {"module": "合同管理", "content": "合同变更是对已生效合同进行条款修改，需重新走审批流程。变更记录需完整留痕，支持版本追溯。", "related_modules": []},
        ],
        "房产模块": [
            {"module": "房产模块", "content": "房产模块负责管理所有物业资产信息，包括房产基本信息（地址、面积、户型）、产权信息、房产状态（空置/出租/维修）。合同签约时需要选择房产作为标的物。", "related_modules": ["合同管理"]},
        ],
        "商户模块": [
            {"module": "商户模块", "content": "商户模块管理所有合作商户信息，包括商户基本信息（名称、法人、联系方式）、资质文件、信用评级。合同签约时需要选择商户作为签约方。", "related_modules": ["合同管理"]},
        ],
    }

    _MOCK_API_DEFS = {
        "合同管理": [
            {"name": "合同创建", "url": "/api/contract/create", "method": "POST", "params": {"house_id": "string", "merchant_id": "string", "terms": "object"}, "returns": {"contract_id": "string", "status": "string"}},
            {"name": "合同审批", "url": "/api/contract/approve", "method": "POST", "params": {"contract_id": "string", "action": "string"}, "returns": {"success": "boolean", "code": "integer"}},
            {"name": "合同查询", "url": "/api/contract/list", "method": "GET", "params": {"page": "integer", "size": "integer", "status": "string"}, "returns": {"total": "integer", "list": "array"}},
        ],
        "房产模块": [
            {"name": "房产信息查询", "url": "/api/house/info", "method": "GET", "params": {"house_id": "string"}, "returns": {"house_id": "string", "address": "string", "area": "number", "status": "string"}},
        ],
        "商户模块": [
            {"name": "商户信息查询", "url": "/api/merchant/info", "method": "GET", "params": {"merchant_id": "string"}, "returns": {"merchant_id": "string", "name": "string", "credit_rating": "string"}},
        ],
        "公共基础服务": [
            {"name": "人员查询", "url": "/api/user/search", "method": "GET", "params": {"keyword": "string"}, "returns": {"user_list": "array"}},
            {"name": "文件上传", "url": "/api/file/upload", "method": "POST", "params": {"file": "binary"}, "returns": {"file_id": "string"}},
        ],
    }

    def _retrieve_product_docs(self, state: State):
        """Hop 1: 根据用户输入检索产品文档。"""
        print("\n--- [Hop 1] 检索产品文档 ---")
        query = state["user_input"]

        # Phase C: 硬编码匹配模块名
        matched_module = None
        for mod_name in self._MOCK_PRODUCT_DOCS:
            if mod_name in query:
                matched_module = mod_name
                break
        if not matched_module:
            matched_module = "合同管理"

        docs = self._MOCK_PRODUCT_DOCS.get(matched_module, [])
        print(f"   => 找到模块 [{matched_module}], {len(docs)} 条文档片段")
        return {"product_docs": docs, "context": "\n".join(d["content"] for d in docs)}

    def _extract_related_modules(self, state: State):
        """从产品文档 metadata 中提取关联模块。"""
        print("\n--- 提取关联模块 ---")
        related = set()
        for doc in state.get("product_docs", []):
            for m in doc.get("related_modules", []):
                related.add(m)
        mods = sorted(related)
        print(f"   => 关联模块: {mods if mods else '无'}")
        return {"related_modules": mods}

    def _retrieve_related_data(self, state: State):
        """Hop 2a+2b: 并发检索关联模块的文档和接口定义。"""
        print("\n--- [Hop 2] 检索关联数据 ---")
        modules = state.get("related_modules", [])
        all_docs = list(state.get("product_docs", []))

        # Hop 2a: 检索关联模块的产品文档
        for mod in modules:
            extra = self._MOCK_PRODUCT_DOCS.get(mod, [])
            for d in extra:
                if d not in all_docs:
                    all_docs.append(d)
                    print(f"   + 追加文档: {mod}")

        # Hop 2b: 检索主模块 + 关联模块 + 公共基础服务的接口定义
        api_defs = []
        main_module = None
        for mod_name in self._MOCK_PRODUCT_DOCS:
            if any(mod_name in d.get("module", "") for d in state.get("product_docs", [])):
                main_module = mod_name
                break
        search_modules = list(dict.fromkeys([m for m in [main_module] + modules if m]))
        search_modules.append("公共基础服务")
        for mod in search_modules:
            apis = self._MOCK_API_DEFS.get(mod, [])
            if apis:
                api_defs.extend(apis)
                print(f"   + 接口: {mod} ({len(apis)} 个)")

        print(f"   => 汇总: {len(all_docs)} 文档片段, {len(api_defs)} 个接口")
        return {"product_docs": all_docs, "api_definitions": api_defs}

    def _analyze_test_points(self, state: State):
        """根据产品文档 + 接口定义分析测试点（thinking 模式）。"""
        print("\n--- 分析测试点（深度思考）---")
        prompt = self.prompt_factory.analyze_test_points()

        docs_text = "\n\n".join(
            f"[{d.get('module', '?')}] {d['content']}"
            for d in state.get("product_docs", [])
        )
        related_text = ", ".join(state.get("related_modules", [])) or "无"
        apis_text = "\n".join(
            f"  - {a['name']} ({a['method']} {a['url']})"
            for a in state.get("api_definitions", [])
        )

        result = self._invoke_structured(prompt, TestPointList,
            method="json_mode",
            thinking=True,
            user_context=state["original_input"],
            product_docs=docs_text,
            related_docs=related_text,
            api_definitions=apis_text,
        )

        if isinstance(result, list):
            result = TestPointList(test_points=result, project_name="Unknown", summary="")

        count = len(result.test_points)
        print(f"   => 完成: {count} 个测试点")
        if result.risk_areas:
            areas_str = "; ".join(
                f"{r.area}({r.reason})" if hasattr(r, 'reason') else str(r)
                for r in result.risk_areas
            )
            print(f"   => 风险区域: {areas_str}")

        return {"test_points": result.model_dump()}

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
        print(f"   => 桥接: {len(api_definition_list)} 个接口 -> api_definition_list")
        return {"api_definition_list": api_definition_list}

    def _generate_data_plan(self, case_steps: str, api_defs_json: str,
                            user_ctx: str) -> dict:
        """场景级数据规划（thinking 模式）：分析数据依赖、提取规则、断言策略。"""
        from prompts.extraction_prompts import generate_data_plan_prompt
        from prompts.response_model import DataPlan

        print("\n--- 场景数据规划（深度思考）---")
        prompt = generate_data_plan_prompt()
        result = self._invoke_structured(prompt, DataPlan,
            method="json_mode",
            thinking=True,
            api_definitions=api_defs_json,
            test_case_steps=case_steps,
            user_context=user_ctx,
        )
        if isinstance(result, list):
            result = DataPlan(steps=result, scenario_name="")
        print(f"   => 规划完成: {len(result.steps)} 步")
        return {"data_plan": result.model_dump()}

    @staticmethod
    def _yaml_to_case_name(yaml_name: str) -> str:
        """
        将 YAML 文件名转为测试方法名: carIn_005.yaml → test_CarIn
        TODO: 后续 Class 分组合理性检查环节可能用到
        """
        stem = os.path.splitext(yaml_name)[0]          # carIn_005
        # 去掉末尾的场景序号 _NNN 或 _YYYYMMDD_NNN
        stem = re.sub(r'_\d{8}_\d+$', '', stem)         # carIn_20260620_008 → carIn
        stem = re.sub(r'_\d+$', '', stem)                # carIn_005 → carIn
        # 首字母大写 + test_ 前缀
        return "test_" + stem[0].upper() + stem[1:] if stem else "test_Step"

    def _generate_py_file(self, excel_path: str, project_name: str = None) -> dict:
        """逐模块生成 .py 测试文件（外层循环 I/O，内层 LLM 单 class 生成）"""
        print("\n🐍 正在生成 Python 测试文件...")

        if not excel_path:
            print("   ⚠️ 无 Excel 路径，跳过 .py 生成")
            return {"py_path": "", "py_file_name": "", "modules": 0, "cases": 0}

        from openpyxl import load_workbook
        from collections import defaultdict
        wb = load_workbook(excel_path)
        ws = wb.active

        expanded_rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue

            # 10 列: 项目名称,Allure Epic,模块名称,Allure Feature,Allure Story,fixture等级,用例名称,执行步骤,测试数据YAML,是否启用
            expanded_rows.append({
                "project_name": row[0], "allure_epic": row[1],
                "module_name": row[2], "allure_feature": row[3],
                "allure_story": row[4], "fixture_level": row[5],
                "case_name": row[6], "steps": row[7],
                "test_data_yaml": row[8], "enabled": row[9],
            })

        if not expanded_rows:
            raise ValueError("Excel 中无数据")

        actual_project = project_name or expanded_rows[0]["project_name"]
        allure_epic = expanded_rows[0]["allure_epic"]

        modules = defaultdict(list)
        for r in expanded_rows:
            modules[r["module_name"]].append(r)

        import_header = (
            "import pytest\n"
            "import allure\n"
            "from common.readyaml import get_testcase_yaml\n"
            "from common.sendrequests import SendRequests\n"
            "from common.recordlog import logs\n"
            "from base.apiutil import RequestsBase\n"
        )
        epic_line = f'\n@allure.epic("{allure_epic}")\n'
        class_codes = []

        total_cases = 0
        prompt = self.prompt_factory.generate_py_class_node()
        mod_names = sorted(modules.keys())

        for idx, mod_name in enumerate(mod_names):
            cases = modules[mod_name]
            total_cases += len(cases)

            # 从 module_name 推导 YAML 子目录: TestParkingBase → ParkingBasetest
            module_subdir = mod_name[4:] + "test" if mod_name.startswith("Test") else mod_name + "test"

            mod_lines = [f"模块: {mod_name}  (feature: {cases[0]['allure_feature']}, story: {cases[0]['allure_story']}, fixture: {cases[0]['fixture_level']}, {len(cases)} 条用例, 子目录: {module_subdir})\n"]
            for i, c in enumerate(cases, 1):
                status = "启用" if c["enabled"] == "Y" else "禁用"
                mod_lines.append(
                    f"  order={i} | {c['case_name']} → {c['test_data_yaml']} [{status}]"
                )
                mod_lines.append(f"    步骤: {c['steps']}")
            module_text = "\n".join(mod_lines)

            print(f"   [{idx + 1}/{len(mod_names)}] 生成 class: {mod_name} ...")

            result = self._invoke_structured(prompt, ClassCode,
                method="json_mode",
                module_data=module_text,
                project_name=actual_project,
                module_subdir=module_subdir,
            )
            class_codes.append(result.class_code)

        # 每个 class 前面加 @allure.epic（Allure 装饰器只作用于紧随其后的 class）
        class_blocks = []
        for code in class_codes:
            class_blocks.append(f"{epic_line.strip()}\n{code}")
        full_content = import_header + "\n\n" + "\n\n".join(class_blocks)
        file_name = f"test_{actual_project}.py"
        output_dir = os.path.dirname(excel_path)
        os.makedirs(output_dir, exist_ok=True)
        py_path = os.path.join(output_dir, file_name)
        with open(py_path, "w", encoding="utf-8") as f:
            f.write(full_content)

        print(f"   📄 Python 文件已保存: {py_path}")
        print(f"   📦 {len(mod_names)} 个模块, {total_cases} 条用例")

        result = {
            "py_path": py_path,
            "py_file_name": file_name,
            "modules": len(mod_names),
            "cases": total_cases,
        }
        self._log_node_output("generate_py_file", result)
        return result

    def _generate_one_yaml(self, row: dict, api_defs_json: str, user_ctx: str, output_path: str) -> str:
        """生成单个 YAML 文件写入指定路径（路径由外层循环决定）"""
        prompt = self.prompt_factory.generate_data_node()
        schema = self.prompt_factory.get_data_schema()
        test_case_logic = f"执行步骤: {row['steps']}"

        # 从 data_factory/methods.yaml 读取出可用工厂方法
        factory_methods_text = self._load_factory_methods()

        result = self._invoke_structured(prompt, TestData,
            method="json_mode",
            json_schema=schema,
            all_apis_info=api_defs_json,
            user_context=user_ctx,
            test_case_logic=test_case_logic,
            data_factory_methods=factory_methods_text,
        )

        yaml_text = yaml.dump(result.data, allow_unicode=True, indent=2, default_flow_style=False)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        return output_path

    def _generate_all_yamls(self, excel_path: str, api_defs_json: str, user_ctx: str) -> dict:
        """读 Excel → 按场景分目录 → 多线程逐条生成 YAML（供 /confirm-plan 调用）"""
        print("\n🔢 正在生成 YAML 测试数据...")

        if not excel_path:
            print("   ⚠️ 无 Excel 路径，跳过 YAML 生成")
            return {"total": 0, "success": 0, "failed": 0}

        from openpyxl import load_workbook
        wb = load_workbook(excel_path)
        ws = wb.active

        output_base = os.path.dirname(excel_path)
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None or row[9] != "Y":
                continue

            # 10 列: 项目名称,Allure Epic,模块名称,Allure Feature,Allure Story,fixture等级,用例名称,执行步骤,测试数据YAML,是否启用
            module_name = row[2]
            module_subdir = module_name[4:] + "test" if module_name.startswith("Test") else module_name + "test"

            yaml_name = row[8].strip()
            if not yaml_name:
                continue
            output_path = os.path.join(output_base, module_subdir, yaml_name)

            # 外层处理文件去重
            if os.path.exists(output_path):
                base, ext = os.path.splitext(yaml_name)
                for i in range(1, 100):
                    alt_path = os.path.join(output_base, module_subdir, f"{base}_{i:02d}{ext}")
                    if not os.path.exists(alt_path):
                        output_path = alt_path
                        break

            rows.append({
                "project_name": row[0],
                "module_name": module_name,
                "case_name": row[6],
                "steps": row[7],
                "test_data_yaml": os.path.basename(output_path),
                "output_path": output_path,
            })

        if not rows:
            print("   ⚠️ 没有启用的用例需要生成 YAML")
            result = {"total": 0, "success": 0, "failed": 0}
            self._log_node_output("generate_all_yamls", result)
            return result

        total = len(rows)
        print(f"   📋 共需生成 {total} 个 YAML 文件（按 module 分目录），并发 5 个线程")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        success = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {
                executor.submit(
                    self._generate_one_yaml, row, api_defs_json, user_ctx, row["output_path"]
                ): row
                for row in rows
            }
            for future in as_completed(future_map):
                row = future_map[future]
                try:
                    future.result()
                    success += 1
                    done = success + failed
                    print(f"      [{done}/{total}] ✅ {row['test_data_yaml']}  ({row['module_name']})")
                except Exception as e:
                    failed += 1
                    done = success + failed
                    print(f"      [{done}/{total}] ❌ {row['test_data_yaml']}: {e}")

        print(f"   ✅ 完成: {success}/{total}，失败 {failed}")
        result = {"total": total, "success": success, "failed": failed}
        self._log_node_output("generate_all_yamls", result)
        return result

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
                ctx = output.get("context", "")
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
                md_lines.append(f"- **文件**: {output.get('excel_path', '')}")
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
        self._cleanup_logs(str(log_dir), max_files=30)

    @staticmethod
    def _cleanup_logs(log_dir: str, max_files: int = 30):
        """保留最多 max_files 个 .json/.md 文件，删除最旧的"""
        if not os.path.isdir(log_dir):
            return
        files = sorted(
            [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith((".json", ".md"))],
            key=os.path.getmtime,
        )
        if len(files) > max_files:
            for f in files[:len(files) - max_files]:
                try:
                    os.remove(f)
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
                           max_retries: int = 2,
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
                elif isinstance(result, model_class):
                    result.model_dump()
                return result
            except (ValidationError, ValueError, TypeError, OutputParserException,
                    openai.BadRequestError) as e:
                last_error = e
                if attempt < max_retries:
                    print(f"   ⚠️ 输出校验失败，第 {attempt + 1} 次重试... ({e})")

        raise RuntimeError(
            f"LLM 结构化输出校验失败（已重试 {max_retries} 次）: {last_error}"
        )
