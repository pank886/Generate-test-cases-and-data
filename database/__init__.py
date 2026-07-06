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
    """创建所有表（幂等：多次调用安全）。"""
    Base.metadata.create_all(get_engine())


def drop_db():
    """删除所有表（仅测试用）。"""
    Base.metadata.drop_all(get_engine())
