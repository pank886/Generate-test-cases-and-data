"""模块树 CRUD 操作。"""

from collections import deque
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database.models import Module, Binding


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
        """重命名模块。同步更新 bindings 中所有引用该名称的记录。

        Returns: (success, message)
        """
        mod = session.get(Module, module_id)
        if not mod:
            return False, "模块不存在"
        old_name = mod.name
        mod.name = new_name
        mod.path = ModuleOps._calc_path(session, mod)

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

        children_count = session.query(Module).filter(
            Module.parent_id == module_id
        ).count()
        if children_count > 0:
            return False, "模块包含子模块，请先删除子模块"

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
        """BFS 逐层计算所有模块路径，O(N) 避免递归 O(N×D)。"""
        roots = session.query(Module).filter(Module.parent_id.is_(None)).all()
        q = deque()
        for root in roots:
            root.path = "/" if root.name == "全部模块" else f"/{root.name}"
            q.append(root)

        while q:
            parent = q.popleft()
            base = "" if parent.path == "/" else parent.path
            for child in session.query(Module).filter(Module.parent_id == parent.id).all():
                child.path = f"{base}/{child.name}"
                q.append(child)

        session.flush()

    @staticmethod
    def get_descendants(session: Session, module_id: str) -> list[str]:
        """递归获取某模块的所有子孙节点 ID（含自身）。"""
        result = [module_id]
        children = session.query(Module).filter(Module.parent_id == module_id).all()
        for c in children:
            result.extend(ModuleOps.get_descendants(session, c.id))
        return result
