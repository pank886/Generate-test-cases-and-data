"""绑定关系 CRUD 操作。"""

from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database.models import Binding, Document


class BindingOps:
    """绑定关系 CRUD。使用 normalize 防止 A→B + B→A。"""

    @staticmethod
    def bind(
        session: Session,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
    ) -> tuple[bool, str]:
        """建立绑定。自动防重。Returns: (success, message)"""
        if source_type == target_type and source_id == target_id:
            return False, "不能和自身绑定"

        lt, li, rt, ri = Binding.normalize(
            source_type, source_id, target_type, target_id,
        )

        existing = session.query(Binding).filter(
            Binding.left_type == lt,
            Binding.left_id == li,
            Binding.right_type == rt,
            Binding.right_id == ri,
        ).first()
        if existing:
            return False, f"绑定已存在: {lt}:{li} <-> {rt}:{ri}"

        binding = Binding(left_type=lt, left_id=li, right_type=rt, right_id=ri)
        session.add(binding)
        return True, "绑定成功"

    @staticmethod
    def unbind(session: Session, binding_id: int) -> bool:
        """解除绑定。"""
        binding = session.get(Binding, binding_id)
        if not binding:
            return False
        session.delete(binding)
        return True

    @staticmethod
    def unbind_by_pair(
        session: Session,
        a_type: str, a_id: str,
        b_type: str, b_id: str,
    ) -> bool:
        """通过双方标识解除绑定。"""
        lt, li, rt, ri = Binding.normalize(a_type, a_id, b_type, b_id)
        binding = session.query(Binding).filter(
            Binding.left_type == lt,
            Binding.left_id == li,
            Binding.right_type == rt,
            Binding.right_id == ri,
        ).first()
        if not binding:
            return False
        session.delete(binding)
        return True

    @staticmethod
    def get_bindings(
        session: Session,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> list[Binding]:
        """查询绑定。可按类型/ID 过滤。"""
        q = session.query(Binding)
        if entity_type and entity_id:
            q = q.filter(
                or_(
                    and_(Binding.left_type == entity_type, Binding.left_id == entity_id),
                    and_(Binding.right_type == entity_type, Binding.right_id == entity_id),
                )
            )
        elif entity_type:
            q = q.filter(
                or_(Binding.left_type == entity_type, Binding.right_type == entity_type)
            )
        return q.order_by(Binding.created_at).all()

    @staticmethod
    def get_partners(
        session: Session,
        entity_type: str,
        entity_id: str,
        partner_type: Optional[str] = None,
    ) -> list[tuple[str, str]]:
        """获取某实体的所有绑定对方。Returns: [(partner_type, partner_id), ...]"""
        bindings = BindingOps.get_bindings(session, entity_type, entity_id)
        partners = []
        for b in bindings:
            if b.left_type == entity_type and b.left_id == entity_id:
                partners.append((b.right_type, b.right_id))
            else:
                partners.append((b.left_type, b.left_id))
        if partner_type:
            partners = [(t, i) for t, i in partners if t == partner_type]
        return partners

    @staticmethod
    def get_partners_batch(
        session: Session,
        entity_type: str,
        entity_ids: list[str],
        partner_type: Optional[str] = None,
    ) -> dict[str, list[tuple[str, str]]]:
        """批量获取多个实体的绑定对方（一次 SQL 查询）。

        Returns: {entity_id: [(partner_type, partner_id), ...], ...}
        """
        if not entity_ids:
            return {}
        bindings = session.query(Binding).filter(
            or_(
                and_(Binding.left_type == entity_type, Binding.left_id.in_(entity_ids)),
                and_(Binding.right_type == entity_type, Binding.right_id.in_(entity_ids)),
            )
        ).all()
        result: dict[str, list[tuple[str, str]]] = {eid: [] for eid in entity_ids}
        for b in bindings:
            if b.left_type == entity_type and b.left_id in result:
                p = (b.right_type, b.right_id)
                if not partner_type or p[0] == partner_type:
                    result[b.left_id].append(p)
            if b.right_type == entity_type and b.right_id in result:
                p = (b.left_type, b.left_id)
                if not partner_type or p[0] == partner_type:
                    result[b.right_id].append(p)
        return result

    @staticmethod
    def delete_bindings_for_doc(session: Session, doc_id: str):
        """删除所有与某文档相关的绑定。"""
        session.query(Binding).filter(
            or_(
                and_(Binding.left_type.in_(["product", "api", "axure"]), Binding.left_id == doc_id),
                and_(Binding.right_type.in_(["product", "api", "axure"]), Binding.right_id == doc_id),
            )
        ).delete()

    @staticmethod
    def get_bound_docs(
        session: Session, module_name: str
    ) -> list[Document]:
        """获取绑定到指定模块的所有文档。"""
        doc_types = ("product", "api", "axure")
        bound_ids = set()
        for b in BindingOps.get_bindings(session, "module", module_name):
            if b.left_type == "module":
                if b.right_type in doc_types:
                    bound_ids.add(b.right_id)
            else:
                if b.left_type in doc_types:
                    bound_ids.add(b.left_id)
        if not bound_ids:
            return []
        return (
            session.query(Document)
            .filter(Document.id.in_(bound_ids))
            .all()
        )


def discover_related_modules(session: Session, module_name: str) -> list[str]:
    """发现模块的关联模块（绑定图三路召回，跳过自身，去重排序）。

    三路召回策略（与 Phase B 节点 extract_related_modules 同源）：
      1. module↔module 直接绑定 —— 模块之间的显式关联
      2. product/axure 文档 → 其他模块 —— 文档被多个模块共享
      3. API 文档 → 其他模块 —— API 被多个模块引用

    直接查询 SQLite 绑定关系，不依赖 ChromaDB 检索结果（不完整）。

    2026-08-20：从 retrievers._extract_related_modules 抽取为共享函数，
    Phase B（retrievers）与 Phase C（web/tasks）复用同一关联模块口径；
    后续「语义检索」增强关联模块发现的挂钩点即此函数。
    """
    related: set[str] = set()
    if not module_name:
        return []

    # 路径 1：module↔module 直接绑定
    mod_partners = BindingOps.get_partners(
        session, "module", module_name, partner_type="module",
    )
    for _ptype, pname in mod_partners:
        if pname and pname != module_name:
            related.add(pname)

    # 路径 2+3：通过模块下所有类型文档查找关联模块
    bound_docs = BindingOps.get_bound_docs(session, module_name)
    product_ids = [d.id for d in bound_docs if d.doc_type == "product"]
    axure_ids = [d.id for d in bound_docs if d.doc_type == "axure"]
    api_ids = [d.id for d in bound_docs if d.doc_type == "api"]
    for doc_type, doc_ids in (("product", product_ids),
                              ("axure", axure_ids),
                              ("api", api_ids)):
        if not doc_ids:
            continue
        results = BindingOps.get_partners_batch(
            session, doc_type, doc_ids, partner_type="module",
        )
        for _doc_id, partners in results.items():
            for _ptype, pname in partners:
                if pname and pname != module_name:
                    related.add(pname)

    return sorted(related)
