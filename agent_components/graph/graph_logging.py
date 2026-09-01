"""工作流日志辅助：节点产出物序列化、累积写盘、清理。

2026-08-07 大文件拆分：自 ``agent_components/nodes.py`` 736–918 迁移。
``nodes.py`` 类内薄转发同名方法（签名不变），对外调用方式不变。

``log_node_output`` 以 ``host``（ChatTestAgentGraph 实例）承载运行时状态
（``_run_data`` / ``_run_timestamp``），避免在日志工具中重复持有可变状态。
"""

import json
import os
import re
from datetime import datetime

from pydantic import BaseModel

from infrastructure.observability import get_logger

logger = get_logger(__name__)


def split_thinking_sections(text: str) -> dict:
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


def serialize_for_log(obj):
    """递归序列化对象为 JSON 可序列化的格式"""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    elif isinstance(obj, dict):
        return {k: serialize_for_log(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_log(v) for v in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)


def log_node_output(host, node_name: str, output: dict):
    """将节点产出物累积到当前运行日志文件（同一次运行共用一份 JSON + MD）。

    Args:
        host: ChatTestAgentGraph 实例（承载 _run_data / _run_timestamp 运行状态）
    """
    from pathlib import Path
    log_dir = Path("logs") / "workflow"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 首次调用时生成时间戳（同一次运行保持不变）
    if host._run_timestamp is None:
        host._run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 累积数据
    host._run_data[node_name] = host._serialize_for_log(output)

    base_name = f"workflow_{host._run_timestamp}"

    # ---- JSON（全量数据） ----
    json_path = log_dir / f"{base_name}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(host._run_data, f, ensure_ascii=False, indent=2)

    # ---- MD（可读摘要） ----
    md_lines = [
        "# 工作流运行日志",
        f"**运行时间**: {host._run_timestamp[:4]}-{host._run_timestamp[4:6]}-{host._run_timestamp[6:8]} "
        f"{host._run_timestamp[9:11]}:{host._run_timestamp[11:13]}:{host._run_timestamp[13:15]}",
        "",
    ]
    node_order = ["generate_excel_plan",
                   "analyze_test_points_raw", "format_test_points",
                   "generate_py_file", "generate_all_yamls"]
    for nname in node_order:
        if nname not in host._run_data:
            continue
        data = host._run_data[nname]
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
    host._cleanup_logs(str(log_dir), max_pairs=15)


def cleanup_logs(log_dir: str, max_pairs: int = 15):
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
