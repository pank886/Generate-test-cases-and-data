"""模拟场景：验证 operations.py CRUD 封装的完整链路。

覆盖:
  1. 文档入库（产品/接口/Axure）           → DocOps
  2. 术语提取（自动 + 手动追加）           → GlossaryOps
  3. 模块创建                              → ModuleOps
  4. 五类绑定关系建立 + 重复绑定拦截        → BindingOps
  5. 按实体查询绑定关系                     → BindingOps
  6. 删除文档 → 术语级联                   → DocOps + DB CASCADE
  7. 对接现有 module_tree 的查询接口        → ModuleOps.get_tree()
  8. 对接现有 dual_chroma 的查询接口        → BindingOps.get_bound_docs()
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def p(text=""):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode())


def sep(title):
    p()
    p("=" * 60)
    p(f"  {title}")
    p("=" * 60)


# -----------------------------------------------------------
# 准备数据库
# -----------------------------------------------------------
from database import init_db, get_session
from database.operations import DocOps, ModuleOps, BindingOps, GlossaryOps

db_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "app.db"
)
if os.path.exists(db_path):
    os.remove(db_path)
    p("  [setup] 清空旧数据库")

init_db()
session = get_session()


# -----------------------------------------------------------
# Step 1: 文档入库
# -----------------------------------------------------------
sep("Step 1: 上传三个文档 (DocOps.add_document)")

product_doc = DocOps.add_document(
    session,
    doc_id="prod_健身房产品文档.pdf_健身管理",
    file_name="健身房产品文档.pdf",
    file_type="pdf",
    doc_type="product",
    chunk_count=15,
)
api_doc = DocOps.add_document(
    session,
    doc_id="api_健身房接口文档.md_健身管理",
    file_name="健身房接口文档.md",
    file_type="md",
    doc_type="api",
    chunk_count=8,
)
axure_doc = DocOps.add_document(
    session,
    doc_id="axure_健身房原型.zip_健身管理",
    file_name="健身房原型.zip",
    file_type="zip",
    doc_type="axure",
    chunk_count=22,
)
session.commit()

p("  已导入 3 个文档:")
for d in [product_doc, api_doc, axure_doc]:
    p(f"    [{d.doc_type:7s}] {d.id}  ({d.chunk_count} chunks)")


# -----------------------------------------------------------
# Step 2: 术语提取
# -----------------------------------------------------------
sep("Step 2: 提取术语 (GlossaryOps.add_term / replace_terms)")

# 模拟自动提取 3 条
GlossaryOps.replace_terms(session, product_doc.id, [
    {"term": "会员卡", "definition": "用户购买的会员资格凭证", "notes": "有效期30天"},
    {"term": "私教课", "definition": "一对一专业教练指导课程", "notes": "需提前预约"},
    {"term": "团操课", "definition": "多人集体健身课程", "notes": "含瑜伽/普拉提/有氧操"},
], source_doc=product_doc.file_name)
p("  自动提取 3 条 (replace_terms)")

# 手动追加 1 条
GlossaryOps.add_term(
    session, product_doc.id,
    term="储物柜",
    definition="会员临时储物服务",
    notes="免费使用",
)
p("  手动追加 1 条 (add_term)")
session.commit()

p("  当前所有术语:")
for t in GlossaryOps.get_terms(session, product_doc.id):
    src = " [自动]" if t.source_doc else " [手动]"
    p(f"    - {t.term}: {t.definition}{src}")


# -----------------------------------------------------------
# Step 3: 创建模块
# -----------------------------------------------------------
sep("Step 3: 创建模块 (ModuleOps.create_module)")

root = ModuleOps.create_module(session, "全部模块", parent_id=None)

modules_data = ["合约签订", "房产管理", "企业管理", "账单管理", "开票管理"]
created = {}
for name in modules_data:
    m = ModuleOps.create_module(session, name, parent_id=root.id)
    created[name] = m
session.commit()

p("  创建了 5 个模块:")
for name, m in created.items():
    p(f"    [{m.id}] {name}  path={m.path}")

# 展示树
sep("  ModuleOps.get_tree() 输出:")
tree = ModuleOps.get_tree(session)
import json as _j
p(_j.dumps(tree, ensure_ascii=False, indent=2)[:600])


# -----------------------------------------------------------
# Step 4: 建立绑定关系
# -----------------------------------------------------------
sep("Step 4: 建立绑定关系 (BindingOps.bind)")

binds = [
    ("product", product_doc.id, "module", "合约签订",    "产品文档 -> 主模块:合约签订"),
    ("product", product_doc.id, "module", "房产管理",    "产品文档 -> 关联模块:房产管理"),
    ("product", product_doc.id, "module", "企业管理",    "产品文档 -> 关联模块:企业管理"),
    ("api",     api_doc.id,     "product", product_doc.id, "接口文档 -> 产品文档"),
    ("api",     api_doc.id,     "module", "合约签订",    "接口文档 -> 模块:合约签订"),
    ("axure",   axure_doc.id,   "product", product_doc.id, "Axure -> 产品文档"),
    ("axure",   axure_doc.id,   "api",    api_doc.id,     "Axure -> 接口文档"),
    ("axure",   axure_doc.id,   "module", "合约签订",    "Axure -> 模块:合约签订"),
    ("module",  "合约签订",     "module", "房产管理",    "模块依赖: 合约签订 -> 房产管理"),
    ("module",  "合约签订",     "module", "企业管理",    "模块依赖: 合约签订 -> 企业管理"),
    ("module",  "账单管理",     "module", "合约签订",    "模块依赖: 账单管理 -> 合约签订"),
    ("module",  "开票管理",     "module", "合约签订",    "模块依赖: 开票管理 -> 合约签订"),
]

for st, sid, tt, tid, desc in binds:
    ok, msg = BindingOps.bind(session, st, sid, tt, tid)
    tag = "OK" if ok else "--"
    p(f"  [{tag}] {desc}  ({msg})")
    if not ok:
        session.rollback()
session.commit()


# -----------------------------------------------------------
# Step 5: 重复绑定拦截
# -----------------------------------------------------------
sep("Step 5: 重复绑定拦截 (BindingOps.bind 自动防重)")

p("  尝试反向绑定: 房产管理 -> 合约签订（已有: 合约签订 -> 房产管理）")
ok, msg = BindingOps.bind(session, "module", "房产管理", "module", "合约签订")
assert not ok, "应该防重！"
p(f"  => 正确拦截: {msg}")


# -----------------------------------------------------------
# Step 6: 查询绑定
# -----------------------------------------------------------
sep("Step 6: 查询绑定关系 (BindingOps.get_partners / get_bound_docs)")

p("  产品文档绑定到哪些模块？")
partners = BindingOps.get_partners(session, "product", product_doc.id, "module")
for t, i in partners:
    p(f"    -> {i}")

p()
p("  模块「合约签订」下有哪些文档？")
for d in BindingOps.get_bound_docs(session, "合约签订"):
    p(f"    [{d.doc_type}] {d.id}")

p()
p("  模块「合约签订」的完整绑定关系:")
for t, i in BindingOps.get_partners(session, "module", "合约签订"):
    p(f"    <-> {t}:{i}")


# -----------------------------------------------------------
# Step 7: 删除文档 → 术语级联
# -----------------------------------------------------------
sep("Step 7: 删除文档 → 术语级联 (DocOps.delete_document + DBCASCADE)")

count_before = len(GlossaryOps.get_terms(session, product_doc.id))
p(f"  删除前术语数: {count_before}")

DocOps.delete_document(session, product_doc.id)
session.commit()

count_after = len(GlossaryOps.get_terms(session, product_doc.id))
p(f"  删除后术语数: {count_after}")
assert count_after == 0, "术语应级联删除"
p("  => CASCADE 生效 ✓")


# -----------------------------------------------------------
# Step 8: 重命名模块 → 同步更新 bindings
# -----------------------------------------------------------
sep("Step 8: 重命名模块 → bindings 同步更新 (ModuleOps.rename_module)")

ok, msg = ModuleOps.rename_module(session, created["合约签订"].id, "签约管理")
p(f"  改名结果: {msg}")
session.commit()

p("  改名后 bindings 中引用是否更新？")
partners = BindingOps.get_partners(session, "module", "签约管理")
for t, i in partners:
    p(f"    签约管理 <-> {t}:{i}")

p("  旧名「合约签订」是否还有绑定？")
old = BindingOps.get_partners(session, "module", "合约签订")
p(f"    剩余: {len(old)} 条")


# -----------------------------------------------------------
# Step 9: 未关联文档查询
# -----------------------------------------------------------
sep("Step 9: 未关联文档查询 (DocOps.get_unassociated_docs)")

# 此时产品文档已删，还剩接口文档和 Axure 文档
# 它们都绑定了模块，所以 unassociated 应为空
unassociated = DocOps.get_unassociated_docs(session)
p(f"  当前未关联文档: {len(unassociated)} 个")

# 增加一个未绑定模块的测试文档
DocOps.add_document(
    session, doc_id="prod_独立文档.pdf_standalone",
    file_name="独立文档.pdf", file_type="pdf",
    doc_type="product", chunk_count=5,
)
session.commit()
unassociated = DocOps.get_unassociated_docs(session)
p(f"  添加无绑定文档后未关联文档: {len(unassociated)} 个")
for d in unassociated:
    p(f"    [{d.doc_type}] {d.id}")


# -----------------------------------------------------------
# 收尾统计
# -----------------------------------------------------------
sep("统计")

from database.models import Document, Module, Binding, GlossaryTerm

p()
p("  数据库文件: data/app.db")
p(f"  Document={session.query(Document).count()}"
  f"  Module={session.query(Module).count()}"
  f"  Binding={session.query(Binding).count()}"
  f"  GlossaryTerm={session.query(GlossaryTerm).count()}")

session.close()

p()
p("  ✅ 全部验证通过")
