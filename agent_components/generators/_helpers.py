"""Phase C 修复循环辅助（错误分类关键词与校验器报错文案对齐）。"""

import re


_ERROR_PATTERN_LABELS = [
    ("B1 双花括号 {{}}", ("双花括号",)),
    ("B2 占位符运算/拼接/未闭合", ("禁止运算/拼接", "未闭合或嵌套")),
    ("B3 非注册表占位符函数", ("未知占位符函数",)),
    ("B4 占位符实参不合规", ("实参个数", "第1个参数仅支持")),
    ("B5/B10 提取字段值须为字符串(无需提取应省略)", ("Input should be a valid string",)),
    ("B6/B7 空列表输出", ("at least 1 item", "too_short")),
    ("B9 json/params/data 并存", ("三选一",)),
]


def _summarize_error_patterns(failures: list) -> str:
    """按 B 类别聚合本轮错误计数（跨文件模式反馈，注入修复轮 prompt）。"""
    counts: dict = {}
    for f in failures:
        err = f.get("error", "")
        matched = False
        for label, keywords in _ERROR_PATTERN_LABELS:
            if any(kw in err for kw in keywords):
                counts[label] = counts.get(label, 0) + 1
                matched = True
        if not matched:
            counts["B8 结构解析失败(缺字段/类型错/JSON坏)"] = \
                counts.get("B8 结构解析失败(缺字段/类型错/JSON坏)", 0) + 1
    if not counts:
        return "（无统计）"
    return "\n".join(f"- {label}: {n} 处" for label, n in counts.items())


def _extract_completion_snippet(err_text: str, limit: int = 500) -> str:
    """从结构化输出异常文本中截取 LLM 原始 completion 片段（修复轮自查材料）。"""
    m = re.search(r"from completion (.+?)(?:\. Got:|$)", err_text, re.DOTALL)
    snippet = m.group(1) if m else err_text
    return snippet[:limit]


def _write_fail_detail(output_base: str, pid: str, case_id: str,
                       yaml_path: str, round_no: int, err_text: str,
                       raw_snippet: str) -> None:
    """单文件生成失败时，将原文和错误点写入详细日志。"""
    import os as _os
    log_path = _os.path.join(output_base, "_generation_error_details.log")
    _parsed_err = err_text
    _m = re.search(r"Got: (.+?)(?:\nFor troubleshooting|$)", err_text, re.DOTALL)
    if _m:
        _parsed_err = _m.group(1).strip()
    with open(log_path, "a", encoding="utf-8") as _f:
        _f.write(f"{'=' * 60}\n")
        _f.write(f"[{pid}] ROUND={round_no} | {case_id} | {yaml_path}\n")
        _f.write(f"{'=' * 60}\n")
        _f.write(f"--- 校验错误 ---\n{_parsed_err[:3000]}\n\n")
        _f.write(f"--- LLM 原始输出 (前 2000 字符) ---\n{raw_snippet[:2000]}\n\n")


def _format_post_issues_for_prompt(issues: list | None) -> str:
    """将后校验问题列表格式化为修复轮 prompt 文本。"""
    if not issues:
        return ""
    lines = ["⚠️ YAML 后校验发现问题（请逐条修正）：", ""]
    for i, iss in enumerate(issues, 1):
        lines.append(f"{i}. [{iss['check']}] {iss['yaml_path']}")
        lines.append(f"   当前: {iss['current']}")
        lines.append(f"   期望: {iss['expected']}")
        lines.append(f"   修复指引: {iss['fix_hint']}")
        lines.append("")
    return "\n".join(lines)
