"""文档 CRUD 操作。"""

from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database.models import Document, Binding


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
        """删除文档（glossary 通过 DB 级联删除，bindings 显式清理）。"""
        doc = session.get(Document, doc_id)
        if not doc:
            return False
        from database.operations.bindings import BindingOps
        BindingOps.delete_bindings_for_doc(session, doc_id)
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
