"""数据库 CRUD 操作封装。

覆盖模块 / 文档 / 绑定关系 / 术语表的增删改查。
统一使用 SQLAlchemy session，异常时由调用方决定 commit/rollback。

用法:
    from database.operations import DocOps, ModuleOps, BindingOps, GlossaryOps

    with get_session() as session:
        doc = DocOps.add_document(session, ...)
        session.commit()
"""

from typing import Optional
from datetime import datetime, timezone

from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from database.models import (
    Module, Document, Binding, GlossaryTerm,
)


# ========================================================================
# 文档操作
# ========================================================================

class DocOps:
    """文档 CRUD。"""

    @staticmethod
    def add_document(
        session: Session,
        doc_id: str,
        file_name: str,
        file_type: str,
        doc_type: str,
        chunk_count: int = 0,
        status: str = "pending",
    ) -> Document:
        """添加上传文档记录。"""
        doc = Document(
            id=doc_id,
            file_name=file_name,
            file_type=file_type,
            doc_type=doc_type,
            chunk_count=chunk_count,
            status=status,
        )
        session.add(doc)
        return doc

    @staticmethod
    def get_document(session: Session, doc_id: str) -> Optional[Document]:
        """按 ID 查询文档。"""
        return session.get(Document, doc_id)

    @staticmethod
    def get_all_documents(
        session: Session,
        doc_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[Document]:
        """查询文档列表，可按 doc_type / status 过滤。"""
        q = session.query(Document)
        if doc_type:
            q = q.filter(Document.doc_type == doc_type)
        if status:
            q = q.filter(Document.status == status)
        return q.order_by(Document.upload_time.desc()).all()

    @staticmethod
    def update_document(session: Session, doc_id: str, **kwargs) -> Optional[Document]:
        """更新文档字段（如 status, chunk_count）。"""
        doc = session.get(Document, doc_id)
        if not doc:
            return None
        for k, v in kwargs.items():
            if hasattr(doc, k):
                setattr(doc, k, v)
        return doc

    @staticmethod
    def delete_document(session: Session, doc_id: str) -> bool:
        """删除文档（glossary 通过 DB 级联删除，bindings 不自动删除）。"""
        doc = session.get(Document, doc_id)
        if not doc:
            return False
        session.delete(doc)
        return True

    @staticmethod
    def get_unassociated_docs(session: Session) -> list[Document]:
        """获取未绑定任何模块的文档。"""
        doc_types = ("product", "api", "axure")
        all_ids = {r[0] for r in session.query(Document.id).filter(
            Document.doc_type.in_(doc_types)).all()}
        bound_ids = {r[0] for r in session.query(Binding.left_id).filter(
            Binding.left_type.in_(doc_types), Binding.right_type == "module",
        ).union(session.query(Binding.right_id).filter(
            Binding.right_type.in_(doc_types), Binding.left_type == "module",
        )).all()}
        unassociated_ids = all_ids - bound_ids
        if not unassociated_ids:
            return []
        return (
            session.query(Document)
            .filter(Document.id.in_(unassociated_ids))
            .order_by(Document.upload_time.desc())
            .all()
        )


# ========================================================================
# 模块操作
# ========================================================================

class ModuleOps:
    """模块树 CRUD。"""

    @staticmethod
    def create_module(
        session: Session,
        name: str,
        parent_id: Optional[str] = None,
    ) -> Module:
        """创建模块。parent_id=None 则为根节点。"""
        mod = Module(name=name, parent_id=parent_id)
        session.add(mod)
        session.flush()  # 获取 id
        mod.path = ModuleOps._calc_path(session, mod)
        return mod

    @staticmethod
    def get_by_id(session: Session, module_id: str) -> Optional[Module]:
        return session.get(Module, module_id)

    @staticmethod
    def get_by_name(session: Session, name: str) -> Optional[Module]:
        return session.query(Module).filter(Module.name == name).first()

    @staticmethod
    def get_all(session: Session) -> list[Module]:
        return session.query(Module).order_by(Module.name).all()

    @staticmethod
    def get_tree(session: Session, parent_id: Optional[str] = None) -> list[dict]:
        """获取树形结构（递归构建）。"""
        if parent_id is None:
            roots = session.query(Module).filter(Module.parent_id == None).all()
        else:
            roots = session.query(Module).filter(Module.parent_id == parent_id).all()

        tree = []
        for m in sorted(roots, key=lambda x: x.name or ""):
            node = {
                "id": m.id,
                "name": m.name,
                "parent_id": m.parent_id,
                "path": m.path,
                "children": ModuleOps.get_tree(session, m.id),
            }
            tree.append(node)
        return tree

    @staticmethod
    def rename_module(
        session: Session, module_id: str, new_name: str
    ) -> tuple[bool, str]:
        """重命名模块。

        注意：模块名是 bindings 中的关联键。
        因此需要同步更新 bindings 表中所有引用该名称的记录。
        Returns: (success, message)
        """
        mod = session.get(Module, module_id)
        if not mod:
            return False, "模块不存在"
        old_name = mod.name
        mod.name = new_name
        mod.path = ModuleOps._calc_path(session, mod)

        # 同步更新 bindings 中所有引用旧名的记录
        for binding in session.query(Binding).filter(
            or_(
                and_(Binding.left_type == "module", Binding.left_id == old_name),
                and_(Binding.right_type == "module", Binding.right_id == old_name),
            )
        ).all():
            if binding.left_type == "module" and binding.left_id == old_name:
                binding.left_id = new_name
            if binding.right_type == "module" and binding.right_id == old_name:
                binding.right_id = new_name

        # 更新子模块路径
        ModuleOps._refresh_paths(session)
        return True, f"{old_name} -> {new_name}"

    @staticmethod
    def delete_module(session: Session, module_id: str) -> tuple[bool, str]:
        """删除模块。非叶子节点禁止删除。"""
        if module_id == "root":
            return False, "不能删除根节点"

        mod = session.get(Module, module_id)
        if not mod:
            return False, "模块不存在"

        # 检查是否有子模块
        children_count = session.query(Module).filter(
            Module.parent_id == module_id
        ).count()
        if children_count > 0:
            return False, "模块包含子模块，请先删除子模块"

        # 删除相关绑定
        module_name = mod.name
        session.query(Binding).filter(
            or_(
                and_(Binding.left_type == "module", Binding.left_id == module_name),
                and_(Binding.right_type == "module", Binding.right_id == module_name),
            )
        ).delete()

        session.delete(mod)
        return True, "已删除"

    @staticmethod
    def merge_modules(
        session: Session, source_id: str, target_id: str
    ) -> tuple[bool, str]:
        """合并模块：source 的绑定关系和子模块迁移到 target，删除 source。"""
        source = session.get(Module, source_id)
        target = session.get(Module, target_id)
        if not source:
            return False, "源模块不存在"
        if not target:
            return False, "目标模块不存在"

        # 迁移 bindings 中 source 名的记录到 target 名
        for binding in session.query(Binding).filter(
            or_(
                and_(Binding.left_type == "module", Binding.left_id == source.name),
                and_(Binding.right_type == "module", Binding.right_id == source.name),
            )
        ).all():
            if binding.left_type == "module" and binding.left_id == source.name:
                binding.left_id = target.name
            if binding.right_type == "module" and binding.right_id == source.name:
                binding.right_id = target.name

        # 迁移子模块
        session.query(Module).filter(Module.parent_id == source_id).update(
            {"parent_id": target_id}
        )

        session.delete(source)
        ModuleOps._refresh_paths(session)
        return True, f"已合并到 {target.name}"

    # ---- 内部辅助 ----

    @staticmethod
    def _calc_path(session: Session, mod: Module) -> str:
        """计算模块的完整路径。"""
        if not mod.parent_id:
            return f"/{mod.name}" if mod.name != "全部模块" else "/"
        parent = session.get(Module, mod.parent_id)
        if parent:
            parent_path = ModuleOps._calc_path(session, parent)
            base = "" if parent_path == "/" else parent_path
            return f"{base}/{mod.name}"
        return f"/{mod.name}"

    @staticmethod
    def _refresh_paths(session: Session):
        """刷新所有模块路径。_calc_path 沿 parent 链递归，单轮遍历即可。"""
        for m in session.query(Module).all():
            m.path = ModuleOps._calc_path(session, m)

    @staticmethod
    def get_descendants(session: Session, module_id: str) -> list[str]:
        """递归获取某模块的所有子孙节点 ID（含自身）。"""
        result = [module_id]
        children = session.query(Module).filter(Module.parent_id == module_id).all()
        for c in children:
            result.extend(ModuleOps.get_descendants(session, c.id))
        return result


# ========================================================================
# 绑定关系操作
# ========================================================================

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
        """建立绑定。自动防重。

        Returns: (success, message)
        """
        if source_type == target_type and source_id == target_id:
            return False, "不能和自身绑定"

        # normalize 排序
        lt, li, rt, ri = Binding.normalize(
            source_type, source_id, target_type, target_id,
        )

        # 检查是否已存在
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
        """获取某实体的所有绑定对方。

        Returns: [(partner_type, partner_id), ...]
        """
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
    def delete_bindings_for_doc(session: Session, doc_id: str):
        """删除所有与某文档相关的绑定。"""
        session.query(Binding).filter(
            or_(
                and_(Binding.left_type.in_(["product", "api", "axure"]), Binding.left_id == doc_id),
                and_(Binding.right_type.in_(["product", "api", "axure"]), Binding.right_id == doc_id),
            )
        ).delete()

    @staticmethod
    def delete_bindings_for_module(session: Session, module_name: str):
        """删除所有与某模块相关的绑定。"""
        session.query(Binding).filter(
            or_(
                and_(Binding.left_type == "module", Binding.left_id == module_name),
                and_(Binding.right_type == "module", Binding.right_id == module_name),
            )
        ).delete()

    @staticmethod
    def delete_bindings_between_docs(session: Session, doc_ids: list[str]):
        """删除指定文档之间的所有 doc↔doc 绑定（SQL 直接过滤，避免全表扫描）。"""
        if len(doc_ids) < 2:
            return
        doc_types = ("product", "api", "axure")
        session.query(Binding).filter(
            Binding.left_type.in_(doc_types),
            Binding.right_type.in_(doc_types),
            Binding.left_id.in_(doc_ids),
            Binding.right_id.in_(doc_ids),
        ).delete(synchronize_session=False)

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


# ========================================================================
# 术语表操作
# ========================================================================

class GlossaryOps:
    """术语表 CRUD。术语始终随产品文档生命周期。"""

    @staticmethod
    def add_term(
        session: Session,
        doc_id: str,
        term: str,
        definition: str = "",
        notes: str = "",
        source_doc: str = "",
    ) -> GlossaryTerm:
        """添加术语。自动提取的传 source_doc=文件名，手动追加传空。"""
        gt = GlossaryTerm(
            doc_id=doc_id,
            term=term,
            definition=definition,
            notes=notes,
            source_doc=source_doc,
        )
        session.add(gt)
        return gt

    @staticmethod
    def update_term(
        session: Session,
        term_id: int,
        term: Optional[str] = None,
        definition: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[GlossaryTerm]:
        """修改术语。"""
        gt = session.get(GlossaryTerm, term_id)
        if not gt:
            return None
        if term is not None:
            gt.term = term
        if definition is not None:
            gt.definition = definition
        if notes is not None:
            gt.notes = notes
        return gt

    @staticmethod
    def delete_term(session: Session, term_id: int) -> bool:
        """删除单条术语。"""
        gt = session.get(GlossaryTerm, term_id)
        if not gt:
            return False
        session.delete(gt)
        return True

    @staticmethod
    def get_terms(session: Session, doc_id: str) -> list[GlossaryTerm]:
        """获取某文档的所有术语。"""
        return (
            session.query(GlossaryTerm)
            .filter(GlossaryTerm.doc_id == doc_id)
            .order_by(GlossaryTerm.created_at)
            .all()
        )

    @staticmethod
    def replace_terms(
        session: Session,
        doc_id: str,
        terms: list[dict],
        source_doc: str = "",
    ):
        """批量替换文档的术语（先删旧术语，再插入新术语）。

        terms: [{"term": ..., "definition": ..., "notes": ...}, ...]
        """
        # 只删指定来源的旧术语（自动提取的 term 有 source_doc）
        if source_doc:
            session.query(GlossaryTerm).filter(
                GlossaryTerm.doc_id == doc_id,
                GlossaryTerm.source_doc == source_doc,
            ).delete()
        else:
            # 没有 source_doc 则全删（一般用于初始导入）
            session.query(GlossaryTerm).filter(
                GlossaryTerm.doc_id == doc_id,
            ).delete()

        for t in terms:
            session.add(GlossaryTerm(
                doc_id=doc_id,
                term=t.get("term", t.get("name", "?")),
                definition=t.get("definition", ""),
                notes=t.get("notes", ""),
                source_doc=source_doc or t.get("source_doc", ""),
            ))

    @staticmethod
    def get_terms_for_module(session: Session, module_name: str) -> list[GlossaryTerm]:
        """获取模块下所有绑定文档的术语（聚合视图）。"""
        doc_ids = [d.id for d in BindingOps.get_bound_docs(session, module_name)]
        if not doc_ids:
            return []
        return (
            session.query(GlossaryTerm)
            .filter(GlossaryTerm.doc_id.in_(doc_ids))
            .order_by(GlossaryTerm.created_at)
            .all()
        )
