"""PY/YAML 生成节点 Mixin"""
import os
import json

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl import load_workbook

import config
from observability import get_logger
from prompts.response_model import ClassCode, TestData

logger = get_logger(__name__)


class GenerationMixin:
    """PY/YAML 测试文件生成节点"""

    def _analyze_data_deps(self, case_steps: str, api_defs_json: str,
                           user_ctx: str) -> str:
        """数据依赖分析（thinking on，自由文本）。"""
        from prompts.extraction_prompts import analyze_data_deps_prompt

        logger.info("\n🧠 分析数据依赖（深度思考）...")
        prompt = analyze_data_deps_prompt()
        llm_kwargs = {}
        if config.ENABLE_THINKING:
            llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        else:
            llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        bound_llm = self.llm.bind(**llm_kwargs)
        result = bound_llm.invoke(prompt.format_messages(
            api_definitions=api_defs_json,
            test_case_steps=case_steps,
            user_context=user_ctx,
        ))
        analysis = result.content if hasattr(result, "content") else str(result)
        logger.info(f"   => 数据依赖分析完成（{len(analysis)} 字符）")
        return analysis

    def _format_data_plan(self, data_analysis: str, case_steps: str,
                          api_defs_json: str, user_ctx: str) -> dict:
        """格式化数据规划（thinking off + json_mode）。"""
        from prompts.extraction_prompts import generate_data_plan_prompt
        from prompts.response_model import DataPlan

        logger.info("\n--- 生成结构化数据规划 ---")
        prompt = generate_data_plan_prompt()
        result = self._invoke_structured(prompt, DataPlan,
            method="json_mode",
            data_analysis=data_analysis,
            api_definitions=api_defs_json,
            test_case_steps=case_steps,
            user_context=user_ctx,
        )
        if isinstance(result, list):
            result = DataPlan(steps=result, scenario_name="")
        logger.info(f"   => 数据规划完成: {len(result.steps)} 步")
        return {"data_plan": result.model_dump()}

    @staticmethod
    def _read_excel_rows(excel_path: str, enabled_only: bool = False) -> list[dict]:
        """读取 Excel 测试计划，返回 dict 列表（10 列，统一解析）。

        列顺序: 项目名称,Allure Epic,模块名称,Allure Feature,Allure Story,
                 fixture等级,用例名称,执行步骤,测试数据YAML,是否启用
        """
        from openpyxl import load_workbook
        wb = load_workbook(excel_path)
        ws = wb.active
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row[0] is None:
                continue
            if enabled_only and row[9] != "Y":
                continue
            rows.append({
                "project_name": row[0], "allure_epic": row[1],
                "module_name": row[2], "allure_feature": row[3],
                "allure_story": row[4], "fixture_level": row[5],
                "case_name": row[6], "steps": row[7],
                "test_data_yaml": row[8], "enabled": row[9],
            })
        return rows

    def _generate_py_file(self, excel_path: str, project_name: str = None) -> dict:
        """逐模块生成 .py 测试文件（外层循环 I/O，内层 LLM 单 class 生成）"""
        logger.info("\n🐍 正在生成 Python 测试文件...")

        if not excel_path:
            logger.info("   ⚠️ 无 Excel 路径，跳过 .py 生成")
            return {"py_path": "", "py_file_name": "", "modules": 0, "cases": 0}

        from collections import defaultdict
        expanded_rows = self._read_excel_rows(excel_path)

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

            # YAML 子目录直接使用模块名；疑似 Python 类名时打警告
            module_subdir = mod_name
            if mod_name.startswith("Test") and len(mod_name) > 4 and mod_name[4].isupper():
                logger.warning(
                    f"   模块名 '{mod_name}' 看起来像 Python 类名，"
                    f"目录名直接使用 '{module_subdir}'，如不符合预期请检查上游命名"
                )

            mod_lines = [f"模块: {mod_name}  (feature: {cases[0]['allure_feature']}, story: {cases[0]['allure_story']}, fixture: {cases[0]['fixture_level']}, {len(cases)} 条用例, 子目录: {module_subdir})\n"]
            for i, c in enumerate(cases, 1):
                status = "启用" if c["enabled"] == "Y" else "禁用"
                mod_lines.append(
                    f"  order={i} | {c['case_name']} → {c['test_data_yaml']} [{status}]"
                )
                mod_lines.append(f"    步骤: {c['steps']}")
            module_text = "\n".join(mod_lines)

            logger.info(f"   [{idx + 1}/{len(mod_names)}] 生成 class: {mod_name} ...")

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
        # 原子写入：先写临时文件再 rename，避免中途崩溃留下半截 .py
        tmp_path = py_path + ".tmp"
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        with open(tmp_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(full_content)
        os.replace(tmp_path, py_path)

        logger.info(f"   📄 Python 文件已保存: {py_path}")
        logger.info(f"   📦 {len(mod_names)} 个模块, {total_cases} 条用例")

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
        test_case_logic = f"执行步骤: {row['steps']}"

        # 从 data_factory/methods.yaml 读取出可用工厂方法
        factory_methods_text = self._load_factory_methods()

        result = self._invoke_structured(prompt, TestData,
            method="function_calling",
            all_apis_info=api_defs_json,
            user_context=user_ctx,
            test_case_logic=test_case_logic,
            data_factory_methods=factory_methods_text,
        )

        # Pydantic V2 模型需先转 dict，否则 yaml.dump 输出含 __pydantic_* 内部字段
        yaml_text = yaml.dump(
            [step.model_dump(exclude_none=True) for step in result.data],
            allow_unicode=True, indent=2, default_flow_style=False,
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # 原子写入：先写临时文件再 rename，避免中途崩溃留下半截文件
        tmp_path = output_path + ".tmp"
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(yaml_text)
        os.replace(tmp_path, output_path)
        return output_path

    def _generate_all_yamls(self, excel_path: str, api_defs_json: str, user_ctx: str) -> dict:
        """读 Excel → 按场景分目录 → 多线程逐条生成 YAML（供 /confirm-plan 调用）"""
        logger.info("\n🔢 正在生成 YAML 测试数据...")

        if not excel_path:
            logger.info("   ⚠️ 无 Excel 路径，跳过 YAML 生成")
            return {"total": 0, "success": 0, "failed": 0}

        output_base = os.path.dirname(excel_path)
        raw_rows = self._read_excel_rows(excel_path, enabled_only=True)
        rows = []
        for r in raw_rows:
            module_name = r["module_name"]
            module_subdir = module_name
            if module_name.startswith("Test") and len(module_name) > 4 and module_name[4].isupper():
                logger.warning(
                    f"   模块名 '{module_name}' 看起来像 Python 类名，"
                    f"目录名直接使用 '{module_subdir}'，如不符合预期请检查上游命名"
                )

            yaml_name = r["test_data_yaml"].strip()
            if not yaml_name:
                continue
            output_path = os.path.join(output_base, module_subdir, yaml_name)

            # 文件去重（上限提取为常量，超限时警告而非静默失败）
            _MAX_DEDUP = 999
            if os.path.exists(output_path):
                base, ext = os.path.splitext(yaml_name)
                for i in range(1, _MAX_DEDUP):
                    alt_path = os.path.join(output_base, module_subdir, f"{base}_{i:02d}{ext}")
                    if not os.path.exists(alt_path):
                        output_path = alt_path
                        break
                else:
                    logger.warning(f"   ⚠️ 文件去重超过 {_MAX_DEDUP} 次上限，覆盖写入: {output_path}")

            rows.append({
                "project_name": r["project_name"],
                "module_name": module_name,
                "case_name": r["case_name"],
                "steps": r["steps"],
                "test_data_yaml": os.path.basename(output_path),
                "output_path": output_path,
            })

        if not rows:
            logger.info("   ⚠️ 没有启用的用例需要生成 YAML")
            result = {"total": 0, "success": 0, "failed": 0}
            self._log_node_output("generate_all_yamls", result)
            return result

        total = len(rows)
        logger.info(f"   📋 共需生成 {total} 个 YAML 文件（按 module 分目录），并发 5 个线程")

        from web.tasks import _BoundedThreadPoolExecutor
        from concurrent.futures import as_completed
        success = 0
        failed = 0
        with _BoundedThreadPoolExecutor(max_workers=config.YAML_CONCURRENCY, max_queue=config.YAML_CONCURRENCY * 2) as executor:
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
                    logger.info(f"      [{done}/{total}] ✅ {row['test_data_yaml']}  ({row['module_name']})")
                except Exception as e:
                    failed += 1
                    done = success + failed
                    logger.info(f"      [{done}/{total}] ❌ {row['test_data_yaml']}: {e}")

        logger.info(f"   ✅ 完成: {success}/{total}，失败 {failed}")
        result = {"total": total, "success": success, "failed": failed}
        self._log_node_output("generate_all_yamls", result)
        return result
