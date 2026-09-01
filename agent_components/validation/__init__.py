"""生成后静态校验包（2026-09-01 归位重构）。

用例生成后、除 Pydantic schema 之外的静态校验统一收纳于此：
- case_validator   —— 用例生成检测（Excel 测试计划 / .py 文件层）
- yaml_validator   —— YAML 数据生成检测（生成产物静态扫描）
"""
