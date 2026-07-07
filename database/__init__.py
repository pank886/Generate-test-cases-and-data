"""数据层：SQLAlchemy 引擎与会话管理。"""
import os
from datetime import datetime
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

_ENGINE = None
_SESSION_LOCAL = None
Base = declarative_base()

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "app.db")


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        os.makedirs(DB_DIR, exist_ok=True)
        _ENGINE = create_engine(
            f"sqlite:///{DB_PATH}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        # 启用 WAL 模式 + 外键约束
        @event.listens_for(_ENGINE, "connect")
        def _set_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return _ENGINE


def get_session():
    global _SESSION_LOCAL
    if _SESSION_LOCAL is None:
        _SESSION_LOCAL = sessionmaker(bind=get_engine())
    return _SESSION_LOCAL()


def init_db():
    """创建所有表（幂等：多次调用安全）。首次运行时从 modules.json 导入种子数据。"""
    # 确保模型类已注册到 Base.metadata
    import database.models  # noqa: F401
    Base.metadata.create_all(get_engine())
    _seed_from_json_if_empty()


def _seed_from_json_if_empty():
    """如果 modules 表为空，从 data/modules.json 导入种子数据（一次性迁移）。"""
    import json
    from database.models import Module

    session = get_session()
    try:
        if session.query(Module).count() > 0:
            return  # 已有数据，跳过

        json_path = os.path.join(DB_DIR, "modules.json")
        if not os.path.exists(json_path):
            return

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        modules_data = data.get("modules", [])
        if not modules_data:
            return

        # old_id → new_id 映射（用于重建 parent_id 关系）
        id_map = {}
        # 先创建所有模块节点
        for mod in sorted(modules_data, key=lambda m: m.get("path", "")):
            new_mod = Module(
                name=mod["name"],
                parent_id=None,  # 先置空，第二轮修复
                path=mod.get("path", "/" + mod["name"]),
            )
            session.add(new_mod)
            session.flush()  # 获取生成的 id
            id_map[mod["id"]] = new_mod.id

        # 第二轮：修复 parent_id
        for mod in modules_data:
            if mod.get("parent_id") and mod["parent_id"] in id_map:
                new_id = id_map[mod["id"]]
                session.query(Module).filter(Module.id == new_id).update(
                    {"parent_id": id_map[mod["parent_id"]]}
                )

        session.commit()
    finally:
        session.close()


def drop_db():
    """删除所有表（仅测试用）。"""
    Base.metadata.drop_all(get_engine())
