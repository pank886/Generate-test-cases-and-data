"""Phase A 完整流程测试：生成 Excel 计划 → 注入异常验证修复 → 生成 YAML。

测试步骤:
  1. 从 ChromaDB 检索已入库的接口定义
  2. 分析场景 → 生成 Excel 计划
  3. 手工注入异常数据，触发 repair 循环
  4. 生成 YAML 数据文件
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

import config
from agent_components.dual_chroma import get_chroma_db
from agent_components.nodes import ChatTestAgentGraph
from prompts.response_model import ApiDefinition, ExcelPlan, ExcelRow


def build_retrieve_context(query: str, k: int = 30) -> str:
    """从 ChromaDB 检索上下文（模拟 _retrieve_node 的产出）。"""
    db = get_chroma_db()
    docs = db.search_product_docs(query, k=k)
    apis = db.search_api_defs(query, k=k)

    parts = []
    api_names = set()
    for api in apis:
        name = api.metadata.get("api_name", "?")
        if name in api_names:
            continue
        api_names.add(name)
        parts.append(f"[API] {api.metadata.get('api_name', '?')}: {api.page_content[:200]}")

    for doc in docs:
        parts.append(f"[DOC] {doc.metadata.get('doc_id', '?')}: {doc.page_content[:200]}")

    return "\n\n---\n\n".join(parts)


def extract_apis_from_context(context: str) -> list[ApiDefinition]:
    """从上下文中提取接口定义列表（模拟 _parse_api_node）。"""
    import re
    apis = []
    api_pattern = re.compile(r'\[API\] (.+?): ({.+})')
    for match in api_pattern.finditer(context):
        name = match.group(1)
        try:
            data = json.loads(match.group(2))
            apis.append(ApiDefinition(
                name=name,
                url=data.get("url", "/unknown"),
                method=data.get("method", "GET"),
                description=data.get("description", name),
                parameters=data.get("parameters", {}),
                returns=data.get("returns", {}),
            ))
        except json.JSONDecodeError:
            continue
    return apis if apis else [
        ApiDefinition(name="新增设施", url="/gymFacility/add", method="POST",
                      description="新增健身房设施", parameters={}, returns={}),
        ApiDefinition(name="查询设施", url="/gymFacility/getPage", method="POST",
                      description="分页查询设施", parameters={}, returns={}),
    ]


def test_analyze_scenarios(graph, api_defs, user_input):
    """测试场景分析（thinking 节点）。"""
    print("\n1. 场景分析...")
    state = {
        "api_definition_list": api_defs,
        "original_input": user_input,
        "scenario_analysis": None,
    }
    result = graph._analyze_scenarios_node(state)
    analysis = result.get("scenario_analysis", "")
    assert analysis, "场景分析不应为空"
    print(f"   分析完成: {len(analysis)} 字符")
    print(f"   前 100 字: {analysis[:100]}...")
    return result


def test_generate_excel(graph, api_defs, user_input, scenario_analysis, inject_errors=False):
    """测试 Excel 计划生成。可选注入错误验证修复循环。"""
    print("\n2. 生成 Excel 测试计划...")

    # 构建 prompt_vars（被 _generate_excel_plan_node 内部使用）
    state = {
        "api_definition_list": api_defs,
        "original_input": user_input,
        "all_apis_json": json.dumps(
            [api.model_dump() for api in api_defs],
            indent=2, ensure_ascii=False,
        ),
        "scenario_analysis": scenario_analysis,
    }

    if inject_errors:
        print("   [注入异常] 将手动篡改 prompt_vars 中的接口定义...")
        # 将 apis 中混入无效字段，触发 validation 错误
        state["all_apis_json"] = state["all_apis_json"].replace(
            '"method": "POST"',
            '"method": "INVALID_METHOD_WITH_VERY_LONG_NAME_THAT_SHOULD_FAIL"',
        )
        print("   [注入异常] 已注入非法 method 值")

    result = graph._generate_excel_plan_node(state)
    excel_plan = result.get("excel_plan")
    requires_review = result.get("requires_review", False)

    if requires_review:
        errors = result.get("error_info", [])
        print(f"   ⚠️ 需要人工审查: {len(errors)} 个错误")
        for err in errors[:3]:
            print(f"     - {err}")
        print("   [符合预期] 异常数据被 repair 循环正确捕获")
    else:
        assert excel_plan is not None, "应生成 Excel 计划"
        assert len(excel_plan.rows) > 0, "应包含至少 1 行用例"
        print(f"   ✅ Excel 计划生成成功: {len(excel_plan.rows)} 条用例")
        for row in excel_plan.rows[:3]:
            print(f"     {row.case_name} ({row.module_name})")

    return result


def test_generate_yamls(graph, excel_plan, api_defs, user_input, output_dir):
    """测试 YAML 数据文件生成。"""
    print(f"\n3. 生成 YAML 数据文件（输出到 {output_dir}）...")

    if excel_plan is None:
        print("   ⏭ 跳过（Excel 计划为空）")
        return None

    api_defs_json = json.dumps(
        [api.model_dump() for api in api_defs],
        indent=2, ensure_ascii=False,
    )

    os.makedirs(output_dir, exist_ok=True)
    count = 0
    errors = 0

    for row in excel_plan.rows:
        output_path = os.path.join(output_dir, row.test_data_yaml)
        try:
            result = graph._generate_one_yaml(
                {"steps": row.steps},
                api_defs_json,
                user_input,
                output_path,
            )
            assert os.path.exists(output_path), f"YAML 文件未生成: {output_path}"
            count += 1
            if count <= 3:
                with open(output_path, "r", encoding="utf-8") as f:
                    preview = f.read()[:120]
                print(f"     ✅ {row.test_data_yaml}: {preview}...")
        except Exception as e:
            errors += 1
            print(f"     ❌ {row.test_data_yaml}: {e}")

    print(f"   ✅ 生成 {count} 个 YAML 文件（{errors} 个失败）")
    return count


def main():
    print("=" * 70)
    print("Phase A 完整流程测试 — 含异常处理验证")
    print("=" * 70)

    graph = ChatTestAgentGraph()
    user_input = "健身房设施管理系统 - 新增、修改、删除、查询设施功能"

    # Step 1: 从 ChromaDB 检索已入库数据
    print("\n[Step 0] 从 ChromaDB 检索已入库接口...")
    context = build_retrieve_context("健身房设施")
    api_defs = extract_apis_from_context(context)
    print(f"   检索到 {len(api_defs)} 个接口定义")

    # Step 2: 正常流程 → 分析场景 + 生成 Excel
    state = test_analyze_scenarios(graph, api_defs, user_input)
    scenario_analysis = state["scenario_analysis"]

    result = test_generate_excel(
        graph, api_defs, user_input, scenario_analysis,
        inject_errors=False,
    )

    # Step 3: 异常流程 → 注入非法数据测试 repair
    print("\n" + "=" * 70)
    print("异常处理验证 — 注入非法 method 值")
    print("=" * 70)
    error_result = test_generate_excel(
        graph, api_defs, user_input, scenario_analysis,
        inject_errors=True,
    )

    # Step 4: YAML 生成
    excel_plan = result.get("excel_plan")
    if excel_plan:
        output_dir = os.path.join(config.TESTCASE_BASE, "健身房设施管理_test")
        yaml_count = test_generate_yamls(
            graph, excel_plan, api_defs, user_input, output_dir,
        )
    else:
        yaml_count = 0

    # Step 5: 用异常数据生成 YAML 验证 repair 有效性
    error_excel = error_result.get("excel_plan")
    requires_review = error_result.get("requires_review", False)
    if requires_review and error_excel:
        print("\n" + "=" * 70)
        print("异常流程 YAML 生成（验证 repair 后的数据仍可产出）")
        print("=" * 70)
        err_output_dir = output_dir + "_repair"
        err_count = test_generate_yamls(
            graph, error_excel, api_defs, user_input, err_output_dir,
        )
    else:
        print(f"\n   ⚠️ 异常场景触发 requires_review={requires_review}")

    print("\n" + "=" * 70)
    print(f"测试完成")
    print(f"  Excel 计划: {len(excel_plan.rows) if excel_plan else 0} 条用例")
    print(f"  YAML 文件: {yaml_count} 个")
    print("=" * 70)


if __name__ == "__main__":
    main()
