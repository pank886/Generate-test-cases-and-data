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
    Column, Integer, String, Text, DateTime, ForeignKey, LargeBinary,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from database import Base


# ========================================================================
# 模块树
# ========================================================================

class Module(Base):
    """模块树节点。支持 parent_id 邻接表实现层级。"""
    __tablename__ = "modules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
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

    # ── API 类型专属字段（仅 doc_type='api' 时填充）──
    api_name        = Column(String(200), default="")
    api_url         = Column(String(500), default="")
    api_method      = Column(String(10), default="")
    api_description = Column(String(500), default="")
    api_headers     = Column(Text, default="")       # JSON: 名→值映射，如 {"Content-Type": "application/json"}
    api_parameters  = Column(Text, default="")       # JSON: [{name, type, required, default, desc, value}]
    api_returns     = Column(Text, default="")       # JSON: [{name, type, required, default, desc, value}]
    api_annotations = Column(Text, default="")       # JSON: {key: {active, source, ...meta}}
    content_hash    = Column(String(64), default="")  # 文档级 SHA256 hash，感知内容变更

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
    # kind: required=必填字段 / filter=筛选项 / explanation=页面说明（Axure 页面块四段）
    kind = Column(String(20), default="required")
    source_doc = Column(String(200), default="")  # 来源文档标识
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="glossary_terms")

    def __repr__(self):
        return f"<GlossaryTerm {self.term}>"


class PageImage(Base):
    """Axure 页面原图（仅无可提取内容页面的内嵌图，BLOB 直存 SQLite）。

    原图字节存 image_data（多模态可直接取字节分析补提字段）；image_path 保留
    原文件名（重名去重 / 展示 alt）。文档删除时级联删除。
    """
    __tablename__ = "page_images"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(200), ForeignKey("documents.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    page_path = Column(String(300), nullable=False)   # 块路径（如 电表管理）
    page_url = Column(String(300), default="")        # 页面 URL（如 电表管理.html）
    image_path = Column(String(500), default="")      # 原文件名（如 u37.png）
    image_data = Column(LargeBinary, nullable=True)   # 原图字节（多模态分析用）
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<PageImage {self.page_path}>"


# ========================================================================
# 模块场景分析（Phase A 入库预处理产物）
# ========================================================================

class ModuleAnalysis(Base):
    """模块的场景+接口映射分析结果。

    Phase A 按钮触发 LLM 三步分析生成，Phase B 消费时跳过重复场景识别。
    通过 module_id (UUID) 关联 modules 表，模块删除时级联删除。

    三步分析输出（自由文本，每步独立存储）：
      - scenario_analysis: Step 1 — 产品文档 → 测试场景总结
      - ui_flow_analysis:  Step 2 — 场景 + Axure → 逻辑关系总结
      - api_analysis:      Step 3 — 场景 + 逻辑关系 + API → 接口总结
    """
    __tablename__ = "module_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    module_id = Column(String(36), ForeignKey("modules.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    module_name = Column(String(200), nullable=False)  # 冗余，便于前端展示

    # ── 三步分析输出（自由文本，新）──
    scenario_analysis = Column(Text, default="")   # Step 1: 产品文档 → 场景
    ui_flow_analysis = Column(Text, default="")    # Step 2: Axure → 逻辑关系
    api_analysis = Column(Text, default="")        # Step 3: API → 接口映射

    # ── 旧版 JSON（已废弃，保留兼容存量数据）──
    analysis_json = Column(Text, nullable=True, default=None)

    status = Column(String(20), default="draft")          # draft | reviewed | approved
    extracted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    modified_at = Column(DateTime, onupdate=lambda: datetime.now(timezone.utc))
    modified_by = Column(String(100), default="")
    version = Column(Integer, default=1)

    def __repr__(self):
        return f"<ModuleAnalysis {self.module_name}>"


# ========================================================================
# 文档切块（product/axure 正文 + 摘要）
# ========================================================================

class DocumentChunk(Base):
    """文档切块：原文 + 阶段1简单摘要 + 阶段2分析后精细总结。

    与 documents 表通过 doc_id 关联（非外键，允许独立删除）。
    阶段1入库时填充 content + simple_summary。
    阶段2分析后填充 analyzed_summary + analyzed_tags + analyzed_at。
    """
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doc_id = Column(String(200), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)

    # ── 阶段1: 原文 + 简单摘要 ──
    content = Column(Text, nullable=False)           # 切块原文
    simple_summary = Column(Text, default="")        # 入库时 LLM 生成简单摘要

    # ── 阶段2: 模块分析后的精细总结 ──
    analyzed_summary = Column(Text, default="")      # 分析后精细总结（含场景归属、接口关联）
    analyzed_tags = Column(Text, default="")         # JSON: ["标签1","标签2"]
    analyzed_at = Column(DateTime, nullable=True)    # 分析时间

    # ── 元数据 ──
    chunk_type = Column(String(20), default="text")  # text | page | section
    page_name = Column(String(200), default="")      # Axure 页面名 / PDF 章节标题
    token_count = Column(Integer, default=0)

    def __repr__(self):
        return f"<DocumentChunk {self.doc_id}[{self.chunk_index}]>"


# ========================================================================
# 补偿任务（LLM 摘要失败 / ChromaDB 重建等异步补偿）
# ========================================================================

class CompensationTask(Base):
    """后台补偿任务队列。

    worker 线程轮询 pending 状态的任务，按 task_type 分发处理。
    """
    __tablename__ = "compensation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(50), nullable=False)       # simple_summary / analyzed_summary / chroma_rebuild / api_search_text
    payload = Column(Text, nullable=False)                # JSON: {"doc_id": "...", "chunk_indices": [...]}
    status = Column(String(20), nullable=False, default="pending")  # pending / running / success / failed
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    error_msg = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                         onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_compensation_status", "status"),
    )

    def __repr__(self):
        return f"<CompensationTask {self.task_type}:{self.status}>"
