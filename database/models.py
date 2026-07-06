"""数据模型：模块 / 文档 / 绑定关系 / 术语表。

数据流关系（用户描述）:

产品文档 ──┬── 绑定 → 主模块（名称）          ← 未来创建
           ├── 绑定 → 接口文档（多个）
           ├── 绑定 → Axure 文档（多个）
           ├── 绑定 → 关联模块（多个）        ← 依赖其他模块功能
           └── 始终持有 → 术语表              ← 文档删则术语删（含手动追加）

接口文档 ──┬── 绑定 → 产品文档（一个）
           ├── 绑定 → Axure 文档（一个）
           └── 绑定 → 模块（名称）            ← 未来创建

Axure 文档 ─┬── 绑定 → 接口文档（多个）
            ├── 绑定 → 产品文档（一个）
            └── 绑定 → 模块（名称）          ← 未来创建

模块 ↔ 模块 ── 关联模块
  一个模块可能依赖另一模块的功能。
  例如"合约签订"依赖"房产管理""企业管理"提供基础数据，
  出单后由"账单管理""开票管理"处理后续。
  这种 module↔module 也存入 bindings 表。

规则:
  - 绑定以模块名称为主键关联
  - 模块间、文档间均可绑定，但不可重复（A→B 后禁止 B→A）
  - 术语始终随产品文档生命周期，手工追加的也同文档绑定，文档删除全删
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey,
    UniqueConstraint, Index, CheckConstraint,
)
from sqlalchemy.orm import relationship

from database import Base


# ========================================================================
# 模块树
# ========================================================================

class Module(Base):
    """模块树节点。支持 parent_id 邻接表实现层级。"""
    __tablename__ = "modules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4())[:8])
    name = Column(String(200), nullable=False, unique=True, index=True)
    parent_id = Column(String(36), ForeignKey("modules.id"), nullable=True)
    path = Column(String(500), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    children = relationship("Module", backref="parent", remote_side="Module.id",
                            lazy="selectin")

    def __repr__(self):
        return f"<Module {self.name}>"


# ========================================================================
# 文档（三种类型的统一抽象）
# ========================================================================

class Document(Base):
    """已上传的文档。

    doc_type: product   — 产品文档（PDF/DOCX）
              api      — 接口文档（MD）
              axure    — Axure 原型（ZIP）
    """
    __tablename__ = "documents"

    id = Column(String(200), primary_key=True)  # doc_id，与 ChromaDB 的 metadata.doc_id 一致
    file_name = Column(String(300), nullable=False)
    file_type = Column(String(20), nullable=False)   # pdf / docx / md / zip
    doc_type = Column(String(20), nullable=False)     # product / api / axure
    upload_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    status = Column(String(20), default="pending")    # pending / bound
    chunk_count = Column(Integer, default=0)

    # 关联
    glossary_terms = relationship("GlossaryTerm", back_populates="document",
                                  cascade="all, delete-orphan",
                                  lazy="selectin")

    def __repr__(self):
        return f"<Document {self.doc_type}:{self.file_name}>"


# ========================================================================
# 绑定关系（核心关联表）
# ========================================================================

class Binding(Base):
    """文档之间、文档与模块之间的绑定关系。

    使用 left/right 规范化存储，防止 A→B / B→A 重复：
      写入时 ((source_type, source_id), (target_type, target_id))
      按 (type, id) 排序后分别存入 left_* / right_*
      UNIQUE(left_type, left_id, right_type, right_id) 天然防重
    """
    __tablename__ = "bindings"

    DOC_TYPES = ("product", "api", "axure", "module")

    id = Column(Integer, primary_key=True, autoincrement=True)
    left_type = Column(String(20), nullable=False)
    left_id = Column(String(200), nullable=False)
    right_type = Column(String(20), nullable=False)
    right_id = Column(String(200), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("left_type", "left_id", "right_type", "right_id",
                         name="uq_binding"),
        Index("ix_binding_left", "left_type", "left_id"),
        Index("ix_binding_right", "right_type", "right_id"),
    )

    @staticmethod
    def normalize(a_type: str, a_id: str, b_type: str, b_id: str):
        """规范化双边，保证 (left, right) 顺序一致。"""
        left, right = sorted(
            [(a_type, a_id), (b_type, b_id)],
            key=lambda x: (x[0], x[1]),
        )
        return left[0], left[1], right[0], right[1]

    @staticmethod
    def make(source_type: str, source_id: str,
             target_type: str, target_id: str):
        """构造一条规范化绑定的属性字典。"""
        lt, li, rt, ri = Binding.normalize(source_type, source_id,
                                            target_type, target_id)
        return {
            "left_type": lt,
            "left_id": li,
            "right_type": rt,
            "right_id": ri,
        }

    def __repr__(self):
        return f"<Binding {self.left_type}:{self.left_id} ↔ {self.right_type}:{self.right_id}>"


# ========================================================================
# 术语表（始终随产品文档生命周期）
# ========================================================================

class GlossaryTerm(Base):
    """产品文档提取的业务术语。文档删除时级联删除。"""
    __tablename__ = "glossary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(200), ForeignKey("documents.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    term = Column(String(200), nullable=False)
    definition = Column(Text, default="")
    notes = Column(Text, default="")
    source_doc = Column(String(200), default="")  # 来源文档标识
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="glossary_terms")

    def __repr__(self):
        return f"<GlossaryTerm {self.term}>"
