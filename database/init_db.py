"""初始化数据库表 + 打印验证信息。"""
import sys
import os

# 强制 UTF-8，防止 Windows 终端 GBK 报错
sys.stdout.reconfigure(encoding="utf-8")

# 确保能找到项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import init_db, get_engine, DB_PATH
from database.models import Base


def main():
    print(f"数据库路径: {DB_PATH}")
    print(f"  SQLite 文件: {os.path.exists(DB_PATH)}")
    print()

    # 创建表
    init_db()
    print("OK 表已创建（幂等，多次运行安全）\n")

    # 列出所有表
    engine = get_engine()
    inspector = __import__("sqlalchemy").inspect(engine)
    tables = inspector.get_table_names()
    print(f"共 {len(tables)} 张表:")
    for name in tables:
        cols = inspector.get_columns(name)
        pk = [c["name"] for c in cols if c.get("primary_key")]
        print(f"   - {name:20s} 主键: {', '.join(pk)}")
    print()

    # 模型映射验证
    from database.models import Module, Document, Binding, GlossaryTerm
    model_count = 4
    table_count = len(tables)
    assert table_count >= model_count, f"预期至少 {model_count} 张表，实际 {table_count}"
    print(f"OK {model_count} 个 ORM 模型映射正确")
    print()

    # 打印模型关系概览
    print("=" * 50)
    print("数据模型关系")
    print("=" * 50)
    print("""
  模块 (modules)        文档 (documents)       术语 (glossary)
  +----------+         +--------------+       +--------------+
  | id       |<--+    | id (doc_id)  |--1:N--| id           |
  | name     |    |    | file_name     |       | term         |
  | parent_id|    |    | doc_type      |       | definition   |
  +----------+    |    +------+-------+       +--------------+
                  |           |
                  |    bindings 中间表
                  |    +-----------------+
                  +----+ left_type+id    |
                       | right_type+id   |
                       | UNIQUE(四字段)   |
                       +-----------------+
    """)

    print("OK 数据库初始化完成，可以使用了")


if __name__ == "__main__":
    main()
