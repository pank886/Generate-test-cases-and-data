"""API 异常标识注册表 — 统一管理接口级特殊处理标注。

入库阶段自动检测 → 写入 api_defs.json → 校验阶段按标识精准放行。

用法:
    from agent_components.api_annotations import ApiAnnotationRegistry

    # 入库阶段：对所有 API 跑检测
    for api in all_apis_dict:
        ApiAnnotationRegistry.apply_all(api)

    # 校验阶段：查标识是否激活
    if ApiAnnotationRegistry.is_active(annotations, "is_export"):
        ...  # 放行

    # 前端：获取全部可选类型
    types = ApiAnnotationRegistry.get_types()
"""

import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ApiAnnotationDef:
    """单个 API 异常标识的元数据定义。

    Attributes:
        key: 唯一标识，如 "is_export" / "has_path_params"
        label: 前端展示名，如 "导出/导入类接口"
        description: 悬停提示文本
        category: 分组 — "request" | "response" | "lifecycle"
        detector: 自动检测函数 (api_dict) -> (matched: bool, meta: dict | None)
    """
    key: str
    label: str
    description: str
    category: str
    detector: Callable[[dict], tuple[bool, dict | None]]


class ApiAnnotationRegistry:
    """API 异常标识注册表 — 统一入口。

    使用类方法集合，无需实例化：
      - register(): 注册新类型
      - apply_all(): 对单个 API 跑全部检测器并写入 annotations 字段
      - is_active(): 校验器用，判断指定标识是否激活
    """

    _registry: dict[str, ApiAnnotationDef] = {}

    # ========== 注册管理 ==========

    @classmethod
    def register(cls, defn: ApiAnnotationDef) -> None:
        """注册一个新的异常类型。重复注册同名 key 会覆盖。"""
        cls._registry[defn.key] = defn

    @classmethod
    def get_type(cls, key: str) -> ApiAnnotationDef | None:
        """按 key 查询单个类型定义。"""
        return cls._registry.get(key)

    @classmethod
    def get_types(cls) -> list[ApiAnnotationDef]:
        """返回全部已注册类型列表 → 供前端渲染选项。"""
        return list(cls._registry.values())

    # ========== 检测逻辑 ==========

    @classmethod
    def detect_all(cls, api_dict: dict) -> dict[str, dict]:
        """对单个 api_dict 跑全部检测器，返回 {key: {active, source, ...meta}}。

        注意：只返回本次检测到的类型，不包含 api_dict 中已有的手动标注。
        """
        result: dict[str, dict] = {}
        for key, defn in cls._registry.items():
            matched, meta = defn.detector(api_dict)
            if matched:
                entry = {"active": True, "source": "auto"}
                if meta:
                    entry.update(meta)
                result[key] = entry
        return result

    @classmethod
    def apply_all(cls, api_dict: dict) -> dict:
        """检测 + 写入 annotations 字段（级联规则见方案 §8.3）。

        规则:
          1. 对每个已注册类型跑 detector
          2. 命中且 annotations 中该 key 不存在 → 写入 auto 标注
          3. 命中但 annotations 中已有（人工改过，source="manual"）→ 保留人工版本
          4. 未命中 → 该 key 不出现（除非用户手动添加过）
        """
        existing = api_dict.get("annotations") or {}
        for key, defn in cls._registry.items():
            matched, meta = defn.detector(api_dict)
            if matched:
                if key not in existing:
                    # 自动检测命中，且无人工干预 → 写入
                    entry = {"active": True, "source": "auto"}
                    if meta:
                        entry.update(meta)
                    existing[key] = entry
                # else: 人工已标注 → 保留，不覆盖 source/active

        if existing:
            api_dict["annotations"] = existing
        # 如果 existing 为空且之前 api_dict 没有 annotations → 不写
        return api_dict

    # ========== 校验辅助 ==========

    @classmethod
    def is_active(cls, annotations: dict | None, key: str) -> bool:
        """供 validator 调用：该标识是否激活。

        annotations=None 或 key 不存在 → False
        annotations[key].active == True → True
        其他情况 → False
        """
        if not annotations:
            return False
        entry = annotations.get(key)
        if not entry:
            return False
        return bool(entry.get("active", False))

    @classmethod
    def has_any(cls, annotations: dict | None) -> bool:
        """是否有任意激活的异常标识。"""
        if not annotations:
            return False
        return any(entry.get("active", False) for entry in annotations.values())


# ====================================================================
# 内置类型注册（模块导入时自动执行）
# ====================================================================

# 1. 导出/导入类接口
ApiAnnotationRegistry.register(ApiAnnotationDef(
    key="is_export",
    label="导出/导入类接口",
    description="返回二进制流（Excel/文件），JSON 路径断言失效，应校验 HTTP status_code",
    category="response",
    detector=lambda api: (
        any(kw in (api.get("url", "") + " " + api.get("name", "")).lower()
            for kw in ("export", "import", "template", "download", "upload")),
        None,
    ),
))

# 2. RESTful 路径参数接口
ApiAnnotationRegistry.register(ApiAnnotationDef(
    key="has_path_params",
    label="RESTful 路径参数",
    description="URL 含 {xxx} 占位符，校验放行字面量模板，运行时替换为 ${get_extract_data(xxx)}",
    category="request",
    detector=lambda api: (
        bool(re.findall(r'\{(\w+)\}', api.get("url", ""))),
        {"path_params": re.findall(r'\{(\w+)\}', api.get("url", ""))}
        if re.findall(r'\{(\w+)\}', api.get("url", "")) else None,
    ),
))

# 3. 预留扩展点（以下示例在实际遇到问题时激活）
#
# ApiAnnotationRegistry.register(ApiAnnotationDef(
#     key="paginated_get_with_body",
#     label="GET 分页查询含请求体",
#     description="部分后端实现要求 GET 分页查询通过 JSON body 传递分页参数",
#     category="request",
#     detector=lambda api: (
#         api.get("method", "").lower() == "get" and
#         any(kw in (api.get("url", "") + " " + api.get("name", "")).lower()
#             for kw in ("getpage", "page", "list", "query", "search")),
#         None,
#     ),
# ))
