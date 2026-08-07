"""Phase C: PY/YAML 生成节点 Mixin（组合层）

2026-08-07 大文件拆分：实现迁移至子模块，本文件仅负责组合与 re-export。
  - excel:        Excel 用例读取 + dependency_map 生成（ExcelMixin）
  - translation:  英文翻译 + 幂等性保障（TranslationMixin）
  - py_export:    pytest .py 文件生成 + 断言解析（PyExportMixin）
  - yaml_gen:     YAML 数据生成 + 轮次修复循环（YamlMixin）
  - _helpers:     修复循环辅助函数
"""
from agent_components.generators.excel import ExcelMixin
from agent_components.generators.translation import TranslationMixin
from agent_components.generators.py_export import PyExportMixin
from agent_components.generators.yaml_gen import YamlMixin

# 模块级辅助函数（拆分自 _helpers.py，re-export 保持既有导入兼容）
from agent_components.generators._helpers import (
    _summarize_error_patterns,
    _extract_completion_snippet,
    _write_fail_detail,
    _format_post_issues_for_prompt,
)


class GenerationMixin(ExcelMixin, TranslationMixin, PyExportMixin, YamlMixin):
    """PY/YAML 测试文件生成节点（组合各子 Mixin，类名/方法签名保持兼容）"""
