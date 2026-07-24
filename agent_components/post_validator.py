"""YAML 生成后快速验证，纯代码，不放 LLM。

挂在 Phase C _generate_all_yamls 返回后、ValidationInterceptor 写报告前。
产出结构化错误信息，可被 _run_yaml_rounds 修复轮直接消费。
"""

import os
import re
import glob as _glob
import yaml as _yaml

from observability import get_logger

logger = get_logger(__name__)

_PLACEHOLDER_RE = re.compile(r'\$\{[^}]+\}')


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
        yaml_files = _glob.glob(pattern, recursive=True)
        for path in yaml_files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
            except Exception:
                continue
            if not isinstance(data, dict) or "data" not in data:
                continue
            for step in data.get("data") or []:
                if not isinstance(step, dict):
                    continue
                issues.extend(self._check_delete_body_wrapper(step, path))
                issues.extend(self._check_assertion_dynamic_key(step, path))
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

    # ---- 检查 3：断言格式拼合检测 ----

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
