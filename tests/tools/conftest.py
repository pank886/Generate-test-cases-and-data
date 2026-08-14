# -*- coding: utf-8 -*-
"""tests/tools/ 下辅助工具的 pytest 收集控制。

`yaml_quote_min_test.py` / `yaml_quote_compat_test.py` 虽以 *_test.py 结尾，
但实为 **print 式一次性验证脚本**（无 def test_*），且模块顶层直接
`import base.apiutil` + 写外部框架 `FILE_PATH['extract']` 文件（PycharmMiscProject）。
pytest 默认按 `*_test.py` 收集时会 import 它们 → 收集期写文件副作用 / ImportError
拖垮整个 `python -m pytest tests/`。这里用 collect_ignore 显式排除，保留原文件名。
"""
collect_ignore = [
    "yaml_quote_min_test.py",
    "yaml_quote_compat_test.py",
]
