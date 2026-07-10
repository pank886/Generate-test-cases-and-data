"""模块目录树管理。

已从 JSON 文件存储迁移到 SQLite（database/operations.py）。
所有函数委托给 SQLite 操作。

Session 管理铁律 [A1]：
  所有函数强制要求调用方传入 session，内部不创建、不提交、不关闭。
  事务控制权完全上交调用方（API / Service 层）。
"""

from observability import get_logger

logger = get_logger(__name__)


# ==================== 查询 ====================

def get_all(session) -> list:
    """获取所有模块列表（扁平）。返回 dict 列表兼容旧格式。"""
    from database.operations import ModuleOps
    modules = ModuleOps.get_all(session)
    return [
        {"id": m.id, "name": m.name, "parent_id": m.parent_id, "path": m.path,
         "created_at": m.created_at.isoformat() if m.created_at else ""}
        for m in modules
    ]


def get_tree(session) -> list:
    """获取树形结构。返回 dict 兼容旧格式。"""
    from database.operations import ModuleOps
    return ModuleOps.get_tree(session)


def get_by_id(module_id: str, session) -> dict | None:
    """按 ID 获取模块。"""
    from database.operations import ModuleOps
    m = ModuleOps.get_by_id(session, module_id)
    if not m:
        return None
    return {"id": m.id, "name": m.name, "parent_id": m.parent_id, "path": m.path,
            "created_at": m.created_at.isoformat() if m.created_at else ""}


def get_by_name(name: str, session) -> dict | None:
    """按名称获取模块。"""
    from database.operations import ModuleOps
    m = ModuleOps.get_by_name(session, name)
    if not m:
        return None
    return {"id": m.id, "name": m.name, "parent_id": m.parent_id, "path": m.path,
            "created_at": m.created_at.isoformat() if m.created_at else ""}


def get_descendants(module_id: str, session) -> list:
    """获取模块的所有后代 ID（含自身）。"""
    from database.operations import ModuleOps
    return ModuleOps.get_descendants(session, module_id)


def path_of(module_id: str, session) -> str:
    """获取模块的完整路径。"""
    mod = get_by_id(module_id, session)
    return mod["path"] if mod else ""


# ==================== 增删改 ====================

def create(name: str, session, parent_id: str = "root") -> dict:
    """创建模块。调用方需自行控制 commit/rollback。

    parent_id="root" 会解析为实际根模块 ID（JSON 时代遗留兼容）。
    """
    from database.operations import ModuleOps

    # 获取根模块的实际 ID（"root" 是 JSON 时代的遗留 ID）
    actual_parent_id = parent_id
    if parent_id == "root":
        root = ModuleOps.get_by_name(session, "全部模块")
        if root:
            actual_parent_id = root.id

    m = ModuleOps.create_module(session, name, actual_parent_id)
    return {"id": m.id, "name": m.name, "parent_id": m.parent_id, "path": m.path}


def rename(module_id: str, new_name: str, session) -> tuple[bool, str]:
    """重命名模块（自动同步 bindings 中的模块名）。调用方需自行控制 commit/rollback。"""
    from database.operations import ModuleOps
    return ModuleOps.rename_module(session, module_id, new_name)


def delete(module_id: str, session):
    """删除模块。先检查约束，再执行级联解绑。调用方需自行控制 commit/rollback。"""
    from database.operations import ModuleOps, BindingOps
    from database.models import Module

    mod = ModuleOps.get_by_id(session, module_id)
    if not mod:
        raise ValueError("模块不存在")
    if mod.name == "全部模块":
        raise ValueError("不能删除根节点")

    # 先检查是否有子模块（避免先做清理再回滚）
    children = session.query(Module).filter(Module.parent_id == module_id).count()
    if children > 0:
        raise ValueError("模块包含子模块，请先删除子模块")

    # 1. 找出该模块下所有绑定的文档
    bound_docs = BindingOps.get_bound_docs(session, mod.name)
    bound_doc_ids = [d.id for d in bound_docs]

    # 2. 解除这些文档之间的所有 doc↔doc 绑定
    BindingOps.delete_bindings_between_docs(session, bound_doc_ids)

    # 3. 解除这些文档与模块的 doc↔module 绑定
    for doc in bound_docs:
        BindingOps.unbind_by_pair(session, "module", mod.name, doc.doc_type, doc.id)

    # 4. 解除模块与其他模块的绑定
    BindingOps.delete_bindings_for_module(session, mod.name)

    # 5. 删除模块本身
    session.delete(mod)


def merge(source_id: str, target_id: str, session) -> tuple[bool, str]:
    """合并模块：将 source 的绑定关系和子模块迁移到 target，删除 source。
    调用方需自行控制 commit/rollback。"""
    from database.operations import ModuleOps
    return ModuleOps.merge_modules(session, source_id, target_id)


# ==================== 术语表管理（已迁移到文档级别） ====================

def get_glossary(module_name: str, session) -> list[dict]:
    """获取模块下所有文档的术语（聚合视图）。"""
    from database.operations import GlossaryOps
    terms = GlossaryOps.get_terms_for_module(session, module_name)
    return [
        {"term": t.term, "definition": t.definition, "notes": t.notes,
         "source_doc": t.source_doc, "id": t.id}
        for t in terms
    ]


def get_glossary_by_doc(doc_id: str, session) -> list[dict]:
    """获取某文档的术语。"""
    from database.operations import GlossaryOps
    terms = GlossaryOps.get_terms(session, doc_id)
    return [
        {"term": t.term, "definition": t.definition, "notes": t.notes,
         "source_doc": t.source_doc, "id": t.id}
        for t in terms
    ]


def add_glossary_term(module_name: str, term: str, definition: str,
                      session, notes: str = "", doc_id: str = None) -> bool:
    """添加术语（需要指定 doc_id，或用模块下第一个产品文档）。
    调用方需自行控制 commit/rollback。"""
    from database.operations import GlossaryOps, BindingOps

    if not doc_id:
        # 找模块下第一个产品文档
        bound_docs = BindingOps.get_bound_docs(session, module_name)
        product_docs = [d for d in bound_docs if d.doc_type == "product"]
        if not product_docs:
            return False
        doc_id = product_docs[0].id

    # upsert: 有则更新，无则插入（避免删后崩溃术语丢失）
    existing = GlossaryOps.get_terms(session, doc_id)
    found = next((t for t in existing if t.term == term), None)
    if found:
        found.definition = definition
        found.notes = notes
    else:
        GlossaryOps.add_term(session, doc_id, term, definition, notes)
    return True


def delete_glossary_term(module_name: str, term: str, session) -> bool:
    """删除模块下某条术语（按名称匹配）。调用方需自行控制 commit/rollback。"""
    from database.operations import GlossaryOps

    all_terms = get_glossary(module_name, session)
    for t in all_terms:
        if t["term"] == term:
            GlossaryOps.delete_term(session, t["id"])
            return True
    return False
