"""提示词变量构建：生成/修复共用的 prompt 变量构造（单一数据源）。

2026-08-07 大文件拆分：自 ``agent_components/nodes.py`` 768–792 迁移。
``nodes.py`` 类内薄转发 ``_prepare_plan_prompt_vars``（签名不变）。
"""

import json

import config


def prepare_plan_prompt_vars(host, state: dict) -> dict:
    """生成/修复共用的 prompt 变量构造（单一数据源）。2026-08 新增。

    供生成节点与修复节点统一调用，保证 API 信息（概要）、模块树、分析段落
    来自同一份来源；入参含 plan_source 数据源标注。
    接线到具体节点待「待删除清单」确认后执行（当前仅新增，不改现有行为）。

    Args:
        host: ChatTestAgentGraph 实例（需提供 _split_thinking_sections）
    """
    module_tree = state.get("module_tree_json") or "[]"
    test_analysis = state.get("test_point_analysis") or "（无）"
    _sections = host._split_thinking_sections(test_analysis)
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
        "db_schema": config.DB_SCHEMA,  # 数据库表结构（占位，为空禁 [db]，2026-08-04 问题 2）
        "plan_source": state.get("plan_source"),
        "user_context": state.get("original_input", ""),
    }
