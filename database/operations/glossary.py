"""术语表 CRUD 操作。"""

from typing import Optional

from sqlalchemy.orm import Session

from database.models import GlossaryTerm


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
        if source_doc:
            session.query(GlossaryTerm).filter(
                GlossaryTerm.doc_id == doc_id,
                GlossaryTerm.source_doc == source_doc,
            ).delete()
        else:
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
        from database.operations.bindings import BindingOps
        doc_ids = [d.id for d in BindingOps.get_bound_docs(session, module_name)]
        if not doc_ids:
            return []
        return (
            session.query(GlossaryTerm)
            .filter(GlossaryTerm.doc_id.in_(doc_ids))
            .order_by(GlossaryTerm.created_at)
            .all()
        )
