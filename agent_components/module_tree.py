"""模块目录树管理。

支持树形结构（parent_id 邻接表）、增删改查、名称变更级联更新向量库 metadata。
存储方式：JSON 文件（后续可替换为数据库）。
"""

import json
import os
import uuid
from datetime import datetime

_MODULE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "modules.json")


def _ensure_file():
    """确保模块数据文件存在。"""
    os.makedirs(os.path.dirname(_MODULE_FILE), exist_ok=True)
    if not os.path.exists(_MODULE_FILE):
        default = {
            "version": 1,
            "modules": [
                {
                    "id": "root",
                    "name": "全部模块",
                    "parent_id": None,
                    "path": "/",
                    "created_at": datetime.now().isoformat(),
                }
            ],
        }
        with open(_MODULE_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    with open(_MODULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    with open(_MODULE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==================== 查询 ====================

def get_all() -> list:
    """获取所有模块列表（扁平）。"""
    data = _ensure_file()
    return data["modules"]


def get_tree() -> list:
    """获取树形结构。"""
    modules = get_all()
    children_map = {}
    for mod in modules:
        pid = mod.get("parent_id")
        if pid not in children_map:
            children_map[pid] = []
        children_map[pid].append(mod)

    def build(node):
        node["children"] = build_tree(children_map.get(node["id"], []))
        return node

    def build_tree(nodes):
        return [build(n) for n in sorted(nodes, key=lambda x: x.get("name", ""))]

    roots = children_map.get(None, [])
    return [build(r) for r in sorted(roots, key=lambda x: x.get("name", ""))]


def get_by_id(module_id: str) -> dict:
    """按 ID 获取模块。"""
    for mod in get_all():
        if mod["id"] == module_id:
            return mod
    return None


def get_by_name(name: str) -> dict:
    """按名称获取模块。"""
    for mod in get_all():
        if mod["name"] == name:
            return mod
    return None


def get_descendants(module_id: str) -> list:
    """获取模块的所有后代（含自身）。"""
    modules = get_all()
    children_map = {}
    for mod in modules:
        pid = mod.get("parent_id")
        children_map.setdefault(pid, []).append(mod)

    result = []

    def collect(mid):
        result.append(mid)
        for child in children_map.get(mid, []):
            collect(child["id"])

    collect(module_id)
    return result


def path_of(module_id: str) -> str:
    """获取模块的完整路径。"""
    mod = get_by_id(module_id)
    if not mod:
        return ""
    if mod.get("parent_id"):
        parent = get_by_id(mod["parent_id"])
        parent_path = path_of(parent["id"]) if parent else ""
        return parent_path + "/" + mod["name"]
    return mod["name"]


# ==================== 增删改 ====================

def create(name: str, parent_id: str = "root") -> dict:
    """创建模块。"""
    data = _ensure_file()
    new_id = str(uuid.uuid4())[:8]
    module = {
        "id": new_id,
        "name": name,
        "parent_id": parent_id,
        "path": "",
        "created_at": datetime.now().isoformat(),
    }
    module["path"] = path_of(new_id) or name
    data["modules"].append(module)
    _save(data)
    return module


def rename(module_id: str, new_name: str):
    """重命名模块，级联更新向量库中所有 chunks 的 metadata.module。"""
    data = _ensure_file()
    for mod in data["modules"]:
        if mod["id"] == module_id:
            old_name = mod["name"]
            mod["name"] = new_name
            mod["path"] = path_of(module_id)
            _save(data)
            # 级联更新向量库
            _update_vector_metadata(old_name, new_name)
            # 递归更新子模块路径
            _refresh_paths(data)
            return {"old_name": old_name, "new_name": new_name}
    return None


def delete(module_id: str):
    """删除模块（非叶子节点禁止删除，除非 force）。"""
    if module_id == "root":
        raise ValueError("不能删除根节点")
    data = _ensure_file()
    descendants = get_descendants(module_id)
    if len(descendants) > 1:
        raise ValueError(f"模块包含子模块，请先删除子模块")
    data["modules"] = [m for m in data["modules"] if m["id"] != module_id]
    _save(data)


def merge(source_id: str, target_id: str):
    """合并模块：将 source 下所有文档重映射到 target，删除 source。"""
    source = get_by_id(source_id)
    target = get_by_id(target_id)
    if not source or not target:
        raise ValueError("模块不存在")

    # 重映射向量库 metadata
    _update_vector_metadata(source["name"], target["name"])

    # 迁移子模块
    data = _ensure_file()
    for mod in data["modules"]:
        if mod.get("parent_id") == source_id:
            mod["parent_id"] = target_id

    # 删除源模块
    data["modules"] = [m for m in data["modules"] if m["id"] != source_id]
    _refresh_paths(data)
    _save(data)


def _refresh_paths(data: dict):
    """刷新所有模块的 path 字段。"""
    def resolve_pid(pid):
        for m in data["modules"]:
            if m["id"] == pid:
                return m
        return None

    def calc_path(mod):
        if mod["id"] == "root":
            return "/"
        parent = resolve_pid(mod.get("parent_id"))
        if parent:
            return calc_path(parent) + "/" + mod["name"] if calc_path(parent) != "/" else "/" + mod["name"]
        return "/" + mod["name"]

    for mod in data["modules"]:
        mod["path"] = calc_path(mod)


def _update_vector_metadata(old_name: str, new_name: str):
    """级联更新向量库中所有 metadata.module 的旧名为新名。"""
    try:
        from agent_components.dual_chroma import DualChromaDB
        db = DualChromaDB()

        # product_docs 集合
        old_docs = db.product_store.get(where={"module": old_name})
        if old_docs and old_docs.get("ids"):
            db.product_store.update(
                ids=old_docs["ids"],
                metadatas=[{"module": new_name}] * len(old_docs["ids"]),
            )

        # api_defs 集合
        old_apis = db.api_store.get(where={"module": old_name})
        if old_apis and old_apis.get("ids"):
            db.api_store.update(
                ids=old_apis["ids"],
                metadatas=[{"module": new_name}] * len(old_apis["ids"]),
            )
    except Exception as e:
        print(f"   ⚠️ 向量库 metadata 更新失败: {e}")


# ==================== CLI 测试 ====================

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "create":
        m = create(sys.argv[2])
        print(f"创建模块: {m}")
    elif len(sys.argv) >= 3 and sys.argv[1] == "rename":
        mod = get_by_name(sys.argv[2])
        if mod:
            r = rename(mod["id"], sys.argv[3])
            print(f"重命名: {r}")
    elif len(sys.argv) >= 2 and sys.argv[1] == "list":
        import json as _j
        print(_j.dumps(get_tree(), ensure_ascii=False, indent=2))
    else:
        print("用法: python module_tree.py <create|rename|list> [参数]")
