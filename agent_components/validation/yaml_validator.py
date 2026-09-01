"""YAML 数据生成检测（2026-09-01 校验包归位重构）。

合并自 agent_components/post_validator.py（YAML 生成后快速验证）与
_repair_helpers.py 迁移的生成后校验函数（_find_missing_yaml_refs 引用完整性 /
_scan_missing_key_refs D4 缺失键扫描），语义归属「YAML 数据生成检测」。

纯代码，不放 LLM。产出结构化错误信息，可被 _run_yaml_rounds 修复轮直接消费。
"""

import glob
import os
import re

import yaml

from infrastructure.observability import get_logger

logger = get_logger(__name__)

_PLACEHOLDER_RE = re.compile(r'\$\{[^}]+\}')
_VALID_OPS = {"eq", "contains", "ne", "db"}
_GET_EXTRACT_RE = re.compile(r"get_extract_data\(\s*['\"]([^'\"]+)['\"]\s*\)")


class YamlPostValidator:
    """YAML 生成后快速验证器。

    每个检查项返回统一结构:
      {yaml_path, check, severity, line, current, expected, fix_hint}
    """

    # ---- 公共入口 ----

    def validate_all(self, output_dir: str) -> list[dict]:
        """遍历所有 YAML 文件，执行全部注册的检查项。"""
        issues: list[dict] = []
        pattern = os.path.join(output_dir, "**", "*.yaml")
        yaml_files = glob.glob(pattern, recursive=True)
        for path in yaml_files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                continue
            if not isinstance(data, dict) or "data" not in data:
                continue
            for step in data.get("data") or []:
                if not isinstance(step, dict):
                    continue
                issues.extend(self._check_delete_body_wrapper(step, path))
                issues.extend(self._check_assertion_dynamic_key(step, path))
                issues.extend(self._check_assertion_op_key(step, path))
                issues.extend(self._check_malformed_assertion(step, path))
        return issues

    # ---- 检查 1：delete body 包裹检测 ----

    def _check_delete_body_wrapper(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 json: {body: [...]} 包裹层误用。

        触发条件（四条同时满足）：
          - method 为 post/put/patch
          - json 中存在且仅有 body 一个 key
          - json.body 为非空数组
          - 数组元素为 dict（有 key，非简单值）
        """
        issues = []
        base_info = step.get("baseInfo")
        if not isinstance(base_info, dict):
            return issues
        method = str(base_info.get("method", "")).lower()
        if method not in ("post", "put", "patch"):
            return issues

        for tc in step.get("testCase") or []:
            body = tc.get("json")
            if not isinstance(body, dict):
                continue
            if set(body.keys()) != {"body"}:
                continue
            inner = body.get("body")
            if not isinstance(inner, list) or not inner:
                continue
            if not all(isinstance(item, dict) and item for item in inner):
                continue
            issues.append({
                "yaml_path": yaml_path,
                "check": "delete_body_wrapper",
                "severity": "P0",
                "line": 0,
                "current": f"json: {{body: [{len(inner)} items]}}",
                "expected": f"json: [{len(inner)} items]",
                "fix_hint": "数组 body 直接用 json: [...]，去掉 body 包裹层",
            })
        return issues

    # ---- 检查 2：断言 key 动态值检测 ----

    def _check_assertion_dynamic_key(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 validation 中 key 位置使用了 ${} 模板变量。

        正则匹配 ${{xxx}} 模板变量，不误伤 $.data.xxx JSONPath。
        """
        issues = []
        for tc in step.get("testCase") or []:
            validation = tc.get("validation") or []
            for item in validation:
                if not isinstance(item, dict):
                    continue
                for key in item.keys():
                    if _PLACEHOLDER_RE.search(key):
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "assertion_dynamic_key",
                            "severity": "P1",
                            "line": 0,
                            "current": f"{key}: ...",
                            "expected": f"$.data.xxx: ${{{_PLACEHOLDER_RE.search(key).group(0)}}}",
                            "fix_hint": "断言的 key 必须是静态 JSONPath（如 $.data.code），动态值放在 : 右边",
                        })
        return issues

    # ---- 检查 3：断言块键白名单检测 ----

    def _check_assertion_op_key(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 validation 块键不是合法运算符（eq/contains/ne/db）。

        P0 兜底：response_model 层已拦截的畸形块若仍落盘，在此标记，
        供修复轮消费。块键必须是运算符，JSONPath 写在操作数 dict 的键位。
        """
        issues = []
        for tc in step.get("testCase") or []:
            validation = tc.get("validation") or []
            for item in validation:
                if not isinstance(item, dict):
                    continue
                for key in item.keys():
                    if key not in _VALID_OPS:
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "assertion_op_key",
                            "severity": "P0",
                            "line": 0,
                            "current": f"{key}: ...",
                            "expected": "块键必须是运算符 eq/contains/ne/db 之一",
                            "fix_hint": "断言块键必须是运算符，JSONPath 写在操作数 dict 的键位，"
                                        "如 {eq: {$.code: 1}}，禁止 {$.retCode: {eq: 1}} 等写法",
                        })
        return issues

    # ---- 检查 4：断言格式拼合检测 ----

    def _check_malformed_assertion(self, step: dict, yaml_path: str) -> list[dict]:
        """检测 validation 中 key 或 value 有未配对的引号。

        仅告警不修复——LLM 没有明确的 expected 值，修复轮可能越修越错。
        """
        issues = []
        for tc in step.get("testCase") or []:
            validation = tc.get("validation") or []
            for item in validation:
                if not isinstance(item, dict):
                    continue
                for key, value in item.items():
                    if isinstance(key, str) and self._has_unmatched_quotes(key):
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "malformed_assertion",
                            "severity": "P2",
                            "line": 0,
                            "current": key,
                            "expected": "修复引号配对",
                            "fix_hint": "检查 assertion key 的引号是否配对（仅告警，不自动修复）",
                        })
                    if isinstance(value, str) and self._has_unmatched_quotes(value):
                        issues.append({
                            "yaml_path": yaml_path,
                            "check": "malformed_assertion",
                            "severity": "P2",
                            "line": 0,
                            "current": value,
                            "expected": "修复引号配对",
                            "fix_hint": "检查 assertion value 的引号是否配对（仅告警，不自动修复）",
                        })
        return issues

    @staticmethod
    def _has_unmatched_quotes(s: str) -> bool:
        """检测字符串中是否有未配对的引号（忽略转义）。"""
        single = s.count("'") - s.count("\\'")
        double = s.count('"') - s.count('\\"')
        return single % 2 == 1 or double % 2 == 1


# ==================== 生成后引用/缺失键校验（迁移自 _repair_helpers.py） ====================


def _find_missing_yaml_refs(output_base: str, project_root: str) -> list:
    """扫描 output_base 下所有 test_*.py 引用的 yaml，返回磁盘缺失清单。

    引用路径形如 './testcase/<batch>/<feature>/<func>/test_data.yaml'
    （pytest 从 project_root 运行），磁盘解析为 project_root + 相对路径。
    2026-08-12 问题 3：_27 曾出现 .py 引用存在但磁盘缺失（空目录）未被拦截，
    此处接入生成收尾，缺失文件禁止静默放行。
    """
    refs = []
    for dp, _dn, fn in os.walk(output_base):
        for f in fn:
            if f.endswith(".py") and f.startswith("test_"):
                _path = os.path.join(dp, f)
                refs.extend(re.findall(r"'\./([^']+\.yaml)'",
                                       open(_path, encoding="utf-8").read()))
    missing = []
    for r in sorted(set(refs)):
        if not os.path.exists(os.path.join(project_root, r)):
            missing.append(r)
    return missing


def _scan_missing_key_refs(output_base: str, setup_keys: dict) -> list:
    """D4 后校验（就地实现，2026-08-27 决策选项 1，不走 validate_all）。

    扫描 test/teardown YAML（跳过 setup_*.yaml）中引用 __MISSING_KEY__ 的用例
    → P1（需人工复核，不进修复轮——根因是 setup 生成失败，重生成无用）。
    避开 2026-08-27 发现的后校验格式错位：validate_all 只处理 {data:[...]} dict，
    而生成器写盘为顶层 list，故此处直接遍历 list 格式产物。
    """
    missing = {k for entry in setup_keys.values()
               for k, v in entry["keys"].items() if v == "__MISSING_KEY__"}
    if not missing:
        return []
    issues = []
    for fp in glob.glob(os.path.join(output_base, "**", "*.yaml"), recursive=True):
        rel = os.path.relpath(fp, output_base).replace("\\", "/")
        if "/setup_data/setup_" in rel:
            continue  # setup 自身不引用提取键
        try:
            data = yaml.safe_load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        for block in data or []:
            for tc in block.get("testCase", []):
                cn = tc.get("case_name", "")
                text = str(tc.get("json") or "") + str(tc.get("validation") or "")
                for key in _GET_EXTRACT_RE.findall(text):
                    if key in missing:
                        issues.append({
                            "yaml_path": fp,  # 绝对/glob 路径，与 validate_all 一致
                            "check": "missing_extract_key",
                            "severity": "P1",
                            "line": 0,
                            "current": f"get_extract_data('{key}')",
                            "expected": "setup 提取失败，需人工复核（改硬编码占位值或改断言）",
                            "fix_hint": "D4: setup 未提取到该键（__MISSING_KEY__），该用例需人工复核修正",
                            "case_name": cn,
                        })
    return issues
