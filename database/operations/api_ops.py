"""API 文档查询操作（D2：按 url 从 SQLite 取接口详情，单一事实源）。

参照 DocOps 模式：static method + Session + Document 模型。
L3 段级模板回退：精确比对失败后，库中 ``{param}`` 段为通配。
"""

import json
import logging

from sqlalchemy.orm import Session

from database.models import Document

logger = logging.getLogger(__name__)


def _deserialize(doc) -> dict:
    """Document -> 接口详情 dict（api_* 列反序列化）。"""
    return {
        "api_name": doc.api_name,
        "api_url": doc.api_url,
        "api_method": doc.api_method,
        "api_headers": json.loads(doc.api_headers or "[]"),
        "api_parameters": json.loads(doc.api_parameters or "[]"),
        "api_returns": json.loads(doc.api_returns or "[]"),
        "api_annotations": json.loads(doc.api_annotations or "{}"),
    }


def _template_match(template: str, concrete: str) -> bool:
    """段级模板比对：template 中 ``{param}`` 段为通配，字面段必须相等。

    例: template=/order/{id}，concrete=/order/123 → True
        template=/order/export，concrete=/order/123 → False
    """
    t = template.split("/")
    c = concrete.split("/")
    if len(t) != len(c):
        return False
    for tp, cp in zip(t, c):
        if "{" in tp and "}" in tp:
            continue  # 通配段
        if tp != cp:
            return False
    return True


class ApiOps:
    """API 查询（doc_type='api'）。"""

    @staticmethod
    def get_by_url(session: Session, method: str, url: str) -> dict | None:
        """按 method + url 查接口详情。

        1. 精确：method case-insensitive + normalize_api_url 后等于库中 api_url
        2. L3 段级模板回退：精确失败后，库中 ``{param}`` 段为通配逐段比对——
           - 恰好一个吻合 → 命中；
           - 多个吻合（歧义）→ 日志列出全部候选 + 返回 None（上层跳过，宁缺毋滥）。

        返回反序列化 dict；查不到返回 None。
        """
        from agent_components.api_annotations import normalize_api_url
        norm_url = normalize_api_url(url)
        m = (method or "").strip().upper()

        exact = (
            session.query(Document)
            .filter(Document.doc_type == "api",
                    Document.api_method == m,
                    Document.api_url == norm_url)
            .first()
        )
        if exact:
            return _deserialize(exact)

        # L3: 段级模板回退（仅遍历同 method 的 api 文档）
        candidates = (
            session.query(Document)
            .filter(Document.doc_type == "api",
                    Document.api_method == m)
            .all()
        )
        hits = [d for d in candidates if _template_match(d.api_url or "", norm_url)]
        if len(hits) == 1:
            return _deserialize(hits[0])
        if len(hits) > 1:
            logger.warning(
                "接口 url 模板歧义，全部候选列出（不生成）: %s -> %s",
                norm_url, [d.api_url for d in hits])
        return None
