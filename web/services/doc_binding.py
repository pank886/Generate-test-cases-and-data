"""文档-模块绑定服务：清理旧绑定 + 绑定新模块 + 级联关联。"""

from database.operations import BindingOps, DocOps


def rebind_doc_to_module(session, doc_id: str, new_module: str, *,
                         with_cascade: bool = True):
    """清理旧绑定 → 绑定新模块 → 可选级联。

    被以下端点复用：
    - update-module
    - change-doc-module
    - disassociate (new_module="" 即只清理不绑定)
    - delete-file（部分逻辑，已直接调 _cleanup_doc_to_doc_bindings）
    """
    doc = DocOps.get_document(session, doc_id)
    if doc is None:
        raise ValueError(f"文档不存在: doc_id={doc_id}")
    doc_type = doc.doc_type

    # 1. 清理旧 doc↔doc 级联绑定
    _cleanup_doc_to_doc_bindings(session, doc_id)

    # 2. 清理旧 doc↔module 绑定
    for b in BindingOps.get_bindings(session, doc_type, doc_id):
        if b.left_type == "module" or b.right_type == "module":
            BindingOps.unbind(session, b.id)

    # 3. 绑定新模块 + 可选级联
    if new_module:
        BindingOps.bind(session, doc_type, doc_id, "module", new_module)
        if with_cascade:
            from ingest_v2 import _cascade_bind_to_module_docs
            _cascade_bind_to_module_docs(session, doc_type, doc_id, new_module)


def _cleanup_doc_to_doc_bindings(session, doc_id: str):
    """删除指定文档的所有 doc↔doc 级联绑定。"""
    from database.models import Binding
    doc_types = ("product", "api", "axure")
    session.query(Binding).filter(
        Binding.left_type.in_(doc_types),
        Binding.right_type.in_(doc_types),
        ((Binding.left_id == doc_id) | (Binding.right_id == doc_id)),
    ).delete(synchronize_session='fetch')  # 'fetch' 确保 session 状态与 DB 一致
