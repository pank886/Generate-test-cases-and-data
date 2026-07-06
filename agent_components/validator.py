"""只读校验节点。纯 Python 代码校验，无 LLM 调用。

职责：
  - Excel 文件层校验（表头、枚举值、必填字段）
  - .py 文件层校验（语法、命名规范、路径格式）

校验失败返回具体错误列表，供自动修复循环使用。
"""

import ast
import os
import re
from typing import List, Tuple


ValidationResult = Tuple[bool, List[str]]
""" (passed: bool, errors: List[str]) """


# ==================== Excel 校验 ====================

VALID_ENABLED = {"Y", "N"}
VALID_FIXTURE_PATTERN = re.compile(r'^[a-zA-Z一-鿿]+(?:[,\s][a-zA-Z一-鿿]+)*$')
EXPECTED_HEADERS = [
    "项目名称", "Allure Epic", "模块名称", "Allure Feature",
    "Allure Story", "fixture等级",
    "用例名称", "执行步骤", "测试数据YAML", "是否启用",
]


def validate_excel_file(excel_path: str) -> ValidationResult:
    """校验 Excel 测试计划文件。"""
    errors = []

    if not os.path.exists(excel_path):
        return False, ["文件不存在: " + excel_path]

    try:
        from openpyxl import load_workbook
        wb = load_workbook(excel_path)
    except Exception as e:
        return False, [f"无法打开 Excel 文件: {e}"]

    ws = wb.active
    if ws is None:
        return False, ["Excel 文件为空"]

    # 1. 检查表头
    headers = [cell.value for cell in ws[1]]
    for i, expected in enumerate(EXPECTED_HEADERS):
        if i >= len(headers) or headers[i] != expected:
            errors.append(f"表头第{i+1}列应为'{expected}'，实际为'{headers[i] if i < len(headers) else '缺失'}'")

    # 2. 逐行校验
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if row[0] is None:
            continue  # 跳过空行

        # case_name: 必须以 test_ 开头
        case_name = str(row[6] or "")
        if not case_name.startswith("test_"):
            errors.append(f"第{row_idx}行: case_name '{case_name}' 必须以 test_ 开头")

        # enabled: 只能是 Y/N
        enabled = str(row[9] or "")
        if enabled not in VALID_ENABLED:
            errors.append(f"第{row_idx}行: enabled '{enabled}' 必须为 Y 或 N")

        # fixture_level: 不为空
        fixture = str(row[5] or "")
        if not fixture.strip():
            errors.append(f"第{row_idx}行: fixture_level 为空")

        # 必填字段
        for col_idx, col_name in [(0, "项目名称"), (1, "Allure Epic"), (2, "模块名称"),
                                   (3, "Allure Feature"), (4, "Allure Story"), (7, "执行步骤"),
                                   (8, "测试数据YAML")]:
            val = row[col_idx]
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"第{row_idx}行: {col_name} 为空")

    wb.close()
    return len(errors) == 0, errors


# ==================== .py 文件校验 ====================

def validate_py_file(py_path: str) -> ValidationResult:
    """校验生成的 Python 测试文件。"""
    errors = []

    if not os.path.exists(py_path):
        return False, ["文件不存在: " + py_path]

    with open(py_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 语法校验
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return False, [f"Python 语法错误: {e}"]

    # 2. 类名规范
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not node.name.startswith("Test"):
                errors.append(f"类名 '{node.name}' 应以 Test 开头")
        if isinstance(node, ast.FunctionDef):
            if not node.name.startswith("test_"):
                errors.append(f"方法名 '{node.name}' 应以 test_ 开头")

    # 3. 检查 YAML 路径引用
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords if hasattr(node, 'keywords') else []:
                if kw.arg == 'arg' and isinstance(kw.value, ast.Constant):
                    if '.yaml' in str(kw.value.value):
                        path = kw.value.value
                        if not path.startswith('./testcase/'):
                            errors.append(f"YAML 路径格式异常: {path}")

    return len(errors) == 0, errors
