"""数据工厂方法注册表加载层。

单一事实源: data_factory/methods.yaml（v2 分类结构）
消费方:
  1. prompt 渲染 — render_for_prompt()（目录 + 分类详情两段）
  2. 占位符校验器 — load_methods() / get_validation_rules()
     (prompts/response_model.py，import 方向 prompts → data_factory 无环)
  3. 单元测试 — 遍历注册表生成合法用例，防止注册表与校验器脱节

兼容旧版扁平 `methods:` 结构（迁移保险）。
"""
import os
import threading

import yaml

_lock = threading.Lock()
_methods_cache: list | None = None
_prompt_cache: str | None = None


def _registry_path() -> str:
    import config
    return os.path.join(config.BASE_DIR, "data_factory", "methods.yaml")


def reset_cache() -> None:
    """清空缓存（测试/热更新用）。"""
    global _methods_cache, _prompt_cache
    with _lock:
        _methods_cache = None
        _prompt_cache = None


def _load_raw() -> list[dict]:
    """读盘并归一化为分类列表 [{name, description, methods: [...]}]。

    兼容两种结构:
      v2: {categories: [{name, description, methods: [...]}]}
      v1: {methods: [...]}  → 归入单一 "默认" 类
    """
    path = _registry_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("categories"), list):
        return [c for c in raw["categories"] if isinstance(c, dict)]
    if isinstance(raw.get("methods"), list):  # 旧版扁平结构兼容
        return [{"name": "默认", "description": "", "methods": raw["methods"]}]
    return []


def load_methods() -> list[dict]:
    """返回扁平方法列表（每条附带 category 字段），带缓存+双检锁。"""
    global _methods_cache
    if _methods_cache is not None:
        return _methods_cache
    with _lock:
        if _methods_cache is not None:
            return _methods_cache
        flat: list[dict] = []
        for cat in _load_raw():
            for m in cat.get("methods") or []:
                if isinstance(m, dict) and m.get("name"):
                    entry = dict(m)
                    entry["category"] = cat.get("name", "")
                    flat.append(entry)
        _methods_cache = flat
        return _methods_cache


def get_validation_rules() -> dict:
    """返回 {函数名: validation 块} 映射；无 validation 块的方法映射为 {}。"""
    return {m["name"]: (m.get("validation") or {}) for m in load_methods()}


def render_for_prompt() -> str:
    """渲染为"目录 + 分类详情"两段文本，目录从 categories 自动派生。

    LLM 先看目录建立能力全景（选择器），再查详情按 syntax 填写（填写说明）。
    validation 块不渲染（仅校验器消费）。
    """
    global _prompt_cache
    if _prompt_cache is not None:
        return _prompt_cache
    with _lock:
        if _prompt_cache is not None:
            return _prompt_cache
        cats = [c for c in _load_raw()
                if any(isinstance(m, dict) and m.get("name")
                       for m in c.get("methods") or [])]
        if not cats:
            _prompt_cache = "（无可用数据工厂方法）"
            return _prompt_cache

        lines = ["【数据工厂方法目录】"]
        for cat in cats:
            names = " / ".join(m["name"] for m in cat["methods"]
                               if isinstance(m, dict) and m.get("name"))
            desc = cat.get("description", "")
            brief = desc.split("——")[0].split("。")[0].strip()
            suffix = f"（{brief}）" if brief else ""
            lines.append(f"- {cat.get('name', '')}{suffix}: {names}")

        lines.append("")
        lines.append("【方法详情】")
        for cat in cats:
            lines.append(f"== {cat.get('name', '')} == {cat.get('description', '')}")
            for m in cat["methods"]:
                if not (isinstance(m, dict) and m.get("name")):
                    continue
                name = m["name"]
                syntax = m.get("syntax", f"${{{name}(...)}}")
                desc = m.get("description", "")
                lines.append(f"   - `{syntax}`：{desc}")
                params = m.get("params")
                if isinstance(params, dict):
                    for pname, pdesc in params.items():
                        lines.append(f"     · 参数 {pname}: {pdesc}")
                for tip in m.get("usage_tips", []):
                    lines.append(f"     - {tip}")
        _prompt_cache = "\n".join(lines)
        return _prompt_cache
